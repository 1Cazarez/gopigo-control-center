"""Dashboard tab -- the control center: the live camera, the robot's battery, distance sensor and IMU, and
the game controller on one screen, plus driving with the controller.

Driving: the controller is read on the Tk thread about 20 times a second, and each motor update goes over a
persistent SSH channel to a small process on the robot (see remote_scripts.py) whose per-update SPI transfer
is written in C (motor_stream_spi.c, compiled on the Pi each time driving starts). If the Pi has no C
compiler, it falls back to a pure-Python driver.

Driving uses racing controls: ZR is the gas, ZL brakes and reverses, the left stick steers (only while
moving, like a car), holding L or R spins the robot on the spot, and the right stick raises or lowers the top
speed. These are fixed; the other buttons can be bound to actions -- lights, camera and so on -- in the
Controller Settings window. The bindings are saved by user_settings.py.
"""

import io
import threading
import tkinter as tk
import urllib.request
from tkinter import messagebox, ttk

import user_settings
from constants import VENV_ACTIVATE, login_shell
from dependencies import Image, ImageTk, pygame
from gamepad import STANDARD_BUTTONS, Gamepad, button_label, list_controllers
from remote_scripts import (
    MOTOR_STREAM_C_SOURCE,
    MOTOR_STREAM_SCRIPT,
    MOTOR_STREAM_SCRIPT_PY_FALLBACK,
    REMOTE_KERNEL_DIR,
    REMOTE_MOTOR_STREAM_C_PATH,
    REMOTE_MOTOR_STREAM_PATH,
    REMOTE_MOTOR_STREAM_SO_PATH,
)
from theme import CARD_BG, FONT_MONO_SMALL, MUTED, style_button
from widgets import ControllerView

from .drive_tab import MAX_STOP_CM, MIN_STOP_CM
from .lights_tab import BLINKER_ON, OFF
from .sensors_panel import SENSORS_OFF

TICK_MS = 50            # how often the controller is read (and a motor update sent while driving)
OPEN_TIMEOUT = 15       # seconds to wait for the robot to open a channel
SPEED_STEP = 50         # deg/sec per press of a speed-up / slow-down button
MIN_SPEED, MAX_SPEED = 50, 500

# The racing controls. These buttons always drive, so they can't be bound to actions.
DRIVE_BUTTONS = {
    "zr": "Gas",
    "zl": "Brake / reverse",
    "leftshoulder": "Spin left (hold)",
    "rightshoulder": "Spin right (hold)",
}
DRIVE_HELP = "ZR gas · ZL brake/reverse · left stick steers · hold L/R to spin · right stick up/down sets top speed"
SPIN_SHARE = 0.6   # spinning on the spot runs the wheels at this share of the top speed
SPEED_RATE = 200   # deg/sec the top speed changes per second with the right stick pushed all the way


def racing_drive(sticks, pressed, top_speed, deadzone, block_forward=False):
    """(left wheel, right wheel, turn signal) in deg/sec for the racing controls.

    block_forward stops the gas from driving forward (something is too close ahead); reversing and spinning
    still work, to get away from it.

    Steering works like a car: it bends the path only while the robot moves, and at full lock the inner wheel
    stops. The turn signal follows the steering, so reversing with the stick to the right still signals right.
    """
    spin = int(top_speed * SPIN_SHARE)
    spin_left, spin_right = "leftshoulder" in pressed, "rightshoulder" in pressed
    if spin_left != spin_right:
        return (-spin, spin, "L") if spin_left else (spin, -spin, "R")

    forward = (("zr" in pressed) - ("zl" in pressed)) * top_speed
    if block_forward and forward > 0:
        forward = 0
    steer = sticks[0] if abs(sticks[0]) >= deadzone else 0.0
    if forward == 0:
        return 0, 0, "-"
    left, right = forward * (1 + steer), forward * (1 - steer)
    scale = max(abs(left), abs(right)) / top_speed  # keep the outer wheel within the top speed
    signal = "R" if steer > 0 else "L" if steer < 0 else "-"
    return int(left / scale), int(right / scale), signal


# Short names for the Dashboard's button legend, where space is tight.
SHORT_BUTTONS = {
    "east": "A", "south": "B", "north": "X", "west": "Y",
    "dpup": "↑", "dpdown": "↓", "dpleft": "←", "dpright": "→",
    "back": "−", "start": "+", "guide": "Home", "leftstick": "LS", "rightstick": "RS",
}
SHORT_ACTIONS = {
    "drive": "Drive on/off", "estop": "E-stop", "speed_up": "Faster", "speed_down": "Slower",
    "left_blinker": "L blinker", "right_blinker": "R blinker", "hazards": "Hazards", "eyes": "Eyes on/off",
    "eye_color": "Eye color", "lights_off": "Lights off", "camera": "Camera", "sensors": "Sensors", "safety": "Safety stop",
}
LEGEND_DRIVING = [("ZR", "Gas"), ("ZL", "Brake/rev"), ("L/R", "Spin"), ("L-stick", "Steer"), ("R-stick", "Top speed")]
LEGEND_COLUMNS = 3


# What a controller button can do: action name -> label shown in the settings window.
ACTIONS = {
    "none": "Nothing",
    "drive": "Start / stop driving",
    "estop": "Emergency stop",
    "speed_up": "Speed up",
    "speed_down": "Slow down",
    "left_blinker": "Left blinker on/off",
    "right_blinker": "Right blinker on/off",
    "hazards": "Hazard lights on/off",
    "eyes": "Eyes on/off",
    "eye_color": "Next eye color (then off)",
    "lights_off": "All lights off",
    "camera": "Camera on/off",
    "sensors": "Sensors on/off",
    "safety": "Safety stop on/off",
}


class DashboardTabMixin:
    def _build_dashboard_tab(self):
        self.gamepad = None
        self.gamepad_pressed = set()
        self.controller_settings_win = None
        self.drive_starting = False
        self.hazards_on = False
        self.shown_signal = "-"     # the turn signal last sent while driving
        self.top_speed_changing = False  # the right stick is changing the top speed
        self.top_speed = 0.0
        self.camera_reader = None   # identifies the running frame reader; None stops it
        self.camera_frame = None    # the newest decoded frame, waiting to be shown
        self.camera_photo = None
        self.camera_image_item = None
        self.camera_size = (640, 480)
        s = self.ui_scale

        outer = ttk.Frame(self.dashboard_tab, style="Card.TFrame", padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(0, weight=1)

        # -- Camera --
        camera = ttk.Frame(outer, style="Card.TFrame")
        camera.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        head = ttk.Frame(camera, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Camera", style="CardBold.TLabel").pack(side="left")
        ttk.Label(head, textvariable=self.camera_status_var, style="CardMuted.TLabel").pack(side="left", padx=10)
        self.dash_camera_btn = style_button(tk.Button(head, text="Start Camera", command=self.toggle_camera_stream), "success")
        self.dash_camera_btn.pack(side="right")
        style_button(tk.Button(head, text="Reset", command=self.reset_camera), "warning").pack(side="right", padx=6)
        self.camera_canvas = tk.Canvas(camera, bg="#12161d", highlightthickness=0, width=1, height=1)
        self.camera_canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.camera_canvas.bind("<Configure>", self._on_camera_canvas_resize)
        self.camera_status_var.trace_add("write", lambda *a: self._sync_camera_button())

        # -- Robot sensors --
        self._build_sensor_panel(outer).grid(row=0, column=1, sticky="nsew")

        # -- Controller --
        controller = ttk.Frame(outer, style="Card.TFrame")
        controller.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.controller_view = ControllerView(controller, scale=s * 0.85)
        self.controller_view.pack(side="left")
        self.controller_view.set_enabled(False)
        # Which lights should be on, as on the Manual Control tab (click one to switch it).
        self._add_lights_view(controller, s * 0.75).pack(side="right", padx=(12, 0))

        side = ttk.Frame(controller, style="Card.TFrame")
        side.pack(side="left", fill="both", expand=True, padx=(16, 0))

        pick = ttk.Frame(side, style="Card.TFrame")
        pick.pack(fill="x")
        ttk.Label(pick, text="Controller:", style="Card.TLabel").pack(side="left")
        self.controller_choice_var = tk.StringVar()
        self.controller_combo = ttk.Combobox(pick, textvariable=self.controller_choice_var, state="readonly", width=24)
        self.controller_combo.pack(side="left", padx=6)
        self.controller_combo.bind("<<ComboboxSelected>>", lambda e: self._open_selected_controller())
        style_button(tk.Button(pick, text="Refresh", command=self.refresh_controllers), "neutral").pack(side="left")
        style_button(tk.Button(pick, text="Settings...", command=self.open_controller_settings), "neutral").pack(
            side="left", padx=6
        )

        drive = ttk.Frame(side, style="Card.TFrame")
        drive.pack(fill="x", pady=10)
        self.drive_btn = style_button(tk.Button(drive, text="Start Driving", command=self.toggle_driving), "success", big=True)
        self.drive_btn.pack(side="left")
        style_button(tk.Button(drive, text="EMERGENCY STOP", command=self.emergency_stop), "danger", big=True).pack(
            side="left", padx=10
        )
        self._safety_stop_checkbox(drive).pack(side="left")

        speed = ttk.Frame(side, style="Card.TFrame")
        speed.pack(fill="x")
        ttk.Label(speed, text="Top speed (deg/sec):", style="Card.TLabel").pack(side="left")
        self.controller_max_speed_var = tk.IntVar(value=self.settings["max_speed"])
        tk.Scale(
            speed, from_=MIN_SPEED, to=MAX_SPEED, resolution=10, orient="horizontal",
            variable=self.controller_max_speed_var, length=int(220 * s), bg=CARD_BG, highlightthickness=0,
            troughcolor="#e2e7ee", bd=0, command=lambda v: self._save_setting("max_speed", int(float(v))),
        ).pack(side="left", padx=8)
        self.motor_dps_var = tk.StringVar(value="Motors: --")
        ttk.Label(speed, textvariable=self.motor_dps_var, style="Card.TLabel").pack(side="left", padx=8)

        # What each button does, in a compact legend (it follows the bindings in Settings).
        self.controls_legend_var = tk.StringVar()
        tk.Label(
            side, textvariable=self.controls_legend_var, font=FONT_MONO_SMALL, bg=CARD_BG, fg=MUTED, justify="left",
        ).pack(anchor="w", pady=(8, 0))
        self._refresh_controls_legend()
        self.controller_status_var = tk.StringVar(value="")
        ttk.Label(side, textvariable=self.controller_status_var, style="CardMuted.TLabel").pack(anchor="w", pady=(4, 0))
        self.raw_buttons_var = tk.StringVar(value="")
        ttk.Label(side, textvariable=self.raw_buttons_var, style="CardMuted.TLabel").pack(anchor="w")

        if pygame is None:
            self.controller_status_var.set("pygame is not installed, so controllers can't be used. Run: pip install pygame")
        else:
            self.refresh_controllers()
        self._draw_camera_placeholder()
        self.root.after(TICK_MS, self._dashboard_tick)

    def _save_setting(self, key, value):
        self.settings[key] = value
        user_settings.save(self.settings)

    # ---------------- The tick: controller input and camera frames ----------------
    def _dashboard_tick(self):
        try:
            self._poll_controller()
            self._show_camera_frame()
        except Exception as e:
            self._set_status(f"Dashboard error: {e}")
        self.root.after(TICK_MS, self._dashboard_tick)

    def _poll_controller(self):
        pad = self.gamepad
        if pad is None:
            return
        if not pad.attached():
            self._close_gamepad()
            self.controller_status_var.set("The controller disconnected. Reconnect it and click Refresh.")
            if self.controller_active:
                self._stop_driving("The controller disconnected, so driving stopped.")
            return

        sticks, pressed = pad.read()
        self._adjust_top_speed(sticks[3])
        self.controller_view.update_state(sticks, pressed)
        if not pad.standard:
            self.raw_buttons_var.set("Pressed: " + (", ".join(sorted(button_label(b) for b in pressed)) or "none"))
        settings_win = self.controller_settings_win
        if settings_win is not None:
            settings_win.view.update_state(sticks, pressed)
        for name in pressed - self.gamepad_pressed:
            if settings_win is not None:
                settings_win.select(name)  # while choosing bindings, a press picks the button rather than acting
            else:
                self._run_action(name)
        self.gamepad_pressed = pressed

        if self.controller_active and self.motor_channel is not None:
            self._send_motor_update(sticks, pressed)

    def _refresh_controls_legend(self):
        bindings = self.settings["bindings"]
        order = [b for b in STANDARD_BUTTONS if b in SHORT_BUTTONS]
        entries = list(LEGEND_DRIVING) + [
            (SHORT_BUTTONS[b], SHORT_ACTIONS.get(bindings[b], bindings[b]))
            for b in order if bindings.get(b, "none") != "none"
        ]
        cells = [f"{key:>7} {text:<12}" for key, text in entries]
        rows = [cells[i:i + LEGEND_COLUMNS] for i in range(0, len(cells), LEGEND_COLUMNS)]
        self.controls_legend_var.set("\n".join("".join(row).rstrip() for row in rows))

    def _adjust_top_speed(self, right_y):
        """Right stick up raises the top speed, down lowers it, smoothly while held. Saved when let go."""
        if abs(right_y) < self.settings["deadzone"]:
            if self.top_speed_changing:
                self.top_speed_changing = False
                self._save_setting("max_speed", self.controller_max_speed_var.get())
            return
        if not self.top_speed_changing:
            self.top_speed_changing = True
            self.top_speed = float(self.controller_max_speed_var.get())
        self.top_speed -= right_y * SPEED_RATE * TICK_MS / 1000  # pushing up reads as negative
        self.top_speed = max(MIN_SPEED, min(MAX_SPEED, self.top_speed))
        self.controller_max_speed_var.set(int(self.top_speed))

    def _run_action(self, button):
        if button in DRIVE_BUTTONS:
            return  # these drive the robot
        action = self.settings["bindings"].get(button, "none")
        if action == "none":
            return
        self.controller_status_var.set(f"{button_label(button)}: {ACTIONS.get(action, action)}")
        handlers = {
            "drive": self.toggle_driving,
            "estop": self.emergency_stop,
            "speed_up": lambda: self._change_speed(SPEED_STEP),
            "speed_down": lambda: self._change_speed(-SPEED_STEP),
            "left_blinker": lambda: self._toggle_lights(["left_blinker"], "Left blinker"),
            "right_blinker": lambda: self._toggle_lights(["right_blinker"], "Right blinker"),
            "hazards": self._toggle_hazards,
            "eyes": lambda: self._toggle_lights(["left_eye", "right_eye"], "Eyes"),
            "eye_color": self._next_eye_color,
            "lights_off": self._dashboard_lights_off,
            "camera": self.toggle_camera_stream,
            "sensors": self.toggle_sensors,
            "safety": self._toggle_safety_stop,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

    def _change_speed(self, step):
        speed = max(MIN_SPEED, min(MAX_SPEED, self.controller_max_speed_var.get() + step))
        self.controller_max_speed_var.set(speed)
        self._save_setting("max_speed", speed)
        self.controller_status_var.set(f"Max speed: {speed} deg/sec")

    def _toggle_hazards(self):
        if not self._require_connection():
            return
        self.hazards_on = not self.hazards_on
        self.shown_signal = "-"  # both blinkers are now in the same state, so no turn signal is showing
        color = BLINKER_ON if self.hazards_on else OFF
        self._set_lights(
            {"left_blinker": color, "right_blinker": color}, f"Hazard lights {'on' if self.hazards_on else 'off'}"
        )

    def _dashboard_lights_off(self):
        self.hazards_on = False
        self.shown_signal = "-"
        self._all_lights_off()

    # ---------------- Controllers ----------------
    def refresh_controllers(self):
        self._close_gamepad()
        names = list_controllers()
        self.controller_combo.config(values=names)
        if names:
            self.controller_combo.current(0)
            self._open_selected_controller()
        else:
            self.controller_choice_var.set("")
            self.controller_status_var.set("No controller found. Connect one (Bluetooth or USB) and click Refresh.")

    def _open_selected_controller(self):
        self._close_gamepad()
        choice = self.controller_choice_var.get()
        if ":" not in choice:
            return
        try:
            self.gamepad = Gamepad(int(choice.split(":")[0]))
        except Exception as e:
            self.controller_status_var.set(f"Couldn't open the controller: {e}")
            return
        self.controller_view.set_enabled(self.gamepad.standard)
        if self.gamepad.standard:
            self.controller_status_var.set("Controller ready.")
            self.raw_buttons_var.set("")
        else:
            self.controller_status_var.set(
                "Controller ready. Its buttons only have numbers, so the picture can't show them."
            )

    def _close_gamepad(self):
        if self.gamepad is not None:
            self.gamepad.close()
        self.gamepad = None
        self.gamepad_pressed = set()
        self.controller_view.set_enabled(False)

    def open_controller_settings(self):
        if self.controller_settings_win is not None:
            self.controller_settings_win.win.lift()
            return
        self.controller_settings_win = ControllerSettingsWindow(self)

    # ---------------- Driving ----------------
    def toggle_driving(self):
        if self.controller_active or self.motor_channel is not None:
            self._stop_driving("Stopped driving.")
            return
        if self.drive_starting:
            return
        if pygame is None:
            messagebox.showerror("Missing dependency", "Install pygame first:\npip install pygame")
            return
        if not self._require_connection():
            return
        if self.gamepad is None:
            messagebox.showwarning("No controller", "Connect a controller, click Refresh and pick it first.")
            return

        self.drive_starting = True
        self.drive_btn.config(text="Starting...", state="disabled")
        self.controller_status_var.set("Starting the motor driver on the robot...")

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass
                with sftp.file(REMOTE_MOTOR_STREAM_C_PATH, "w") as f:
                    f.write(MOTOR_STREAM_C_SOURCE)
                with sftp.file(REMOTE_MOTOR_STREAM_PATH, "w") as f:
                    f.write(MOTOR_STREAM_SCRIPT)
                sftp.close()

                # Compile the C SPI driver into a shared library. This runs every time driving starts -- it's
                # well under a second, and keeps the app from needing a separate install step on the Pi.
                compile_cmd = f"gcc -O2 -shared -fPIC -o {REMOTE_MOTOR_STREAM_SO_PATH} {REMOTE_MOTOR_STREAM_C_PATH}"
                _, stdout, _ = self.ssh_client.exec_command(login_shell(compile_cmd), timeout=15)
                driver = "C driver"
                if stdout.channel.recv_exit_status() != 0:
                    driver = "Python driver -- the C one didn't compile; is gcc installed on the Pi?"
                    sftp = self.ssh_client.open_sftp()
                    with sftp.file(REMOTE_MOTOR_STREAM_PATH, "w") as f:
                        f.write(MOTOR_STREAM_SCRIPT_PY_FALLBACK)
                    sftp.close()

                channel = self.ssh_client.get_transport().open_session(timeout=OPEN_TIMEOUT)
                channel.exec_command(login_shell(f"{VENV_ACTIVATE} && python3 -u {REMOTE_MOTOR_STREAM_PATH}"))
                self.root.after(0, lambda: self._on_driving_started(channel, driver))
            except Exception as e:
                error = str(e)
                self.root.after(0, lambda: self._on_driving_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_driving_started(self, channel, driver):
        self.drive_starting = False
        if self.ssh_client is None:  # disconnected while starting
            channel.close()
            self._on_driving_failed("the connection closed")
            return
        self.motor_channel = channel
        self.controller_active = True
        self.shown_signal = "-"
        self.drive_btn.config(text="Stop Driving", state="normal")
        style_button(self.drive_btn, "danger", big=True)
        self.controller_status_var.set(f"Driving ({driver}).")
        # Driving goes with seeing where you're going: start the camera and sensors too, unless they're
        # already running or starting.
        if self.camera_channel is None and "starting" not in self.camera_status_var.get():
            self.toggle_camera_stream()
        if self.telemetry_channel is None:
            self.toggle_sensors()

    def _on_driving_failed(self, error):
        self.drive_starting = False
        self.drive_btn.config(text="Start Driving", state="normal")
        style_button(self.drive_btn, "success", big=True)
        self.controller_status_var.set(f"Couldn't start driving: {error}")

    def _send_motor_update(self, sticks, pressed):
        channel = self.motor_channel
        if channel.exit_status_ready():  # the driver on the robot quit
            error = b""
            while channel.recv_stderr_ready():
                error += channel.recv_stderr(4096)
            lines = error.decode(errors="replace").strip().splitlines()
            self._stop_driving(f"The robot's motor driver stopped: {lines[-1] if lines else 'no error message'}")
            return

        left, right, signal = racing_drive(
            sticks, pressed, self.controller_max_speed_var.get(), self.settings["deadzone"], self.obstacle_close
        )

        line = f"{left},{right}"
        if not self.hazards_on:
            if not self.turn_signals_var.get():
                signal = "-"
            if signal != self.shown_signal:  # only when it changes, so the blinkers don't flicker
                line += f",{signal}"
                self.shown_signal = signal
                self._record_lights(left_blinker=signal == "L", right_blinker=signal == "R")
        try:
            channel.send(line + "\n")
        except Exception as e:
            self._stop_driving(f"Lost the motor channel: {e}")
            return
        self.motor_dps_var.set(f"Motors: L={left}  R={right}")

    def _stop_driving(self, message):
        self.controller_active = False
        channel, self.motor_channel = self.motor_channel, None
        if channel is not None:
            try:
                channel.send("0,0\n")
                channel.close()  # the driver stops the motors and turns off any blinker it lit as it exits
            except Exception:
                pass
            self._record_lights(left_blinker=False, right_blinker=False)
            self.hazards_on = False
        self.drive_btn.config(text="Start Driving", state="normal")
        style_button(self.drive_btn, "success", big=True)
        self.motor_dps_var.set("Motors: --")
        self.controller_status_var.set(message)

    def emergency_stop(self):
        self._stop_driving("Emergency stopped.")
        if self.ssh_client is not None:
            self._drive_cmd("gpg.stop()", "-")

    # ---------------- Camera ----------------
    def _sync_camera_button(self):
        status = self.camera_status_var.get()
        if "starting" in status or "resetting" in status:
            self.dash_camera_btn.config(text="Starting...", state="disabled")
        elif self.camera_channel is not None:
            self.dash_camera_btn.config(text="Stop Camera", state="normal")
            style_button(self.dash_camera_btn, "danger")
        else:
            self.dash_camera_btn.config(text="Start Camera", state="normal")
            style_button(self.dash_camera_btn, "success")

    def _dashboard_camera_ready(self, url):
        """Called once the robot's camera stream is up: start pulling its frames into the Dashboard."""
        if Image is None:
            self._draw_camera_placeholder(
                "Install Pillow to see the camera here: pip install Pillow\n(or use Open in Browser on the Jupyter & Camera tab)"
            )
            return
        token = object()
        self.camera_reader = token
        threading.Thread(target=self._camera_frames_loop, args=(url, token), daemon=True).start()

    def _dashboard_camera_stopped(self):
        self.camera_reader = None
        self.camera_frame = None
        self._draw_camera_placeholder()

    def _camera_frames_loop(self, url, token):
        """Read the MJPEG stream: each frame is a small header block with a Content-Length, then the JPEG."""
        try:
            with urllib.request.urlopen(url + "stream.mjpg", timeout=10) as stream:
                while self.camera_reader is token:
                    length = None
                    while True:
                        line = stream.readline()
                        if not line:
                            raise ConnectionError("the stream ended")
                        line = line.strip()
                        if line.lower().startswith(b"content-length:"):
                            length = int(line.split(b":", 1)[1])
                        elif not line and length:
                            break
                    frame = Image.open(io.BytesIO(stream.read(length)))
                    frame.draft("RGB", self.camera_size)  # lets the JPEG decoder skip detail we won't show
                    frame.thumbnail(self.camera_size, Image.Resampling.BILINEAR)
                    self.camera_frame = frame
        except Exception as e:
            error = str(e)
            if self.camera_reader is token:
                self.camera_reader = None
                self.root.after(0, lambda: self._draw_camera_placeholder(f"Lost the camera picture: {error}"))

    def _show_camera_frame(self):
        frame, self.camera_frame = self.camera_frame, None
        if frame is None or self.camera_reader is None:
            return
        self.camera_photo = ImageTk.PhotoImage(frame)  # keep a reference, or Tk shows nothing
        w, h = self.camera_size
        if self.camera_image_item is None:
            self.camera_canvas.delete("all")
            self.camera_image_item = self.camera_canvas.create_image(w / 2, h / 2, image=self.camera_photo)
        else:
            self.camera_canvas.coords(self.camera_image_item, w / 2, h / 2)
            self.camera_canvas.itemconfig(self.camera_image_item, image=self.camera_photo)

    def _on_camera_canvas_resize(self, event):
        self.camera_size = (max(event.width, 1), max(event.height, 1))
        if self.camera_image_item is None:
            self._draw_camera_placeholder(self._camera_placeholder_text)

    _camera_placeholder_text = None

    def _draw_camera_placeholder(self, text=None):
        self._camera_placeholder_text = text
        canvas = self.camera_canvas
        canvas.delete("all")
        self.camera_image_item = None
        w, h = self.camera_size
        canvas.create_text(
            w / 2, h / 2, text=text or "Camera off -- click Start Camera", fill=MUTED, font=FONT_MONO_SMALL,
            width=max(w - 40, 100), justify="center",
        )

    # ---------------- Disconnect ----------------
    def _dashboard_on_disconnect(self):
        self.drive_starting = False
        self._stop_driving("Disconnected.")
        self._stop_telemetry(SENSORS_OFF)


class ControllerSettingsWindow:
    """Choose what each controller button does. Pick a button by clicking it on the picture or pressing it."""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("Controller Settings")
        self.win.configure(bg=CARD_BG)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(self.win, style="Card.TFrame", padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Controller Settings", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(
            frame,
            text="Click a button on the picture, or press it on the controller, then choose what it does. "
            "Buttons don't do anything else while this window is open. ZL, ZR, L, R and the sticks always "
            "drive: " + DRIVE_HELP + ".",
            style="CardMuted.TLabel", wraplength=int(620 * app.ui_scale), justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))

        self.view = ControllerView(frame, scale=app.ui_scale, on_click=self.select)
        self.view.grid(row=2, column=0, sticky="n")
        self.view.set_enabled(True)

        side = ttk.Frame(frame, style="Card.TFrame")
        side.grid(row=2, column=1, sticky="nw", padx=(20, 0))
        self.button_var = tk.StringVar()
        ttk.Label(side, textvariable=self.button_var, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(side, text="does:", style="Card.TLabel").pack(anchor="w", pady=(6, 2))
        self.action_combo = ttk.Combobox(side, state="readonly", values=list(ACTIONS.values()), width=26)
        self.action_combo.pack(anchor="w")
        self.action_combo.bind("<<ComboboxSelected>>", lambda e: self._set_action())

        ttk.Label(side, text="Stick dead zone:", style="Card.TLabel").pack(anchor="w", pady=(16, 0))
        self.deadzone_var = tk.DoubleVar(value=app.settings["deadzone"])
        tk.Scale(
            side, from_=0.05, to=0.4, resolution=0.01, orient="horizontal", variable=self.deadzone_var,
            length=int(200 * app.ui_scale), bg=CARD_BG, highlightthickness=0, troughcolor="#e2e7ee", bd=0,
            command=lambda v: app._save_setting("deadzone", float(v)),
        ).pack(anchor="w")
        ttk.Label(side, text="(how far a stick must move before the robot does)", style="CardMuted.TLabel").pack(anchor="w")

        ttk.Label(side, text="Safety stop distance (cm):", style="Card.TLabel").pack(anchor="w", pady=(12, 0))
        self.obstacle_var = tk.IntVar(value=app.settings["obstacle_stop_cm"])
        tk.Scale(
            side, from_=MIN_STOP_CM, to=MAX_STOP_CM, orient="horizontal", variable=self.obstacle_var,
            length=int(200 * app.ui_scale), bg=CARD_BG, highlightthickness=0, troughcolor="#e2e7ee", bd=0,
            command=lambda v: app._set_stop_distance(int(float(v))),
        ).pack(anchor="w")
        app._safety_stop_checkbox(side).pack(anchor="w")
        ttk.Label(
            side, text="(no driving forward closer than this; needs the sensors on)", style="CardMuted.TLabel",
        ).pack(anchor="w")

        style_button(tk.Button(side, text="Reset to Defaults", command=self._reset), "neutral").pack(anchor="w", pady=(16, 0))

        self.summary_var = tk.StringVar()
        ttk.Label(frame, text="Current buttons", style="CardBold.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 2))
        tk.Label(
            frame, textvariable=self.summary_var, font=FONT_MONO_SMALL, bg=CARD_BG, justify="left", anchor="w",
        ).grid(row=4, column=0, columnspan=2, sticky="w")
        style_button(tk.Button(frame, text="Close", command=self.close), "primary").grid(
            row=5, column=1, sticky="e", pady=(12, 0)
        )

        self.buttons = app.gamepad.buttons() if app.gamepad is not None else list(STANDARD_BUTTONS)
        self._refresh_summary()
        self.select(next(b for b in self.buttons if b not in DRIVE_BUTTONS))

    def select(self, name):
        self.selected = name
        self.view.set_selected(name)
        self.button_var.set(button_label(name))
        if name in DRIVE_BUTTONS:
            self.action_combo.set(f"Driving: {DRIVE_BUTTONS[name]}")
            self.action_combo.config(state="disabled")
        else:
            self.action_combo.config(state="readonly")
            self.action_combo.set(ACTIONS.get(self.app.settings["bindings"].get(name, "none"), "Nothing"))

    def _set_action(self):
        label = self.action_combo.get()
        action = next(key for key, text in ACTIONS.items() if text == label)
        bindings = self.app.settings["bindings"]
        if action == "none":
            bindings.pop(self.selected, None)
        else:
            bindings[self.selected] = action
        self.app._save_setting("bindings", bindings)
        self._refresh_summary()
        self.app._refresh_controls_legend()

    def _reset(self):
        defaults = user_settings.DEFAULTS
        self.app._save_setting("bindings", dict(defaults["bindings"]))
        self.app._save_setting("deadzone", defaults["deadzone"])
        self.deadzone_var.set(defaults["deadzone"])
        self.app._set_stop_distance(defaults["obstacle_stop_cm"])
        self.obstacle_var.set(defaults["obstacle_stop_cm"])
        self.select(self.selected)
        self._refresh_summary()
        self.app._refresh_controls_legend()

    def _refresh_summary(self):
        bindings = self.app.settings["bindings"]
        entries = [f"{button_label(b):<20}{text}" for b, text in DRIVE_BUTTONS.items() if b in self.buttons]
        entries += [
            f"{button_label(b):<20}{ACTIONS.get(bindings[b], bindings[b])}"
            for b in self.buttons if b in bindings and b not in DRIVE_BUTTONS
        ]
        if not entries:
            self.summary_var.set("(none)")
            return
        half = (len(entries) + 1) // 2
        left, right = entries[:half], entries[half:]
        self.summary_var.set("\n".join(f"{l:<44}{r}" for l, r in zip(left, right + [""])))

    def close(self):
        self.app.controller_settings_win = None
        self.win.destroy()

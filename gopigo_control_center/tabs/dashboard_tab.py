"""Dashboard tab -- the control center: the live camera, the robot's battery, distance sensor and IMU, and
the game controller on one screen, plus driving with the controller.

Driving: the controller is read on the Tk thread about 20 times a second, and each motor update goes over a
persistent SSH channel to a small process on the robot (see remote_scripts.py) whose per-update SPI transfer
is written in C (motor_stream_spi.c, compiled on the Pi each time driving starts). If the Pi has no C
compiler, it falls back to a pure-Python driver.

Controller buttons can be bound to actions -- lights, speed, camera and so on -- in the Controller Settings
window. The bindings are saved by user_settings.py.
"""

import io
import json
import threading
import time
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
    REMOTE_TELEMETRY_PATH,
    TELEMETRY_SCRIPT,
)
from theme import CARD_BG, FONT_MONO_SMALL, MUTED, style_button
from widgets import BatteryMeter, Compass, ControllerView, ProximityBar

from .lights_tab import BLINKER_ON, OFF, turn_signal

TICK_MS = 50            # how often the controller is read (and a motor update sent while driving)
OPEN_TIMEOUT = 15       # seconds to wait for the robot to open a channel
SPEED_STEP = 50         # deg/sec per press of a speed-up / slow-down button
MIN_SPEED, MAX_SPEED = 50, 500
OUT_OF_RANGE_MM = 3000  # what the distance sensor reports when nothing is in range

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
    "eye_color": "Next eye color",
    "lights_off": "All lights off",
    "camera": "Camera on/off",
    "sensors": "Sensors on/off",
}


class DashboardTabMixin:
    def _build_dashboard_tab(self):
        self.settings = user_settings.load()
        self.gamepad = None
        self.gamepad_pressed = set()
        self.controller_settings_win = None
        self.drive_starting = False
        self.hazards_on = False
        self.shown_signal = "-"     # the turn signal last sent while driving
        self.telemetry_channel = None
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
        self.camera_canvas = tk.Canvas(camera, bg="#12161d", highlightthickness=0, width=1, height=1)
        self.camera_canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.camera_canvas.bind("<Configure>", self._on_camera_canvas_resize)
        self.camera_status_var.trace_add("write", lambda *a: self._sync_camera_button())

        # -- Robot sensors --
        robot = ttk.Frame(outer, style="Card.TFrame")
        robot.grid(row=0, column=1, sticky="nsew")
        head = ttk.Frame(robot, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Robot", style="CardBold.TLabel").pack(side="left")
        self.sensors_btn = style_button(tk.Button(head, text="Start Sensors", command=self.toggle_sensors), "success")
        self.sensors_btn.pack(side="right")

        readings = ttk.Frame(robot, style="Card.TFrame")
        readings.pack(fill="x", pady=(8, 0))

        def row_label(row, text):
            ttk.Label(readings, text=text, style="Card.TLabel").grid(row=row, column=0, sticky="ne", padx=(0, 8), pady=4)

        row_label(0, "Battery:")
        self.dash_battery = BatteryMeter(readings, scale=s)
        self.dash_battery.grid(row=0, column=1, sticky="w", pady=4)
        row_label(1, "Distance:")
        self.dash_distance = ProximityBar(readings, scale=s)
        self.dash_distance.grid(row=1, column=1, sticky="w", pady=4)
        row_label(2, "IMU:")
        imu = ttk.Frame(readings, style="Card.TFrame")
        imu.grid(row=2, column=1, sticky="w", pady=4)
        self.dash_compass = Compass(imu, scale=s)
        self.dash_compass.pack(side="left")
        self.imu_var = tk.StringVar(value="--")
        ttk.Label(imu, textvariable=self.imu_var, style="Card.TLabel", justify="left").pack(side="left", padx=10)
        row_label(3, "Gyro:")
        self.gyro_dash_var = tk.StringVar(value="--")
        ttk.Label(readings, textvariable=self.gyro_dash_var, style="Card.TLabel").grid(row=3, column=1, sticky="w", pady=4)
        row_label(4, "Encoders:")
        self.encoders_var = tk.StringVar(value="--")
        ttk.Label(readings, textvariable=self.encoders_var, style="Card.TLabel").grid(row=4, column=1, sticky="w", pady=4)

        self.telemetry_status_var = tk.StringVar(value="Sensors off. Connect, then click Start Sensors.")
        ttk.Label(
            robot, textvariable=self.telemetry_status_var, style="CardMuted.TLabel", wraplength=int(330 * s),
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        # -- Controller --
        controller = ttk.Frame(outer, style="Card.TFrame")
        controller.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.controller_view = ControllerView(controller, scale=s * 0.85)
        self.controller_view.pack(side="left")
        self.controller_view.set_enabled(False)

        side = ttk.Frame(controller, style="Card.TFrame")
        side.pack(side="left", fill="both", expand=True, padx=(16, 0))

        pick = ttk.Frame(side, style="Card.TFrame")
        pick.pack(fill="x")
        ttk.Label(pick, text="Controller:", style="Card.TLabel").pack(side="left")
        self.controller_choice_var = tk.StringVar()
        self.controller_combo = ttk.Combobox(pick, textvariable=self.controller_choice_var, state="readonly", width=34)
        self.controller_combo.pack(side="left", padx=6)
        self.controller_combo.bind("<<ComboboxSelected>>", lambda e: self._open_selected_controller())
        style_button(tk.Button(pick, text="Refresh", command=self.refresh_controllers), "neutral").pack(side="left")
        style_button(tk.Button(pick, text="Controller Settings...", command=self.open_controller_settings), "neutral").pack(
            side="left", padx=6
        )

        drive = ttk.Frame(side, style="Card.TFrame")
        drive.pack(fill="x", pady=10)
        self.drive_btn = style_button(tk.Button(drive, text="Start Driving", command=self.toggle_driving), "success", big=True)
        self.drive_btn.pack(side="left")
        style_button(tk.Button(drive, text="EMERGENCY STOP", command=self.emergency_stop), "danger", big=True).pack(
            side="left", padx=10
        )

        speed = ttk.Frame(side, style="Card.TFrame")
        speed.pack(fill="x")
        ttk.Label(speed, text="Max speed (deg/sec):", style="Card.TLabel").pack(side="left")
        self.controller_max_speed_var = tk.IntVar(value=self.settings["max_speed"])
        tk.Scale(
            speed, from_=MIN_SPEED, to=MAX_SPEED, resolution=10, orient="horizontal",
            variable=self.controller_max_speed_var, length=int(220 * s), bg=CARD_BG, highlightthickness=0,
            troughcolor="#e2e7ee", bd=0, command=lambda v: self._save_setting("max_speed", int(float(v))),
        ).pack(side="left", padx=8)
        self.motor_dps_var = tk.StringVar(value="Motors: --")
        ttk.Label(speed, textvariable=self.motor_dps_var, style="Card.TLabel").pack(side="left", padx=8)

        self.controller_status_var = tk.StringVar(value="")
        ttk.Label(side, textvariable=self.controller_status_var, style="CardMuted.TLabel").pack(anchor="w", pady=(8, 0))
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
            self._send_motor_update(sticks)

    def _run_action(self, button):
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

    def _on_driving_failed(self, error):
        self.drive_starting = False
        self.drive_btn.config(text="Start Driving", state="normal")
        style_button(self.drive_btn, "success", big=True)
        self.controller_status_var.set(f"Couldn't start driving: {error}")

    def _send_motor_update(self, sticks):
        channel = self.motor_channel
        if channel.exit_status_ready():  # the driver on the robot quit
            error = b""
            while channel.recv_stderr_ready():
                error += channel.recv_stderr(4096)
            lines = error.decode(errors="replace").strip().splitlines()
            self._stop_driving(f"The robot's motor driver stopped: {lines[-1] if lines else 'no error message'}")
            return

        deadzone = self.settings["deadzone"]
        x = 0.0 if abs(sticks[0]) < deadzone else sticks[0]
        y = 0.0 if abs(sticks[1]) < deadzone else sticks[1]
        max_speed = self.controller_max_speed_var.get()
        forward = -y * max_speed  # pushing the stick up reads as negative
        turn = x * max_speed      # pushing it right reads as positive: speed up the left wheel to turn right
        left = max(-max_speed, min(max_speed, int(forward + turn)))
        right = max(-max_speed, min(max_speed, int(forward - turn)))

        line = f"{left},{right}"
        if not self.hazards_on:
            signal = turn_signal(left, right) if self.turn_signals_var.get() else "-"
            if signal != self.shown_signal:  # only when it changes, so the blinkers don't flicker
                line += f",{signal}"
                self.shown_signal = signal
                self.light_on.update(left_blinker=signal == "L", right_blinker=signal == "R")
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
            self.light_on.update(left_blinker=False, right_blinker=False)
            self.hazards_on = False
        self.drive_btn.config(text="Start Driving", state="normal")
        style_button(self.drive_btn, "success", big=True)
        self.motor_dps_var.set("Motors: --")
        self.controller_status_var.set(message)

    def emergency_stop(self):
        self._stop_driving("Emergency stopped.")
        if self.ssh_client is not None:
            self._drive_cmd("gpg.stop()", "-")

    # ---------------- Sensors ----------------
    def toggle_sensors(self):
        if self.telemetry_channel is not None:
            self._stop_telemetry("Sensors off.")
            return
        if not self._require_connection():
            return
        self.sensors_btn.config(text="Starting...", state="disabled")
        self.telemetry_status_var.set("Starting the sensor reader on the robot...")

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass
                with sftp.file(REMOTE_TELEMETRY_PATH, "w") as f:
                    f.write(TELEMETRY_SCRIPT)
                sftp.close()
                channel = self.ssh_client.get_transport().open_session(timeout=OPEN_TIMEOUT)
                channel.exec_command(login_shell(f"{VENV_ACTIVATE} && python3 -u {REMOTE_TELEMETRY_PATH}"))
                self.telemetry_channel = channel
                self.root.after(0, self._on_telemetry_started)
                self._telemetry_loop(channel)
            except Exception as e:
                error = str(e)
                self.root.after(0, lambda: self._stop_telemetry(f"Couldn't start the sensors: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_telemetry_started(self):
        self.sensors_btn.config(text="Stop Sensors", state="normal")
        style_button(self.sensors_btn, "danger")
        self.telemetry_status_var.set("Sensors on. Looking for the distance sensor and IMU...")

    def _telemetry_loop(self, channel):
        buffer, errors = b"", b""
        while self.telemetry_channel is channel:
            if channel.recv_ready():
                buffer += channel.recv(4096)
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if line.startswith(b"{"):
                        try:
                            reading = json.loads(line)
                        except ValueError:
                            continue
                        self.root.after(0, lambda r=reading: self._show_telemetry(r, channel))
            elif channel.recv_stderr_ready():
                errors = (errors + channel.recv_stderr(4096))[-2000:]
            elif channel.exit_status_ready():
                lines = errors.decode(errors="replace").strip().splitlines()
                message = f"The sensor reader stopped: {lines[-1] if lines else 'no error message'}"
                self.root.after(0, lambda: self._stop_telemetry(message, channel))
                return
            else:
                time.sleep(0.05)

    def _show_telemetry(self, reading, channel):
        if self.telemetry_channel is not channel:
            return  # stopped since this reading arrived
        self.dash_battery.set_voltage(reading.get("battery"))

        mm = reading.get("distance_mm")
        if mm is None:
            self.dash_distance.set_distance(None, "no distance sensor")
        elif mm >= OUT_OF_RANGE_MM:
            self.dash_distance.set_distance(None, "nothing in range")
        elif mm <= 0:
            self.dash_distance.set_distance(None, "no reading")
        else:
            self.dash_distance.set_distance(mm / 10)

        imu = reading.get("imu")
        if imu is None:
            self.dash_compass.set_heading(None)
            self.imu_var.set("no IMU found\n(plug it into AD1, AD2 or I2C)")
            self.gyro_dash_var.set("--")
        else:
            self.dash_compass.set_heading(imu["heading"])
            calibration = imu["calibration"]
            hint = "" if calibration == 3 else "\nmove it in a figure-8 to calibrate"
            self.imu_var.set(
                f"heading {imu['heading']:.0f}°\nroll {imu['roll']:.1f}°  pitch {imu['pitch']:.1f}°\n"
                f"compass calibration {max(calibration, 0)}/3{hint}"
            )
            gx, gy, gz = imu["gyro"]
            self.gyro_dash_var.set(f"x {gx:.1f}  y {gy:.1f}  z {gz:.1f} °/s")

        encoders = reading.get("encoders")
        self.encoders_var.set(f"L {encoders[0]}°  R {encoders[1]}°" if encoders else "--")
        self.telemetry_status_var.set("Sensors on.")

    def _stop_telemetry(self, message, channel=None):
        if channel is not None and self.telemetry_channel is not channel:
            return  # that reader was already replaced
        old, self.telemetry_channel = self.telemetry_channel, None
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        self.sensors_btn.config(text="Start Sensors", state="normal")
        style_button(self.sensors_btn, "success")
        self.telemetry_status_var.set(message)
        self.dash_battery.set_voltage(None)
        self.dash_distance.set_distance(None)
        self.dash_compass.set_heading(None)
        for var in (self.imu_var, self.gyro_dash_var, self.encoders_var):
            var.set("--")

    # ---------------- Camera ----------------
    def _sync_camera_button(self):
        status = self.camera_status_var.get()
        if "starting" in status:
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
        self._stop_telemetry("Sensors off. Connect, then click Start Sensors.")


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
            "Buttons don't do anything else while this window is open.",
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
        self.select(self.buttons[0])

    def select(self, name):
        self.selected = name
        self.view.set_selected(name)
        self.button_var.set(button_label(name))
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

    def _reset(self):
        defaults = user_settings.DEFAULTS
        self.app._save_setting("bindings", dict(defaults["bindings"]))
        self.app._save_setting("deadzone", defaults["deadzone"])
        self.deadzone_var.set(defaults["deadzone"])
        self.select(self.selected)
        self._refresh_summary()

    def _refresh_summary(self):
        bindings = self.app.settings["bindings"]
        entries = [f"{button_label(b):<20}{ACTIONS.get(bindings[b], bindings[b])}" for b in self.buttons if b in bindings]
        if not entries:
            self.summary_var.set("(none)")
            return
        half = (len(entries) + 1) // 2
        left, right = entries[:half], entries[half:]
        self.summary_var.set("\n".join(f"{l:<44}{r}" for l, r in zip(left, right + [""])))

    def close(self):
        self.app.controller_settings_win = None
        self.win.destroy()

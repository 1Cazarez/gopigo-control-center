"""Controller tab -- drive with a Nintendo Switch Pro Controller / Joy-Con.

The joystick is read in Python (via pygame), but each motor update is sent
to the robot over a persistent SSH channel to a small remote process that
talks to the GoPiGo3 board's SPI interface directly. That process's hot
path -- one SPI transfer per update -- is written in C for speed
(motor_stream_spi.c, compiled on the Pi the first time you start Controller
Mode) and wrapped in a few lines of Python that keep the exact same stdin
protocol the app already speaks. If a C compiler isn't available on the Pi,
this falls back automatically to a pure-Python driver.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from constants import VENV_ACTIVATE, login_shell
from dependencies import pygame
from remote_scripts import (
    MOTOR_STREAM_C_SOURCE,
    MOTOR_STREAM_SCRIPT,
    MOTOR_STREAM_SCRIPT_PY_FALLBACK,
    REMOTE_KERNEL_DIR,
    REMOTE_MOTOR_STREAM_C_PATH,
    REMOTE_MOTOR_STREAM_PATH,
    REMOTE_MOTOR_STREAM_SO_PATH,
)
from theme import style_button


class ControllerTabMixin:
    def _build_controller_tab(self):
        outer = ttk.Frame(self.controller_tab, style="Card.TFrame", padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Game Controller Drive", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Works with a Nintendo Switch Pro Controller or Joy-Con, over Bluetooth or USB-C. "
            "Motor updates run through a C driver on the robot for speed.",
            style="CardMuted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, 12))

        row = ttk.Frame(outer, style="Card.TFrame")
        row.pack(fill="x", pady=4)
        style_button(tk.Button(row, text="Refresh Controllers", command=self.refresh_joysticks), "neutral").pack(
            side="left"
        )
        self.joystick_var = tk.StringVar(value="No controller found")
        self.joystick_menu = tk.OptionMenu(row, self.joystick_var, "No controller found")
        self.joystick_menu.config(relief="flat", bg="#e2e7ee", highlightthickness=0)
        self.joystick_menu.pack(side="left", padx=8)

        speed_row = ttk.Frame(outer, style="Card.TFrame")
        speed_row.pack(fill="x", pady=10)
        ttk.Label(speed_row, text="Max speed (deg/sec):", style="Card.TLabel").pack(side="left")
        self.controller_max_speed_var = tk.IntVar(value=250)
        tk.Scale(
            speed_row, from_=50, to=500, orient="horizontal", variable=self.controller_max_speed_var, length=220,
            bg="#ffffff", highlightthickness=0, troughcolor="#e2e7ee", bd=0,
        ).pack(side="left", padx=8)

        self.controller_toggle_btn = style_button(
            tk.Button(outer, text="Start Controller Mode", command=self.toggle_controller_mode, height=2),
            "success",
            big=True,
        )
        self.controller_toggle_btn.pack(pady=14, fill="x")

        style_button(
            tk.Button(outer, text="EMERGENCY STOP", command=self._controller_emergency_stop, height=2),
            "danger",
            big=True,
        ).pack(pady=4, fill="x")

        readout = ttk.Frame(outer, style="Card.TFrame")
        readout.pack(pady=16)
        ttk.Label(readout, text="Left stick X:", style="Card.TLabel").grid(row=0, column=0, sticky="e", padx=4)
        self.stick_x_var = tk.StringVar(value="--")
        ttk.Label(readout, textvariable=self.stick_x_var, style="CardBold.TLabel").grid(
            row=0, column=1, sticky="w"
        )

        ttk.Label(readout, text="Left stick Y:", style="Card.TLabel").grid(row=1, column=0, sticky="e", padx=4)
        self.stick_y_var = tk.StringVar(value="--")
        ttk.Label(readout, textvariable=self.stick_y_var, style="CardBold.TLabel").grid(
            row=1, column=1, sticky="w"
        )

        ttk.Label(readout, text="Left / Right motor DPS:", style="Card.TLabel").grid(
            row=2, column=0, sticky="e", padx=4
        )
        self.motor_dps_var = tk.StringVar(value="--")
        ttk.Label(readout, textvariable=self.motor_dps_var, style="CardBold.TLabel").grid(
            row=2, column=1, sticky="w"
        )

        self.controller_status_var = tk.StringVar(value="Not started.")
        ttk.Label(outer, textvariable=self.controller_status_var, style="CardMuted.TLabel").pack(pady=6)

        if pygame is None:
            ttk.Label(
                outer,
                text="pygame is not installed. Run: pip install pygame",
                style="Card.TLabel",
                foreground="#e0473b",
            ).pack(pady=10)

        self.refresh_joysticks()

    def refresh_joysticks(self):
        if pygame is None:
            return
        try:
            pygame.joystick.quit()
            pygame.joystick.init()
            count = pygame.joystick.get_count()
        except Exception:
            count = 0

        names = []
        for i in range(count):
            js = pygame.joystick.Joystick(i)
            js.init()
            names.append(f"{i}: {js.get_name()}")

        menu = self.joystick_menu["menu"]
        menu.delete(0, "end")
        if not names:
            self.joystick_var.set("No controller found")
            menu.add_command(label="No controller found", command=lambda: self.joystick_var.set("No controller found"))
        else:
            for name in names:
                menu.add_command(label=name, command=lambda n=name: self.joystick_var.set(n))
            self.joystick_var.set(names[0])

    def toggle_controller_mode(self):
        if self.controller_active:
            self.controller_active = False
            self.controller_toggle_btn.config(text="Start Controller Mode", bg="#2ea36f")
            self.controller_status_var.set("Stopped.")
            return

        if pygame is None:
            messagebox.showerror("Missing dependency", "Install pygame first:\npip install pygame")
            return
        if not self._require_connection():
            return
        selection = self.joystick_var.get()
        if selection == "No controller found" or ":" not in selection:
            messagebox.showwarning("No controller", "Click 'Refresh Controllers' and select one first.")
            return

        joystick_index = int(selection.split(":")[0])

        self.controller_status_var.set("Starting...")

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

                # Compile the C SPI driver into a shared library. This runs
                # every time Controller Mode starts -- it's well under a
                # second, and keeps the app from needing a separate install
                # step on the Pi.
                compile_cmd = f"gcc -O2 -shared -fPIC -o {REMOTE_MOTOR_STREAM_SO_PATH} {REMOTE_MOTOR_STREAM_C_PATH}"
                _, stdout, stderr = self.ssh_client.exec_command(login_shell(compile_cmd), timeout=15)
                compile_exit = stdout.channel.recv_exit_status()
                driver_label = "C"

                if compile_exit != 0:
                    driver_label = "Python fallback"
                    self.root.after(
                        0,
                        lambda: self.controller_status_var.set(
                            "C driver failed to compile (is gcc installed on the Pi?) -- using Python fallback."
                        ),
                    )
                    sftp = self.ssh_client.open_sftp()
                    with sftp.file(REMOTE_MOTOR_STREAM_PATH, "w") as f:
                        f.write(MOTOR_STREAM_SCRIPT_PY_FALLBACK)
                    sftp.close()

                channel = self.ssh_client.get_transport().open_session()
                command = f"{VENV_ACTIVATE} && python3 -u {REMOTE_MOTOR_STREAM_PATH}"
                channel.exec_command(login_shell(command))
                self.motor_channel = channel

                self.controller_active = True
                self.root.after(
                    0, lambda: self.controller_toggle_btn.config(text="Stop Controller Mode", bg="#e0473b")
                )
                self.root.after(
                    0,
                    lambda d=driver_label: self.controller_status_var.set(f"Controller mode active ({d} driver)."),
                )

                threading.Thread(target=self._controller_loop, args=(joystick_index,), daemon=True).start()
            except Exception as e:
                error = str(e)
                self.root.after(0, lambda: self.controller_status_var.set(f"Error: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    def _controller_loop(self, joystick_index):
        try:
            js = pygame.joystick.Joystick(joystick_index)
            js.init()
        except Exception as e:
            self._set_status(f"Controller error: {e}")
            self.controller_active = False
            return

        deadzone = 0.15
        while self.controller_active and self.motor_channel is not None:
            try:
                pygame.event.pump()
                x = js.get_axis(0) if js.get_numaxes() > 0 else 0.0
                y = js.get_axis(1) if js.get_numaxes() > 1 else 0.0

                if abs(x) < deadzone:
                    x = 0.0
                if abs(y) < deadzone:
                    y = 0.0

                max_speed = self.controller_max_speed_var.get()
                forward = -y * max_speed  # stick forward (up) is usually negative Y
                turn = x * max_speed

                left_dps = int(forward - turn)
                right_dps = int(forward + turn)
                left_dps = max(-max_speed, min(max_speed, left_dps))
                right_dps = max(-max_speed, min(max_speed, right_dps))

                self.motor_channel.send(f"{left_dps},{right_dps}\n")

                self.root.after(0, lambda x=x, y=y: self.stick_x_var.set(f"{x:.2f}"))
                self.root.after(0, lambda x=x, y=y: self.stick_y_var.set(f"{y:.2f}"))
                self.root.after(
                    0, lambda l=left_dps, r=right_dps: self.motor_dps_var.set(f"L={l}  R={r}")
                )
            except Exception as e:
                self._set_status(f"Controller loop error: {e}")
                break

            time.sleep(0.08)  # ~12 updates/sec

        # Make sure the robot stops moving when controller mode ends
        try:
            if self.motor_channel:
                self.motor_channel.send("0,0\n")
                self.motor_channel.close()
        except Exception:
            pass
        self.motor_channel = None

    def _controller_emergency_stop(self):
        self.controller_active = False
        self.controller_toggle_btn.config(text="Start Controller Mode", bg="#2ea36f")
        try:
            if self.motor_channel:
                self.motor_channel.send("0,0\n")
        except Exception:
            pass
        self._drive_cmd("gpg.stop()")
        self.controller_status_var.set("Emergency stopped.")

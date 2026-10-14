"""Manual driving -- directional buttons and a speed slider, on the Manual Control tab."""

import threading
import tkinter as tk
from tkinter import ttk

from constants import VENV_ACTIVATE, login_shell
from theme import style_button


class DriveTabMixin:
    def _build_drive_section(self, parent):
        """The driving controls, in a new frame in parent (not yet placed); returns the frame."""
        wrap = ttk.Frame(parent, style="Card.TFrame")

        ttk.Label(wrap, text="Drive", style="Heading.TLabel").grid(row=0, column=0, columnspan=3, pady=(0, 10), sticky="w")

        btn_opts = dict(width=9, height=2)

        style_button(
            tk.Button(wrap, text="Forward", command=lambda: self._drive_cmd("gpg.forward()", "-"), **btn_opts),
            "primary",
        ).grid(row=1, column=1, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="Left", command=lambda: self._drive_cmd("gpg.left()", "L"), **btn_opts), "primary"
        ).grid(row=2, column=0, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="STOP", command=lambda: self._drive_cmd("gpg.stop()", "-"), **btn_opts), "danger"
        ).grid(row=2, column=1, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="Right", command=lambda: self._drive_cmd("gpg.right()", "R"), **btn_opts), "primary"
        ).grid(row=2, column=2, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="Backward", command=lambda: self._drive_cmd("gpg.backward()", "-"), **btn_opts),
            "primary",
        ).grid(row=3, column=1, padx=6, pady=6)

        speed_frame = ttk.Frame(wrap, style="Card.TFrame")
        speed_frame.grid(row=4, column=0, columnspan=3, pady=12)
        ttk.Label(speed_frame, text="Speed (deg/sec):", style="Card.TLabel").pack(side="left")
        self.speed_var = tk.IntVar(value=200)
        self.drive_speed = self.speed_var.get()  # the speed last set with Set Speed
        tk.Scale(
            speed_frame, from_=50, to=500, orient="horizontal", variable=self.speed_var, length=200,
            bg="#ffffff", highlightthickness=0, troughcolor="#e2e7ee", bd=0,
        ).pack(side="left", padx=8)
        style_button(tk.Button(speed_frame, text="Set Speed", command=self._set_speed), "neutral").pack(
            side="left", padx=6
        )

        self.drive_status_var = tk.StringVar(value="")
        ttk.Label(wrap, textvariable=self.drive_status_var, style="CardMuted.TLabel").grid(
            row=5, column=0, columnspan=3
        )
        return wrap

    def _drive_cmd(self, gpg_call, signal=None):
        """signal: the blinker to show with this move -- "L", "R" or "-" (none) -- or None to leave them alone."""
        if not self._require_connection():
            return
        # Every press runs a new EasyGoPiGo3, which starts at its default speed, so apply ours first.
        snippet = f"import easygopigo3 as easy; gpg = easy.EasyGoPiGo3(); gpg.set_speed({self.drive_speed}); {gpg_call}"
        if signal is not None and self.turn_signals_var.get():
            left, right = (255 if signal == "L" else 0), (255 if signal == "R" else 0)
            self._record_lights(left_blinker=bool(left), right_blinker=bool(right))
            snippet += f"; gpg.set_led(gpg.LED_BLINKER_LEFT, {left}); gpg.set_led(gpg.LED_BLINKER_RIGHT, {right})"
        command = f"{VENV_ACTIVATE} && python3 -c \"{snippet}\""

        def worker():
            try:
                self.ssh_client.exec_command(login_shell(command))
                self.root.after(0, lambda: self.drive_status_var.set(f"Sent: {gpg_call}"))
            except Exception as e:
                error = str(e)
                self.root.after(0, lambda: self.drive_status_var.set(f"Error: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    def _set_speed(self):
        self.drive_speed = self.speed_var.get()
        self.drive_status_var.set(f"Speed set to {self.drive_speed} deg/sec for the next move.")

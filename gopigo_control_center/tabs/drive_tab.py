"""Manual Drive tab -- simple directional buttons and a speed slider."""

import threading
import tkinter as tk
from tkinter import ttk

from constants import VENV_ACTIVATE, login_shell
from theme import style_button


class DriveTabMixin:
    def _build_drive_tab(self):
        outer = ttk.Frame(self.drive_tab, style="Card.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        wrap = ttk.Frame(outer, style="Card.TFrame")
        wrap.pack(expand=True)

        ttk.Label(wrap, text="Manual Drive Controls", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=3, pady=(0, 16)
        )

        btn_opts = dict(width=10, height=3)

        style_button(
            tk.Button(wrap, text="Forward", command=lambda: self._drive_cmd("gpg.forward()"), **btn_opts),
            "primary",
        ).grid(row=1, column=1, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="Left", command=lambda: self._drive_cmd("gpg.left()"), **btn_opts), "primary"
        ).grid(row=2, column=0, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="STOP", command=lambda: self._drive_cmd("gpg.stop()"), **btn_opts), "danger"
        ).grid(row=2, column=1, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="Right", command=lambda: self._drive_cmd("gpg.right()"), **btn_opts), "primary"
        ).grid(row=2, column=2, padx=6, pady=6)
        style_button(
            tk.Button(wrap, text="Backward", command=lambda: self._drive_cmd("gpg.backward()"), **btn_opts),
            "primary",
        ).grid(row=3, column=1, padx=6, pady=6)

        speed_frame = ttk.Frame(wrap, style="Card.TFrame")
        speed_frame.grid(row=4, column=0, columnspan=3, pady=20)
        ttk.Label(speed_frame, text="Speed (deg/sec):", style="Card.TLabel").pack(side="left")
        self.speed_var = tk.IntVar(value=200)
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

    def _drive_cmd(self, gpg_call):
        if not self._require_connection():
            return
        snippet = f"import easygopigo3 as easy; gpg = easy.EasyGoPiGo3(); {gpg_call}"
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
        speed = self.speed_var.get()
        self._drive_cmd(f"gpg.set_speed({speed})")

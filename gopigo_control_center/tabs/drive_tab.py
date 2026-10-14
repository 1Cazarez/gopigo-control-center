"""Manual driving -- directional buttons and a speed slider, on the Manual Control tab."""

import threading
import tkinter as tk
from tkinter import ttk

from constants import VENV_ACTIVATE, login_shell
from theme import CARD_BG, style_button

# Safety stop: with it on, once something is within the stop distance (a setting, 20 cm by default), forward
# driving is blocked until it's this much farther away, so a reading hovering at the limit doesn't flick it on
# and off.
OBSTACLE_CLEAR_MARGIN_CM = 3
MIN_STOP_CM, MAX_STOP_CM = 5, 40


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

        self._safety_stop_checkbox(wrap).grid(row=5, column=0, columnspan=3, pady=(0, 6))

        self.drive_status_var = tk.StringVar(value="")
        ttk.Label(wrap, textvariable=self.drive_status_var, style="CardMuted.TLabel").grid(
            row=6, column=0, columnspan=3
        )
        return wrap

    def _drive_cmd(self, gpg_call, signal=None, note=None):
        """signal: the blinker to show with this move -- "L", "R" or "-" (none) -- or None to leave them alone.
        note: what to show once it's sent, instead of the command."""
        if not self._require_connection():
            return
        if signal is not None:  # a move from the drive buttons
            if self.nav_channel is not None:
                self.stop_autopilot("You took over with the drive buttons.")
            if gpg_call == "gpg.forward()" and self.obstacle_close:
                self.drive_status_var.set("Something is too close ahead to drive forward. Back up or turn first.")
                return
            self.manual_forward = gpg_call == "gpg.forward()"
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
                self.root.after(0, lambda: self.drive_status_var.set(note or f"Sent: {gpg_call}"))
            except Exception as e:
                error = str(e)
                self.root.after(0, lambda: self.drive_status_var.set(f"Error: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Safety stop ----------------
    def _init_safety_stop(self):
        """Called once at startup, before the tabs are built."""
        self.settings["obstacle_stop_cm"] = max(MIN_STOP_CM, min(MAX_STOP_CM, int(self.settings["obstacle_stop_cm"])))
        self.safety_stop_var = tk.BooleanVar(value=bool(self.settings["safety_stop"]))
        self.safety_label_var = tk.StringVar()
        self.safety_stop_var.trace_add("write", lambda *a: self._on_safety_toggled())
        self._refresh_safety_label()

    def _safety_stop_checkbox(self, parent):
        """A Safety stop checkbox in parent (not yet placed). All of them share one setting."""
        return tk.Checkbutton(
            parent, textvariable=self.safety_label_var, variable=self.safety_stop_var,
            bg=CARD_BG, activebackground=CARD_BG, highlightthickness=0,
        )

    def _refresh_safety_label(self):
        self.safety_label_var.set(f"Safety stop ({self.settings['obstacle_stop_cm']} cm)")

    def _set_stop_distance(self, cm):
        self._save_setting("obstacle_stop_cm", cm)
        self._refresh_safety_label()

    def _toggle_safety_stop(self):
        self.safety_stop_var.set(not self.safety_stop_var.get())

    def _on_safety_toggled(self):
        on = self.safety_stop_var.get()
        self._save_setting("safety_stop", on)
        if not on:
            self._clear_obstacle()
        self._set_status(f"Safety stop {'on' if on else 'off'}.")

    def _clear_obstacle(self):
        if self.obstacle_close:
            self.obstacle_close = False
            self._update_eyes()

    def _check_obstacle(self, mm):
        """Called with each distance reading (mm, or None without a distance sensor). With the safety stop on,
        within the stop distance: stop a forward Manual Drive move and block forward driving until the way is
        clear again."""
        stop_cm = self.settings["obstacle_stop_cm"]
        if not self.safety_stop_var.get() or mm is None:
            self._clear_obstacle()  # turned off, or no distance sensor
            return
        if mm <= 0:
            return  # a failed reading: keep the last decision
        cm = mm / 10
        was_close = self.obstacle_close
        self.obstacle_close = cm <= stop_cm + (OBSTACLE_CLEAR_MARGIN_CM if was_close else 0)
        if self.obstacle_close != was_close:
            self._update_eyes()
        if self.obstacle_close and not was_close:
            message = f"Safety stop: something {cm:.0f} cm ahead."
            if self.manual_forward:
                self.manual_forward = False
                self._drive_cmd("gpg.stop()", "-", note=message)
            if self.controller_active:
                self.controller_status_var.set(f"{message} Forward is blocked -- reverse or spin away.")
        elif was_close and not self.obstacle_close and self.controller_active:
            self.controller_status_var.set("The way ahead is clear.")

    def _set_speed(self):
        self.drive_speed = self.speed_var.get()
        self.drive_status_var.set(f"Speed set to {self.drive_speed} deg/sec for the next move.")

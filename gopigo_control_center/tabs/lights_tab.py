"""Lights tab -- the LEDs on the GoPiGo3 board: two RGB eyes, two red blinkers and the RGB Wi-Fi LED."""

import threading
import tkinter as tk
from tkinter import colorchooser, ttk

from constants import VENV_ACTIVATE, login_shell
from theme import BORDER, CARD_BG, style_button

# Each light: (label, its bit in gopigo3's LED mask -- LED_EYE_LEFT and so on -- takes a color?).
# Blinkers are single red LEDs.
LIGHTS = {
    "left_eye": ("Left eye", 0x02, True),
    "right_eye": ("Right eye", 0x01, True),
    "left_blinker": ("Left blinker", 0x04, False),
    "right_blinker": ("Right blinker", 0x08, False),
    "wifi": ("Wi-Fi LED", 0x80, True),
}
DEFAULT_COLORS = {"left_eye": (0, 255, 255), "right_eye": (0, 255, 255), "wifi": (0, 0, 255)}  # cyan is EasyGoPiGo3's eye default
BLINKER_ON = (255, 0, 0)  # a blinker takes the red value as its brightness
OFF = (0, 0, 0)
# The colors a controller button steps the eyes through.
EYE_COLORS = [(0, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (255, 0, 255), (255, 140, 0)]

# Turn signals: how much faster one wheel must spin than the other (deg/sec) before the robot counts as turning.
TURN_SIGNAL_THRESHOLD = 40


def turn_signal(left_dps, right_dps):
    """Which blinker matches this motion: "L" when turning left (right wheel faster), "R" when turning right,
    or "-" for neither."""
    if right_dps - left_dps >= TURN_SIGNAL_THRESHOLD:
        return "L"
    if left_dps - right_dps >= TURN_SIGNAL_THRESHOLD:
        return "R"
    return "-"


def _hex(color):
    return "#{:02x}{:02x}{:02x}".format(*color)


class LightsTabMixin:
    def _build_lights_tab(self):
        self.led_colors = dict(DEFAULT_COLORS)
        self.light_on = {key: False for key in LIGHTS}  # as last set from this app; the robot starts unknown
        self.eye_color_index = 0
        self.turn_signals_var = tk.BooleanVar(value=True)
        self.led_swatches = {}

        outer = ttk.Frame(self.lights_tab, style="Card.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        wrap = ttk.Frame(outer, style="Card.TFrame")
        wrap.pack(expand=True)

        ttk.Label(wrap, text="Board Lights", style="Heading.TLabel").pack(pady=(0, 16))

        rows = ttk.Frame(wrap, style="Card.TFrame")
        rows.pack()
        for row, key in enumerate(LIGHTS):
            label, _, has_color = LIGHTS[key]
            ttk.Label(rows, text=f"{label}:", style="Card.TLabel").grid(row=row, column=0, sticky="e", padx=8, pady=6)
            if has_color:
                swatch = tk.Label(
                    rows, width=4, bg=_hex(self.led_colors[key]), cursor="hand2",
                    highlightthickness=1, highlightbackground=BORDER,
                )
                swatch.grid(row=row, column=1, sticky="ns", padx=4, pady=6)
                swatch.bind("<Button-1>", lambda e, k=key: self._pick_led_color(k))
                self.led_swatches[key] = swatch
                style_button(
                    tk.Button(rows, text="Color...", command=lambda k=key: self._pick_led_color(k)), "neutral"
                ).grid(row=row, column=2, padx=4, pady=6)
            style_button(tk.Button(rows, text="On", command=lambda k=key: self._light_on(k)), "success").grid(
                row=row, column=3, padx=4, pady=6
            )
            style_button(tk.Button(rows, text="Off", command=lambda k=key: self._light_off(k)), "neutral").grid(
                row=row, column=4, padx=4, pady=6
            )

        ttk.Label(
            wrap,
            text="(on some robot images a background service also drives the Wi-Fi LED, and may change it back)",
            style="CardMuted.TLabel",
        ).pack(pady=(4, 0))

        tk.Checkbutton(
            wrap, text="Blinkers follow driving direction (Manual Drive and Dashboard)",
            variable=self.turn_signals_var, bg=CARD_BG, activebackground=CARD_BG, highlightthickness=0,
        ).pack(pady=(14, 0))

        style_button(tk.Button(wrap, text="All Lights Off", command=self._all_lights_off), "danger").pack(pady=16)

        self.lights_status_var = tk.StringVar(value="")
        ttk.Label(wrap, textvariable=self.lights_status_var, style="CardMuted.TLabel").pack()

    def _pick_led_color(self, key):
        rgb, _ = colorchooser.askcolor(
            color=_hex(self.led_colors[key]), title=f"{LIGHTS[key][0]} color", parent=self.root
        )
        if rgb is None:  # cancelled
            return
        self.led_colors[key] = tuple(int(c) for c in rgb)
        self.led_swatches[key].config(bg=_hex(self.led_colors[key]))
        self._light_on(key)

    def _on_color(self, key):
        return self.led_colors[key] if LIGHTS[key][2] else BLINKER_ON

    def _light_on(self, key):
        self._set_lights({key: self._on_color(key)}, f"{LIGHTS[key][0]} on")

    def _light_off(self, key):
        self._set_lights({key: OFF}, f"{LIGHTS[key][0]} off")

    def _all_lights_off(self):
        self._set_lights({key: OFF for key in LIGHTS}, "All lights off")

    def _toggle_lights(self, keys, description):
        """Turn these lights all off if any is on, otherwise all on."""
        on = not any(self.light_on[key] for key in keys)
        self._set_lights({key: self._on_color(key) if on else OFF for key in keys}, f"{description} {'on' if on else 'off'}")

    def _next_eye_color(self):
        self.eye_color_index = (self.eye_color_index + 1) % len(EYE_COLORS)
        color = EYE_COLORS[self.eye_color_index]
        for key in ("left_eye", "right_eye"):
            self.led_colors[key] = color
            self.led_swatches[key].config(bg=_hex(color))
        self._set_lights({"left_eye": color, "right_eye": color}, f"Eyes {_hex(color)}")

    def _set_lights(self, changes, description):
        """changes: {light key: (r, g, b)}, where OFF turns a light off.

        While the controller is driving, this goes down its open channel to the robot and happens at once;
        otherwise it runs as a separate command, which takes a second or so.
        """
        if not self._require_connection():
            return
        for key, color in changes.items():
            self.light_on[key] = color != OFF
        settings = [(LIGHTS[key][1], color) for key, color in changes.items()]
        if self.controller_active and self.motor_channel is not None:
            try:
                self.motor_channel.send("".join(f"LED,{mask},{r},{gr},{b}\n" for mask, (r, gr, b) in settings))
                self.lights_status_var.set(description)
                return
            except Exception:
                pass  # the channel just went away: fall back to a separate command
        self._led_cmd(settings, description)

    def _led_cmd(self, settings, description):
        """settings: (LED mask, (r, g, b)) pairs, applied in one command on the robot."""
        calls = "; ".join(f"g.set_led({mask}, {r}, {gr}, {b})" for mask, (r, gr, b) in settings)
        snippet = f"import gopigo3; g = gopigo3.GoPiGo3(); {calls}"
        command = f"{VENV_ACTIVATE} && python3 -c \"{snippet}\""
        self.lights_status_var.set(f"Sending: {description}...")

        def worker():
            try:
                _, stdout, stderr = self.ssh_client.exec_command(login_shell(command), timeout=15)
                if stdout.channel.recv_exit_status() == 0:
                    message = description
                else:
                    lines = stderr.read().decode(errors="replace").strip().splitlines()
                    message = f"Error: {lines[-1] if lines else 'the command failed on the robot'}"
            except Exception as e:
                message = f"Error: {e}"
            self.root.after(0, lambda: self.lights_status_var.set(message))

        threading.Thread(target=worker, daemon=True).start()

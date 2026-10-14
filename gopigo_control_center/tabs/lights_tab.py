"""Lights -- the LEDs on the GoPiGo3 board: two RGB eyes, two red blinkers and the RGB Wi-Fi LED.

Shown on the Manual Control tab; controller buttons on the Dashboard use the same methods.
"""

import threading
import tkinter as tk
from tkinter import colorchooser, ttk

import user_settings
from constants import VENV_ACTIVATE, login_shell
from theme import BORDER, CARD_BG, MUTED, style_button

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
# Lit when the robot has just booted: the Wi-Fi LED, which the robot's own software turns on.
LIGHTS_ON_AT_BOOT = {"wifi"}
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
    def _build_lights_section(self, parent):
        """The lights controls, in a new frame in parent (not yet placed); returns the frame."""
        saved_colors = self.settings.get("light_colors", {})
        self.led_colors = {key: tuple(saved_colors.get(key, color)) for key, color in DEFAULT_COLORS.items()}
        # The board can't report its lights, so this is what the app last set them to, saved between runs.
        # On connecting, it resets to how a freshly booted robot looks if the robot has rebooted since.
        saved_on = self.settings.get("lights", {})
        self.light_on = {key: bool(saved_on.get(key, key in LIGHTS_ON_AT_BOOT)) for key in LIGHTS}
        self.eye_color_index = 0
        self.turn_signals_var = tk.BooleanVar(value=True)
        self.led_swatches = {}
        self.led_indicators = {}
        self.led_toggles = {}
        s = self.ui_scale

        wrap = ttk.Frame(parent, style="Card.TFrame")
        ttk.Label(wrap, text="Lights", style="Heading.TLabel").pack(anchor="w", pady=(0, 10))

        rows = ttk.Frame(wrap, style="Card.TFrame")
        rows.pack(anchor="w")
        dot = int(18 * s)
        for row, key in enumerate(LIGHTS):
            label, _, has_color = LIGHTS[key]
            indicator = tk.Canvas(rows, width=dot, height=dot, bg=CARD_BG, highlightthickness=0)
            indicator.grid(row=row, column=0, padx=(0, 6), pady=5)
            self.led_indicators[key] = indicator
            ttk.Label(rows, text=label, style="Card.TLabel").grid(row=row, column=1, sticky="w", padx=(0, 10), pady=5)
            toggle = style_button(tk.Button(rows, width=9, command=lambda k=key: self._toggle_lights([k], LIGHTS[k][0])))
            toggle.grid(row=row, column=2, padx=4, pady=5)
            self.led_toggles[key] = toggle
            if has_color:
                swatch = tk.Label(
                    rows, width=3, bg=_hex(self.led_colors[key]), cursor="hand2",
                    highlightthickness=1, highlightbackground=BORDER,
                )
                swatch.grid(row=row, column=3, sticky="ns", padx=4, pady=5)
                swatch.bind("<Button-1>", lambda e, k=key: self._pick_led_color(k))
                self.led_swatches[key] = swatch
                style_button(
                    tk.Button(rows, text="Color...", command=lambda k=key: self._pick_led_color(k)), "neutral"
                ).grid(row=row, column=4, padx=4, pady=5)

        # The power LED is wired to the power supply: always on, nothing to control.
        power = tk.Canvas(rows, width=dot, height=dot, bg=CARD_BG, highlightthickness=0)
        power.grid(row=len(LIGHTS), column=0, padx=(0, 6), pady=5)
        power.create_oval(2, 2, dot - 2, dot - 2, fill="#3ddc84", outline="")
        ttk.Label(rows, text="Power", style="Card.TLabel").grid(row=len(LIGHTS), column=1, sticky="w", pady=5)
        ttk.Label(rows, text="always on while the robot has power", style="CardMuted.TLabel").grid(
            row=len(LIGHTS), column=2, columnspan=3, sticky="w", padx=4, pady=5
        )

        ttk.Label(
            wrap,
            text="The robot can't report its lights, so this shows what the app last set. On some robot "
            "images a background service also drives the Wi-Fi LED.",
            style="CardMuted.TLabel", wraplength=int(330 * s), justify="left",
        ).pack(anchor="w", pady=(6, 0))

        tk.Checkbutton(
            wrap, text="Blinkers follow driving direction",
            variable=self.turn_signals_var, bg=CARD_BG, activebackground=CARD_BG, highlightthickness=0,
        ).pack(anchor="w", pady=(10, 0))

        style_button(tk.Button(wrap, text="All Lights Off", command=self._all_lights_off), "danger").pack(
            anchor="w", pady=10
        )

        self.lights_status_var = tk.StringVar(value="")
        ttk.Label(wrap, textvariable=self.lights_status_var, style="CardMuted.TLabel").pack(anchor="w")
        self._refresh_light_indicators()
        return wrap

    def _record_lights(self, **states):
        """Note which lights are now on or off (light key=True/False): show it, and save it."""
        self.light_on.update(states)
        self.settings["lights"] = dict(self.light_on)
        self.settings["light_colors"] = {key: list(color) for key, color in self.led_colors.items()}
        user_settings.save(self.settings)
        self._refresh_light_indicators()

    def _refresh_light_indicators(self):
        for key, indicator in self.led_indicators.items():
            on = self.light_on[key]
            size = int(indicator["width"])
            indicator.delete("all")
            indicator.create_oval(
                2, 2, size - 2, size - 2, fill=_hex(self._on_color(key)) if on else "#e2e7ee", outline=MUTED,
            )
            toggle = self.led_toggles[key]
            toggle.config(text="Turn Off" if on else "Turn On")
            style_button(toggle, "neutral" if on else "success")

    def _check_robot_boot(self):
        """After connecting: if the robot has rebooted since the lights were last set, they're back to how a
        freshly booted robot looks (only the Wi-Fi LED, which the robot itself lights, is on)."""
        def worker():
            try:
                _, stdout, _ = self.ssh_client.exec_command("cat /proc/sys/kernel/random/boot_id", timeout=10)
                boot_id = stdout.read().decode(errors="replace").strip()
            except Exception:
                return
            if boot_id:
                self.root.after(0, lambda: self._on_robot_boot_id(boot_id))

        threading.Thread(target=worker, daemon=True).start()

    def _on_robot_boot_id(self, boot_id):
        if boot_id == self.settings.get("robot_boot_id"):
            return
        self.settings["robot_boot_id"] = boot_id
        self._record_lights(**{key: key in LIGHTS_ON_AT_BOOT for key in LIGHTS})

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
        self._record_lights(**{key: color != OFF for key, color in changes.items()})
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

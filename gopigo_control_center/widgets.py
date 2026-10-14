"""Small reusable Tkinter widgets shared across tabs."""

import math
import tkinter as tk

from theme import CARD_BG, DANGER, MUTED, NEUTRAL_DARK, PRIMARY, SUCCESS, TEXT, WARNING, FONT_MONO_SMALL

# The GoPiGo3 runs happily on anything from about 9V (getting low) up to a
# fresh pack around 12V. This is an approximation for the meter's fill
# level, not a precise fuel gauge -- exact numbers vary by battery type.
MIN_VOLTAGE = 9.0
MAX_VOLTAGE = 12.0


class BatteryMeter(tk.Canvas):
    """A small horizontal battery icon that fills and changes color based
    on the robot's reported battery voltage."""

    WIDTH = 140
    HEIGHT = 34
    BODY_W = 68
    BODY_H = 28

    def __init__(self, parent, scale=1.0, bg=CARD_BG, **kwargs):
        self.s = scale
        kwargs.setdefault("width", int(self.WIDTH * scale))
        kwargs.setdefault("height", int(self.HEIGHT * scale))
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(parent, bg=bg, **kwargs)
        self._draw(None)

    def set_voltage(self, voltage):
        """voltage: a float, or None to show the 'unknown' state."""
        self._draw(voltage)

    def _draw(self, voltage):
        self.delete("all")
        s = self.s
        x0, y0 = 2, (self.HEIGHT - self.BODY_H) * s / 2
        x1, y1 = x0 + self.BODY_W * s, y0 + self.BODY_H * s

        self.create_rectangle(x0, y0, x1, y1, outline=MUTED, width=2)
        self.create_rectangle(x1, y0 + 7 * s, x1 + 6 * s, y1 - 7 * s, fill=MUTED, outline=MUTED)

        if voltage is None:
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="--", fill=MUTED, font=FONT_MONO_SMALL)
            self.create_text(x1 + 16 * s, (y0 + y1) / 2, text="no data", fill=MUTED, font=FONT_MONO_SMALL, anchor="w")
            return

        pct = (voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)
        pct = max(0.03, min(1.0, pct))
        color = DANGER if pct < 0.2 else WARNING if pct < 0.5 else SUCCESS

        pad = 3 * s
        fill_w = int((self.BODY_W * s - pad * 2) * pct)
        self.create_rectangle(x0 + pad, y0 + pad, x0 + pad + fill_w, y1 - pad, fill=color, outline="")

        self.create_text(
            x1 + 16 * s, (y0 + y1) / 2, text=f"{voltage:.1f}V", fill=MUTED, font=FONT_MONO_SMALL, anchor="w"
        )


class Compass(tk.Canvas):
    """A compass dial whose needle points along the robot's heading (0 = north, clockwise)."""

    SIZE = 110

    def __init__(self, parent, scale=1.0, bg=CARD_BG, **kwargs):
        self.s = scale
        self.size = int(self.SIZE * scale)
        super().__init__(parent, width=self.size, height=self.size, bg=bg, highlightthickness=0, **kwargs)
        self.set_heading(None)

    def set_heading(self, heading):
        """heading: degrees, or None when unknown."""
        self.delete("all")
        c = self.size / 2
        r = c - 4
        self.create_oval(c - r, c - r, c + r, c + r, outline=MUTED, width=2)
        for angle, letter in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            x = c + (r - 13 * self.s) * math.sin(math.radians(angle))
            y = c - (r - 13 * self.s) * math.cos(math.radians(angle))
            self.create_text(x, y, text=letter, fill=DANGER if letter == "N" else MUTED, font=FONT_MONO_SMALL)
        if heading is None:
            self.create_text(c, c, text="--", fill=MUTED, font=FONT_MONO_SMALL)
            return
        tip_x = c + (r - 26 * self.s) * math.sin(math.radians(heading))
        tip_y = c - (r - 26 * self.s) * math.cos(math.radians(heading))
        self.create_line(c, c, tip_x, tip_y, fill=PRIMARY, width=4, arrow="last", arrowshape=(10, 12, 5))
        self.create_oval(c - 4, c - 4, c + 4, c + 4, fill=TEXT, outline="")


class ProximityBar(tk.Canvas):
    """How close the nearest obstacle is: the bar fills up and turns red as something gets nearer."""

    WIDTH = 220
    HEIGHT = 30
    RANGE_CM = 100   # anything farther than this shows an empty bar
    NEAR_CM = 25     # red from here in
    CAUTION_CM = 50  # amber from here in

    def __init__(self, parent, scale=1.0, bg=CARD_BG, **kwargs):
        self.w, self.h = int(self.WIDTH * scale), int(self.HEIGHT * scale)
        super().__init__(parent, width=self.w, height=self.h, bg=bg, highlightthickness=0, **kwargs)
        self.set_distance(None)

    def set_distance(self, cm, label=None):
        """cm: distance in centimetres, or None when there's no reading. label overrides the text shown."""
        self.delete("all")
        self.create_rectangle(1, 1, self.w - 1, self.h - 1, outline=MUTED, width=1)
        if cm is not None:
            fill = max(0.0, min(1.0, 1 - cm / self.RANGE_CM))
            color = DANGER if cm <= self.NEAR_CM else WARNING if cm <= self.CAUTION_CM else SUCCESS
            if fill > 0:
                self.create_rectangle(3, 3, 3 + (self.w - 6) * fill, self.h - 3, fill=color, outline="")
        text = label if label is not None else "--" if cm is None else f"{cm:.1f} cm"
        self.create_text(self.w / 2, self.h / 2, text=text, fill=TEXT, font=FONT_MONO_SMALL)


class ControllerView(tk.Canvas):
    """A drawing of a Switch Pro Controller that shows which buttons are held and where the sticks are.

    Buttons are named as in gamepad.py. Clicking one calls on_click(name), if given.
    """

    WIDTH, HEIGHT = 360, 210
    IDLE = "#4a5566"
    BODY = "#2b3543"

    # name: (shape, x0, y0, x1, y1, label), in a WIDTH x HEIGHT design that's scaled to fit.
    SHAPES = {
        "zl": ("rect", 54, 0, 112, 17, "ZL"),
        "leftshoulder": ("rect", 40, 19, 120, 36, "L"),
        "zr": ("rect", 248, 0, 306, 17, "ZR"),
        "rightshoulder": ("rect", 240, 19, 320, 36, "R"),
        "back": ("rect", 140, 46, 158, 58, "−"),
        "start": ("rect", 202, 46, 220, 58, "+"),
        "guide": ("oval", 194, 78, 212, 96, "⌂"),
        "dpup": ("rect", 128, 112, 144, 128, ""),
        "dpdown": ("rect", 128, 144, 144, 160, ""),
        "dpleft": ("rect", 112, 128, 128, 144, ""),
        "dpright": ("rect", 144, 128, 160, 144, ""),
        "north": ("oval", 256, 50, 276, 70, "X"),
        "east": ("oval", 276, 70, 296, 90, "A"),
        "south": ("oval", 256, 90, 276, 110, "B"),
        "west": ("oval", 236, 70, 256, 90, "Y"),
        "leftstick": ("oval", 70, 56, 120, 106, ""),
        "rightstick": ("oval", 200, 112, 250, 162, ""),
    }
    STICK_TRAVEL = 12

    def __init__(self, parent, scale=1.0, on_click=None, bg=CARD_BG, **kwargs):
        self.s = scale
        super().__init__(
            parent, width=int(self.WIDTH * scale), height=int(self.HEIGHT * scale), bg=bg, highlightthickness=0,
            **kwargs,
        )
        self.on_click = on_click
        self.pressed = set()
        self.selected = None
        self.sticks = (0.0, 0.0, 0.0, 0.0)
        self.enabled = True
        self._draw()

    def _xy(self, *coords):
        return [v * self.s for v in coords]

    def _draw(self):
        self.delete("all")
        # Body: two grips and the middle. Greyed out while there's no controller to show.
        body = self.BODY if self.enabled else NEUTRAL_DARK
        self.create_oval(*self._xy(24, 44, 150, 205), fill=body, outline="")
        self.create_oval(*self._xy(210, 44, 336, 205), fill=body, outline="")
        self.create_rectangle(*self._xy(60, 38, 300, 150), fill=body, outline="")
        self.create_oval(*self._xy(40, 34, 140, 100), fill=body, outline="")
        self.create_oval(*self._xy(220, 34, 320, 100), fill=body, outline="")
        for name, (shape, x0, y0, x1, y1, label) in self.SHAPES.items():
            pressed = name in self.pressed and self.enabled
            fill = PRIMARY if pressed else self.IDLE if self.enabled else MUTED
            outline = WARNING if name == self.selected else ""
            make = self.create_oval if shape == "oval" else self.create_rectangle
            make(*self._xy(x0, y0, x1, y1), fill=fill, outline=outline, width=3, tags=(name,))
            if label:
                self.create_text(
                    *self._xy((x0 + x1) / 2, (y0 + y1) / 2), text=label, fill="#ffffff", font=FONT_MONO_SMALL,
                    tags=(name,),
                )
            if self.on_click is not None:
                self.tag_bind(name, "<Button-1>", lambda e, n=name: self.on_click(n))
                self.tag_bind(name, "<Enter>", lambda e: self.config(cursor="hand2"))
                self.tag_bind(name, "<Leave>", lambda e: self.config(cursor=""))
        # Stick caps, offset by the stick position.
        for name, (sx, sy) in (("leftstick", self.sticks[:2]), ("rightstick", self.sticks[2:])):
            _, x0, y0, x1, y1, _ = self.SHAPES[name]
            cx = (x0 + x1) / 2 + sx * self.STICK_TRAVEL
            cy = (y0 + y1) / 2 + sy * self.STICK_TRAVEL
            self.create_oval(*self._xy(cx - 11, cy - 11, cx + 11, cy + 11), fill="#c9d0da", outline="", tags=(name,))

    def update_state(self, sticks, pressed):
        sticks = tuple(round(v, 2) for v in sticks)
        if sticks != self.sticks or pressed != self.pressed:
            self.sticks, self.pressed = sticks, set(pressed)
            self._draw()

    def set_selected(self, name):
        self.selected = name
        self._draw()

    def set_enabled(self, enabled):
        if enabled != self.enabled:
            self.enabled = enabled
            if not enabled:
                self.sticks, self.pressed = (0.0, 0.0, 0.0, 0.0), set()
            self._draw()


class RobotLightsView(tk.Canvas):
    """A top-down sketch of the robot (front at the top) with its lights where they sit on the board.

    Lit lights show in their color. Clicking a light calls on_click(key); the power light is always on and
    can't be clicked. Hovering names the light.
    """

    WIDTH, HEIGHT = 200, 190
    BOARD = "#c0392b"   # the GoPiGo3 board is red
    UNLIT = "#5a2620"
    # key: (x, y, radius, name)
    SPOTS = {
        "left_blinker": (54, 40, 6, "Left blinker"),
        "left_eye": (80, 44, 10, "Left eye"),
        "right_eye": (120, 44, 10, "Right eye"),
        "right_blinker": (146, 40, 6, "Right blinker"),
        "wifi": (100, 116, 8, "Wi-Fi LED"),
    }
    POWER = (100, 142, 5)

    def __init__(self, parent, scale=1.0, on_click=None, bg=CARD_BG, **kwargs):
        self.s = scale
        super().__init__(
            parent, width=int(self.WIDTH * scale), height=int(self.HEIGHT * scale), bg=bg, highlightthickness=0,
            **kwargs,
        )
        self.on_click = on_click
        self.states, self.colors = {}, {}
        self.hover = ""
        self._draw()

    def _xy(self, *coords):
        return [v * self.s for v in coords]

    def set_lights(self, states, colors):
        """states: {key: on?}; colors: {key: (r, g, b)} for the lights that are on."""
        self.states, self.colors = dict(states), dict(colors)
        self._draw()

    def _draw(self):
        self.delete("all")
        self.create_text(*self._xy(100, 9), text="▲ front", fill=MUTED, font=FONT_MONO_SMALL)
        self.create_rectangle(*self._xy(22, 70, 40, 140), fill=TEXT, outline="")    # wheels
        self.create_rectangle(*self._xy(160, 70, 178, 140), fill=TEXT, outline="")
        self.create_rectangle(*self._xy(40, 22, 160, 160), fill=self.BOARD, outline="")
        for key, (x, y, r, name) in self.SPOTS.items():
            on = self.states.get(key, False)
            fill = "#{:02x}{:02x}{:02x}".format(*self.colors[key]) if on and key in self.colors else self.UNLIT
            if on:  # a soft halo so a lit light reads as lit
                self.create_oval(*self._xy(x - r - 4, y - r - 4, x + r + 4, y + r + 4), outline=fill, width=2, tags=(key,))
            self.create_oval(*self._xy(x - r, y - r, x + r, y + r), fill=fill, outline="#ffffff", width=1, tags=(key,))
            self.tag_bind(key, "<Enter>", lambda e, n=name: self._set_hover(n))
            self.tag_bind(key, "<Leave>", lambda e: self._set_hover(""))
            if self.on_click is not None:
                self.tag_bind(key, "<Button-1>", lambda e, k=key: self.on_click(k))
        x, y, r = self.POWER
        self.create_oval(*self._xy(x - r, y - r, x + r, y + r), fill="#3ddc84", outline="", tags=("power",))
        self.tag_bind("power", "<Enter>", lambda e: self._set_hover("Power (always on)"))
        self.tag_bind("power", "<Leave>", lambda e: self._set_hover(""))
        self.create_text(*self._xy(100, 177), text=self.hover or "click to switch", fill=MUTED,
                         font=FONT_MONO_SMALL)

    def _set_hover(self, text):
        self.hover = text
        self.config(cursor="hand2" if text and not text.startswith("Power") and self.on_click else "")
        self._draw()

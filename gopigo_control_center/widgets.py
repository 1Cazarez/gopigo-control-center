"""Small reusable Tkinter widgets shared across tabs."""

import tkinter as tk

from theme import CARD_BG, DANGER, MUTED, SUCCESS, WARNING, FONT_MONO_SMALL

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

    def __init__(self, parent, bg=CARD_BG, **kwargs):
        kwargs.setdefault("width", self.WIDTH)
        kwargs.setdefault("height", self.HEIGHT)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(parent, bg=bg, **kwargs)
        self._draw(None)

    def set_voltage(self, voltage):
        """voltage: a float, or None to show the 'unknown' state."""
        self._draw(voltage)

    def _draw(self, voltage):
        self.delete("all")
        x0, y0 = 2, (self.HEIGHT - self.BODY_H) // 2
        x1, y1 = x0 + self.BODY_W, y0 + self.BODY_H

        self.create_rectangle(x0, y0, x1, y1, outline=MUTED, width=2)
        self.create_rectangle(x1, y0 + 7, x1 + 6, y1 - 7, fill=MUTED, outline=MUTED)

        if voltage is None:
            self.create_text((x0 + x1) / 2, (y0 + y1) / 2, text="--", fill=MUTED, font=FONT_MONO_SMALL)
            self.create_text(x1 + 16, (y0 + y1) / 2, text="no data", fill=MUTED, font=FONT_MONO_SMALL, anchor="w")
            return

        pct = (voltage - MIN_VOLTAGE) / (MAX_VOLTAGE - MIN_VOLTAGE)
        pct = max(0.03, min(1.0, pct))
        color = DANGER if pct < 0.2 else WARNING if pct < 0.5 else SUCCESS

        pad = 3
        fill_w = int((self.BODY_W - pad * 2) * pct)
        self.create_rectangle(x0 + pad, y0 + pad, x0 + pad + fill_w, y1 - pad, fill=color, outline="")

        self.create_text(
            x1 + 16, (y0 + y1) / 2, text=f"{voltage:.1f}V", fill=MUTED, font=FONT_MONO_SMALL, anchor="w"
        )

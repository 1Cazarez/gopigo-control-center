"""Manual Control tab -- drive with on-screen buttons, watch the sensors and set the lights, on one screen."""

from tkinter import ttk

from theme import divider


class ManualTabMixin:
    def _build_manual_tab(self):
        outer = ttk.Frame(self.manual_tab, style="Card.TFrame", padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure((0, 1), weight=1)

        left = ttk.Frame(outer, style="Card.TFrame")
        left.grid(row=0, column=0, sticky="n", padx=(0, 24))
        self._build_drive_section(left).pack(anchor="w")
        divider(left).pack(fill="x", pady=12)
        self._build_sensor_panel(left, title="Sensors").pack(anchor="w", fill="x")

        self._build_lights_section(outer).grid(row=0, column=1, sticky="n")

"""Shared look and feel for the GoPiGo Control Center.

One place to tweak colors and fonts instead of hunting through every tab.
Call init_style(root) once, right after creating the Tk root, then use
style_button(btn, kind) on every tk.Button so they all match.
"""

import tkinter as tk
from tkinter import ttk

# ---- Palette ----
BG = "#f4f6f9"          # app background
CARD_BG = "#ffffff"     # panel / section background
BAR_BG = "#1f2733"      # dark connection bar across the top
BAR_FG = "#e7ebf1"
BORDER = "#e2e7ee"

TEXT = "#1f2733"
MUTED = "#6b7684"

PRIMARY = "#3b6fe0"       # links, Send & Run, primary actions
PRIMARY_DARK = "#2f58b8"
SUCCESS = "#2ea36f"       # Start / Connect / go actions
SUCCESS_DARK = "#25865a"
DANGER = "#e0473b"        # Stop / Disconnect / emergency stop
DANGER_DARK = "#b83a30"
WARNING = "#e0a63b"       # Restart kernel, caution actions
WARNING_DARK = "#b8862f"
NEUTRAL = "#e2e7ee"
NEUTRAL_DARK = "#c9d0da"

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

FONT_BASE = (FONT_FAMILY, 10)
FONT_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
FONT_HEADING = (FONT_FAMILY, 14, "bold")
FONT_MONO_BASE = (FONT_MONO, 10)
FONT_MONO_SMALL = (FONT_MONO, 9)


def init_style(root):
    """Set up fonts, colors and ttk styling for the whole app."""
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD_BG)

    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_BASE)
    style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT, font=FONT_BASE)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=FONT_SMALL)
    style.configure("CardMuted.TLabel", background=CARD_BG, foreground=MUTED, font=FONT_SMALL)
    style.configure("Heading.TLabel", background=CARD_BG, foreground=TEXT, font=FONT_HEADING)
    style.configure("CardBold.TLabel", background=CARD_BG, foreground=TEXT, font=FONT_BOLD)

    style.configure("TEntry", fieldbackground="#ffffff", padding=4)
    style.configure("TSeparator", background=BORDER)

    style.configure(
        "TNotebook",
        background=BG,
        borderwidth=0,
        tabmargins=(10, 10, 10, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=NEUTRAL,
        foreground=MUTED,
        padding=(16, 9),
        font=FONT_BOLD,
        borderwidth=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", CARD_BG)],
        foreground=[("selected", PRIMARY)],
    )


_KIND_COLORS = {
    "primary": (PRIMARY, PRIMARY_DARK, "#ffffff"),
    "success": (SUCCESS, SUCCESS_DARK, "#ffffff"),
    "danger": (DANGER, DANGER_DARK, "#ffffff"),
    "warning": (WARNING, WARNING_DARK, "#ffffff"),
    "neutral": (NEUTRAL, NEUTRAL_DARK, TEXT),
}


def style_button(button, kind="primary", big=False):
    """Flat, modern color a tk.Button consistently across the app.

    kind: "primary" | "success" | "danger" | "warning" | "neutral"
    """
    bg, active, fg = _KIND_COLORS.get(kind, _KIND_COLORS["primary"])
    button.configure(
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=fg,
        disabledforeground="#c9d0da",
        relief="flat",
        bd=0,
        padx=14 if big else 12,
        pady=9 if big else 6,
        cursor="hand2",
        font=(FONT_FAMILY, 12 if big else 10, "bold"),
        highlightthickness=0,
    )
    return button


def divider(parent):
    return ttk.Separator(parent, orient="horizontal")

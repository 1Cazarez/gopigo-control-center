"""Optional third-party dependencies.

Imported once here and shared across the app so every module sees the same
"None if missing" fallback instead of each file guessing on its own.

Install them with:
    pip install paramiko pygame Pillow
"""

import os

try:
    import paramiko
except ImportError:
    paramiko = None

# pygame needs no window, just joystick input -- but it still wants the
# display subsystem initialized, even headless.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
# Report face buttons by position, which is what gamepad.py expects, on every SDL version.
os.environ["SDL_GAMECONTROLLER_USE_BUTTON_LABELS"] = "0"
try:
    import pygame
except ImportError:
    pygame = None

# Pillow decodes the camera's JPEG frames for the Dashboard; without it the Dashboard just says so.
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

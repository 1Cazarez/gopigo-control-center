"""Optional third-party dependencies.

Imported once here and shared across the app so every module sees the same
"None if missing" fallback instead of each file guessing on its own.

Install them with:
    pip install paramiko pygame Pillow opencv-python
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
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None

# OpenCV finds faces in the camera picture (camera_view.py); without it there's just no face detection.
try:
    import cv2
    import numpy
except ImportError:
    cv2 = numpy = None

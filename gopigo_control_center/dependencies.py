"""Optional third-party dependencies.

Imported once here and shared across the app so every module sees the same
"None if missing" fallback instead of each file guessing on its own.

Install both with:
    pip install paramiko pygame
"""

import os

try:
    import paramiko
except ImportError:
    paramiko = None

# pygame needs no window, just joystick input -- but it still wants the
# display subsystem initialized, even headless.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
try:
    import pygame
except ImportError:
    pygame = None

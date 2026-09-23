"""Small constants and helpers shared across the app."""

import shlex

DEFAULT_TEMPLATE = '''import easygopigo3 as easy
import time

gpg = easy.EasyGoPiGo3()

gpg.drive_cm(10)
gpg.turn_degrees(90)
gpg.stop()

print("Done!")
'''

VENV_ACTIVATE = "source ~/.venv/gopigo3/bin/activate"

# Defaults for the path fields. Both are relative to the robot user's home
# folder (where SSH and SFTP sessions start), so they work for any username.
DEFAULT_REMOTE_DIR = "."
DEFAULT_REMOTE_SCRIPT = "my_robot.py"


def login_shell(command):
    """Wrap `command` so the robot runs it under `bash -lc`.

    shlex.quote keeps the command intact even when it contains its own
    single or double quotes (e.g. an inline `python3 -c "..."` snippet).
    """
    return f"bash -lc {shlex.quote(command)}"

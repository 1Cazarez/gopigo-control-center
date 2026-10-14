"""Settings that persist between runs (controller button bindings, driving speed), kept in a small JSON
file in the user's home folder.
"""

import json
import os

PATH = os.path.join(os.path.expanduser("~"), ".gopigo_control_center.json")

DEFAULTS = {
    # Button name (see gamepad.py) -> action name (see ACTIONS in tabs/dashboard_tab.py).
    "bindings": {
        "start": "drive",
        "south": "estop",  # the B button
        "rightshoulder": "speed_up",
        "leftshoulder": "speed_down",
        "dpleft": "left_blinker",
        "dpright": "right_blinker",
        "west": "hazards",  # Y
        "dpup": "eyes",
        "north": "eye_color",  # X
        "dpdown": "lights_off",
        "east": "camera",  # A
        "back": "sensors",
    },
    "max_speed": 250,
    "deadzone": 0.15,
}


def load():
    """The saved settings, with defaults for anything missing or unreadable."""
    settings = json.loads(json.dumps(DEFAULTS))  # a deep copy
    try:
        with open(PATH, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return settings


def save(settings):
    try:
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass  # not being able to save settings shouldn't break the app

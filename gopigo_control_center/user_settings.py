"""Settings that persist between runs (controller button bindings, driving speed, light states), kept in a
small JSON file in the user's home folder.
"""

import json
import os

PATH = os.path.join(os.path.expanduser("~"), ".gopigo_control_center.json")

DEFAULTS = {
    "host": "robot1.local",
    "user": "pi",
    "remember_password": False,  # the password itself is kept in the system's password store (credentials.py)
    # Button name (see gamepad.py) -> action name (see ACTIONS in tabs/dashboard_tab.py).
    "bindings": {
        "start": "drive",
        "south": "estop",  # the B button
        "dpleft": "left_blinker",
        "dpright": "right_blinker",
        "west": "hazards",  # Y
        "dpup": "eyes",
        "north": "eye_color",  # X
        "dpdown": "lights_off",
        "east": "camera",  # A
        "back": "sensors",
        "guide": "safety",  # Home
    },
    "max_speed": 250,
    "deadzone": 0.15,
    "straight_assist": True,  # the IMU keeps the robot on course while driving straight with the controller
    "safety_stop": True,     # don't drive forward into anything closer than obstacle_stop_cm
    "obstacle_stop_cm": 20,
    "low_battery_v": 9.0,    # the Autopilot logs a low-battery incident at or below this
    # Which board lights are on and their colors, as last set from the app (the board can't report them),
    # and the robot's boot ID when they were set: after a reboot they're back to the robot's defaults.
    "lights": {},
    "light_colors": {},
    "robot_boot_id": "",
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

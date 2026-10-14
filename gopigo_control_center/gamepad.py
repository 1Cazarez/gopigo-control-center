"""Game controller input, read through pygame (SDL).

Controllers SDL knows -- the Switch Pro Controller, a pair of Joy-Con, Xbox and PlayStation pads --
are opened through SDL's game-controller layer, which gives every button the same name whatever the
model or driver. Anything else falls back to the raw joystick, whose buttons only have numbers.

The four face buttons are named by position -- south, east, west, north -- because SDL reports them by
position (south is A on an Xbox pad but B on a Switch controller). They're labelled with the Switch letters.
The Capture button isn't reported.
"""

from dependencies import pygame

try:
    from pygame._sdl2 import controller as sdl_controller
except Exception:
    sdl_controller = None

# Standard button names, in the order they're listed in the settings window.
STANDARD_BUTTONS = [
    "east", "south", "north", "west", "dpup", "dpdown", "dpleft", "dpright",
    "leftshoulder", "rightshoulder", "zl", "zr", "back", "start", "guide", "leftstick", "rightstick",
]

BUTTON_LABELS = {
    "east": "A", "south": "B", "north": "X", "west": "Y",  # as printed on a Switch controller
    "dpup": "D-pad up", "dpdown": "D-pad down", "dpleft": "D-pad left", "dpright": "D-pad right",
    "leftshoulder": "L", "rightshoulder": "R", "zl": "ZL", "zr": "ZR",
    "back": "− (minus)", "start": "+ (plus)", "guide": "Home",
    "leftstick": "Left stick (press)", "rightstick": "Right stick (press)",
}

if pygame is not None:
    _SDL_BUTTONS = {
        # SDL's A/B/X/Y are positions on an Xbox pad: A is the bottom button, B the right one, and so on.
        "south": pygame.CONTROLLER_BUTTON_A, "east": pygame.CONTROLLER_BUTTON_B,
        "west": pygame.CONTROLLER_BUTTON_X, "north": pygame.CONTROLLER_BUTTON_Y,
        "dpup": pygame.CONTROLLER_BUTTON_DPAD_UP, "dpdown": pygame.CONTROLLER_BUTTON_DPAD_DOWN,
        "dpleft": pygame.CONTROLLER_BUTTON_DPAD_LEFT, "dpright": pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
        "leftshoulder": pygame.CONTROLLER_BUTTON_LEFTSHOULDER, "rightshoulder": pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
        "back": pygame.CONTROLLER_BUTTON_BACK, "start": pygame.CONTROLLER_BUTTON_START,
        "guide": pygame.CONTROLLER_BUTTON_GUIDE,
        "leftstick": pygame.CONTROLLER_BUTTON_LEFTSTICK, "rightstick": pygame.CONTROLLER_BUTTON_RIGHTSTICK,
    }
    _SDL_STICKS = (
        pygame.CONTROLLER_AXIS_LEFTX, pygame.CONTROLLER_AXIS_LEFTY,
        pygame.CONTROLLER_AXIS_RIGHTX, pygame.CONTROLLER_AXIS_RIGHTY,
    )
AXIS_MAX = 32767
TRIGGER_PRESSED = AXIS_MAX // 2  # ZL / ZR are axes in SDL; past halfway counts as pressed


def button_label(name):
    if name in BUTTON_LABELS:
        return BUTTON_LABELS[name]
    return f"Button {name[6:]}" if name.startswith("button") else name


def list_controllers():
    """'index: name' for each connected controller. Rescans, so close any open Gamepad first."""
    if pygame is None:
        return []
    try:
        pygame.joystick.quit()
        pygame.joystick.init()
        return [f"{i}: {pygame.joystick.Joystick(i).get_name()}" for i in range(pygame.joystick.get_count())]
    except Exception:
        return []


class Gamepad:
    def __init__(self, index):
        self.standard = False
        if sdl_controller is not None:
            try:
                sdl_controller.init()
                if sdl_controller.is_controller(index):
                    self._pad = sdl_controller.Controller(index)
                    self.standard = True
            except Exception:
                self.standard = False
        if not self.standard:
            self._pad = pygame.joystick.Joystick(index)
            self._pad.init()

    def buttons(self):
        """Names of every button this controller can report."""
        if self.standard:
            return list(STANDARD_BUTTONS)
        names = [f"button{i}" for i in range(self._pad.get_numbuttons())]
        if self._pad.get_numhats():
            names += ["dpup", "dpdown", "dpleft", "dpright"]
        return names

    def attached(self):
        try:
            return bool(self._pad.attached() if self.standard else self._pad.get_init())
        except Exception:
            return False

    def read(self):
        """((left_x, left_y, right_x, right_y), pressed button names). Sticks run -1..1, with up negative."""
        pygame.event.pump()
        pad = self._pad
        if self.standard:
            sticks = tuple(max(-1.0, pad.get_axis(a) / AXIS_MAX) for a in _SDL_STICKS)
            pressed = {name for name, b in _SDL_BUTTONS.items() if pad.get_button(b)}
            if pad.get_axis(pygame.CONTROLLER_AXIS_TRIGGERLEFT) > TRIGGER_PRESSED:
                pressed.add("zl")
            if pad.get_axis(pygame.CONTROLLER_AXIS_TRIGGERRIGHT) > TRIGGER_PRESSED:
                pressed.add("zr")
            return sticks, pressed

        count = pad.get_numaxes()
        sticks = tuple(pad.get_axis(i) if i < count else 0.0 for i in range(4))
        pressed = {f"button{i}" for i in range(pad.get_numbuttons()) if pad.get_button(i)}
        if pad.get_numhats():
            hat_x, hat_y = pad.get_hat(0)
            pressed |= {name for name, on in (
                ("dpleft", hat_x < 0), ("dpright", hat_x > 0), ("dpup", hat_y > 0), ("dpdown", hat_y < 0),
            ) if on}
        return sticks, pressed

    def close(self):
        try:
            self._pad.quit()
        except Exception:
            pass

"""Small constants and helpers shared across the app."""

import shlex

VENV_ACTIVATE = "source ~/.venv/gopigo3/bin/activate"


def login_shell(command):
    """Wrap `command` so the robot runs it under `bash -lc`.

    shlex.quote keeps the command intact even when it contains its own
    single or double quotes (e.g. an inline `python3 -c "..."` snippet).
    """
    return f"bash -lc {shlex.quote(command)}"

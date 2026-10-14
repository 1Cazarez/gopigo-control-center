# GoPiGo Control Center

A desktop GUI for controlling the GoPiGo3 robot over SSH: a dashboard with the
live camera, sensors and a game controller; manual driving with sensor readouts
and control of the board's lights; one-click JupyterLab launch with a live camera
stream and snapshots; the robot's Wi-Fi; and the course modules.

## Setup

See the [top-level README](../README.md) for the full setup (Python, Tkinter, and what the
robot needs). In short, inside a virtual environment:

```bash
pip install -r requirements.txt
```

For a Nintendo Switch Pro Controller / Joy-Con: Linux needs the built-in
`hid-nintendo` kernel driver (present on most modern kernels). Pair over
Bluetooth with `bluetoothctl`, or just plug in via USB-C -- either way it
shows up as a regular joystick once connected.

Jupyter Lab and the camera stream run **on the robot**, not your laptop --
they just need `jupyter` and `picamera2` already installed there, where the
robot's `~/.venv/gopigo3` virtualenv (the one the app activates) can see
them. The Dashboard's motor driver
compiles a small C file on the robot each time you start driving with a
controller, so the Pi needs `gcc` (already present on the full Raspberry Pi OS
image; if it's missing, the app falls back to a pure-Python driver
automatically).

## Run

```bash
python3 main.py
```

## Folder layout

```
gopigo_control_center/
├── main.py              entry point
├── app.py                GoPiGoApp -- connection bar, tabs, status bar
├── theme.py               colors, fonts, ttk styling
├── widgets.py              small reusable widgets (BatteryMeter, Compass, ProximityBar, ControllerView, RobotLightsView)
├── gamepad.py               reads game controllers through pygame/SDL
├── user_settings.py          saved settings (controller buttons, speed, light states)
├── constants.py             shared constants and helpers (venv path, login shell)
├── dependencies.py           optional imports (paramiko, pygame)
├── remote_scripts.py          scripts deployed onto the robot over SFTP
├── wifi.py                       nmcli commands + parsing for the Wi-Fi tab (no Tk, easy to test)
├── requirements.txt
├── lessons/                       course modules shown in the Modules tab (Markdown)
└── tabs/
    ├── dashboard_tab.py          Dashboard (camera, sensors, controller driving and bindings)
    ├── manual_tab.py             Manual Control: lays out the three sections below
    ├── drive_tab.py               on-screen driving buttons
    ├── sensors_panel.py           sensor panels (also on the Dashboard) and the telemetry stream behind them
    ├── lights_tab.py              board lights: state, toggles, colors, turn signals
    ├── jupyter_camera_tab.py     Jupyter & Camera
    ├── wifi_tab.py               Wi-Fi
    └── education_tab.py          Modules
```

Each tab is a small mixin class; `GoPiGoApp` in `app.py` combines all of
them, so every tab's methods end up on `self` as if they were written in
one file, but each lives in its own module.

## The Wi-Fi tab

`wifi.py` builds the `sudo nmcli ...` command lines (every argument shell-quoted), runs them over
SSH, and parses the output; `tabs/wifi_tab.py` is the Tk side. Saved networks are identified by
UUID rather than name, so odd characters in a network name can't confuse a command. A saved Wi-Fi
profile in access-point mode is treated as the robot's fallback hotspot and is protected from
deletion. See the [top-level README](../README.md#managing-the-robots-wi-fi) for how switching
networks behaves and what the robot needs.

## The Modules tab

Each `.md` file in `lessons/` is one document; its first `# ` line is the name shown in the picker
(files are listed in natural order, so `module10` comes after `module9`),
and each `## ` heading becomes an entry in the section list. The tab understands a small part of
Markdown: `#`/`##`/`###` headings, `- ` bullets (indent four spaces to nest), `1. ` numbered steps,
fenced code blocks and `` `inline code` ``. Web addresses become clickable links.

## The Dashboard tab

Three things feed it:

- **The camera.** Start Camera runs the same MJPEG server as the Jupyter & Camera tab; the Dashboard
  reads its stream and draws each frame (decoded with Pillow) on a canvas.
- **Sensors.** Start Sensors deploys `telemetry.py` (`TELEMETRY_SCRIPT` in `remote_scripts.py`), which
  prints one JSON line of readings about five times a second. It reads the distance sensor and IMU
  through `di_sensors` with its cross-process lock on, and it never creates an `EasyGoPiGo3` (whose
  constructor resets the motor speed limit). It looks for the IMU on AD1, AD2 and I2C, in that order.
- **The controller.** `gamepad.py` reads it about 20 times a second on the Tk thread. Driving uses
  racing controls (`racing_drive` in `tabs/dashboard_tab.py`); ZL, ZR, L and R are reserved for it. Controllers SDL
  knows get fixed button names; the four face buttons are named by position (`south` is the Switch's
  B), since that's how SDL reports them. Newly pressed buttons run their bound action (see `ACTIONS` in
  `tabs/dashboard_tab.py`); while driving, each read also sends one motor update.

While driving, light changes go down the motor channel as `LED,mask,red,green,blue` lines, so they
happen at once; otherwise they run as a separate command, which takes a second or so.

## The C motor driver

Controller driving's per-update motor command is the one part of the app
that's latency-sensitive enough to be worth writing in C: `motor_stream_spi.c`
talks directly to the GoPiGo3 board over `/dev/spidev0.1` using the same
protocol as the official Python driver. It's deployed to the robot and
compiled into `libmotorstream.so` when you start driving,
and a thin Python wrapper (`motor_stream.py`, also deployed to the robot)
loads it with `ctypes` and reads `"left_dps,right_dps"` lines from stdin,
plus the optional turn-signal field and `LED,...` lines described in
`remote_scripts.py`.

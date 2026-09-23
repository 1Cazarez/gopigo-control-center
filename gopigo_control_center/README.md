# GoPiGo Control Center

A desktop GUI for controlling the GoPiGo3 robot over SSH: a code editor with
one-click send-and-run, Jupyter-style notebook cells, drag-and-drop block
coding, manual and controller-based driving, live sensor readouts (including
a battery meter), a remote file browser, one-click JupyterLab launch, and a
live camera stream + snapshot tool.

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
them. The Controller tab's motor driver
compiles a small C file on the robot the first time you start Controller
Mode, so the Pi needs `gcc` (already present on the full Raspberry Pi OS
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
├── widgets.py              small reusable widgets (BatteryMeter)
├── constants.py             shared constants and helpers (venv path, default paths, starter template)
├── dependencies.py           optional imports (paramiko, pygame)
├── remote_scripts.py          scripts deployed onto the robot over SFTP
├── block_server.py             local web server behind the Block Builder tab
├── wifi.py                       nmcli commands + parsing for the Wi-Fi tab (no Tk, easy to test)
├── requirements.txt
├── web/                          Block Builder page + vendored Blockly (with its LICENSE)
└── tabs/
    ├── code_tab.py           Code Editor & Run
    ├── notebook_tab.py         Notebook Cells
    ├── blocks_tab.py            Block Builder
    ├── drive_tab.py             Manual Drive
    ├── controller_tab.py         Controller (C motor driver)
    ├── sensor_tab.py               Live Sensors
    ├── files_tab.py                 Remote Files
    ├── jupyter_camera_tab.py         Jupyter & Camera
    └── wifi_tab.py                     Wi-Fi
```

Each tab is a small mixin class; `GoPiGoApp` in `app.py` combines all of
them, so every tab's methods end up on `self` as if they were written in
one file, but each lives in its own module.

## The Block Builder tab

Tkinter can't host a web view, so the block editor ([Blockly](https://github.com/google/blockly))
opens in your web browser. The Control Center serves it from a small web server inside the app
(`block_server.py`), and the page's Send / Send & Run / Stop buttons call back into the app, which
relays them to the robot over the connection you already made -- there's no second login. The
generated Python is written to `block_builder_output.py` in the robot user's home folder.

Since that server can run code on the robot, it only listens on `127.0.0.1` (this computer) on a
random port, and it rejects any request that doesn't carry the random per-session key from the link
the app opens -- including requests from other websites open in your browser.

## The Wi-Fi tab

`wifi.py` builds the `sudo nmcli ...` command lines (every argument shell-quoted), runs them over
SSH, and parses the output; `tabs/wifi_tab.py` is the Tk side. Saved networks are identified by
UUID rather than name, so odd characters in a network name can't confuse a command. A saved Wi-Fi
profile in access-point mode is treated as the robot's fallback hotspot and is protected from
deletion. See the [top-level README](../README.md#managing-the-robots-wi-fi) for how switching
networks behaves and what the robot needs.

## The C motor driver

Controller Mode's per-update motor command is the one part of the app
that's latency-sensitive enough to be worth writing in C: `motor_stream_spi.c`
talks directly to the GoPiGo3 board over `/dev/spidev0.1` using the same
protocol as the official Python driver. It's deployed to the robot and
compiled into `libmotorstream.so` the first time you start Controller Mode,
and a thin Python wrapper (`motor_stream.py`, also deployed to the robot)
loads it with `ctypes` and forwards the exact same
`"left_dps,right_dps"` stdin lines the app already sent -- so nothing about
how the desktop app talks to the robot changed, only what runs on the other
end of that pipe.

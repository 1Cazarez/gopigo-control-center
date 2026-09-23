# GoPiGo Control Center

A desktop app for programming a GoPiGo3 robot over SSH. It runs on your own computer and talks to
the robot over your network. Tabs:

- **Code Editor & Run** -- write a script, send it to the robot, run it, and watch live output.
- **Notebook Cells** -- Jupyter-style cells that share variables between runs.
- **Block Builder** -- drag-and-drop block coding ([Blockly](https://developers.google.com/blockly))
  that generates Python and runs it on the robot. It opens in your web browser.
- **Manual Drive** and **Controller** -- drive with buttons or a game controller.
- **Live Sensors** -- battery meter, distance sensor, gyro.
- **Remote Files** -- browse and open the robot's `.py` files.
- **Jupyter & Camera** -- launch JupyterLab on the robot, watch a live camera stream, take photos.

## What you need

**On your computer**

- Python 3 (developed and tested on 3.14, on Linux). Windows and macOS should work but haven't
  been tested.
- Tkinter. It ships with Python on Windows and macOS. On Linux, install it with your package manager:
  - Debian / Ubuntu / Raspberry Pi OS: `sudo apt install python3-tk`
  - Arch: `sudo pacman -S tk`
- A web browser, for the Block Builder tab.

**On the robot**

- A GoPiGo3 with SSH enabled, reachable from your computer. The app defaults to the host
  `robot1.local` and user `pi`; both can be changed in the app.
- A Python virtualenv at `~/.venv/gopigo3` with the GoPiGo3 software (`easygopigo3`) installed. The
  app activates it before running anything. If yours lives elsewhere, change `VENV_ACTIVATE` in
  [`gopigo_control_center/constants.py`](gopigo_control_center/constants.py).
- Optional, only for the matching features:
  - `jupyter`, for the JupyterLab launcher
  - `picamera2` and a camera module, for the live stream and snapshots
  - SPI enabled and `gcc` installed, for game-controller driving. Without `gcc` it falls back to a
    slower pure-Python driver.
- **Not tested on a fresh Raspberry Pi 5 install.**

## Run it on your computer

From the folder containing this README:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r gopigo_control_center/requirements.txt
python3 gopigo_control_center/main.py
```

Enter the robot's host, username and password at the top of the window and click **Connect**. In
the **Block Builder** tab, click **Open Block Builder** to open the editor in your browser; it uses
the connection you already made. If you open a new terminal later, run the `source .venv/bin/activate`
line again first.

### Where things land on the robot

All remote paths are relative to the robot user's home folder, so any username works.

- Scripts from the Code Editor default to `my_robot.py`; the Block Builder writes `block_builder_output.py`.
- Helper scripts the app deploys (notebook kernel, motor driver, camera server) live in the hidden
  folder `~/.gopigo_ide/`.
- Photos from the Camera Snapshot button are saved on your computer in a `gopigo_photos/` folder
  inside whatever directory you launched the app from.

## Security notes

- The app automatically trusts any SSH host key the first time it connects (paramiko's
  `AutoAddPolicy`) and signs in with a password. That's fine on a home or classroom network you
  trust, but don't use it across an untrusted network.
- JupyterLab (port 8888, protected by a token) and the camera stream (port 8000, **no
  password**) listen on all of the robot's network interfaces while running. Anyone on the same
  network can reach them.
- The Block Builder page is served from a local web server that only listens on `127.0.0.1` and
  requires a random per-session key, so other computers and other websites can't use it to run code
  on the robot.

## Repository layout

```
.
├── gopigo_control_center/   the app (see its README for a tour of the code)
│   └── web/                 Block Builder page and vendored Blockly 13.3.0 (with its Apache-2.0 LICENSE)
├── LICENSE
├── THIRD_PARTY_NOTICES.md
└── README.md
```

## License

The code in this repository is released under the [MIT License](LICENSE).

Third-party work is covered separately; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md):

- The Block Builder bundles [Blockly](https://github.com/google/blockly) (Google), licensed under
  the Apache License 2.0. Its license text is in
  [`gopigo_control_center/web/blockly/LICENSE`](gopigo_control_center/web/blockly/LICENSE).
- The camera streaming server is adapted from an example in
  [picamera2](https://github.com/raspberrypi/picamera2) (Raspberry Pi, BSD 2-Clause).

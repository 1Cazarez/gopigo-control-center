# GoPiGo Control Center

A desktop app for driving and working with a GoPiGo3 robot over SSH. It runs on your own computer and talks to
the robot over your network. Tabs:

- **Dashboard** -- the control center: the live camera picture, battery, distance sensor, IMU
  (compass, roll, pitch, gyro) and wheel encoders, and a game controller drawn on screen showing
  what you press. Drive with a Nintendo Switch Pro Controller (or Joy-Con, or another gamepad) using
  racing controls (**Start Driving** also starts the camera and sensors): **ZR** gas, **ZL** brake/reverse, **left stick** steers (while moving, like a car),
  hold **L**/**R** to spin on the spot, and the **right stick** up/down raises or lowers the top speed.
  **Keep straight (IMU)** (in Controller Settings, on by default): while you drive straight, the IMU's
  gyro keeps the robot on its heading, correcting a weaker motor or uneven wheels; it needs the sensors
  on and an IMU plugged in.
  **Safety stop** (a checkbox on the Dashboard and Manual Control, or the Home button): with the sensors
  on, the robot won't drive forward into anything closer than 20 cm (the distance is set in Controller
  Settings); it can still reverse or spin away. The eyes turn red whenever the camera sees a face, in
  any mode, and yellow while driving when the safety stop is holding the robot back.
  Choose what the other buttons do -- blinkers, hazard lights, eye colors (X steps through them, then
  off), camera and more -- in **Controller Settings**; a legend on the Dashboard lists what every
  button does, and a drawing of the robot shows which lights should be on. Your choices are saved in
  `~/.gopigo_control_center.json`.
- **Manual Control** -- drive with on-screen buttons, watch the same sensors, and set the board's
  lights: the two eye LEDs (with colors), the two red blinkers and the Wi-Fi LED, on a drawing of the
  robot that shows which are on (click a light to switch it). The board can't report its lights, so the app remembers what it last set (in the
  same settings file), and after the robot reboots it goes back to the robot's defaults: only the Wi-Fi
  LED on. The blinkers can also act as turn signals, lighting up on the side the robot turns toward.
- **Autopilot** -- the robot drives itself. Draw a path on a grid (click cells, or use Snake or
  Perimeter), set the cell size and which way the robot faces, put it on the start cell and click Start.
  It keeps track of where it is from the wheel encoders and watches the distance sensor and, if you
  like, the camera: something in the way gets driven around, and a cell with something on it is
  skipped. The tab shows the live picture with faces boxed, the robot's position on the grid, the
  battery and the distance ahead. The eyes turn red when a face is in view and yellow while it gets
  round an obstacle. **Incidents** (faces, obstacles, skipped cells, getting stuck, a low battery --
  the threshold is adjustable for your rechargeable packs) are listed in the tab and appended to
  `gopigo_logs/autopilot_incidents.csv`, with a photo of each face in `gopigo_logs/faces/`. The
  autopilot stops when you click STOP or Emergency Stop, drive by hand, disconnect, or if the app
  stops hearing from it for two seconds.
- **Jupyter & Camera** -- launch JupyterLab on the robot, watch a live camera stream, take photos.
  **Reset Camera** frees the camera if it's stuck as busy.
- **Wi-Fi** -- scan for networks, save new ones and switch the robot between them.
- **Modules** -- read course modules (lab handouts, exercises and assignments) inside the app. Each
  is a Markdown file in [`gopigo_control_center/lessons/`](gopigo_control_center/lessons/); add a `.md`
  file there and it appears in the picker.

## What you need

**On your computer**

- Python 3 (developed and tested on 3.14, on Linux). Windows and macOS should work but haven't
  been tested.
- Tkinter. It ships with Python on Windows and macOS. On Linux, install it with your package manager:
  - Debian / Ubuntu / Raspberry Pi OS: `sudo apt install python3-tk`
  - Arch: `sudo pacman -S tk`
- A web browser, for JupyterLab.
- Optional: OpenCV (`opencv-python-headless`, in `requirements.txt`), for face detection. The face model
  ships with the app; without OpenCV everything else still works.

**On the robot**

- A GoPiGo3 with SSH enabled, reachable from your computer. The app defaults to the host
  `robot1.local` and user `pi`; both can be changed in the app.
- A Python virtualenv at `~/.venv/gopigo3` with the GoPiGo3 software (`easygopigo3`) installed. The
  app activates it before running anything. If yours lives elsewhere, change `VENV_ACTIVATE` in
  [`gopigo_control_center/constants.py`](gopigo_control_center/constants.py).
- Optional, only for the matching features:
  - `jupyter`, for the JupyterLab launcher
  - `picamera2` and a camera module, for the live stream and snapshots
  - SPI enabled and `gcc` installed, for driving with a game controller. Without `gcc` it falls back to a
    slower pure-Python driver.
  - NetworkManager (`nmcli`) and `sudo` rights for the SSH user, for the Wi-Fi tab (the app gives sudo
    the password you connected with). A fallback hotspot is strongly recommended too; see
    [Managing the robot's Wi-Fi](#managing-the-robots-wi-fi).
- **Not tested on a fresh Raspberry Pi 5 install.**

## Run it on your computer

From the folder containing this README:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r gopigo_control_center/requirements.txt
python3 gopigo_control_center/main.py
```

Enter the robot's host, username and password at the top of the window and click **Connect**.
The **□** button at the top right (or F11; Esc leaves it) fills the screen, and the app reopens the way
you left it. In full screen, where the title bar is hidden, **✕** next to it quits, disconnecting first
so everything on the robot stops. Tick
**Remember** to have the app fill them in next time: the host and username go in the settings file, and
the password in your system's password store (GNOME Keyring or KWallet, Windows Credential Manager, the
macOS Keychain) through the `keyring` library -- never in a file. Unticking it deletes the stored password. If you
open a new terminal later, run the `source .venv/bin/activate` line again first.

### Where things land on the robot

All remote paths are relative to the robot user's home folder, so any username works.

- Helper scripts the app deploys (motor driver, sensor reader, camera server) live in the hidden
  folder `~/.gopigo_ide/`.
- Photos from the Camera Snapshot button are saved on your computer in a `gopigo_photos/` folder
  inside whatever directory you launched the app from; the Autopilot's incident log and face photos go
  in `gopigo_logs/` there.

### If the connection to the robot drops

The app checks the link every few seconds. If the robot goes out of range, reboots, or its Wi-Fi gets
overloaded (a live camera stream uses a lot of bandwidth), the top bar changes to **Connection lost**
and a dialog says so; click **Connect** to reconnect. If JupyterLab or the camera stream fails to start,
or stops by itself, a dialog shows the robot's own error message.

## Managing the robot's Wi-Fi

The **Wi-Fi** tab runs NetworkManager's `nmcli` on the robot (over your SSH connection) to show its
saved networks, scan for nearby ones, save a new network, and switch between them.

- **Save Network** only remembers a network; the robot joins it by itself whenever it's in range. New
  networks get autoconnect priority 5, below a home network at 10 and above a fallback hotspot at 0.
- **Save & Connect Now** and **Connect** switch the robot immediately. If you're reaching the robot over
  Wi-Fi, this ends your connection: join the same network on your computer, then click **Connect** at
  the top again. The robot may have a new address, so try `robot1.local` or check your router.
- If the robot can't join the new network (wrong password, out of range), it should fall back to
  its hotspot. This is why a fallback hotspot is recommended: without one, a bad switch can leave
  the robot unreachable until you reach it another way. A network you just added that fails to
  connect (typically a wrong password) is removed again.
- The robot's hotspot profile (any Wi-Fi profile in access-point mode) is shown as such and can't
  be forgotten from the app, since it's your way back in.
- While the robot is hosting its hotspot it may be unable to scan for other networks, so the tab
  doesn't force a rescan then. You can still type a network's name and password and save it.
- WPA/WPA2 (password), WPA3 and open networks are supported, including hidden ones. Enterprise
  networks (WPA-Enterprise / 802.1X, and portals that need a browser login, common at schools and
  cafes) aren't.
- The Wi-Fi password is passed to `nmcli` as an argument, so it's briefly visible in the robot's
  process list. It isn't stored by this app; NetworkManager keeps it in a root-only file on the robot.

An example of giving the robot a fallback hotspot with NetworkManager, using your own name and a
strong password of your choosing (`10.42.0.1` is the address NetworkManager gives the robot on it):

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name Hotspot autoconnect yes ssid <hotspot-name>
sudo nmcli connection modify Hotspot 802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<a-strong-password>" connection.autoconnect-priority 0
```

## Security notes

- The app automatically trusts any SSH host key the first time it connects (paramiko's
  `AutoAddPolicy`) and signs in with a password. That's fine on a home or classroom network you
  trust, but don't use it across an untrusted network.
- JupyterLab (port 8888, protected by a token) and the camera stream or the Autopilot's live picture
  (port 8000, **no
  password**) listen on all of the robot's network interfaces while running. Anyone on the same
  network can reach them.

## Repository layout

```
.
├── gopigo_control_center/   the app (see its README for a tour of the code)
├── LICENSE
├── THIRD_PARTY_NOTICES.md
└── README.md
```

## License

The code in this repository is released under the [MIT License](LICENSE).

Third-party work is covered separately; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

- Face detection uses the [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
  model (MIT License), bundled in `gopigo_control_center/models/`.
- The camera streaming server is adapted from an example in
  [picamera2](https://github.com/raspberrypi/picamera2) (Raspberry Pi, BSD 2-Clause).

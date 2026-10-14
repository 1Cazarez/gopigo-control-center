"""Small Python scripts deployed onto the robot over SFTP, plus the
constants that describe where they live on the Pi and how the app talks
to them over their SSH channel.
"""

import re

# Helper scripts live in this hidden folder. The path is relative: SSH/SFTP
# sessions start in the robot user's home folder, so it works for any username.
REMOTE_KERNEL_DIR = ".gopigo_ide"

# ---- Live controller-drive support ----
# A tiny persistent process on the Pi holds the motor connection open and
# applies "left_dps,right_dps" lines as fast as they arrive over the SSH
# channel -- much lighter than spawning a new SSH command per joystick
# update. The per-update SPI transfer itself is written in C for speed
# (compiled to a small shared library on the robot when
# controller driving starts) and called from a thin Python wrapper that
# keeps the exact same stdin protocol the app already speaks -- nothing on
# the app side changes.
#
# A line may carry an optional third field that sets the blinkers (turn
# signals): "L" (left on), "R" (right on) or "-" (both off). The app only
# adds it when the blinkers should change. A line "LED,mask,red,green,blue"
# sets any of the board's lights instead (the mask uses gopigo3's LED_*
# values), so controller buttons can switch lights without a delay. The
# driver turns any blinker it lit off again when it exits.
#
# Protocol reference (verified against the official driver):
#   github.com/DexterInd/GoPiGo3 -- Software/Python/gopigo3/gopigo3.py
REMOTE_MOTOR_STREAM_PATH = f"{REMOTE_KERNEL_DIR}/motor_stream.py"
REMOTE_MOTOR_STREAM_C_PATH = f"{REMOTE_KERNEL_DIR}/motor_stream_spi.c"
REMOTE_MOTOR_STREAM_SO_PATH = f"{REMOTE_KERNEL_DIR}/libmotorstream.so"

# The C driver: opens /dev/spidev0.1 (SPI bus 0, chip select 1 -- the same
# device the Python gopigo3 library talks to), configures it exactly the
# way the board firmware expects, and exposes a tiny C API for sending one
# SET_MOTOR_DPS command. This is the only part that runs per motor update,
# so it's the part worth having in C.
MOTOR_STREAM_C_SOURCE = r'''// gpg_spi.c -- minimal SPI driver for the GoPiGo3 board's SET_MOTOR_DPS
// command. Compiled into libmotorstream.so and called from Python via
// ctypes, so the hot per-update SPI transfer runs in C while everything
// else (reading stdin, wiring it into the app) stays plain Python.
//
// Also sets the blinker LEDs (SET_LED), so turn signals ride the same channel.
//
// Packet format: [address, message_type, port, dps_high_byte, dps_low_byte]
//   address        = 8  (GoPiGo3's default SPI address)
//   message_type   = 14 (SET_MOTOR_DPS)
//   port           = 1 (left), 2 (right), or 3 (both)
//   dps ticks      = requested_dps * MOTOR_TICKS_PER_DEGREE (2.0 by default:
//                    120 gear ratio * 6 encoder ticks / 360), signed 16-bit,
//                    sent MSB first

#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <linux/spi/spidev.h>

#define SPI_DEVICE "/dev/spidev0.1"
#define SPI_ADDRESS 8
#define SET_LED 6
#define SET_MOTOR_DPS 14
#define SET_MOTOR_LIMITS 15
#define MOTOR_TICKS_PER_DEGREE 2.0
#define SPI_SPEED_HZ 500000

static int spi_fd = -1;

int gpg_spi_open(void) {
    if (spi_fd >= 0) {
        return 0;  // already open
    }
    spi_fd = open(SPI_DEVICE, O_RDWR);
    if (spi_fd < 0) {
        return -1;
    }

    uint8_t mode = SPI_MODE_0;
    uint8_t bits = 8;
    uint32_t speed = SPI_SPEED_HZ;

    if (ioctl(spi_fd, SPI_IOC_WR_MODE, &mode) < 0) return -2;
    if (ioctl(spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0) return -3;
    if (ioctl(spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) return -4;

    return 0;
}

void gpg_spi_close(void) {
    if (spi_fd >= 0) {
        close(spi_fd);
        spi_fd = -1;
    }
}

// port: 1 = left motor, 2 = right motor, 3 = both. Returns 0 on success.
int gpg_set_motor_dps(int port, int dps) {
    if (spi_fd < 0) {
        return -1;
    }

    int ticks = (int)(dps * MOTOR_TICKS_PER_DEGREE);
    uint8_t tx[5] = {
        SPI_ADDRESS,
        SET_MOTOR_DPS,
        (uint8_t)(port & 0xFF),
        (uint8_t)((ticks >> 8) & 0xFF),
        (uint8_t)(ticks & 0xFF),
    };
    uint8_t rx[5];
    memset(rx, 0, sizeof(rx));

    struct spi_ioc_transfer tr;
    memset(&tr, 0, sizeof(tr));
    tr.tx_buf = (unsigned long)tx;
    tr.rx_buf = (unsigned long)rx;
    tr.len = sizeof(tx);
    tr.speed_hz = SPI_SPEED_HZ;
    tr.bits_per_word = 8;

    if (ioctl(spi_fd, SPI_IOC_MESSAGE(1), &tr) < 1) {
        return -2;
    }
    return 0;
}

// Remove the speed and power limits on both motors. EasyGoPiGo3 sets a speed
// limit (its speed setting) that stays on the board after its program ends,
// and would otherwise cap the speeds sent here. Returns 0 on success.
int gpg_clear_motor_limits(void) {
    if (spi_fd < 0) {
        return -1;
    }

    // [address, SET_MOTOR_LIMITS, port (3 = both), power (0 = none), dps high, dps low (0 = none)]
    uint8_t tx[6] = {SPI_ADDRESS, SET_MOTOR_LIMITS, 3, 0, 0, 0};
    uint8_t rx[6];
    memset(rx, 0, sizeof(rx));

    struct spi_ioc_transfer tr;
    memset(&tr, 0, sizeof(tr));
    tr.tx_buf = (unsigned long)tx;
    tr.rx_buf = (unsigned long)rx;
    tr.len = sizeof(tx);
    tr.speed_hz = SPI_SPEED_HZ;
    tr.bits_per_word = 8;

    if (ioctl(spi_fd, SPI_IOC_MESSAGE(1), &tr) < 1) {
        return -2;
    }
    return 0;
}

// led: a bitmask of the board's LEDs (0x04 = left blinker, 0x08 = right
// blinker). A blinker takes the red value as its brightness. Returns 0 on success.
int gpg_set_led(int led, int red, int green, int blue) {
    if (spi_fd < 0) {
        return -1;
    }

    uint8_t tx[6] = {
        SPI_ADDRESS,
        SET_LED,
        (uint8_t)(led & 0xFF),
        (uint8_t)(red & 0xFF),
        (uint8_t)(green & 0xFF),
        (uint8_t)(blue & 0xFF),
    };
    uint8_t rx[6];
    memset(rx, 0, sizeof(rx));

    struct spi_ioc_transfer tr;
    memset(&tr, 0, sizeof(tr));
    tr.tx_buf = (unsigned long)tx;
    tr.rx_buf = (unsigned long)rx;
    tr.len = sizeof(tx);
    tr.speed_hz = SPI_SPEED_HZ;
    tr.bits_per_word = 8;

    if (ioctl(spi_fd, SPI_IOC_MESSAGE(1), &tr) < 1) {
        return -2;
    }
    return 0;
}
'''

# Thin Python interface over the compiled C driver. Reads the stdin lines
# described above; the pure-Python fallback below reads exactly the same ones.
MOTOR_STREAM_SCRIPT = r'''import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
lib = ctypes.CDLL(os.path.join(HERE, "libmotorstream.so"))
lib.gpg_spi_open.restype = ctypes.c_int
lib.gpg_set_motor_dps.argtypes = [ctypes.c_int, ctypes.c_int]
lib.gpg_set_led.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

MOTOR_LEFT = 1
MOTOR_RIGHT = 2
MOTOR_BOTH = 3
BLINKER_LEFT = 0x04
BLINKER_RIGHT = 0x08
BLINKERS = {"L": BLINKER_LEFT, "R": BLINKER_RIGHT, "-": 0}
blinker = 0  # the blinkers lit (as a mask), so they can be turned off on exit


def set_blinker(lit):
    lib.gpg_set_led(BLINKER_LEFT + BLINKER_RIGHT - lit, 0, 0, 0)
    if lit:
        lib.gpg_set_led(lit, 255, 0, 0)

rc = lib.gpg_spi_open()
if rc != 0:
    sys.stderr.write(f"gpg_spi_open failed (code {rc}) -- is SPI enabled on the Pi?\n")
    sys.stderr.flush()
    sys.exit(1)
lib.gpg_clear_motor_limits()  # a speed limit left by an EasyGoPiGo3 program would cap the controller

try:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            fields = line.split(",")
            if fields[0] == "LED":
                led, red, green, blue = (int(v) for v in fields[1:5])
                lib.gpg_set_led(led, red, green, blue)
                blinker = blinker | (led & 0x0C) if red else blinker & ~led
                continue
            lib.gpg_set_motor_dps(MOTOR_LEFT, int(fields[0]))
            lib.gpg_set_motor_dps(MOTOR_RIGHT, int(fields[1]))
            if len(fields) > 2:
                blinker = BLINKERS[fields[2]]
                set_blinker(blinker)
        except Exception:
            pass
finally:
    lib.gpg_set_motor_dps(MOTOR_BOTH, 0)
    if blinker:
        set_blinker(0)
    lib.gpg_spi_close()
'''

# Pure-Python fallback, used automatically if the Pi doesn't have a C
# compiler available (e.g. gcc missing on a minimal OS image). Functionally
# identical, just slower per update.
MOTOR_STREAM_SCRIPT_PY_FALLBACK = r'''import sys
import gopigo3

g = gopigo3.GoPiGo3()
g.set_motor_limits(g.MOTOR_LEFT + g.MOTOR_RIGHT, 0, 0)  # 0 = no limit; see gpg_clear_motor_limits
BLINKERS = {"L": g.LED_BLINKER_LEFT, "R": g.LED_BLINKER_RIGHT, "-": 0}
blinker = 0


def set_blinker(lit):
    g.set_led(g.LED_BLINKER_LEFT + g.LED_BLINKER_RIGHT - lit, 0)
    if lit:
        g.set_led(lit, 255)


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        fields = line.split(",")
        if fields[0] == "LED":
            led, red, green, blue = (int(v) for v in fields[1:5])
            g.set_led(led, red, green, blue)
            blinker = blinker | (led & 0x0C) if red else blinker & ~led
            continue
        g.set_motor_dps(g.MOTOR_LEFT, int(fields[0]))
        g.set_motor_dps(g.MOTOR_RIGHT, int(fields[1]))
        if len(fields) > 2:
            blinker = BLINKERS[fields[2]]
            set_blinker(blinker)
    except Exception:
        pass

g.set_motor_dps(g.MOTOR_LEFT + g.MOTOR_RIGHT, 0)
if blinker:
    set_blinker(0)
'''

# ---- Dashboard telemetry ----
# A small process on the Pi that prints one JSON line of sensor readings about
# five times a second -- battery, wheel encoders, and the distance sensor and
# IMU when they're plugged in -- for the Dashboard tab. One long-running
# process is far quicker than starting Python on the robot for every reading.
# It looks for missing sensors again every few seconds, so one plugged in
# later shows up. Any other line it prints (library warnings) isn't JSON and
# the app ignores it.
REMOTE_TELEMETRY_PATH = f"{REMOTE_KERNEL_DIR}/telemetry.py"

TELEMETRY_SCRIPT = r'''import json
import time

import gopigo3

INTERVAL = 0.2       # seconds between readings
PROBE_EVERY = 5.0    # seconds between looks for a missing sensor
IMU_PORTS = ("AD1", "AD2", "I2C")

g = gopigo3.GoPiGo3()


def open_distance_sensor():
    try:
        from di_sensors.easy_distance_sensor import EasyDistanceSensor
        return EasyDistanceSensor(use_mutex=True)
    except Exception:
        return None


def open_imu():
    try:
        from di_sensors.easy_inertial_measurement_unit import EasyIMUSensor
    except Exception:
        return None
    for port in IMU_PORTS:
        try:
            return EasyIMUSensor(port=port, use_mutex=True)
        except Exception:
            pass
    return None


distance_sensor = imu = None
last_probe = 0.0
while True:
    started = time.time()
    if started - last_probe >= PROBE_EVERY and (distance_sensor is None or imu is None):
        last_probe = started
        distance_sensor = distance_sensor or open_distance_sensor()
        imu = imu or open_imu()

    reading = {}
    try:
        reading["battery"] = round(g.get_voltage_battery(), 2)
        reading["encoders"] = [g.get_motor_encoder(g.MOTOR_LEFT), g.get_motor_encoder(g.MOTOR_RIGHT)]
    except Exception:
        pass
    if distance_sensor is not None:
        try:
            reading["distance_mm"] = distance_sensor.read_mm()
        except Exception:
            distance_sensor = None
    if imu is not None:
        try:
            heading, roll, pitch = imu.safe_read_euler()
            reading["imu"] = {
                "heading": round(heading, 1), "roll": round(roll, 1), "pitch": round(pitch, 1),
                "gyro": [round(v, 1) for v in imu.safe_read_gyroscope()],
                "calibration": imu.safe_calibration_status(),
            }
        except Exception:
            imu = None

    print(json.dumps(reading), flush=True)  # fails, ending the program, once the app closes the channel
    time.sleep(max(0.0, INTERVAL - (time.time() - started)))
'''

# ---- JupyterLab launch support ----
# Launched with --ip 0.0.0.0 so it's reachable from any machine on the
# network, but the URL we build for the user always uses the same host
# they typed in the connection bar (never the bare hostname Jupyter prints),
# to sidestep .local resolution quirks.
JUPYTER_PORT = 8888
JUPYTER_TOKEN_RE = re.compile(r"token=([a-zA-Z0-9]+)")

# ---- Camera MJPEG streaming support ----
# A small standalone HTTP server (adapted from picamera2's official MJPEG
# example) runs on the Pi and serves a live browser-viewable video stream --
# much smoother than pulling individual frames into a notebook cell.
#
# The server code is derived from picamera2's examples/mjpeg_server.py
# (Copyright (c) 2021, Raspberry Pi; BSD 2-Clause). The full license text is
# in THIRD_PARTY_NOTICES.md at the root of this repository.
REMOTE_CAMERA_STREAM_PATH = f"{REMOTE_KERNEL_DIR}/camera_stream.py"
CAMERA_STREAM_PORT = 8000

CAMERA_STREAM_SCRIPT = r'''#!/usr/bin/env python3
import io
import socketserver
from http import server
from threading import Condition

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GoPiGo Live Camera</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: radial-gradient(circle at top, #1b2735 0%, #0a0d12 100%);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    color: #e8ecf1;
    padding: 24px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 18px;
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #4ee08c;
    box-shadow: 0 0 8px 2px rgba(78, 224, 140, 0.7);
    animation: pulse 1.6s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.35; }
  }
  h1 {
    font-size: 1.15rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    margin: 0;
  }
  .frame {
    padding: 10px;
    border-radius: 16px;
    background: linear-gradient(145deg, #232a36, #171c24);
    box-shadow: 0 20px 45px rgba(0, 0, 0, 0.45), inset 0 0 0 1px rgba(255, 255, 255, 0.05);
  }
  img {
    display: block;
    width: min(90vw, 820px);
    height: auto;
    border-radius: 10px;
  }
  footer {
    margin-top: 14px;
    font-size: 0.8rem;
    color: #7c8698;
  }
</style>
</head>
<body>
  <header>
    <span class="dot"></span>
    <h1>GoPiGo Live Camera</h1>
  </header>
  <div class="frame">
    <img src="stream.mjpg" alt="Live camera stream">
  </div>
  <footer>Streaming from the robot &middot; refresh if the feed stalls</footer>
</body>
</html>
"""


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class StreamingHandler(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html")
            self.end_headers()
        elif self.path == "/index.html":
            content = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except Exception:
                pass
        else:
            self.send_error(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # keep stdout clean so the app can watch for the ready marker


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (820, 616)}))
output = StreamingOutput()
picam2.start()
# Picamera2 otherwise tends to pick a cropped sensor mode for a small preview
# size, which looks "zoomed in". Pin the crop to the full sensor array so the
# stream shows the whole field of view.
picam2.set_controls({"ScalerCrop": (0, 0) + picam2.camera_properties["PixelArraySize"]})
picam2.start_recording(JpegEncoder(), FileOutput(output))

try:
    address = ("", 8000)
    server_obj = StreamingServer(address, StreamingHandler)
    # Only announce "ready" once the port is bound, so a browser opened right away can connect.
    print("CAMERA_STREAM_READY", flush=True)
    server_obj.serve_forever()
finally:
    picam2.stop_recording()
'''

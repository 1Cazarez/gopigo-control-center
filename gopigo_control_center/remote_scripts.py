"""Small Python scripts deployed onto the robot over SFTP, plus the
constants that describe where they live on the Pi and how the app talks
to them over their SSH channel.
"""

import re

# ---- Notebook-cell "kernel" support ----
# A tiny persistent process runs on the Pi and execs each cell's code into a
# shared namespace, so variables/imports persist between cells like a real
# notebook kernel. Cells are separated by CELL_END_MARKER; the kernel replies
# with CELL_DONE_MARKER once a cell has finished running.
CELL_END_MARKER = "###CELL_END###"
CELL_DONE_MARKER = "###OUTPUT_END###"
# Relative paths: SSH/SFTP sessions start in the robot user's home folder, so
# this resolves correctly whatever username you log in with.
REMOTE_KERNEL_DIR = ".gopigo_ide"
REMOTE_KERNEL_PATH = f"{REMOTE_KERNEL_DIR}/cell_kernel.py"

KERNEL_SCRIPT = r'''import sys, traceback

END_MARKER = "###CELL_END###"
DONE_MARKER = "###OUTPUT_END###"

namespace = {}
buffer = []

for line in sys.stdin:
    line = line.rstrip("\n")
    if line == END_MARKER:
        code = "\n".join(buffer)
        buffer = []
        try:
            exec(code, namespace)
        except Exception:
            traceback.print_exc()
        print(DONE_MARKER)
        sys.stdout.flush()
    else:
        buffer.append(line)
'''

# ---- Block Builder support ----
# The generated Python from the Block Builder tab is written here (in the
# robot user's home folder, so you can open or edit it later) and run.
REMOTE_BLOCKS_SCRIPT_PATH = "block_builder_output.py"

# ---- Live controller-drive support ----
# A tiny persistent process on the Pi holds the motor connection open and
# applies "left_dps,right_dps" lines as fast as they arrive over the SSH
# channel -- much lighter than spawning a new SSH command per joystick
# update. The per-update SPI transfer itself is written in C for speed
# (compiled to a small shared library on the robot the first time
# Controller Mode is started) and called from a thin Python wrapper that
# keeps the exact same stdin protocol the app already speaks -- nothing on
# the app side changes.
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
#define SET_MOTOR_DPS 14
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
'''

# Thin Python interface over the compiled C driver. Reads the same
# "left_dps,right_dps" stdin lines the old pure-Python version did, so the
# desktop app's side of the protocol is completely unchanged.
MOTOR_STREAM_SCRIPT = r'''import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
lib = ctypes.CDLL(os.path.join(HERE, "libmotorstream.so"))
lib.gpg_spi_open.restype = ctypes.c_int
lib.gpg_set_motor_dps.argtypes = [ctypes.c_int, ctypes.c_int]

MOTOR_LEFT = 1
MOTOR_RIGHT = 2
MOTOR_BOTH = 3

rc = lib.gpg_spi_open()
if rc != 0:
    sys.stderr.write(f"gpg_spi_open failed (code {rc}) -- is SPI enabled on the Pi?\n")
    sys.stderr.flush()
    sys.exit(1)

try:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            left, right = line.split(",")
            lib.gpg_set_motor_dps(MOTOR_LEFT, int(left))
            lib.gpg_set_motor_dps(MOTOR_RIGHT, int(right))
        except Exception:
            pass
finally:
    lib.gpg_set_motor_dps(MOTOR_BOTH, 0)
    lib.gpg_spi_close()
'''

# Pure-Python fallback, used automatically if the Pi doesn't have a C
# compiler available (e.g. gcc missing on a minimal OS image). Functionally
# identical, just slower per update.
MOTOR_STREAM_SCRIPT_PY_FALLBACK = r'''import sys
import gopigo3

g = gopigo3.GoPiGo3()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        left, right = line.split(",")
        g.set_motor_dps(g.MOTOR_LEFT, int(left))
        g.set_motor_dps(g.MOTOR_RIGHT, int(right))
    except Exception:
        pass

g.set_motor_dps(g.MOTOR_LEFT + g.MOTOR_RIGHT, 0)
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

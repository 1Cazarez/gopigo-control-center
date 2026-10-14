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

# The motor driver's main loop, shared by the C-backed driver and the
# pure-Python fallback below; each defines set_dps(port, dps),
# set_led(mask, red, green, blue) and close_board() first.
#
# Straight-line assist: while the app asks for both wheels at the same speed
# (driving straight), the driver keeps the robot on the heading it had when
# that started, by speeding one wheel up and slowing the other. The heading
# comes from the IMU through the telemetry process (YAW_FILE): the IMU has
# one owner, since opening it resets it. A line "HOLD,1" / "HOLD,0" turns the
# assist on or off, and the driver reports what it's doing in "STATUS ..."
# lines. During deliberate turns it checks that the IMU agrees with the wheels
# about which way is clockwise, and flips its correction if it doesn't.
MOTOR_STREAM_COMMON = r'''
import math
import os
import time

MOTOR_LEFT, MOTOR_RIGHT, MOTOR_BOTH = 1, 2, 3
BLINKER_LEFT, BLINKER_RIGHT = 0x04, 0x08
BLINKERS = {"L": BLINKER_LEFT, "R": BLINKER_RIGHT, "-": 0}
YAW_FILE = "/dev/shm/gopigo_yaw" if os.path.isdir("/dev/shm") else "/tmp/gopigo_yaw"
YAW_FRESH_S = 0.5         # an older heading than this means the telemetry isn't running
HOLD_GAIN = 8.0           # deg/sec of wheel-speed difference per degree off course
HOLD_MAX_SHARE = 0.3      # the correction is at most this share of the driving speed
TURN_CHECK_DPS = 100      # a deliberate turn: one wheel at least this much faster than the other
TURN_CHECK_RATE = 20      # deg/sec of turning the IMU must see to judge the direction
TURN_CHECK_COUNT = 4      # readings in a row that must agree (or disagree)

blinker = 0  # the blinkers lit (as a mask), so they can be turned off on exit


def status(message):
    print("STATUS " + message, flush=True)


def set_blinker(lit):
    set_led(BLINKER_LEFT + BLINKER_RIGHT - lit, 0, 0, 0)
    if lit:
        set_led(lit, 255, 0, 0)


def read_yaw():
    """The robot's heading in degrees (clockwise seen from above, from an arbitrary zero), or None."""
    try:
        with open(YAW_FILE) as f:
            yaw, stamp = (float(v) for v in f.read().split())
    except (OSError, ValueError):
        return None
    return yaw if time.time() - stamp < YAW_FRESH_S else None


class StraightAssist:
    def __init__(self):
        self.enabled = True
        self.target = None       # the heading being held, while driving straight
        self.sign = 1            # flipped if the IMU turns out to count the other way round
        self.checked = False     # the turn check has confirmed the direction
        self.votes = 0           # agreeing (+) or disagreeing (-) readings in a row
        self.last = None         # (yaw, time) of the previous update, for the turning rate
        self.said = None

    def say(self, message):
        if message != self.said:
            self.said = message
            status(message)

    def wheels(self, left, right):
        """The wheel speeds to use for a request of (left, right)."""
        yaw = read_yaw()
        now = time.time()
        rate = None
        if yaw is not None and self.last is not None and now > self.last[1]:
            rate = (yaw - self.last[0]) / (now - self.last[1])
        self.last = (yaw, now) if yaw is not None else None
        if not self.enabled:
            self.target = None
            return left, right
        if yaw is None:
            self.target = None
            self.say("Straight assist paused: no IMU heading (are the sensors on, with an IMU plugged in?)")
            return left, right
        self.say("Straight assist on (IMU).")
        if left == right and left != 0:
            if self.target is None:
                self.target = yaw
            off_course = self.sign * (yaw - self.target)  # degrees, + = turned right
            limit = HOLD_MAX_SHARE * abs(left)
            correction = max(-limit, min(limit, HOLD_GAIN * off_course))
            return left - correction, right + correction
        self.target = None
        if not self.checked and rate is not None and abs(left - right) >= TURN_CHECK_DPS and abs(rate) >= TURN_CHECK_RATE:
            agrees = (self.sign * rate > 0) == (left > right)  # left wheel faster turns the robot clockwise
            self.votes = max(1, self.votes + 1) if agrees else min(-1, self.votes - 1)
            if self.votes >= TURN_CHECK_COUNT:
                self.checked = True
            elif self.votes <= -TURN_CHECK_COUNT:
                self.sign = -self.sign
                self.votes = 0
                self.said = None
                status("Straight assist: the IMU counts turns the other way round; corrected.")
        return left, right


assist = StraightAssist()
try:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            fields = line.split(",")
            if fields[0] == "LED":
                led, red, green, blue = (int(v) for v in fields[1:5])
                set_led(led, red, green, blue)
                blinker = blinker | (led & 0x0C) if red else blinker & ~led
                continue
            if fields[0] == "HOLD":
                assist.enabled = fields[1] == "1"
                assist.said = None
                if not assist.enabled:
                    assist.say("Straight assist off.")
                continue
            left, right = assist.wheels(int(fields[0]), int(fields[1]))
            set_dps(MOTOR_LEFT, left)
            set_dps(MOTOR_RIGHT, right)
            if len(fields) > 2:
                blinker = BLINKERS[fields[2]]
                set_blinker(blinker)
        except Exception:
            pass
finally:
    set_dps(MOTOR_BOTH, 0)
    if blinker:
        set_blinker(0)
    close_board()
'''

# The driver backed by the compiled C library.
MOTOR_STREAM_SCRIPT = r'''import ctypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
lib = ctypes.CDLL(os.path.join(HERE, "libmotorstream.so"))
lib.gpg_spi_open.restype = ctypes.c_int
lib.gpg_set_motor_dps.argtypes = [ctypes.c_int, ctypes.c_int]
lib.gpg_set_led.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

rc = lib.gpg_spi_open()
if rc != 0:
    sys.stderr.write(f"gpg_spi_open failed (code {rc}) -- is SPI enabled on the Pi?\n")
    sys.stderr.flush()
    sys.exit(1)
lib.gpg_clear_motor_limits()  # a speed limit left by an EasyGoPiGo3 program would cap the controller


def set_dps(port, dps):
    lib.gpg_set_motor_dps(port, int(dps))


def set_led(mask, red, green, blue):
    lib.gpg_set_led(mask, red, green, blue)


def close_board():
    lib.gpg_spi_close()
''' + MOTOR_STREAM_COMMON

# Pure-Python fallback, used automatically if the Pi doesn't have a C
# compiler available (e.g. gcc missing on a minimal OS image). Functionally
# identical, just slower per update.
MOTOR_STREAM_SCRIPT_PY_FALLBACK = r'''import sys
import gopigo3

g = gopigo3.GoPiGo3()
g.set_motor_limits(g.MOTOR_LEFT + g.MOTOR_RIGHT, 0, 0)  # 0 = no limit; see gpg_clear_motor_limits


def set_dps(port, dps):
    g.set_motor_dps(port, int(dps))


def set_led(mask, red, green, blue):
    g.set_led(mask, red, green, blue)


def close_board():
    pass
''' + MOTOR_STREAM_COMMON

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
import math
import os
import threading
import time

import gopigo3

INTERVAL = 0.2       # seconds between readings sent to the app
PROBE_EVERY = 5.0    # seconds between looks for a missing sensor
IMU_PORTS = ("AD1", "AD2", "I2C")
YAW_FILE = "/dev/shm/gopigo_yaw" if os.path.isdir("/dev/shm") else "/tmp/gopigo_yaw"
YAW_EVERY = 0.03     # seconds between gyro readings
DISPLAY_EVERY = 0.2  # seconds between the slower IMU readings shown in the app
STILL_SECONDS = 0.5  # wheels unmoved this long: the robot is still, so the gyro's reading is its bias
BIAS_MAX_DPS = 3.0   # ...unless it reads more than this: that's real turning (just started, or turned by hand)

g = gopigo3.GoPiGo3()
still_since = [time.time()]  # when the wheels last moved, from the main loop's encoder readings


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
            return IMUWorker(EasyIMUSensor(port=port, use_mutex=True))
        except Exception:
            pass
    return None


class IMUWorker:
    """The IMU's only user. Adds up the turning rate about the vertical axis -- whichever way the IMU is
    mounted, found from gravity -- into a heading, published in YAW_FILE for the motor driver's straight-line
    assist, and takes the slower readings shown in the app."""

    def __init__(self, imu):
        self.imu = imu
        self.up = self._up()
        self.bias = 0.0
        self.yaw = 0.0       # degrees clockwise seen from above, from where it started
        self.display = None
        self.failed = False
        threading.Thread(target=self._loop, daemon=True).start()

    def _up(self):
        ax, ay, az = self.imu.safe_read_accelerometer()  # at rest it measures gravity's push, pointing up
        n = math.sqrt(ax * ax + ay * ay + az * az)
        if n < 5:
            raise ValueError("the accelerometer reading doesn't look like gravity")
        return ax / n, ay / n, az / n

    def _turn_rate(self):
        gx, gy, gz = self.imu.safe_read_gyroscope()  # deg/sec about the IMU's own axes
        ux, uy, uz = self.up
        return -(gx * ux + gy * uy + gz * uz)  # clockwise seen from above

    def _publish(self, now):
        tmp = YAW_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{self.yaw:.3f} {now:.3f}\n")
        os.replace(tmp, YAW_FILE)

    def _loop(self):
        last = time.time()
        last_display = last_up = 0.0
        while True:
            try:
                rate = self._turn_rate() - self.bias
                now = time.time()
                if now - still_since[0] >= STILL_SECONDS and abs(rate) < BIAS_MAX_DPS:
                    self.bias += 0.05 * rate  # still: whatever it reads is bias
                    if now - last_up >= 1.0:
                        last_up, self.up = now, self._up()
                else:
                    self.yaw += rate * (now - last)
                last = now
                self._publish(now)
                if now - last_display >= DISPLAY_EVERY:
                    last_display = now
                    heading, roll, pitch = self.imu.safe_read_euler()
                    self.display = {
                        "heading": round(heading, 1), "roll": round(roll, 1), "pitch": round(pitch, 1),
                        "gyro": [round(v, 1) for v in self.imu.safe_read_gyroscope()],
                        "calibration": self.imu.safe_calibration_status(),
                        "yaw": round(self.yaw, 1),
                    }
            except Exception:
                self.failed = True
                return
            time.sleep(YAW_EVERY)


distance_sensor = imu = None
last_probe = 0.0
last_encoders = None
while True:
    started = time.time()
    if imu is not None and imu.failed:
        imu = None
    if started - last_probe >= PROBE_EVERY and (distance_sensor is None or imu is None):
        last_probe = started
        distance_sensor = distance_sensor or open_distance_sensor()
        imu = imu or open_imu()

    reading = {}
    try:
        reading["battery"] = round(g.get_voltage_battery(), 2)
        encoders = [g.get_motor_encoder(g.MOTOR_LEFT), g.get_motor_encoder(g.MOTOR_RIGHT)]
        reading["encoders"] = encoders
        if encoders != last_encoders:
            still_since[0] = started
            last_encoders = encoders
    except Exception:
        pass
    if distance_sensor is not None:
        try:
            reading["distance_mm"] = distance_sensor.read_mm()
        except Exception:
            distance_sensor = None
    if imu is not None and imu.display is not None:
        reading["imu"] = imu.display

    print(json.dumps(reading), flush=True)  # fails, ending the program, once the app closes the channel
    time.sleep(max(0.0, INTERVAL - (time.time() - started)))
'''

# ---- Autopilot (grid navigation) ----
# The Autopilot tab's navigator. It runs on the robot, so Wi-Fi lag can't get
# into the control loop. The app sends one JSON line with the plan (cell
# centres to visit, in cm from the start), then lines of its own: "ping"
# while it's watching, "face 1" / "face 0" when the app sees a face appear or
# go (face detection runs on the computer, which is far faster than the Pi),
# and "stop". The robot keeps track of where it is from the wheel encoders and
# reports back in JSON lines: pose, step, sensors, vision, eyes, incident,
# log, stream and done.
#
# The navigator holds the camera, so it also serves the live picture (MJPEG on
# CAMERA_STREAM_PORT, like the camera stream). If asked, it also uses the
# picture to spot obstacles: it learns what the floor looks like at the start
# and treats anything just ahead that doesn't look like floor as an obstacle.
# It only ever drives straight from one cell's centre to the next one's. If
# something is in the way it backs up to the centre it left, marks that cell
# blocked and takes the shortest way round through free cells of the grid (it
# never leaves the grid); a target cell that is blocked, or can't be reached,
# is skipped. The eyes go yellow while it takes the long way round an obstacle
# and red while the app reports a face; if asked
# ("pause_for_faces"), it also stands still until the face has gone.
#
# It stops the motors when told to, when stdin closes (the app quit or the
# connection dropped), or when the pings stop for PING_TIMEOUT seconds (a
# connection that hangs without closing).
REMOTE_NAVIGATOR_PATH = f"{REMOTE_KERNEL_DIR}/navigator.py"

NAVIGATOR_SCRIPT = r'''import collections
import io
import json
import math
import socketserver
import sys
import threading
import time
from http import server

import easygopigo3

PING_TIMEOUT = 2.0        # seconds without word from the app before stopping
LOOP = 0.05               # seconds between checks while moving
ARRIVED_DEGREES = 5       # wheel degrees from the target that count as there
STUCK_SECONDS = 2.5       # no wheel progress this long while driving: stuck
CREEP_GAP_CM = 5          # an obstacle past the end of the leg is fine if this much room is left in front
VISION_BLOCKED = 0.30     # share of the view just ahead that must look unlike the floor to count as blocked
STREAM_PORT = 8000
FRAME_SIZE = (320, 240)
SENSORS_EVERY = 0.5       # seconds between sensor reports
BATTERY_EVERY = 5.0       # seconds between battery readings
RED, YELLOW, OFF = (255, 0, 0), (255, 160, 0), (0, 0, 0)

stop_event = threading.Event()
last_ping = [time.time()]
face_seen = [False]


class Stopped(Exception):
    pass


def emit(event, **data):
    data["type"] = event
    print(json.dumps(data), flush=True)


def listen():
    for line in sys.stdin:
        line = line.strip()
        if line == "stop":
            emit("log", msg="Stop requested.")
            break
        if line.startswith("face "):
            face_seen[0] = line == "face 1"
        last_ping[0] = time.time()
    stop_event.set()  # "stop", or the app closed the channel


def watchdog():
    while not stop_event.is_set():
        if time.time() - last_ping[0] > PING_TIMEOUT:
            emit("log", msg="Lost contact with the app, so stopping.")
            stop_event.set()
        time.sleep(0.2)


class Camera:
    """Serves the live picture and spots things just ahead that don't look like the floor. The floor's colour
    is learned at the start from the bottom-middle of the picture, so start with clear floor in front."""

    def __init__(self):
        self.ok = False
        self.error = ""
        self.left = self.center = self.right = 0.0
        self.jpeg = None
        self.new_frame = threading.Condition()
        self.cam = None
        try:
            import numpy as np
            from picamera2 import Picamera2
            self.np = np
            self.encode = self._encoder()
            self.cam = Picamera2()
            self.cam.configure(self.cam.create_preview_configuration(
                main={"size": FRAME_SIZE, "format": "RGB888"}, raw={"size": self._full_view_mode()},
            ))
            self.cam.start()
            # As in the camera stream: keep the crop at the whole sensor, or the picture looks zoomed in.
            self.cam.set_controls({"ScalerCrop": (0, 0) + tuple(self.cam.camera_properties["PixelArraySize"])})
            time.sleep(1.0)  # let the exposure settle
            w, h = FRAME_SIZE
            samples = np.concatenate([
                self._frame()[int(h * 0.8):, int(w * 0.3):int(w * 0.7)].reshape(-1, 3).astype("int16")
                for _ in range(5)
            ])
            self.floor = samples.mean(axis=0)
            self.tolerance = np.maximum(samples.std(axis=0) * 3, 18)
            self.ok = True
            threading.Thread(target=self._loop, daemon=True).start()
            threading.Thread(target=self._serve, daemon=True).start()
        except Exception as e:
            text = str(e).strip()
            self.error = text.splitlines()[-1] if text else type(e).__name__
            self.close()

    def _full_view_mode(self):
        """The size of the smallest sensor mode that still sees the whole sensor. For a small picture the
        camera library would otherwise pick a mode that reads only the middle of it: a zoomed-in view."""
        width, height = self.cam.camera_properties["PixelArraySize"]
        modes = self.cam.sensor_modes
        whole = [m for m in modes if tuple(m.get("crop_limits", (0, 0, width, height)))[2:] == (width, height)]
        return min(whole or modes, key=lambda m: m["size"][0] * m["size"][1])["size"]

    @staticmethod
    def _encoder():
        """A function turning a BGR frame (as picamera2's RGB888 format lays it out) into JPEG bytes."""
        try:
            import simplejpeg
            return lambda frame: simplejpeg.encode_jpeg(frame, quality=70, colorspace="BGR")
        except ImportError:
            from PIL import Image

            def encode(frame):
                buffer = io.BytesIO()
                Image.fromarray(frame[:, :, ::-1]).save(buffer, "JPEG", quality=70)
                return buffer.getvalue()
            return encode

    def _frame(self):
        return self.cam.capture_array()[:, :, :3]

    def _loop(self):
        np = self.np
        w, h = FRAME_SIZE
        third = w // 3
        last_report = 0.0
        while not stop_event.is_set():
            try:
                frame = self._frame()
                jpeg = self.encode(np.ascontiguousarray(frame))
            except Exception:
                self.ok = False
                emit("log", msg="The camera stopped; carrying on with the distance sensor.")
                return
            with self.new_frame:
                self.jpeg = jpeg
                self.new_frame.notify_all()
            near = frame[int(h * 0.55):].astype("int16")  # the floor just ahead
            unlike_floor = (np.abs(near - self.floor) > self.tolerance).any(axis=2)
            self.left = float(unlike_floor[:, :third].mean())
            self.center = float(unlike_floor[:, third:2 * third].mean())
            self.right = float(unlike_floor[:, 2 * third:].mean())
            if time.time() - last_report > 0.5:
                last_report = time.time()
                emit("vision", left=round(self.left, 2), center=round(self.center, 2), right=round(self.right, 2),
                     blocked=self.looks_blocked())
            time.sleep(0.05)

    def _serve(self):
        camera = self

        class Handler(server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/stream.mjpg":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
                self.end_headers()
                try:
                    while not stop_event.is_set():
                        with camera.new_frame:
                            camera.new_frame.wait(1.0)
                            frame = camera.jpeg
                        if frame is None:
                            continue
                        self.wfile.write(b"--FRAME\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(frame)))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                except Exception:
                    pass

            def log_message(self, format, *args):
                pass  # keep stdout for the JSON lines

        class Server(socketserver.ThreadingMixIn, server.HTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        try:
            httpd = Server(("", STREAM_PORT), Handler)
        except OSError as e:
            emit("log", msg=f"Can't serve the live picture: {e}")
            return
        emit("stream", port=STREAM_PORT)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        stop_event.wait()
        httpd.shutdown()

    def looks_blocked(self):
        return self.ok and self.center > VISION_BLOCKED

    def close(self):
        try:
            if self.cam is not None:
                self.cam.stop()
                self.cam.close()
        except Exception:
            pass


class Navigator:
    def __init__(self, plan, gpg, distance_sensor, camera):
        self.plan, self.gpg, self.ds, self.camera = plan, gpg, distance_sensor, camera
        self.stop_cm = plan["stop_cm"]
        self.use_vision = plan.get("vision") and camera is not None and camera.ok
        self.x = self.y = 0.0          # cm east and north of the start
        self.heading = plan["heading"]  # degrees clockwise from north (the top of the grid)
        self.wheel_mm = gpg.WHEEL_CIRCUMFERENCE
        self.base_mm = getattr(gpg, "WHEEL_BASE_CIRCUMFERENCE", 117 * math.pi)
        self.here = tuple(plan["start"])  # the cell it's in (or last left)
        self.blocked_cells = set()
        self.cell = None
        self.avoiding = False
        self.eye_color = None
        self.last_sensors = self.last_battery_read = 0.0
        self.battery = None

    # ---- sensing and reporting ----
    def distance_ahead_cm(self):
        if self.ds is None:
            return None
        try:
            mm = self.ds.read_mm()
        except Exception:
            return None
        return mm / 10 if 0 < mm < 3000 else None

    def blocked(self, left_to_go):
        """None if the way ahead is clear for the left_to_go cm still to drive, otherwise what's blocking it, for
        the incident log. Something past the end of the leg (a wall beyond the last cell) doesn't count."""
        cm = self.distance_ahead_cm()
        if cm is not None and cm <= self.stop_cm and cm < left_to_go + CREEP_GAP_CM:
            return f"distance sensor: {cm:.0f} cm"
        if self.use_vision and self.camera.looks_blocked():
            return "camera"
        return None

    def housekeeping(self):
        """Run often: keep the eyes right and send sensor readings now and then."""
        if self.plan.get("eyes"):
            color = YELLOW if self.avoiding else RED if face_seen[0] else OFF
            if color != self.eye_color:
                self.eye_color = color
                self.gpg.set_led(self.gpg.LED_EYE_LEFT + self.gpg.LED_EYE_RIGHT, *color)
                emit("eyes", color=list(color))
        now = time.time()
        if now - self.last_sensors >= SENSORS_EVERY:
            self.last_sensors = now
            if now - self.last_battery_read >= BATTERY_EVERY:
                self.last_battery_read = now
                try:
                    self.battery = round(self.gpg.volt(), 2)
                except Exception:
                    self.battery = None
            emit("sensors", battery=self.battery, distance_cm=self.distance_ahead_cm())

    def incident(self, kind, msg):
        emit("incident", kind=kind, msg=msg, cell=self.cell)

    # ---- moving ----
    def encoders(self):
        return self.gpg.get_motor_encoder(self.gpg.MOTOR_LEFT), self.gpg.get_motor_encoder(self.gpg.MOTOR_RIGHT)

    def _move(self, left_deg, right_deg, watch_cm=None):
        """Turn the wheels by these amounts. Returns (result, left moved, right moved, why); result is "done",
        "blocked" (driving a watch_cm leg and something's in the way) or "stuck". Raises Stopped when told to stop."""
        g = self.gpg
        l0, r0 = self.encoders()
        g.set_motor_position(g.MOTOR_LEFT, l0 + left_deg)
        g.set_motor_position(g.MOTOR_RIGHT, r0 + right_deg)
        last, last_progress = (l0, r0), time.time()
        while True:
            if stop_event.is_set():
                g.stop()
                raise Stopped()
            self.housekeeping()
            if self.plan.get("pause_for_faces") and face_seen[0]:
                g.stop()
                self.wait_while_face()
                # Carry on to the same targets, so the move still ends where it should.
                g.set_motor_position(g.MOTOR_LEFT, l0 + left_deg)
                g.set_motor_position(g.MOTOR_RIGHT, r0 + right_deg)
                last_progress = time.time()
            l, r = self.encoders()
            if abs(l - l0 - left_deg) <= ARRIVED_DEGREES and abs(r - r0 - right_deg) <= ARRIVED_DEGREES:
                return "done", l - l0, r - r0, None
            why = None
            if watch_cm is not None:
                driven_cm = (abs(l - l0) + abs(r - r0)) / 2 / 360 * self.wheel_mm / 10
                why = self.blocked(watch_cm - driven_cm)
            if why:
                g.stop()
                time.sleep(0.15)
                l, r = self.encoders()
                return "blocked", l - l0, r - r0, why
            if (l, r) != last:
                last, last_progress = (l, r), time.time()
            elif time.time() - last_progress > STUCK_SECONDS:
                g.stop()
                return "stuck", l - l0, r - r0, None
            time.sleep(LOOP)

    def wait_while_face(self):
        """Stand still while the app sees a face, then return. Raises Stopped if told to stop meanwhile."""
        self.incident("paused", "A face is in view: waiting until it's gone.")
        while face_seen[0]:
            if stop_event.is_set():
                raise Stopped()
            self.housekeeping()
            time.sleep(LOOP)
        self.incident("resumed", "No face in view any more: carrying on.")

    def drive(self, cm, watch=True):
        """Drive forward cm, or back for negative cm (watching for obstacles going forward). Returns (result,
        cm driven, why it stopped)."""
        wheel_deg = cm * 10 / self.wheel_mm * 360
        result, dl, dr, why = self._move(wheel_deg, wheel_deg, cm if watch and cm > 0 else None)
        moved = (dl + dr) / 2 / 360 * self.wheel_mm / 10
        self.x += moved * math.sin(math.radians(self.heading))
        self.y += moved * math.cos(math.radians(self.heading))
        self.report_pose()
        return result, moved, why

    def turn(self, degrees):
        """Spin on the spot, clockwise for positive degrees."""
        if abs(degrees) < 1:
            return
        self.signal(degrees)
        wheel_deg = self.base_mm * degrees / 360 / self.wheel_mm * 360
        try:
            _, dl, dr, _ = self._move(wheel_deg, -wheel_deg)
        finally:
            self.signal(0)
        turned = (dl - dr) / 2 / 360 * self.wheel_mm / self.base_mm * 360
        self.heading = (self.heading + turned) % 360
        self.report_pose()

    def face(self, heading):
        self.turn((heading - self.heading + 180) % 360 - 180)

    def signal(self, degrees):
        if not self.plan.get("turn_signals"):
            return
        g = self.gpg
        g.set_led(g.LED_BLINKER_LEFT, 255 if degrees < 0 else 0)
        g.set_led(g.LED_BLINKER_RIGHT, 255 if degrees > 0 else 0)

    def report_pose(self):
        emit("pose", x=round(self.x, 1), y=round(self.y, 1), heading=round(self.heading))

    # ---- navigating ----
    def centre(self, cell):
        """Where a cell's centre is, in cm east and north of the start."""
        (r0, c0), cell_cm = self.plan["start"], self.plan["cell_cm"]
        return (cell[1] - c0) * cell_cm, (r0 - cell[0]) * cell_cm

    def route(self, goal):
        """The shortest way from here to goal through free cells of the grid, as the cells to step to in turn,
        or None if there's none."""
        rows, cols = self.plan["rows"], self.plan["cols"]
        came_from = {self.here: None}
        queue = collections.deque([self.here])
        while queue:
            cell = queue.popleft()
            if cell == goal:
                steps = []
                while cell != self.here:
                    steps.append(cell)
                    cell = came_from[cell]
                return steps[::-1]
            r, c = cell
            for nxt in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if (0 <= nxt[0] < rows and 0 <= nxt[1] < cols and nxt not in came_from
                        and nxt not in self.blocked_cells):
                    came_from[nxt] = cell
                    queue.append(nxt)
        return None

    def step_to(self, cell):
        """Drive to the centre of a neighbouring cell. Returns "done", "stuck" or "blocked" -- then it has
        backed up to the centre of the cell it left and marked this one blocked."""
        tx, ty = self.centre(cell)
        dx, dy = tx - self.x, ty - self.y
        self.face(math.degrees(math.atan2(dx, dy)) % 360)
        result, moved, why = self.drive(math.hypot(dx, dy))
        if result == "done":
            self.here = cell
        elif result == "blocked":
            self.blocked_cells.add(cell)
            emit("blocked", cell=list(cell))
            self.incident("obstacle", f"Something in the way ({why}); cell {cell[0]},{cell[1]} marked blocked.")
            self.drive(-moved, watch=False)
        return result

    def go_to(self, goal):
        """Drive to a cell, cell by cell, the shortest way round blocked ones. Returns "reached", "skipped" or
        "stuck"."""
        while self.here != goal:
            if goal in self.blocked_cells:
                self.incident("obstacle", "Something is on this cell; skipping it.")
                return "skipped"
            steps = self.route(goal)
            if steps is None:
                self.incident("obstacle", "No way to this cell round the blocked ones; skipping it.")
                return "skipped"
            self.avoiding = len(steps) > abs(goal[0] - self.here[0]) + abs(goal[1] - self.here[1])
            if self.step_to(steps[0]) == "stuck":
                return "stuck"
        return "reached"

    def run(self):
        for i, cell in enumerate(self.plan["targets"]):
            self.cell = cell
            emit("step", index=i, cell=cell, state="going")
            try:
                result = self.go_to(tuple(cell))
            finally:
                self.avoiding = False
            emit("step", index=i, cell=cell, state=result)
            if result == "stuck":
                self.incident("stuck", "The wheels stopped turning -- the robot may be caught on something. Stopping.")
                return

def main():
    plan = json.loads(sys.stdin.readline())
    threading.Thread(target=listen, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    gpg = easygopigo3.EasyGoPiGo3(use_mutex=True)
    gpg.set_speed(plan["speed"])
    distance_sensor = gpg.init_distance_sensor()
    camera = Camera()
    emit("sensors_found", distance=distance_sensor is not None, camera=camera.ok, camera_error=camera.error)
    if distance_sensor is None and not (plan.get("vision") and camera.ok):
        emit("log", msg="Warning: no distance sensor and no camera, so obstacles won't be seen.")
    navigator = Navigator(plan, gpg, distance_sensor, camera)
    stopped = False
    try:
        navigator.run()
    except Stopped:
        stopped = True
    finally:
        # Let go of everything before saying "done", so whatever the app starts next finds the camera free.
        stop_event.set()
        gpg.stop()
        gpg.set_led(gpg.LED_BLINKER_LEFT + gpg.LED_BLINKER_RIGHT, 0)
        if plan.get("eyes"):
            gpg.set_led(gpg.LED_EYE_LEFT + gpg.LED_EYE_RIGHT, 0, 0, 0)
        camera.close()
    emit("done", stopped=stopped)


main()
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

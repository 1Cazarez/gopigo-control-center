"""Robot sensor panels -- battery, distance sensor, IMU and wheel encoders -- fed by one telemetry stream.

Start Sensors runs telemetry.py on the robot (TELEMETRY_SCRIPT in remote_scripts.py), which prints a JSON
line of readings about five times a second. Any number of panels can be built (the Dashboard and the Manual
Control tab each have one); they all show the same stream, and any panel's button starts or stops it.
"""

import json
import threading
import time
import tkinter as tk
from tkinter import ttk

from constants import VENV_ACTIVATE, login_shell
from remote_scripts import REMOTE_KERNEL_DIR, REMOTE_TELEMETRY_PATH, TELEMETRY_SCRIPT
from theme import style_button
from widgets import BatteryMeter, Compass, ProximityBar

OPEN_TIMEOUT = 15       # seconds to wait for the robot to open a channel
OUT_OF_RANGE_MM = 3000  # what the distance sensor reports when nothing is in range
SENSORS_OFF = "Sensors off. Connect, then click Start Sensors."


class SensorPanel:
    """The widgets of one sensor panel."""

    def __init__(self, parent, app, title="Robot"):
        s = app.ui_scale
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        head = ttk.Frame(self.frame, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=title, style="CardBold.TLabel").pack(side="left")
        self.button = style_button(tk.Button(head, text="Start Sensors", command=app.toggle_sensors), "success")
        self.button.pack(side="right")

        readings = ttk.Frame(self.frame, style="Card.TFrame")
        readings.pack(fill="x", pady=(8, 0))

        def row_label(row, text):
            ttk.Label(readings, text=text, style="Card.TLabel").grid(row=row, column=0, sticky="ne", padx=(0, 8), pady=4)

        row_label(0, "Battery:")
        self.battery = BatteryMeter(readings, scale=s)
        self.battery.grid(row=0, column=1, sticky="w", pady=4)
        row_label(1, "Distance:")
        self.distance = ProximityBar(readings, scale=s)
        self.distance.grid(row=1, column=1, sticky="w", pady=4)
        row_label(2, "IMU:")
        imu = ttk.Frame(readings, style="Card.TFrame")
        imu.grid(row=2, column=1, sticky="w", pady=4)
        self.compass = Compass(imu, scale=s)
        self.compass.pack(side="left")
        self.imu_var = tk.StringVar(value="--")
        ttk.Label(imu, textvariable=self.imu_var, style="Card.TLabel", justify="left").pack(side="left", padx=10)
        row_label(3, "Gyro:")
        self.gyro_var = tk.StringVar(value="--")
        ttk.Label(readings, textvariable=self.gyro_var, style="Card.TLabel").grid(row=3, column=1, sticky="w", pady=4)
        row_label(4, "Encoders:")
        self.encoders_var = tk.StringVar(value="--")
        ttk.Label(readings, textvariable=self.encoders_var, style="Card.TLabel").grid(row=4, column=1, sticky="w", pady=4)

        self.status_var = tk.StringVar(value=SENSORS_OFF)
        ttk.Label(
            self.frame, textvariable=self.status_var, style="CardMuted.TLabel", wraplength=int(330 * s), justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def show(self, reading):
        self.battery.set_voltage(reading.get("battery"))

        mm = reading.get("distance_mm")
        if mm is None:
            self.distance.set_distance(None, "no distance sensor")
        elif mm >= OUT_OF_RANGE_MM:
            self.distance.set_distance(None, "nothing in range")
        elif mm <= 0:
            self.distance.set_distance(None, "no reading")
        else:
            self.distance.set_distance(mm / 10)

        imu = reading.get("imu")
        if imu is None:
            self.compass.set_heading(None)
            self.imu_var.set("no IMU found\n(plug it into AD1, AD2 or I2C)")
            self.gyro_var.set("--")
        else:
            self.compass.set_heading(imu["heading"])
            calibration = imu["calibration"]
            hint = "" if calibration == 3 else "\nmove it in a figure-8 to calibrate"
            self.imu_var.set(
                f"heading {imu['heading']:.0f}°\nroll {imu['roll']:.1f}°  pitch {imu['pitch']:.1f}°\n"
                f"compass calibration {max(calibration, 0)}/3{hint}"
            )
            gx, gy, gz = imu["gyro"]
            self.gyro_var.set(f"x {gx:.1f}  y {gy:.1f}  z {gz:.1f} °/s")

        encoders = reading.get("encoders")
        self.encoders_var.set(f"L {encoders[0]}°  R {encoders[1]}°" if encoders else "--")
        self.status_var.set("Sensors on.")

    def clear(self, message):
        self.status_var.set(message)
        self.battery.set_voltage(None)
        self.distance.set_distance(None)
        self.compass.set_heading(None)
        for var in (self.imu_var, self.gyro_var, self.encoders_var):
            var.set("--")

    def set_running(self, running, starting=False):
        if starting:
            self.button.config(text="Starting...", state="disabled")
        elif running:
            self.button.config(text="Stop Sensors", state="normal")
            style_button(self.button, "danger")
        else:
            self.button.config(text="Start Sensors", state="normal")
            style_button(self.button, "success")


class SensorsMixin:
    def _build_sensor_panel(self, parent, title="Robot"):
        """A new sensor panel in parent (not yet placed); returns its frame."""
        if not hasattr(self, "sensor_panels"):
            self.sensor_panels = []
            self.telemetry_channel = None
            self.telemetry_starting = False
        panel = SensorPanel(parent, self, title)
        self.sensor_panels.append(panel)
        return panel.frame

    def toggle_sensors(self):
        if self.telemetry_channel is not None:
            self._stop_telemetry("Sensors off.")
            return
        if self.telemetry_starting or not self._require_connection():
            return
        self.telemetry_starting = True
        for panel in self.sensor_panels:
            panel.set_running(False, starting=True)
            panel.status_var.set("Starting the sensor reader on the robot...")

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass
                with sftp.file(REMOTE_TELEMETRY_PATH, "w") as f:
                    f.write(TELEMETRY_SCRIPT)
                sftp.close()
                channel = self.ssh_client.get_transport().open_session(timeout=OPEN_TIMEOUT)
                channel.exec_command(login_shell(f"{VENV_ACTIVATE} && python3 -u {REMOTE_TELEMETRY_PATH}"))
                self.telemetry_channel = channel
                self.telemetry_starting = False
                self.root.after(0, self._on_telemetry_started)
                self._telemetry_loop(channel)
            except Exception as e:
                error = str(e)
                self.telemetry_starting = False
                self.root.after(0, lambda: self._stop_telemetry(f"Couldn't start the sensors: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_telemetry_started(self):
        for panel in self.sensor_panels:
            panel.set_running(True)
            panel.status_var.set("Sensors on. Looking for the distance sensor and IMU...")

    def _telemetry_loop(self, channel):
        buffer, errors = b"", b""
        while self.telemetry_channel is channel:
            if channel.recv_ready():
                buffer += channel.recv(4096)
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if line.startswith(b"{"):  # anything else is a library printing a warning
                        try:
                            reading = json.loads(line)
                        except ValueError:
                            continue
                        self.root.after(0, lambda r=reading: self._show_telemetry(r, channel))
            elif channel.recv_stderr_ready():
                errors = (errors + channel.recv_stderr(4096))[-2000:]
            elif channel.exit_status_ready():
                lines = errors.decode(errors="replace").strip().splitlines()
                message = f"The sensor reader stopped: {lines[-1] if lines else 'no error message'}"
                self.root.after(0, lambda: self._stop_telemetry(message, channel))
                return
            else:
                time.sleep(0.05)

    def _show_telemetry(self, reading, channel):
        if self.telemetry_channel is not channel:
            return  # stopped since this reading arrived
        for panel in self.sensor_panels:
            panel.show(reading)

    def _stop_telemetry(self, message, channel=None):
        if channel is not None and self.telemetry_channel is not channel:
            return  # that reader was already replaced
        old, self.telemetry_channel = self.telemetry_channel, None
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        for panel in self.sensor_panels:
            panel.set_running(False)
            panel.clear(message)

"""Live Sensors tab -- battery meter, distance sensor, gyro."""

import threading
import time
import tkinter as tk
from tkinter import ttk

from constants import VENV_ACTIVATE, login_shell
from theme import style_button
from widgets import BatteryMeter


class SensorTabMixin:
    def _build_sensor_tab(self):
        outer = ttk.Frame(self.sensor_tab, style="Card.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        wrap = ttk.Frame(outer, style="Card.TFrame")
        wrap.pack(expand=True)

        ttk.Label(wrap, text="Live Sensor Readings", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, pady=(0, 20)
        )

        ttk.Label(wrap, text="Battery:", style="Card.TLabel").grid(row=1, column=0, sticky="e", padx=8, pady=6)
        self.battery_meter = BatteryMeter(wrap)
        self.battery_meter.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(wrap, text="Distance sensor:", style="Card.TLabel").grid(
            row=2, column=0, sticky="e", padx=8, pady=6
        )
        self.distance_var = tk.StringVar(value="--")
        ttk.Label(wrap, textvariable=self.distance_var, style="CardBold.TLabel").grid(
            row=2, column=1, sticky="w", padx=8
        )
        ttk.Label(
            wrap, text="(requires a Distance Sensor plugged into an I2C port)", style="CardMuted.TLabel"
        ).grid(row=3, column=0, columnspan=2)

        ttk.Label(wrap, text="Gyro (x, y, z deg/s):", style="Card.TLabel").grid(
            row=4, column=0, sticky="e", padx=8, pady=6
        )
        self.gyro_var = tk.StringVar(value="--")
        ttk.Label(wrap, textvariable=self.gyro_var, style="CardBold.TLabel").grid(
            row=4, column=1, sticky="w", padx=8
        )
        ttk.Label(
            wrap, text="(requires the IMU sensor plugged into an I2C port)", style="CardMuted.TLabel"
        ).grid(row=5, column=0, columnspan=2)

        self.poll_btn = style_button(
            tk.Button(wrap, text="Start Live Sensors", command=self._toggle_polling), "success"
        )
        self.poll_btn.grid(row=6, column=0, columnspan=2, pady=16)

    def _toggle_polling(self):
        if self.sensor_polling:
            self.sensor_polling = False
            self.poll_btn.config(text="Start Live Sensors", bg="#2ea36f")
            return
        if not self._require_connection():
            return
        self.sensor_polling = True
        self.poll_btn.config(text="Stop Live Sensors", bg="#e0473b")
        threading.Thread(target=self._poll_sensors_loop, daemon=True).start()

    def _poll_sensors_loop(self):
        voltage_cmd = (
            f"{VENV_ACTIVATE} && python3 -c "
            "\"import gopigo3; g = gopigo3.GoPiGo3(); print(g.get_voltage_battery())\""
        )
        distance_cmd = (
            f"{VENV_ACTIVATE} && python3 -c "
            "\"import easygopigo3 as easy; gpg = easy.EasyGoPiGo3(); "
            "ds = gpg.init_distance_sensor(); print(ds.read_mm())\""
        )
        gyro_cmd = (
            f"{VENV_ACTIVATE} && python3 -c "
            "\"import easygopigo3 as easy; gpg = easy.EasyGoPiGo3(); "
            "imu = gpg.init_imu_sensor(); x, y, z = imu.read_gyroscope(); "
            "print(f'{x:.1f}, {y:.1f}, {z:.1f}')\""
        )
        while self.sensor_polling and self.ssh_client is not None:
            try:
                _, stdout, _ = self.ssh_client.exec_command(login_shell(voltage_cmd), timeout=8)
                voltage_out = stdout.read().decode(errors="replace").strip()
                try:
                    voltage = float(voltage_out)
                except ValueError:
                    voltage = None
                self.root.after(0, lambda v=voltage: self.battery_meter.set_voltage(v))
            except Exception:
                self.root.after(0, lambda: self.battery_meter.set_voltage(None))

            try:
                _, stdout, _ = self.ssh_client.exec_command(login_shell(distance_cmd), timeout=8)
                dist_out = stdout.read().decode(errors="replace").strip()
                self.root.after(0, lambda d=dist_out: self.distance_var.set(f"{d} mm" if d else "N/A"))
            except Exception:
                self.root.after(0, lambda: self.distance_var.set("N/A"))

            try:
                _, stdout, _ = self.ssh_client.exec_command(login_shell(gyro_cmd), timeout=8)
                gyro_out = stdout.read().decode(errors="replace").strip()
                self.root.after(0, lambda g=gyro_out: self.gyro_var.set(g if g else "N/A"))
            except Exception:
                self.root.after(0, lambda: self.gyro_var.set("N/A"))

            time.sleep(2)

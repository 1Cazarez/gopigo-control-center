"""Autopilot tab -- the robot drives itself from grid cell to grid cell along a path you draw, going around
obstacles, with the live camera, face detection, sensors and a log of incidents.

The driving runs on the robot (NAVIGATOR_SCRIPT in remote_scripts.py) so Wi-Fi lag can't upset it. This tab
sends it the plan, pings it every half second (it stops if the pings stop), tells it when the camera sees a
face (detection runs here, on the computer), and shows what it reports. Incidents -- faces, obstacles,
skipped cells, a stuck robot, a low battery -- are listed here and appended to gopigo_logs/
autopilot_incidents.csv, with a photo for each face, in the folder the app was started from.
"""

import csv
import json
import math
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from camera_view import CameraView
from constants import VENV_ACTIVATE, login_shell
from remote_scripts import CAMERA_STREAM_PORT, NAVIGATOR_SCRIPT, REMOTE_KERNEL_DIR, REMOTE_NAVIGATOR_PATH
from theme import BORDER, CARD_BG, DANGER, FONT_MONO_SMALL, MUTED, PRIMARY, SUCCESS, TEXT, style_button
from widgets import BatteryMeter, ProximityBar

OPEN_TIMEOUT = 15
PING_MS = 500
FACINGS = {"up": 0, "right": 90, "down": 180, "left": 270}  # which way the robot faces at the start
LOG_DIR = "gopigo_logs"
BATTERY_RECOVERED_V = 0.3   # after a low-battery incident, the next one waits until it has risen this much
FACE_INCIDENT_GAP_S = 10    # at most one face incident this often
KILL_NAVIGATORS = "pkill -f '[n]avigator.py'"


def route(a, b):
    """The cells from a to b (a excluded, b included), down/up the rows first, then along the columns."""
    (r, c), (r2, c2) = a, b
    cells = []
    while r != r2:
        r += 1 if r2 > r else -1
        cells.append((r, c))
    while c != c2:
        c += 1 if c2 > c else -1
        cells.append((r, c))
    return cells


class PathGrid(tk.Canvas):
    """The grid the robot drives on, top = the way the robot faces when it's "up". Click cells to lay a path."""

    STATE_COLORS = {"going": "#fff3c4", "reached": "#d4f5e2", "occupied": "#f8d7d4", "gave up": "#f8d7d4", "stuck": "#f8d7d4"}

    def __init__(self, parent, size, on_change=None):
        super().__init__(parent, width=size, height=size, bg=CARD_BG, highlightthickness=0)
        self.size = size
        self.on_change = on_change
        self.rows = self.cols = 4
        self.path = []
        self.states = {}
        self.robot = None       # (row, col, heading) while running, fractional
        self.facing = 0
        self.editable = True
        self.bind("<Button-1>", self._click)
        self.draw()

    def configure_grid(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.path = [cell for cell in self.path if cell[0] < rows and cell[1] < cols]
        self._changed()

    def _cell_px(self):
        return (self.size - 8) / max(self.rows, self.cols)

    def _center(self, row, col):
        p = self._cell_px()
        return 4 + (col + 0.5) * p, 4 + (row + 0.5) * p

    def _click(self, event):
        if not self.editable:
            return
        p = self._cell_px()
        col, row = int((event.x - 4) // p), int((event.y - 4) // p)
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            return
        if not self.path:
            self.path = [(row, col)]
        elif (row, col) != self.path[-1]:
            self.path += route(self.path[-1], (row, col))
        self._changed()

    def undo(self):
        if self.path:
            self.path.pop()
            self._changed()

    def clear(self):
        self.path = []
        self._changed()

    def snake(self):
        """Every cell, row by row, turning at the ends -- like mowing a lawn -- from the bottom-left."""
        self.path = []
        for i, row in enumerate(range(self.rows - 1, -1, -1)):
            cols = range(self.cols) if i % 2 == 0 else range(self.cols - 1, -1, -1)
            self.path += [(row, c) for c in cols]
        self._changed()

    def perimeter(self):
        """Round the edge once, from the bottom-left, back to the start."""
        r, c = self.rows - 1, self.cols - 1
        corners = [(r, 0), (0, 0), (0, c), (r, c), (r, 0)]
        self.path = [corners[0]]
        for corner in corners[1:]:
            self.path += route(self.path[-1], corner)
        self._changed()

    def _changed(self):
        self.states = {}
        self.draw()
        if self.on_change is not None:
            self.on_change()

    def draw(self):
        self.delete("all")
        p = self._cell_px()
        for r in range(self.rows):
            for c in range(self.cols):
                x0, y0 = 4 + c * p, 4 + r * p
                fill = self.STATE_COLORS.get(self.states.get((r, c)), "#ffffff")
                self.create_rectangle(x0, y0, x0 + p, y0 + p, fill=fill, outline=BORDER)
                if self.states.get((r, c)) in ("occupied", "gave up", "stuck"):
                    self.create_line(x0 + 6, y0 + 6, x0 + p - 6, y0 + p - 6, fill=DANGER, width=2)
                    self.create_line(x0 + p - 6, y0 + 6, x0 + 6, y0 + p - 6, fill=DANGER, width=2)
        if len(self.path) > 1:
            self.create_line(*[v for cell in self.path for v in self._center(*cell)], fill=PRIMARY, width=3,
                             arrow="last", arrowshape=(10, 12, 4))
        for i, cell in enumerate(self.path[1:], start=1):
            x, y = self._center(*cell)
            self.create_text(x + p * 0.28, y - p * 0.28, text=str(i), fill=MUTED, font=FONT_MONO_SMALL)
        if self.path:
            x, y = self._center(*self.path[0])
            self.create_oval(x - p * 0.22, y - p * 0.22, x + p * 0.22, y + p * 0.22, fill=SUCCESS, outline="")
            self.create_text(x, y, text="S", fill="#ffffff", font=FONT_MONO_SMALL)
            if self.robot is None:
                self._arrow(x, y, self.facing, p * 0.42, SUCCESS)
        if self.robot is not None:
            row, col, heading = self.robot
            x, y = self._center(row, col)
            self.create_oval(x - p * 0.18, y - p * 0.18, x + p * 0.18, y + p * 0.18, fill=TEXT, outline="#ffffff", width=2)
            self._arrow(x, y, heading, p * 0.38, TEXT)

    def _arrow(self, x, y, heading, length, color):
        dx, dy = math.sin(math.radians(heading)) * length, -math.cos(math.radians(heading)) * length
        self.create_line(x, y, x + dx, y + dy, fill=color, width=3, arrow="last", arrowshape=(9, 11, 4))


class AutopilotTabMixin:
    def _build_autopilot_tab(self):
        self.nav_channel = None
        self.nav_stopping = False
        self.nav_plan = None
        self.nav_battery_warned = None   # voltage at the last low-battery incident
        self.nav_last_face_incident = 0.0
        s = self.ui_scale

        outer = ttk.Frame(self.autopilot_tab, style="Card.TFrame", padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(0, weight=3)
        outer.rowconfigure(1, weight=2)

        # -- Camera --
        camera = ttk.Frame(outer, style="Card.TFrame")
        camera.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        head = ttk.Frame(camera, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Robot's view", style="CardBold.TLabel").pack(side="left")
        self.nav_face_var = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.nav_face_var, style="CardMuted.TLabel").pack(side="left", padx=10)
        self.nav_camera = CameraView(
            camera, self.root, idle_text="The live picture appears here while the autopilot runs.",
            on_face_change=self._on_autopilot_face,
        )
        self.nav_camera.canvas.pack(fill="both", expand=True, pady=(6, 0))
        self.camera_views.append(self.nav_camera)

        # -- Incident log --
        log = ttk.Frame(outer, style="Card.TFrame")
        log.grid(row=1, column=0, sticky="nsew", padx=(0, 12), pady=(10, 0))
        head = ttk.Frame(log, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="Incidents", style="CardBold.TLabel").pack(side="left")
        style_button(tk.Button(head, text="Open Log Folder", command=self._open_incident_folder), "neutral").pack(side="right")
        style_button(tk.Button(head, text="Clear", command=self._clear_incidents), "neutral").pack(side="right", padx=6)
        self.incident_list = ttk.Treeview(log, columns=("time", "what", "details"), show="headings", height=5)
        for column, width in (("time", 70), ("what", 90), ("details", 420)):
            self.incident_list.heading(column, text=column.capitalize())
            self.incident_list.column(column, width=int(width * s), stretch=column == "details")
        self.incident_list.pack(fill="both", expand=True, pady=(4, 0))

        # -- Path, settings, run --
        side = ttk.Frame(outer, style="Card.TFrame")
        side.grid(row=0, column=1, rowspan=2, sticky="nsew")

        ttk.Label(side, text="Path (click cells; S = start)", style="CardBold.TLabel").pack(anchor="w")
        self.path_grid = PathGrid(side, int(250 * s), on_change=self._on_path_changed)
        self.path_grid.pack(anchor="w", pady=(4, 4))
        edit = ttk.Frame(side, style="Card.TFrame")
        edit.pack(anchor="w")
        for text, command in (("Snake", self.path_grid.snake), ("Perimeter", self.path_grid.perimeter),
                              ("Undo", self.path_grid.undo), ("Clear", self.path_grid.clear)):
            style_button(tk.Button(edit, text=text, command=command), "neutral").pack(side="left", padx=(0, 4))

        form = ttk.Frame(side, style="Card.TFrame")
        form.pack(anchor="w", pady=(8, 0))
        self.nav_rows_var = tk.IntVar(value=4)
        self.nav_cols_var = tk.IntVar(value=4)
        self.nav_cell_var = tk.IntVar(value=50)
        self.nav_facing_var = tk.StringVar(value="up")
        self.nav_speed_var = tk.IntVar(value=150)
        self.nav_vision_var = tk.BooleanVar(value=True)
        self.nav_eyes_var = tk.BooleanVar(value=True)
        self.nav_low_battery_var = tk.DoubleVar(value=self.settings.get("low_battery_v", 9.0))

        def spin(row, label, var, low, high, step=1, col=0):
            ttk.Label(form, text=label, style="Card.TLabel").grid(row=row, column=col, sticky="e", padx=(0, 4), pady=2)
            box = ttk.Spinbox(form, from_=low, to=high, increment=step, textvariable=var, width=5,
                              command=self._on_grid_size_changed)
            box.grid(row=row, column=col + 1, sticky="w", pady=2)
            box.bind("<FocusOut>", lambda e: self._on_grid_size_changed())
            return box

        spin(0, "Rows", self.nav_rows_var, 2, 8)
        spin(0, "Columns", self.nav_cols_var, 2, 8, col=2)
        spin(1, "Cell (cm)", self.nav_cell_var, 20, 150, 5)
        ttk.Label(form, text="Robot faces", style="Card.TLabel").grid(row=1, column=2, sticky="e", padx=(8, 4), pady=2)
        facing = ttk.Combobox(form, textvariable=self.nav_facing_var, values=list(FACINGS), state="readonly", width=6)
        facing.grid(row=1, column=3, sticky="w", pady=2)
        facing.bind("<<ComboboxSelected>>", lambda e: self._on_path_changed())
        spin(2, "Speed (deg/s)", self.nav_speed_var, 50, 400, 10)
        box = spin(2, "Low battery (V)", self.nav_low_battery_var, 6.0, 12.0, 0.1, col=2)
        box.config(command=self._save_low_battery)
        box.bind("<FocusOut>", lambda e: self._save_low_battery())
        tk.Checkbutton(form, text="Use the camera to spot obstacles", variable=self.nav_vision_var, bg=CARD_BG,
                       activebackground=CARD_BG, highlightthickness=0).grid(row=3, column=0, columnspan=4, sticky="w")
        tk.Checkbutton(form, text="Eyes: red for a face, yellow going round an obstacle", variable=self.nav_eyes_var,
                       bg=CARD_BG, activebackground=CARD_BG, highlightthickness=0).grid(row=4, column=0, columnspan=4, sticky="w")

        run = ttk.Frame(side, style="Card.TFrame")
        run.pack(anchor="w", pady=(8, 0))
        self.nav_start_btn = style_button(tk.Button(run, text="Start Autopilot", command=self.start_autopilot), "success", big=True)
        self.nav_start_btn.pack(side="left")
        style_button(tk.Button(run, text="STOP", command=lambda: self.stop_autopilot("Stopped.")), "danger", big=True).pack(
            side="left", padx=8
        )
        self.nav_status_var = tk.StringVar(value="Draw a path, put the robot on S facing the arrow, then Start.")
        ttk.Label(side, textvariable=self.nav_status_var, style="CardMuted.TLabel", wraplength=int(330 * s),
                  justify="left").pack(anchor="w", pady=(6, 0))

        sensors = ttk.Frame(side, style="Card.TFrame")
        sensors.pack(anchor="w", pady=(6, 0))
        ttk.Label(sensors, text="Battery:", style="Card.TLabel").grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.nav_battery = BatteryMeter(sensors, scale=s)
        self.nav_battery.grid(row=0, column=1, sticky="w")
        ttk.Label(sensors, text="Ahead:", style="Card.TLabel").grid(row=1, column=0, sticky="e", padx=(0, 6))
        self.nav_distance = ProximityBar(sensors, scale=s * 0.8)
        self.nav_distance.grid(row=1, column=1, sticky="w", pady=2)
        self.nav_vision_text = tk.StringVar(value="")
        ttk.Label(sensors, textvariable=self.nav_vision_text, style="CardMuted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w")

        self.path_grid.snake()

    # ---------------- Editing ----------------
    def _on_grid_size_changed(self):
        try:
            rows, cols = int(self.nav_rows_var.get()), int(self.nav_cols_var.get())
        except (tk.TclError, ValueError):
            return
        rows, cols = max(2, min(8, rows)), max(2, min(8, cols))
        if (rows, cols) != (self.path_grid.rows, self.path_grid.cols):
            self.path_grid.configure_grid(rows, cols)

    def _on_path_changed(self):
        self.path_grid.facing = FACINGS[self.nav_facing_var.get()]
        self.path_grid.draw()

    def _save_low_battery(self):
        try:
            volts = round(float(self.nav_low_battery_var.get()), 1)
        except (tk.TclError, ValueError):
            return
        self._save_setting("low_battery_v", volts)

    # ---------------- Running ----------------
    def start_autopilot(self):
        if self.nav_channel is not None:
            return
        if not self._require_connection():
            return
        path = self.path_grid.path
        if len(path) < 2:
            messagebox.showwarning("Autopilot", "Draw a path of at least two cells first.")
            return
        if self.controller_active or self.motor_channel is not None:
            self._stop_driving("The autopilot took over.")
        try:
            cell = int(self.nav_cell_var.get())
            speed = int(self.nav_speed_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Autopilot", "The cell size and speed must be numbers.")
            return
        r0, c0 = path[0]
        self.nav_plan = {
            "targets": [{"x": (c - c0) * cell, "y": (r0 - r) * cell, "cell": [r, c]} for r, c in path[1:]],
            "heading": FACINGS[self.nav_facing_var.get()],
            "speed": speed,
            "stop_cm": self.settings["obstacle_stop_cm"],
            "vision": self.nav_vision_var.get(),
            "eyes": self.nav_eyes_var.get(),
            "turn_signals": self.turn_signals_var.get(),
            "cell_cm": cell,
            "start": [r0, c0],
        }
        self.path_grid.editable = False
        self.path_grid.states = {}
        self.path_grid.robot = (r0, c0, self.nav_plan["heading"])
        self.path_grid.draw()
        self.nav_stopping = False
        self.nav_battery_warned = None
        self.nav_start_btn.config(text="Starting...", state="disabled")
        self.nav_status_var.set("Starting the navigator on the robot...")
        camera_was_on = self.camera_channel is not None
        if camera_was_on:  # the navigator needs the camera itself, and serves the picture
            self.camera_stopping = True
            try:
                self.camera_channel.close()
            except Exception:
                pass
            self._on_camera_stopped()
        self.dash_camera.stop("The autopilot is using the camera -- see the Autopilot tab.")
        plan = json.dumps(self.nav_plan)

        def worker():
            try:
                self._stop_stray_camera_streams()  # frees the camera
                _, stdout, _ = self.ssh_client.exec_command(f"{KILL_NAVIGATORS}; true", timeout=OPEN_TIMEOUT)
                stdout.channel.recv_exit_status()
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass
                with sftp.file(REMOTE_NAVIGATOR_PATH, "w") as f:
                    f.write(NAVIGATOR_SCRIPT)
                sftp.close()
                channel = self.ssh_client.get_transport().open_session(timeout=OPEN_TIMEOUT)
                channel.exec_command(login_shell(f"{VENV_ACTIVATE} && python3 -u {REMOTE_NAVIGATOR_PATH}"))
                channel.send(plan + "\n")
                self.root.after(0, lambda: self._on_autopilot_started(channel))
                self._autopilot_reader(channel)
            except Exception as e:
                error = str(e)
                self.root.after(0, lambda: self._autopilot_finished(f"Couldn't start the autopilot: {error}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_autopilot_started(self, channel):
        self.nav_channel = channel
        self.nav_start_btn.config(text="Running...", state="disabled")
        self.nav_status_var.set("Running. STOP, Emergency Stop or driving by hand stops it.")
        self._log_incident("start", f"Autopilot started: {len(self.nav_plan['targets'])} cells.")
        self._autopilot_ping()

    def _autopilot_ping(self):
        channel = self.nav_channel
        if channel is None or self.nav_stopping:
            return
        try:
            channel.send("ping\n")
        except Exception:
            return
        self.root.after(PING_MS, self._autopilot_ping)

    def _autopilot_reader(self, channel):
        buffer, errors = b"", b""
        while True:
            if channel.recv_ready():
                buffer += channel.recv(4096)
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if line.startswith(b"{"):
                        try:
                            message = json.loads(line)
                        except ValueError:
                            continue
                        self.root.after(0, lambda m=message: self._on_autopilot_message(m, channel))
            elif channel.recv_stderr_ready():
                errors = (errors + channel.recv_stderr(4096))[-3000:]
            elif channel.exit_status_ready():
                lines = errors.decode(errors="replace").strip().splitlines()
                self.root.after(0, lambda: self._on_autopilot_exit(channel, lines[-1] if lines else ""))
                return
            else:
                time.sleep(0.05)

    def _on_autopilot_message(self, m, channel):
        if channel is not self.nav_channel and m["type"] != "done":
            return
        kind = m["type"]
        if kind == "pose":
            r0, c0 = self.nav_plan["start"]
            cell = self.nav_plan["cell_cm"]
            self.path_grid.robot = (r0 - m["y"] / cell, c0 + m["x"] / cell, m["heading"])
            self.path_grid.draw()
        elif kind == "step":
            self.path_grid.states[tuple(m["cell"])] = m["state"]
            self.path_grid.draw()
            if m["state"] != "going":
                self.nav_status_var.set(f"Cell {m['index'] + 1} of {len(self.nav_plan['targets'])}: {m['state']}.")
        elif kind == "sensors":
            self.nav_battery.set_voltage(m.get("battery"))
            cm = m.get("distance_cm")
            self.nav_distance.set_distance(cm, None if cm is not None else "nothing in range")
            self._check_battery(m.get("battery"))
        elif kind == "sensors_found":
            parts = ["distance sensor" if m["distance"] else "no distance sensor",
                     "camera" if m["camera"] else f"no camera ({m['camera_error'] or 'unavailable'})"]
            self.nav_status_var.set("Running with " + " and ".join(parts) + ".")
        elif kind == "stream":
            self.nav_camera.start(f"http://{self.host_var.get().strip()}:{m.get('port', CAMERA_STREAM_PORT)}/")
        elif kind == "vision":
            self.nav_vision_text.set(
                f"Camera, not floor: left {m['left']:.0%}  ahead {m['center']:.0%}  right {m['right']:.0%}"
                + ("  -- blocked" if m["blocked"] else "")
            )
        elif kind == "eyes":
            on = any(m["color"])
            self._record_lights(left_eye=on, right_eye=on)
        elif kind == "incident":
            self._log_incident(m["kind"], m["msg"], m.get("cell"))
        elif kind == "log":
            self.nav_status_var.set(m["msg"])
        elif kind == "done":
            self._autopilot_finished("Stopped." if m.get("stopped") else "Finished the path.")

    def _on_autopilot_exit(self, channel, last_error):
        if channel is not self.nav_channel:
            return
        if last_error and not self.nav_stopping:
            self._log_incident("error", f"The navigator stopped: {last_error}")
            self._autopilot_finished(f"The navigator stopped with an error: {last_error}")
        else:
            self._autopilot_finished(self.nav_status_var.get())

    def _on_autopilot_face(self, present, snapshot):
        self.nav_face_var.set("Face in view" if present else "")
        channel = self.nav_channel
        if channel is not None:
            try:
                channel.send(f"face {1 if present else 0}\n")
            except Exception:
                pass
        if present and channel is not None and time.time() - self.nav_last_face_incident >= FACE_INCIDENT_GAP_S:
            self.nav_last_face_incident = time.time()
            photo = self._save_face_photo(snapshot)
            self._log_incident("face", "Face detected" + (f" (photo: {os.path.basename(photo)})" if photo else ""),
                               photo=photo)

    def _check_battery(self, volts):
        if volts is None:
            return
        low = self.settings.get("low_battery_v", 9.0)
        if volts <= low and self.nav_battery_warned is None:
            self.nav_battery_warned = volts
            self._log_incident("battery", f"Battery may be low: {volts:.2f} V (warning at {low:.1f} V). Recharge soon.")
        elif self.nav_battery_warned is not None and volts >= low + BATTERY_RECOVERED_V:
            self.nav_battery_warned = None

    def stop_autopilot(self, reason):
        channel = self.nav_channel
        if channel is None:
            return
        self.nav_stopping = True
        self.nav_status_var.set(f"{reason} Stopping...")
        try:
            channel.send("stop\n")
        except Exception:
            pass
        # If it doesn't confirm, close the channel anyway: the robot stops when its input closes.
        self.root.after(3000, lambda: self._autopilot_finished(reason) if self.nav_channel is channel else None)

    def _autopilot_finished(self, message):
        channel, self.nav_channel = self.nav_channel, None
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
            self._log_incident("stop", message)
        self.nav_stopping = False
        self.nav_camera.stop()
        self.nav_face_var.set("")
        self.dash_camera.stop()
        self.path_grid.editable = True
        self.path_grid.robot = None
        self.path_grid.draw()
        self.nav_start_btn.config(text="Start Autopilot", state="normal")
        style_button(self.nav_start_btn, "success", big=True)
        self.nav_status_var.set(message)
        if self.nav_plan and self.nav_plan.get("eyes"):
            self._record_lights(left_eye=False, right_eye=False)  # the navigator turns them off as it exits

    # ---------------- Incident log ----------------
    def _incident_csv(self):
        folder = os.path.join(os.getcwd(), LOG_DIR)
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "autopilot_incidents.csv")

    def _log_incident(self, what, details, cell=None, photo=None):
        stamp = time.strftime("%H:%M:%S")
        if cell is not None:
            details = f"{details} [cell {cell[0]},{cell[1]}]"
        self.incident_list.insert("", 0, values=(stamp, what, details))
        try:
            path = self._incident_csv()
            new = not os.path.exists(path)
            with open(path, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if new:
                    writer.writerow(["time", "what", "details", "photo"])
                writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), what, details, photo or ""])
        except OSError as e:
            self._set_status(f"Couldn't write the incident log: {e}")

    def _save_face_photo(self, snapshot):
        if snapshot is None:
            return None
        try:
            folder = os.path.join(os.getcwd(), LOG_DIR, "faces")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"face_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
            snapshot.save(path, "JPEG", quality=85)
            return path
        except OSError:
            return None

    def _clear_incidents(self):
        self.incident_list.delete(*self.incident_list.get_children())

    def _open_incident_folder(self):
        folder = os.path.join(os.getcwd(), LOG_DIR)
        os.makedirs(folder, exist_ok=True)
        self._open_local_file(folder)

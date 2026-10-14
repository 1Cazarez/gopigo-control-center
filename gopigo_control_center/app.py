"""GoPiGoApp -- combines every tab mixin into the main Tkinter application,
plus the connection bar and status bar shared by all of them.
"""

import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import user_settings
from dependencies import paramiko, pygame
from tabs import (
    DashboardTabMixin,
    DriveTabMixin,
    EducationTabMixin,
    JupyterCameraTabMixin,
    LightsTabMixin,
    ManualTabMixin,
    SensorsMixin,
    WifiTabMixin,
)
from theme import BAR_BG, FONT_BASE, init_style, style_button

# Window size for text of normal size. Some setups (for example Tk on XWayland) render text about twice
# as large as requested, which would clip most tabs at a fixed size, so the window grows with the text.
BASE_WIDTH, BASE_HEIGHT = 1040, 780
NORMAL_LINE_HEIGHT = 18  # px: a 10pt UI font on an ordinary 96-DPI screen

# Connection watchdog: every WATCH_INTERVAL seconds, ask the robot to open a channel. If that fails
# WATCH_MISSES times in a row (or the link is already closed), the connection is declared lost.
WATCH_INTERVAL = 5
WATCH_PING_TIMEOUT = 10
WATCH_MISSES = 2
KEEPALIVE_SECONDS = 20


def text_scale(root):
    """How much larger than normal text renders here (1.0 or more). Drawings scale by this to match."""
    line_height = tkfont.Font(root=root, font=FONT_BASE).metrics("linespace")
    return max(1.0, line_height / NORMAL_LINE_HEIGHT)


def initial_geometry(root):
    """A window size that suits how large text really renders here, without overflowing the screen."""
    scale = text_scale(root)
    width = min(int(BASE_WIDTH * scale), root.winfo_screenwidth() - 100)
    height = min(int(BASE_HEIGHT * scale), root.winfo_screenheight() - 140)
    return f"{max(width, 640)}x{max(height, 480)}"


class GoPiGoApp(
    DashboardTabMixin,
    ManualTabMixin,
    DriveTabMixin,
    SensorsMixin,
    LightsTabMixin,
    JupyterCameraTabMixin,
    WifiTabMixin,
    EducationTabMixin,
):
    def __init__(self, root):
        self.root = root
        self.root.title("GoPiGo Control Center")
        self.root.geometry(initial_geometry(root))
        init_style(root)
        self.ui_scale = text_scale(root)

        self.ssh_client = None
        self.ssh_password = ""  # the password used to connect; the Wi-Fi tab gives it to sudo
        self.settings = user_settings.load()  # controller buttons, speed, light states

        # Controller-drive state (Dashboard tab)
        self.motor_channel = None
        self.controller_active = False

        # JupyterLab launch state
        self.jupyter_channel = None
        self.jupyter_url = None
        self.jupyter_stopping = False  # True once the user asked to stop, so an exit isn't reported as a failure

        # Camera streaming state
        self.camera_channel = None
        self.camera_url = None
        self.camera_stopping = False
        self.last_photo_path = None

        if pygame is not None:
            try:
                pygame.init()  # needed even headless -- event.pump() requires the display subsystem
            except Exception:
                pass

        self._build_connection_bar()
        self._build_tabs()
        self._build_status_bar()

    # ---------------- Connection bar (shared across all tabs) ----------------
    def _build_connection_bar(self):
        frame = tk.Frame(self.root, padx=14, pady=10, bg=BAR_BG)
        frame.pack(fill="x")

        tk.Label(frame, text="GoPiGo Control Center", bg=BAR_BG, fg="#ffffff", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", padx=(0, 24)
        )

        entry_opts = dict(bg="#2b3543", fg="#ffffff", insertbackground="#ffffff", relief="flat", bd=0)

        tk.Label(frame, text="Host", bg=BAR_BG, fg="#9aa5b4", font=("Segoe UI", 9)).grid(
            row=0, column=1, sticky="e", padx=(0, 4)
        )
        self.host_var = tk.StringVar(value="robot1.local")
        tk.Entry(frame, textvariable=self.host_var, width=18, **entry_opts).grid(
            row=0, column=2, padx=(0, 12), ipady=3
        )

        tk.Label(frame, text="User", bg=BAR_BG, fg="#9aa5b4", font=("Segoe UI", 9)).grid(
            row=0, column=3, sticky="e", padx=(0, 4)
        )
        self.user_var = tk.StringVar(value="pi")
        tk.Entry(frame, textvariable=self.user_var, width=8, **entry_opts).grid(
            row=0, column=4, padx=(0, 12), ipady=3
        )

        tk.Label(frame, text="Password", bg=BAR_BG, fg="#9aa5b4", font=("Segoe UI", 9)).grid(
            row=0, column=5, sticky="e", padx=(0, 4)
        )
        self.pass_var = tk.StringVar()
        tk.Entry(frame, textvariable=self.pass_var, show="*", width=12, **entry_opts).grid(
            row=0, column=6, padx=(0, 14), ipady=3
        )

        self.connect_btn = style_button(
            tk.Button(frame, text="Connect", command=self.toggle_connection), "success"
        )
        self.connect_btn.grid(row=0, column=7, padx=(0, 12))

        self.conn_status_var = tk.StringVar(value="Not connected")
        self.conn_status_label = tk.Label(
            frame, textvariable=self.conn_status_var, bg=BAR_BG, fg="#e0473b", font=("Segoe UI", 9, "bold")
        )
        self.conn_status_label.grid(row=0, column=8, padx=6)

    def toggle_connection(self):
        if self.ssh_client is not None:
            self._disconnect()
            return

        if paramiko is None:
            messagebox.showerror(
                "Missing dependency",
                "paramiko is not installed.\n\nInstall it with:\n"
                "pip install paramiko",
            )
            return

        host = self.host_var.get().strip()
        user = self.user_var.get().strip()
        password = self.pass_var.get()
        if not host or not user:
            messagebox.showerror("Missing info", "Host and username are required.")
            return

        self.conn_status_var.set("Connecting...")
        self.root.update_idletasks()

        def worker():
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(host, username=user, password=password, timeout=10)
                client.get_transport().set_keepalive(KEEPALIVE_SECONDS)  # also keeps Wi-Fi routers from dropping an idle link
                self.ssh_password = password
                self.ssh_client = client
                self.root.after(0, self._on_connected)
            except Exception as e:
                error = str(e)  # `e` is deleted when this block ends, before the lambda runs
                self.root.after(0, lambda: self._on_connect_failed(error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_connected(self):
        self.conn_status_var.set("Connected")
        self.conn_status_label.config(fg="#3ddc84")
        self.connect_btn.config(text="Disconnect")
        style_button(self.connect_btn, "danger")
        self._set_status("Connected to robot.")
        self._check_robot_boot()
        self._start_connection_watch()

    def _on_connect_failed(self, err):
        self.conn_status_var.set("Not connected")
        self.conn_status_label.config(fg="#e0473b")
        messagebox.showerror("Connection error", err)
        self._set_status(f"Connection failed: {err}")

    def _start_connection_watch(self):
        """Notice when the link to the robot dies or stalls, and say so.

        Without this, a dropped connection leaves the top bar saying "Connected" while every button
        quietly fails, and nothing tells you to reconnect.
        """
        client = self.ssh_client

        def watch():
            misses = 0
            while self.ssh_client is client and client is not None:
                time.sleep(WATCH_INTERVAL)
                if self.ssh_client is not client:
                    return
                transport = client.get_transport()
                alive = transport is not None and transport.is_active()
                if alive:
                    try:
                        transport.open_session(timeout=WATCH_PING_TIMEOUT).close()  # a real round trip to the robot
                        misses = 0
                    except Exception:
                        misses += 1
                        alive = misses < WATCH_MISSES
                if not alive:
                    self.root.after(0, lambda: self._connection_lost(client))
                    return

        threading.Thread(target=watch, daemon=True).start()

    def _connection_lost(self, client):
        if self.ssh_client is not client:  # already disconnected some other way
            return
        self._disconnect()
        self.conn_status_var.set("Connection lost")
        self._set_status("Lost the connection to the robot. Click Connect to reconnect.")
        messagebox.showwarning(
            "Connection lost",
            "The connection to the robot was lost. It may have gone out of range or rebooted, or its Wi-Fi "
            "may be overloaded (a live camera stream uses a lot of bandwidth).\n\nClick Connect to reconnect.",
        )

    def _disconnect(self):
        self.jupyter_stopping = True  # closing these channels below is on purpose, not a failure to report
        self.camera_stopping = True
        self.controller_active = False
        try:
            if self.motor_channel:
                self.motor_channel.close()
        except Exception:
            pass
        self.motor_channel = None
        try:
            if self.jupyter_channel:
                self.jupyter_channel.close()
        except Exception:
            pass
        self.jupyter_channel = None
        self.jupyter_url = None
        try:
            if self.camera_channel:
                self.camera_channel.close()
        except Exception:
            pass
        self.camera_channel = None
        self.camera_url = None
        try:
            if self.ssh_client:
                self.ssh_client.close()
        except Exception:
            pass
        self.ssh_client = None
        self.ssh_password = ""
        self.conn_status_var.set("Not connected")
        self.conn_status_label.config(fg="#e0473b")
        self.connect_btn.config(text="Connect")
        style_button(self.connect_btn, "success")
        # Put every start/stop button back to "Start", so nothing reads "Stop" for a service that's gone.
        self._on_jupyter_stopped()
        self._on_camera_stopped()
        self._dashboard_on_disconnect()
        self._set_status("Disconnected.")

    def _require_connection(self):
        if self.ssh_client is None:
            messagebox.showwarning("Not connected", "Click Connect first.")
            return False
        return True

    # ---------------- Tabs ----------------
    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=8)

        self.dashboard_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.manual_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.jupyter_camera_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.wifi_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.education_tab = ttk.Frame(self.notebook, style="Card.TFrame")

        self.notebook.add(self.dashboard_tab, text="Dashboard")
        self.notebook.add(self.manual_tab, text="Manual Control")
        self.notebook.add(self.jupyter_camera_tab, text="Jupyter & Camera")
        self.notebook.add(self.wifi_tab, text="Wi-Fi")
        self.notebook.add(self.education_tab, text="Modules")

        self._build_manual_tab()
        self._build_jupyter_camera_tab()
        self._build_wifi_tab()
        self._build_education_tab()
        self._build_dashboard_tab()  # last: it shows the camera status the Jupyter & Camera tab keeps

    # ---------------- Status bar ----------------
    def _build_status_bar(self):
        self.status_var = tk.StringVar(value="Ready. Fill in connection details and click Connect.")
        status = tk.Label(
            self.root, textvariable=self.status_var, anchor="w", bg=BAR_BG, fg="#3ddc84",
            font=("Consolas", 9), padx=10, pady=4,
        )
        status.pack(fill="x", side="bottom")

    def _set_status(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

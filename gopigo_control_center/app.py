"""GoPiGoApp -- combines every tab mixin into the main Tkinter application,
plus the connection bar and status bar shared by all of them.
"""

import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from constants import DEFAULT_TEMPLATE
from dependencies import paramiko, pygame
from tabs import (
    BlocksTabMixin,
    CodeTabMixin,
    ControllerTabMixin,
    DriveTabMixin,
    FilesTabMixin,
    JupyterCameraTabMixin,
    NotebookTabMixin,
    SensorTabMixin,
    WifiTabMixin,
)
from theme import BAR_BG, FONT_BASE, init_style, style_button

# Window size for text of normal size. Some setups (for example Tk on XWayland) render text about twice
# as large as requested, which would clip most tabs at a fixed size, so the window grows with the text.
BASE_WIDTH, BASE_HEIGHT = 1040, 780
NORMAL_LINE_HEIGHT = 18  # px: a 10pt UI font on an ordinary 96-DPI screen


def initial_geometry(root):
    """A window size that suits how large text really renders here, without overflowing the screen."""
    line_height = tkfont.Font(root=root, font=FONT_BASE).metrics("linespace")
    scale = max(1.0, line_height / NORMAL_LINE_HEIGHT)
    width = min(int(BASE_WIDTH * scale), root.winfo_screenwidth() - 100)
    height = min(int(BASE_HEIGHT * scale), root.winfo_screenheight() - 140)
    return f"{max(width, 640)}x{max(height, 480)}"


class GoPiGoApp(
    CodeTabMixin,
    NotebookTabMixin,
    BlocksTabMixin,
    DriveTabMixin,
    ControllerTabMixin,
    SensorTabMixin,
    FilesTabMixin,
    JupyterCameraTabMixin,
    WifiTabMixin,
):
    def __init__(self, root):
        self.root = root
        self.root.title("GoPiGo Control Center")
        self.root.geometry(initial_geometry(root))
        init_style(root)

        self.ssh_client = None
        self.current_filepath = None
        self.sensor_polling = False
        self.run_channel = None  # active channel for a running whole-script

        # Notebook-cell kernel state
        self.kernel_channel = None
        self.kernel_lock = threading.Lock()
        self.cells = []
        self.current_cell = None

        # Block Builder: local web server, started the first time the tab's button is clicked
        self.block_server = None

        # Controller-drive state
        self.motor_channel = None
        self.controller_active = False
        self.joystick = None

        # JupyterLab launch state
        self.jupyter_channel = None
        self.jupyter_url = None

        # Camera streaming state
        self.camera_channel = None
        self.camera_url = None
        self.last_photo_path = None

        if pygame is not None:
            try:
                pygame.init()  # needed even headless -- event.pump() requires the display subsystem
            except Exception:
                pass

        self._build_connection_bar()
        self._build_tabs()
        self._build_status_bar()

        self.editor.insert("1.0", DEFAULT_TEMPLATE)

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
        self.refresh_file_list()

    def _on_connect_failed(self, err):
        self.conn_status_var.set("Not connected")
        self.conn_status_label.config(fg="#e0473b")
        messagebox.showerror("Connection error", err)
        self._set_status(f"Connection failed: {err}")

    def _disconnect(self):
        self.sensor_polling = False
        self.controller_active = False
        try:
            if self.motor_channel:
                self.motor_channel.close()
        except Exception:
            pass
        self.motor_channel = None
        try:
            if self.kernel_channel:
                self.kernel_channel.close()
        except Exception:
            pass
        self.kernel_channel = None
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
        self.conn_status_var.set("Not connected")
        self.conn_status_label.config(fg="#e0473b")
        self.connect_btn.config(text="Connect")
        style_button(self.connect_btn, "success")
        if hasattr(self, "kernel_status_var"):
            self.kernel_status_var.set("Kernel: not running")
        if hasattr(self, "jupyter_status_var"):
            self.jupyter_status_var.set("Jupyter: not running")
        if hasattr(self, "camera_status_var"):
            self.camera_status_var.set("Camera stream: not running")
        if hasattr(self, "battery_meter"):
            self.battery_meter.set_voltage(None)
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

        self.code_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.notebook_cells_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.blocks_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.drive_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.controller_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.sensor_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.files_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.jupyter_camera_tab = ttk.Frame(self.notebook, style="Card.TFrame")
        self.wifi_tab = ttk.Frame(self.notebook, style="Card.TFrame")

        self.notebook.add(self.code_tab, text="Code Editor & Run")
        self.notebook.add(self.notebook_cells_tab, text="Notebook Cells")
        self.notebook.add(self.blocks_tab, text="Block Builder")
        self.notebook.add(self.drive_tab, text="Manual Drive")
        self.notebook.add(self.controller_tab, text="Controller")
        self.notebook.add(self.sensor_tab, text="Live Sensors")
        self.notebook.add(self.files_tab, text="Remote Files")
        self.notebook.add(self.jupyter_camera_tab, text="Jupyter & Camera")
        self.notebook.add(self.wifi_tab, text="Wi-Fi")

        self._build_code_tab()
        self._build_notebook_cells_tab()
        self._build_blocks_tab()
        self._build_drive_tab()
        self._build_controller_tab()
        self._build_sensor_tab()
        self._build_files_tab()
        self._build_jupyter_camera_tab()
        self._build_wifi_tab()

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

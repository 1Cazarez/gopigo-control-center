"""Jupyter & Camera tab -- launch JupyterLab on the robot, watch a live
camera stream, and grab single photo snapshots.
"""

import os
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk

from constants import VENV_ACTIVATE, login_shell
from remote_scripts import (
    CAMERA_STREAM_PORT,
    CAMERA_STREAM_SCRIPT,
    JUPYTER_PORT,
    JUPYTER_TOKEN_RE,
    REMOTE_CAMERA_STREAM_PATH,
    REMOTE_KERNEL_DIR,
)
from theme import divider, style_button


class JupyterCameraTabMixin:
    def _build_jupyter_camera_tab(self):
        outer = ttk.Frame(self.jupyter_camera_tab, style="Card.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        # -- JupyterLab section --
        ttk.Label(outer, text="JupyterLab", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Starts jupyter lab on the robot and builds a link using the host above.",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(2, 8))

        jupyter_row = ttk.Frame(outer, style="Card.TFrame")
        jupyter_row.pack(fill="x", pady=4)
        self.jupyter_toggle_btn = style_button(
            tk.Button(jupyter_row, text="Start Jupyter Lab", command=self.toggle_jupyter), "success"
        )
        self.jupyter_toggle_btn.pack(side="left")
        self.jupyter_open_btn = style_button(
            tk.Button(jupyter_row, text="Open in Browser", command=self.open_jupyter_in_browser, state="disabled"),
            "neutral",
        )
        self.jupyter_open_btn.pack(side="left", padx=8)

        self.jupyter_status_var = tk.StringVar(value="Jupyter: not running")
        ttk.Label(
            outer, textvariable=self.jupyter_status_var, style="CardMuted.TLabel", wraplength=650, justify="left"
        ).pack(anchor="w", pady=(6, 2))

        self.jupyter_url_var = tk.StringVar(value="")
        ttk.Entry(outer, textvariable=self.jupyter_url_var, width=70).pack(anchor="w", pady=(0, 16), fill="x")

        divider(outer).pack(fill="x", pady=8)

        # -- Camera streaming section --
        ttk.Label(outer, text="Live Camera Stream", style="Heading.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(
            outer,
            text="Runs a small MJPEG video server on the robot (needs the camera module).",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(2, 8))

        camera_row = ttk.Frame(outer, style="Card.TFrame")
        camera_row.pack(fill="x", pady=4)
        self.camera_toggle_btn = style_button(
            tk.Button(camera_row, text="Start Camera Stream", command=self.toggle_camera_stream), "success"
        )
        self.camera_toggle_btn.pack(side="left")
        self.camera_open_btn = style_button(
            tk.Button(camera_row, text="Open in Browser", command=self.open_camera_in_browser, state="disabled"),
            "neutral",
        )
        self.camera_open_btn.pack(side="left", padx=8)

        self.camera_status_var = tk.StringVar(value="Camera stream: not running")
        ttk.Label(outer, textvariable=self.camera_status_var, style="CardMuted.TLabel").pack(
            anchor="w", pady=(6, 2)
        )

        self.camera_url_var = tk.StringVar(value="")
        ttk.Entry(outer, textvariable=self.camera_url_var, width=70).pack(anchor="w", pady=(0, 16), fill="x")

        divider(outer).pack(fill="x", pady=8)

        # -- Single snapshot section --
        ttk.Label(outer, text="Camera Snapshot", style="Heading.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(
            outer,
            text="Grabs one still photo and downloads it to your laptop. Stop the live stream first --"
            " only one program can use the camera at a time.",
            style="CardMuted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, 8))

        photo_row = ttk.Frame(outer, style="Card.TFrame")
        photo_row.pack(fill="x", pady=4)
        self.photo_btn = style_button(tk.Button(photo_row, text="Take Photo", command=self.take_photo), "success")
        self.photo_btn.pack(side="left")
        self.photo_open_btn = style_button(
            tk.Button(photo_row, text="Open Last Photo", command=self.open_last_photo, state="disabled"), "neutral"
        )
        self.photo_open_btn.pack(side="left", padx=8)

        self.photo_status_var = tk.StringVar(value="No photo taken yet.")
        ttk.Label(
            outer, textvariable=self.photo_status_var, style="CardMuted.TLabel", wraplength=650, justify="left"
        ).pack(anchor="w", pady=(6, 2))

    # -- JupyterLab --
    def toggle_jupyter(self):
        if self.jupyter_channel is not None:
            self.stop_jupyter()
            return
        if not self._require_connection():
            return

        self.jupyter_status_var.set("Jupyter: starting...")
        self.jupyter_toggle_btn.config(state="disabled")
        host = self.host_var.get().strip()

        def worker():
            try:
                channel = self.ssh_client.get_transport().open_session()
                # Jupyter logs its startup lines -- including the token URL --
                # to stderr, not stdout. Combine the two so recv() sees it.
                channel.set_combine_stderr(True)
                command = (
                    f"{VENV_ACTIVATE} && jupyter lab --no-browser --ip 0.0.0.0 --port {JUPYTER_PORT}"
                )
                channel.exec_command(login_shell(command))
                self.jupyter_channel = channel

                self.root.after(
                    0, lambda: self.jupyter_toggle_btn.config(text="Stop Jupyter Lab", bg="#e0473b", state="normal")
                )
                threading.Thread(target=self._jupyter_reader_loop, args=(host,), daemon=True).start()
            except Exception as e:
                self.jupyter_channel = None
                self.root.after(0, lambda: self.jupyter_status_var.set(f"Error: {e}"))
                self.root.after(0, lambda: self.jupyter_toggle_btn.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _jupyter_reader_loop(self, host):
        buffer = ""
        channel = self.jupyter_channel
        found_token = False
        started = time.time()
        warned_slow = False
        while self.jupyter_channel is channel and channel is not None:
            try:
                if channel.recv_ready():
                    data = channel.recv(4096).decode(errors="replace")
                    buffer += data
                    if not found_token:
                        # Search the whole buffer, not just this chunk --
                        # the token line can be split across two reads.
                        match = JUPYTER_TOKEN_RE.search(buffer)
                        if match:
                            found_token = True
                            token = match.group(1)
                            url = f"http://{host}:{JUPYTER_PORT}/lab?token={token}"
                            self.jupyter_url = url
                            self.root.after(0, lambda: self._on_jupyter_ready(url))
                elif channel.exit_status_ready():
                    self.root.after(0, self._on_jupyter_stopped)
                    break
                else:
                    time.sleep(0.1)

                if not found_token and not warned_slow and time.time() - started > 15:
                    warned_slow = True
                    tail = buffer.strip()[-300:] or "(no output yet -- is 'jupyter' installed in the venv?)"
                    self.root.after(
                        0, lambda t=tail: self.jupyter_status_var.set(f"Jupyter: still starting... last output: {t}")
                    )
            except Exception:
                self.root.after(0, self._on_jupyter_stopped)
                break

    def _on_jupyter_ready(self, url):
        self.jupyter_status_var.set("Jupyter: running")
        self.jupyter_url_var.set(url)
        self.jupyter_open_btn.config(state="normal")
        self._set_status("Jupyter Lab is up.")

    def _on_jupyter_stopped(self):
        self.jupyter_channel = None
        self.jupyter_url = None
        self.jupyter_status_var.set("Jupyter: not running")
        self.jupyter_url_var.set("")
        self.jupyter_open_btn.config(state="disabled")
        self.jupyter_toggle_btn.config(text="Start Jupyter Lab", bg="#2ea36f", state="normal")

    def stop_jupyter(self):
        def worker():
            try:
                if self.jupyter_channel:
                    self.jupyter_channel.close()
            except Exception:
                pass
            try:
                self.ssh_client.exec_command("pkill -f 'jupyter-lab'")
            except Exception:
                pass
            self.root.after(0, self._on_jupyter_stopped)
            self.root.after(0, lambda: self._set_status("Jupyter stopped."))

        threading.Thread(target=worker, daemon=True).start()

    def open_jupyter_in_browser(self):
        if self.jupyter_url:
            webbrowser.open(self.jupyter_url)

    # -- Camera streaming --
    def toggle_camera_stream(self):
        if self.camera_channel is not None:
            self.stop_camera_stream()
            return
        if not self._require_connection():
            return

        self.camera_status_var.set("Camera stream: starting...")
        self.camera_toggle_btn.config(state="disabled")
        host = self.host_var.get().strip()

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass
                with sftp.file(REMOTE_CAMERA_STREAM_PATH, "w") as f:
                    f.write(CAMERA_STREAM_SCRIPT)
                sftp.close()

                channel = self.ssh_client.get_transport().open_session()
                command = f"{VENV_ACTIVATE} && python3 -u {REMOTE_CAMERA_STREAM_PATH}"
                channel.exec_command(login_shell(command))
                self.camera_channel = channel

                url = f"http://{host}:{CAMERA_STREAM_PORT}/"
                self.camera_url = url

                threading.Thread(target=self._camera_reader_loop, args=(url,), daemon=True).start()
                self.root.after(
                    0, lambda: self.camera_toggle_btn.config(text="Stop Camera Stream", bg="#e0473b", state="normal")
                )
            except Exception as e:
                self.camera_channel = None
                self.root.after(0, lambda: self.camera_status_var.set(f"Error: {e}"))
                self.root.after(0, lambda: self.camera_toggle_btn.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _camera_reader_loop(self, url):
        buffer = ""
        channel = self.camera_channel
        found_ready = False
        while self.camera_channel is channel and channel is not None:
            try:
                if channel.recv_ready():
                    data = channel.recv(4096).decode(errors="replace")
                    buffer += data
                    if not found_ready and "CAMERA_STREAM_READY" in buffer:
                        found_ready = True
                        self.root.after(0, lambda: self._on_camera_ready(url))
                elif channel.exit_status_ready():
                    self.root.after(0, self._on_camera_stopped)
                    break
                else:
                    time.sleep(0.1)
            except Exception:
                self.root.after(0, self._on_camera_stopped)
                break

    def _on_camera_ready(self, url):
        self.camera_status_var.set("Camera stream: running")
        self.camera_url_var.set(url)
        self.camera_open_btn.config(state="normal")
        self._set_status("Camera stream is up.")

    def _on_camera_stopped(self):
        self.camera_channel = None
        self.camera_url = None
        self.camera_status_var.set("Camera stream: not running")
        self.camera_url_var.set("")
        self.camera_open_btn.config(state="disabled")
        self.camera_toggle_btn.config(text="Start Camera Stream", bg="#2ea36f", state="normal")

    def stop_camera_stream(self):
        def worker():
            try:
                if self.camera_channel:
                    self.camera_channel.close()
            except Exception:
                pass
            try:
                self.ssh_client.exec_command(f"pkill -f {os.path.basename(REMOTE_CAMERA_STREAM_PATH)}")
            except Exception:
                pass
            self.root.after(0, self._on_camera_stopped)
            self.root.after(0, lambda: self._set_status("Camera stream stopped."))

        threading.Thread(target=worker, daemon=True).start()

    def open_camera_in_browser(self):
        if self.camera_url:
            webbrowser.open(self.camera_url)

    # -- Single photo snapshot --
    def take_photo(self):
        if not self._require_connection():
            return
        self.photo_status_var.set("Taking photo...")
        self.photo_btn.config(state="disabled")

        def worker():
            remote_path = f"{REMOTE_KERNEL_DIR}/last_photo.jpg"
            snippet = (
                "from picamera2 import Picamera2; import time; "
                "cam = Picamera2(); cam.configure(cam.create_still_configuration()); "
                "cam.start(); time.sleep(1); "
                f"cam.capture_file('{remote_path}'); cam.stop()"
            )
            command = f"{VENV_ACTIVATE} && python3 -c \"{snippet}\""
            try:
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass
                sftp.close()

                _, stdout, stderr = self.ssh_client.exec_command(login_shell(command), timeout=20)
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    err = stderr.read().decode(errors="replace").strip()
                    raise RuntimeError(err or "camera capture failed -- is the live stream still running?")

                local_dir = os.path.join(os.getcwd(), "gopigo_photos")
                os.makedirs(local_dir, exist_ok=True)
                filename = f"photo_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                local_path = os.path.join(local_dir, filename)

                sftp = self.ssh_client.open_sftp()
                sftp.get(remote_path, local_path)
                sftp.close()

                self.last_photo_path = local_path
                self.root.after(0, lambda: self._on_photo_ready(local_path))
            except Exception as e:
                self.root.after(0, lambda: self._on_photo_failed(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_photo_ready(self, path):
        self.photo_status_var.set(f"Saved: {path}")
        self.photo_btn.config(state="normal")
        self.photo_open_btn.config(state="normal")
        self._set_status("Photo captured.")

    def _on_photo_failed(self, err):
        self.photo_status_var.set(f"Error: {err}")
        self.photo_btn.config(state="normal")

    def open_last_photo(self):
        if self.last_photo_path and os.path.exists(self.last_photo_path):
            self._open_local_file(self.last_photo_path)

    def _open_local_file(self, path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: this only exists on Windows
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            webbrowser.open("file://" + path)

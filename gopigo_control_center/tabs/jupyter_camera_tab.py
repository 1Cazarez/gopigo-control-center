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
from tkinter import messagebox, ttk

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

# How long to wait for the robot to open a channel before giving up. Without a limit, a stalled
# connection would leave the Start button disabled forever.
OPEN_TIMEOUT = 15

# Stops any camera stream still running on the robot. The [c] keeps pkill from matching this command's
# own shell, whose command line contains the pattern too.
KILL_CAMERA_STREAMS = f"pkill -f '[{os.path.basename(REMOTE_CAMERA_STREAM_PATH)[0]}]{os.path.basename(REMOTE_CAMERA_STREAM_PATH)[1:]}'"


# Lists other programs of this user that have the camera open (libcamera holds /dev/media*), as "pid command".
FIND_CAMERA_USERS = (
    "for p in /proc/[0-9]*; do "
    "ls -l $p/fd 2>/dev/null | grep -q '/dev/media' && echo \"${p#/proc/} $(tr '\\0' ' ' < $p/cmdline)\"; "
    "done; true"
)


def camera_failure_reason(output):
    """A plain-language reason the camera failed, from everything the robot printed."""
    lowered = output.lower()
    if "busy" in lowered or "failed to acquire camera" in lowered or "in use" in lowered:
        return ("The camera is busy: another program on the robot is using it -- perhaps Take Photo, "
                "JupyterLab or a script of yours. Stop that program and try again.")
    if "list index out of range" in lowered or "no cameras" in lowered:
        return ("No camera was found. Check that the ribbon cable is pushed in firmly at both ends, with "
                "the contacts facing the right way, then reboot the robot.")
    if "no module named" in lowered and "picamera2" in lowered:
        return "picamera2 isn't installed where the robot's ~/.venv/gopigo3 can see it."
    if "address already in use" in lowered:
        return "Port 8000 is already in use on the robot, so the stream can't start there."
    lines = output.strip().splitlines()
    return lines[-1] if lines else "No output -- is picamera2 installed, and is the camera connected?"


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
        style_button(tk.Button(camera_row, text="Reset Camera", command=self.reset_camera), "warning").pack(side="left")

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
        self.jupyter_stopping = False
        host = self.host_var.get().strip()

        def worker():
            try:
                channel = self.ssh_client.get_transport().open_session(timeout=OPEN_TIMEOUT)
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
                error = str(e)
                self.jupyter_channel = None
                self.root.after(0, lambda: self.jupyter_status_var.set(f"Error: {error}"))
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
                    if self.jupyter_stopping:
                        self.root.after(0, self._on_jupyter_stopped)
                    else:  # it ended without us asking: tell the user why
                        exit_code = channel.recv_exit_status()
                        output = "\n".join(buffer.strip().splitlines()[-8:])  # the last few whole lines
                        self.root.after(0, lambda: self._on_jupyter_died(exit_code, output, found_token))
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

    def _on_jupyter_died(self, exit_code, output, was_running):
        self._on_jupyter_stopped()
        what = "JupyterLab stopped on its own." if was_running else "JupyterLab didn't start."
        code = f" (exit code {exit_code})" if exit_code >= 0 else ""
        detail = output or "No output -- is jupyter installed in the robot's ~/.venv/gopigo3?"
        self.jupyter_status_var.set("Jupyter: stopped unexpectedly" if was_running else "Jupyter: failed to start")
        messagebox.showerror("JupyterLab", f"{what}{code}\n\nLast output from the robot:\n{detail}")

    def _on_jupyter_stopped(self):
        self.jupyter_channel = None
        self.jupyter_url = None
        self.jupyter_status_var.set("Jupyter: not running")
        self.jupyter_url_var.set("")
        self.jupyter_open_btn.config(state="disabled")
        self.jupyter_toggle_btn.config(text="Start Jupyter Lab", bg="#2ea36f", state="normal")

    def stop_jupyter(self):
        self.jupyter_stopping = True  # so the reader loop knows this exit was requested

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
    def _camera_used_by_autopilot(self):
        if self.nav_channel is None:
            return False
        messagebox.showinfo("Camera", "The autopilot is using the camera; its live picture is on the Autopilot tab.")
        return True

    def toggle_camera_stream(self):
        if self.camera_channel is not None:
            self.stop_camera_stream()
            return
        if not self._require_connection() or self._camera_used_by_autopilot():
            return

        self.camera_status_var.set("Camera stream: starting...")
        self.camera_toggle_btn.config(state="disabled")
        self.camera_stopping = False
        host = self.host_var.get().strip()

        def worker():
            try:
                # A stream from an earlier session (the connection dropped, or the app was closed) keeps
                # running on the robot and holding the camera, so stop it first.
                self._stop_stray_camera_streams()
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass
                with sftp.file(REMOTE_CAMERA_STREAM_PATH, "w") as f:
                    f.write(CAMERA_STREAM_SCRIPT)
                sftp.close()

                channel = self.ssh_client.get_transport().open_session(timeout=OPEN_TIMEOUT)
                # Errors (a missing camera, a busy camera) go to stderr: combine it so we can show them.
                channel.set_combine_stderr(True)
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
                error = str(e)
                self.camera_channel = None
                self.root.after(0, lambda: self.camera_status_var.set(f"Error: {error}"))
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
                    if self.camera_stopping:
                        self.root.after(0, self._on_camera_stopped)
                    else:  # it ended without us asking: tell the user why
                        exit_code = channel.recv_exit_status()
                        output = buffer.replace("CAMERA_STREAM_READY", "")
                        self.root.after(0, lambda: self._on_camera_died(exit_code, output, found_ready))
                    break
                else:
                    time.sleep(0.1)
            except Exception:
                self.root.after(0, self._on_camera_stopped)
                break

    def _on_camera_died(self, exit_code, output, was_running):
        self._on_camera_stopped()
        what = "The camera stream stopped on its own." if was_running else "The camera stream didn't start."
        code = f" (exit code {exit_code})" if exit_code >= 0 else ""
        # The reason comes first: a long traceback would push it off the bottom of the dialog.
        message = f"{what}{code}\n\n{camera_failure_reason(output)}"
        last_line = output.strip().splitlines()[-1:] if output.strip() else []
        if last_line and last_line[0] not in message:
            message += f"\n\nThe robot's last message:\n{last_line[0]}"
        self.camera_status_var.set("Camera stream: stopped unexpectedly" if was_running else "Camera stream: failed to start")
        messagebox.showerror("Camera stream", message)

    def _on_camera_ready(self, url):
        self.camera_status_var.set("Camera stream: running")
        self._dashboard_camera_ready(url)
        self.camera_url_var.set(url)
        self.camera_open_btn.config(state="normal")
        self._set_status("Camera stream is up.")

    def _on_camera_stopped(self):
        self._dashboard_camera_stopped()
        self.camera_channel = None
        self.camera_url = None
        self.camera_status_var.set("Camera stream: not running")
        self.camera_url_var.set("")
        self.camera_open_btn.config(state="disabled")
        self.camera_toggle_btn.config(text="Start Camera Stream", bg="#2ea36f", state="normal")

    def stop_camera_stream(self):
        self.camera_stopping = True  # so the reader loop knows this exit was requested

        def worker():
            try:
                if self.camera_channel:
                    self.camera_channel.close()
            except Exception:
                pass
            try:
                self._stop_stray_camera_streams()
            except Exception:
                pass
            self.root.after(0, self._on_camera_stopped)
            self.root.after(0, lambda: self._set_status("Camera stream stopped."))

        threading.Thread(target=worker, daemon=True).start()

    def reset_camera(self):
        """Free the camera: stop the app's camera stream and any camera program the app left running on the
        robot (a stream from an earlier session, or a stuck Take Photo), then say if anything else has it."""
        if not self._require_connection():
            return
        self.camera_stopping = True
        self.camera_status_var.set("Camera stream: resetting...")

        def worker():
            try:
                if self.camera_channel is not None:
                    self.camera_channel.close()
                self._stop_stray_camera_streams()
                _, stdout, _ = self.ssh_client.exec_command(
                    f"pkill -f '[l]ast_photo.jpg'; {FIND_CAMERA_USERS}", timeout=OPEN_TIMEOUT
                )
                users = stdout.read().decode(errors="replace").strip()
                error = None
            except Exception as e:
                users, error = "", str(e)
            self.root.after(0, lambda: self._on_camera_reset(users, error))

        threading.Thread(target=worker, daemon=True).start()

    def _on_camera_reset(self, users, error):
        self._on_camera_stopped()
        if error:
            messagebox.showerror("Reset Camera", f"Couldn't reset the camera: {error}")
        elif users:
            messagebox.showwarning(
                "Reset Camera",
                "The app's camera programs are stopped, but these programs on the robot still have the camera "
                f"open, so it may stay busy until they finish or you stop them:\n\n{users}",
            )
        else:
            self._set_status("Camera reset: nothing on the robot is using it now.")
            messagebox.showinfo("Reset Camera", "The camera is free: nothing on the robot is using it now.")

    def _stop_stray_camera_streams(self):
        """Stop any camera stream running on the robot, waiting until the camera is free again."""
        _, stdout, _ = self.ssh_client.exec_command(f"{KILL_CAMERA_STREAMS} && sleep 1", timeout=OPEN_TIMEOUT)
        stdout.channel.recv_exit_status()

    def open_camera_in_browser(self):
        if self.camera_url:
            webbrowser.open(self.camera_url)

    # -- Single photo snapshot --
    def take_photo(self):
        if not self._require_connection() or self._camera_used_by_autopilot():
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
                if self.camera_channel is None:
                    self._stop_stray_camera_streams()  # one left from an earlier session would hold the camera
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
                    reason = camera_failure_reason(err) if err else "camera capture failed"
                    if self.camera_channel is not None:
                        reason += " -- the live stream is using the camera, so stop it first"
                    raise RuntimeError(reason)

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
                error = str(e)
                self.root.after(0, lambda: self._on_photo_failed(error))

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

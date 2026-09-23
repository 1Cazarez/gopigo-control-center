"""Code Editor & Run tab -- write a script, send it to the robot, run it,
and watch live console output stream back.
"""

import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from constants import DEFAULT_REMOTE_SCRIPT, VENV_ACTIVATE, login_shell
from theme import CARD_BG, FONT_MONO_BASE, FONT_MONO_SMALL, style_button


class CodeTabMixin:
    """Mixed into GoPiGoApp. Expects self.code_tab, self.ssh_client, etc."""

    def _build_code_tab(self):
        outer = ttk.Frame(self.code_tab, style="Card.TFrame", padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(top, text="Remote path:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        self.remote_path_var = tk.StringVar(value=DEFAULT_REMOTE_SCRIPT)
        ttk.Entry(top, textvariable=self.remote_path_var, width=30).pack(side="left", padx=(0, 10))

        style_button(tk.Button(top, text="New", command=self.new_file), "neutral").pack(side="left", padx=3)
        style_button(tk.Button(top, text="Open Local...", command=self.open_file), "neutral").pack(
            side="left", padx=3
        )
        style_button(tk.Button(top, text="Save Local", command=self.save_file), "neutral").pack(side="left", padx=3)
        style_button(tk.Button(top, text="Send to Robot", command=self.send_file), "primary").pack(
            side="left", padx=(10, 3)
        )
        style_button(tk.Button(top, text="Send & Run", command=self.send_and_run), "success").pack(
            side="left", padx=3
        )
        style_button(tk.Button(top, text="Stop Script", command=self.stop_remote), "danger").pack(
            side="left", padx=3
        )

        paned = tk.PanedWindow(outer, orient="vertical", sashrelief="flat", sashwidth=6, bg=CARD_BG, bd=0)
        paned.pack(fill="both", expand=True)

        self.editor = scrolledtext.ScrolledText(
            paned, wrap="none", font=FONT_MONO_BASE, undo=True, relief="flat", borderwidth=1,
            highlightthickness=1, highlightbackground="#e2e7ee",
        )
        paned.add(self.editor, height=400)

        console_frame = tk.Frame(paned, bg=CARD_BG)
        ttk.Label(console_frame, text="Live Output", style="CardMuted.TLabel").pack(fill="x", pady=(4, 2))
        self.console = scrolledtext.ScrolledText(
            console_frame, font=FONT_MONO_SMALL, bg="#12161d", fg="#3ddc84", state="disabled",
            relief="flat", borderwidth=0, insertbackground="#3ddc84",
        )
        self.console.pack(fill="both", expand=True)
        paned.add(console_frame, height=200)

    def new_file(self):
        self.editor.delete("1.0", tk.END)
        self.current_filepath = None
        self._set_status("New file.")

    def open_file(self):
        path = filedialog.askopenfilename(filetypes=[("Python files", "*.py"), ("All files", "*.*")])
        if not path:
            return
        with open(path, "r") as f:
            content = f.read()
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", content)
        self.current_filepath = path
        self._set_status(f"Opened {path}")

    def save_file(self):
        if not self.current_filepath:
            path = filedialog.asksaveasfilename(defaultextension=".py", filetypes=[("Python files", "*.py")])
            if not path:
                return
            self.current_filepath = path
        with open(self.current_filepath, "w") as f:
            f.write(self.editor.get("1.0", tk.END))
        self._set_status(f"Saved {self.current_filepath}")

    def _console_write(self, text, clear=False):
        def do_write():
            self.console.config(state="normal")
            if clear:
                self.console.delete("1.0", tk.END)
            self.console.insert(tk.END, text)
            self.console.see(tk.END)
            self.console.config(state="disabled")

        self.root.after(0, do_write)

    def send_file(self):
        if not self._require_connection():
            return
        remote_path = self.remote_path_var.get().strip()
        code = self.editor.get("1.0", tk.END)

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                with sftp.file(remote_path, "w") as f:
                    f.write(code)
                sftp.close()
                self._set_status(f"Sent to {remote_path}")
            except Exception as e:
                self._set_status(f"Error sending file: {e}")
                messagebox.showerror("Send error", str(e))

        threading.Thread(target=worker, daemon=True).start()

    def send_and_run(self):
        if not self._require_connection():
            return
        remote_path = self.remote_path_var.get().strip()
        code = self.editor.get("1.0", tk.END)
        self._console_write("", clear=True)
        self._set_status("Sending and running...")

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                with sftp.file(remote_path, "w") as f:
                    f.write(code)
                sftp.close()

                command = f"{VENV_ACTIVATE} && python3 -u {remote_path}"
                channel = self.ssh_client.get_transport().open_session()
                channel.get_pty()
                channel.exec_command(login_shell(command))
                self.run_channel = channel

                while True:
                    if channel.recv_ready():
                        data = channel.recv(4096).decode(errors="replace")
                        if data:
                            self._console_write(data)
                    if channel.exit_status_ready():
                        # drain anything left
                        while channel.recv_ready():
                            self._console_write(channel.recv(4096).decode(errors="replace"))
                        break
                    time.sleep(0.1)

                self.run_channel = None
                self._set_status("Run finished.")
            except Exception as e:
                self._set_status(f"Error: {e}")
                self._console_write(f"\n[error] {e}\n")

        threading.Thread(target=worker, daemon=True).start()

    def stop_remote(self):
        if not self._require_connection():
            return
        remote_path = self.remote_path_var.get().strip()
        script_name = os.path.basename(remote_path)

        def worker():
            try:
                self.ssh_client.exec_command(f"pkill -f {script_name}")
                if self.run_channel:
                    try:
                        self.run_channel.close()
                    except Exception:
                        pass
                self._set_status(f"Stop signal sent for {script_name}.")
                self._console_write("\n[stopped by user]\n")
            except Exception as e:
                self._set_status(f"Error: {e}")

        threading.Thread(target=worker, daemon=True).start()

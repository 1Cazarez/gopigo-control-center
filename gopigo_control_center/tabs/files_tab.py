"""Remote Files tab -- browse and open .py files on the robot."""

import threading
import tkinter as tk
from tkinter import ttk

from constants import DEFAULT_REMOTE_DIR
from theme import BORDER, CARD_BG, FONT_MONO_BASE, style_button


class FilesTabMixin:
    def _build_files_tab(self):
        outer = ttk.Frame(self.files_tab, style="Card.TFrame", padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(top, text="Remote directory:", style="Card.TLabel").pack(side="left", padx=(0, 6))
        self.remote_dir_var = tk.StringVar(value=DEFAULT_REMOTE_DIR)
        ttk.Entry(top, textvariable=self.remote_dir_var, width=30).pack(side="left", padx=(0, 8))
        style_button(tk.Button(top, text="Refresh", command=self.refresh_file_list), "neutral").pack(
            side="left", padx=3
        )
        style_button(tk.Button(top, text="Open in Editor", command=self.open_remote_file), "primary").pack(
            side="left", padx=(8, 0)
        )

        self.file_listbox = tk.Listbox(
            outer, font=FONT_MONO_BASE, bg=CARD_BG, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, selectbackground="#3b6fe0", selectforeground="#ffffff",
        )
        self.file_listbox.pack(fill="both", expand=True, pady=(4, 0))
        self.file_listbox.bind("<Double-Button-1>", lambda e: self.open_remote_file())

    def refresh_file_list(self):
        if self.ssh_client is None:
            return
        remote_dir = self.remote_dir_var.get().strip()

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                entries = sorted(sftp.listdir(remote_dir))
                py_files = [f for f in entries if f.endswith(".py")]
                sftp.close()
                self.root.after(0, lambda: self._populate_file_list(py_files))
            except Exception as e:
                self._set_status(f"Error listing files: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _populate_file_list(self, files):
        self.file_listbox.delete(0, tk.END)
        for f in files:
            self.file_listbox.insert(tk.END, f)

    def open_remote_file(self):
        selection = self.file_listbox.curselection()
        if not selection or not self._require_connection():
            return
        filename = self.file_listbox.get(selection[0])
        remote_dir = self.remote_dir_var.get().strip()
        remote_path = f"{remote_dir.rstrip('/')}/{filename}"

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                with sftp.file(remote_path, "r") as f:
                    content = f.read().decode(errors="replace")
                sftp.close()
                self.root.after(0, lambda: self._load_into_editor(content, remote_path))
            except Exception as e:
                self._set_status(f"Error opening remote file: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _load_into_editor(self, content, remote_path):
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", content)
        self.remote_path_var.set(remote_path)
        self.notebook.select(self.code_tab)
        self._set_status(f"Loaded {remote_path} into editor.")

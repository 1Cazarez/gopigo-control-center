"""Notebook Cells tab -- Jupyter-style stacked cells that share variables
via a persistent Python "kernel" process running on the robot.
"""

import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from constants import VENV_ACTIVATE, login_shell
from remote_scripts import CELL_DONE_MARKER, CELL_END_MARKER, KERNEL_SCRIPT, REMOTE_KERNEL_DIR, REMOTE_KERNEL_PATH
from theme import BORDER, CARD_BG, FONT_MONO_SMALL, style_button


class NotebookTabMixin:
    def _build_notebook_cells_tab(self):
        outer = ttk.Frame(self.notebook_cells_tab, style="Card.TFrame", padding=12)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 8))

        self.kernel_status_var = tk.StringVar(value="Kernel: not running")
        ttk.Label(top, textvariable=self.kernel_status_var, style="CardMuted.TLabel").pack(side="left", padx=(0, 10))

        style_button(tk.Button(top, text="Start Kernel", command=self.start_kernel), "success").pack(
            side="left", padx=4
        )
        style_button(tk.Button(top, text="Restart Kernel", command=self.restart_kernel), "warning").pack(
            side="left", padx=4
        )
        style_button(tk.Button(top, text="+ Add Cell", command=self.add_cell), "primary").pack(
            side="left", padx=(14, 0)
        )

        ttk.Label(
            outer, text="Variables persist between cells until you restart the kernel.", style="CardMuted.TLabel"
        ).pack(anchor="w", pady=(0, 8))

        # Scrollable area holding the stacked cells
        container = tk.Frame(outer, bg=CARD_BG)
        container.pack(fill="both", expand=True)

        canvas = tk.Canvas(container, highlightthickness=0, bg=CARD_BG)
        scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.cells_frame = tk.Frame(canvas, bg=CARD_BG)

        self.cells_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.cells_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Start with one empty cell so the tab isn't blank
        self.add_cell()

    def add_cell(self):
        index = len(self.cells)
        cell_frame = tk.LabelFrame(
            self.cells_frame, text=f"Cell {index + 1}", padx=8, pady=8, bg=CARD_BG,
            fg="#5b6472", font=("Segoe UI", 9, "bold"), bd=1, relief="solid", highlightbackground=BORDER,
        )
        cell_frame.pack(fill="x", padx=4, pady=6, anchor="n")

        btn_row = tk.Frame(cell_frame, bg=CARD_BG)
        btn_row.pack(fill="x")

        code_widget = scrolledtext.ScrolledText(
            cell_frame, height=5, font=FONT_MONO_SMALL, undo=True, relief="flat",
            highlightthickness=1, highlightbackground=BORDER,
        )
        code_widget.pack(fill="x", pady=(4, 4))

        output_widget = scrolledtext.ScrolledText(
            cell_frame, height=4, font=FONT_MONO_SMALL, bg="#12161d", fg="#3ddc84", state="disabled",
            relief="flat",
        )
        output_widget.pack(fill="x")

        cell = {
            "frame": cell_frame,
            "code_widget": code_widget,
            "output_widget": output_widget,
        }
        self.cells.append(cell)

        style_button(
            tk.Button(btn_row, text="Run Cell (Shift+Enter)", command=lambda: self.run_cell(cell)), "success"
        ).pack(side="left", padx=2)
        style_button(
            tk.Button(btn_row, text="Delete Cell", command=lambda: self.delete_cell(cell)), "danger"
        ).pack(side="left", padx=2)

        # Shift+Enter runs the cell, like Jupyter
        code_widget.bind("<Shift-Return>", lambda e: (self.run_cell(cell), "break"))

    def delete_cell(self, cell):
        if cell in self.cells:
            cell["frame"].destroy()
            self.cells.remove(cell)

    def start_kernel(self):
        if not self._require_connection():
            return
        if self.kernel_channel is not None:
            self._set_status("Kernel already running.")
            return

        self.kernel_status_var.set("Kernel: starting...")

        def worker():
            try:
                sftp = self.ssh_client.open_sftp()
                try:
                    sftp.mkdir(REMOTE_KERNEL_DIR)
                except IOError:
                    pass  # directory probably already exists
                with sftp.file(REMOTE_KERNEL_PATH, "w") as f:
                    f.write(KERNEL_SCRIPT)
                sftp.close()

                channel = self.ssh_client.get_transport().open_session()
                command = f"{VENV_ACTIVATE} && python3 -u {REMOTE_KERNEL_PATH}"
                channel.exec_command(login_shell(command))
                self.kernel_channel = channel

                threading.Thread(target=self._kernel_reader_loop, daemon=True).start()
                self.root.after(0, lambda: self.kernel_status_var.set("Kernel: running"))
                self._set_status("Kernel started.")
            except Exception as e:
                self.root.after(0, lambda: self.kernel_status_var.set("Kernel: failed to start"))
                self._set_status(f"Error starting kernel: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def restart_kernel(self):
        try:
            if self.kernel_channel:
                self.kernel_channel.close()
        except Exception:
            pass
        self.kernel_channel = None
        self.current_cell = None
        try:
            self.kernel_lock.release()
        except RuntimeError:
            pass  # wasn't locked
        self.kernel_status_var.set("Kernel: restarting...")
        self.start_kernel()

    def _kernel_reader_loop(self):
        buffer = ""
        channel = self.kernel_channel
        while self.kernel_channel is channel and channel is not None:
            try:
                if channel.recv_ready():
                    data = channel.recv(4096).decode(errors="replace")
                    buffer += data
                    while CELL_DONE_MARKER in buffer:
                        idx = buffer.find(CELL_DONE_MARKER)
                        output_part = buffer[:idx]
                        buffer = buffer[idx + len(CELL_DONE_MARKER):]
                        if buffer.startswith("\n"):
                            buffer = buffer[1:]
                        self._deliver_output(output_part)
                elif channel.exit_status_ready():
                    self.root.after(0, lambda: self.kernel_status_var.set("Kernel: stopped"))
                    self.kernel_channel = None
                    break
                else:
                    time.sleep(0.05)
            except Exception:
                break

    def run_cell(self, cell):
        if self.kernel_channel is None:
            messagebox.showwarning("Kernel not running", "Click 'Start Kernel' first.")
            return
        if not self.kernel_lock.acquire(blocking=False):
            messagebox.showinfo("Kernel busy", "Another cell is currently running. Wait for it to finish.")
            return

        self.current_cell = cell
        cell["output_widget"].config(state="normal")
        cell["output_widget"].delete("1.0", tk.END)
        cell["output_widget"].insert("1.0", "Running...")
        cell["output_widget"].config(state="disabled")

        code = cell["code_widget"].get("1.0", tk.END)

        def worker():
            try:
                for line in code.splitlines():
                    self.kernel_channel.send(line + "\n")
                self.kernel_channel.send(CELL_END_MARKER + "\n")
            except Exception as e:
                self._set_status(f"Error running cell: {e}")
                try:
                    self.kernel_lock.release()
                except RuntimeError:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _deliver_output(self, text):
        def do():
            if self.current_cell:
                ow = self.current_cell["output_widget"]
                ow.config(state="normal")
                ow.delete("1.0", tk.END)
                ow.insert("1.0", text if text.strip() else "(no output)")
                ow.config(state="disabled")
            self.current_cell = None
            try:
                self.kernel_lock.release()
            except RuntimeError:
                pass

        self.root.after(0, do)

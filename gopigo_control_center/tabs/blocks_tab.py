"""Block Builder tab -- drag-and-drop block coding (Blockly) that generates
Python and sends it to the robot over this app's existing connection.

Tkinter can't host a web view, so the editor opens in your web browser. It's
served from a small localhost-only server (see block_server.py) that relays
the page's Send / Run / Stop buttons to the robot.
"""

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from block_server import BlockBuilderServer
from theme import style_button


class BlocksTabMixin:
    def _build_blocks_tab(self):
        outer = ttk.Frame(self.blocks_tab, style="Card.TFrame", padding=20)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Block Builder", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Snap blocks together, watch the generated Python update live, then send it to the robot "
            "and run it. The editor opens in your web browser and uses this window's robot connection -- "
            "click Connect above first.",
            style="CardMuted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(2, 8))

        row = ttk.Frame(outer, style="Card.TFrame")
        row.pack(fill="x", pady=4)
        style_button(tk.Button(row, text="Open Block Builder", command=self.open_block_builder), "success").pack(
            side="left"
        )

        self.blocks_status_var = tk.StringVar(value="Block Builder: not opened yet")
        ttk.Label(outer, textvariable=self.blocks_status_var, style="CardMuted.TLabel").pack(anchor="w", pady=(6, 2))

        self.blocks_url_var = tk.StringVar(value="")
        ttk.Entry(outer, textvariable=self.blocks_url_var, width=70).pack(anchor="w", pady=(0, 8), fill="x")
        ttk.Label(
            outer,
            text="If your browser doesn't open, paste this link into it. The link only works on this computer "
            "and only until you close this app.",
            style="CardMuted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w")

    def open_block_builder(self):
        if self.block_server is None:
            server = BlockBuilderServer(get_client=lambda: self.ssh_client, notify=self._set_status)
            try:
                server.start()
            except OSError as e:
                messagebox.showerror("Block Builder", f"Couldn't start the local Block Builder server:\n{e}")
                return
            self.block_server = server

        url = self.block_server.url
        self.blocks_url_var.set(url)
        self.blocks_status_var.set("Block Builder: running -- opened in your browser")
        self._set_status("Block Builder opened in your browser.")
        webbrowser.open(url)

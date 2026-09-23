"""Wi-Fi tab -- see and change which network the robot's Wi-Fi joins.

Talks to NetworkManager on the robot through wifi.py. Switching networks can
cut the very SSH link this app is using (the robot leaves the network you were
on), so switching always asks first and explains how to get back in.
"""

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from theme import BORDER, CARD_BG, FONT_MONO_SMALL, style_button
from wifi import (
    ENTERPRISE, KIND_LABELS, OPEN, SAE, UNSUPPORTED, WPA_PSK,
    LinkLost, WifiError, WifiManager, validate_network,
)

_KIND_BY_LABEL = {label: kind for kind, label in KIND_LABELS.items()}
_SECURITY_CHOICES = [KIND_LABELS[WPA_PSK], KIND_LABELS[SAE], KIND_LABELS[OPEN]]


class WifiTabMixin:
    def _build_wifi_tab(self):
        self.wifi_saved = []           # saved profiles from the last refresh
        self.wifi_scan_rows = []       # rows from the last scan
        self.wifi_hotspot_mode = False  # is the robot currently hosting its hotspot?

        outer = ttk.Frame(self.wifi_tab, style="Card.TFrame", padding=14)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Card.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="Robot Wi-Fi", style="Heading.TLabel").pack(side="left")
        style_button(tk.Button(header, text="Refresh", command=self.wifi_refresh), "neutral").pack(side="right")

        self.wifi_status_var = tk.StringVar(value="Connect to the robot, then click Refresh.")
        ttk.Label(
            outer, textvariable=self.wifi_status_var, style="CardBold.TLabel", wraplength=900, justify="left"
        ).pack(anchor="w", pady=(0, 4))

        # -- Saved and nearby networks, side by side --
        lists = ttk.Frame(outer, style="Card.TFrame")
        lists.pack(fill="x")
        lists.columnconfigure(0, weight=1, uniform="wifi_lists")
        lists.columnconfigure(1, weight=1, uniform="wifi_lists")

        saved_col = ttk.Frame(lists, style="Card.TFrame")
        saved_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ttk.Label(saved_col, text="Saved networks (auto-join in range)", style="CardMuted.TLabel").pack(anchor="w")
        self.wifi_saved_list = self._wifi_listbox(saved_col)
        saved_buttons = ttk.Frame(saved_col, style="Card.TFrame")
        saved_buttons.pack(fill="x", pady=(4, 0))
        style_button(tk.Button(saved_buttons, text="Connect", command=self.wifi_connect_selected), "primary").pack(
            side="left"
        )
        style_button(tk.Button(saved_buttons, text="Forget", command=self.wifi_forget_selected), "danger").pack(
            side="left", padx=6
        )

        nearby_col = ttk.Frame(lists, style="Card.TFrame")
        nearby_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ttk.Label(nearby_col, text="Nearby (click to fill the form)", style="CardMuted.TLabel").pack(anchor="w")
        self.wifi_scan_list = self._wifi_listbox(nearby_col)
        self.wifi_scan_list.bind("<<ListboxSelect>>", lambda e: self._wifi_pick_scanned())
        nearby_buttons = ttk.Frame(nearby_col, style="Card.TFrame")
        nearby_buttons.pack(fill="x", pady=(4, 0))
        style_button(tk.Button(nearby_buttons, text="Scan", command=self.wifi_scan), "neutral").pack(side="left")

        # -- Add a network --
        ttk.Label(outer, text="Add a network", style="CardBold.TLabel").pack(anchor="w", pady=(4, 0))
        form = ttk.Frame(outer, style="Card.TFrame")
        form.pack(fill="x")
        self.wifi_ssid_var = tk.StringVar()
        self.wifi_password_var = tk.StringVar()
        self.wifi_security_var = tk.StringVar(value=_SECURITY_CHOICES[0])
        self.wifi_hidden_var = tk.BooleanVar(value=False)

        ttk.Label(form, text="Network name:", style="Card.TLabel").grid(row=0, column=0, sticky="e", padx=(0, 4), pady=3)
        ttk.Entry(form, textvariable=self.wifi_ssid_var, width=16).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(form, text="Security:", style="Card.TLabel").grid(row=0, column=2, sticky="e", padx=(16, 4), pady=3)
        ttk.Combobox(
            form, textvariable=self.wifi_security_var, values=_SECURITY_CHOICES, state="readonly", width=20
        ).grid(row=0, column=3, sticky="w", pady=3)
        ttk.Label(form, text="Password:", style="Card.TLabel").grid(row=1, column=0, sticky="e", padx=(0, 4), pady=3)
        ttk.Entry(form, textvariable=self.wifi_password_var, show="*", width=16).grid(row=1, column=1, sticky="w", pady=3)
        tk.Checkbutton(
            form, text="Hidden network", variable=self.wifi_hidden_var, bg=CARD_BG, activebackground=CARD_BG,
            highlightthickness=0,
        ).grid(row=1, column=3, sticky="w", padx=(0, 0))

        buttons = ttk.Frame(outer, style="Card.TFrame")
        buttons.pack(fill="x", pady=(6, 0))
        style_button(tk.Button(buttons, text="Save Network", command=lambda: self.wifi_save(False)), "success").pack(
            side="left"
        )
        style_button(
            tk.Button(buttons, text="Save & Connect Now", command=lambda: self.wifi_save(True)), "primary"
        ).pack(side="left", padx=8)
        ttk.Label(
            outer, text="Save Network only remembers it. Connect Now switches the robot immediately and drops this connection.",
            style="CardMuted.TLabel", wraplength=900, justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def _wifi_listbox(self, parent):
        box = tk.Listbox(
            parent, font=FONT_MONO_SMALL, bg=CARD_BG, relief="flat", highlightthickness=1, highlightbackground=BORDER,
            selectbackground="#3b6fe0", selectforeground="#ffffff", exportselection=False, height=4, width=10,
        )
        box.pack(fill="x", pady=(2, 0))
        return box

    # ---------------- running robot commands off the UI thread ----------------
    def _wifi_task(self, busy_text, work, done, on_link_lost=None):
        """Run work() on a thread; then call done(result) on the Tk thread, or show the failure."""
        if not self._require_connection():
            return
        self.wifi_status_var.set(busy_text)

        def worker():
            try:
                result = work()
            except LinkLost as e:
                error = str(e)  # `e` is deleted when this block ends, before the lambda runs
                handler = on_link_lost or self._wifi_link_lost
                self.root.after(0, lambda: handler(error))
            except Exception as e:  # WifiError, or anything unexpected
                error = str(e)
                self.root.after(0, lambda: self._wifi_failed(error))
            else:
                self.root.after(0, lambda: done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _wifi_failed(self, error):
        self.wifi_status_var.set(f"Wi-Fi: {error}")
        messagebox.showerror("Robot Wi-Fi", error)

    def _wifi_link_lost(self, detail):
        self._disconnect()
        self.wifi_status_var.set("Lost contact with the robot.")
        messagebox.showwarning(
            "Robot Wi-Fi", "Lost contact with the robot while talking to it. Check that it's still powered and on "
            "the same network, then click Connect again.",
        )

    def _wifi_switched_away(self, ssid):
        """The SSH link died as the robot left the network: what we expect after Connect Now."""
        self._disconnect()
        self.wifi_status_var.set(f"Robot is switching to '{ssid}'.")
        messagebox.showinfo(
            "Robot Wi-Fi",
            f"The robot is switching to '{ssid}', so this connection ended.\n\n"
            f"Join '{ssid}' on your computer, then click Connect at the top again. The robot may have a new "
            "address, so try robot1.local or look it up in your router.\n\n"
            "If it can't join, it should go back to its hotspot after a short wait.",
        )

    # ---------------- actions ----------------
    def wifi_refresh(self):
        def work():
            manager = WifiManager(self.ssh_client)
            saved = manager.saved_networks()
            return saved, manager.status(saved)

        self._wifi_task("Checking the robot's Wi-Fi...", work, self._wifi_show_state)

    def _wifi_show_state(self, result):
        saved, status = result
        self.wifi_saved = saved
        self.wifi_hotspot_mode = status["hotspot"]

        self.wifi_saved_list.delete(0, tk.END)
        for network in saved:
            tags = []
            if network["active"]:
                tags.append("connected")
            if network["hotspot"]:
                tags.append("hotspot")
            tags.append(f"prio {network['priority']}")
            self.wifi_saved_list.insert(tk.END, f"{network['name']:<24.24} {', '.join(tags)}")

        where = f" (address {status['ip']})" if status["ip"] else ""
        if status["hotspot"]:
            self.wifi_status_var.set(
                f"The robot is hosting its hotspot '{status['connection']}'{where}. Scanning for other networks "
                "is limited while it does -- you can still type a network's name below."
            )
        elif status["connection"]:
            self.wifi_status_var.set(f"The robot is connected to '{status['connection']}'{where}.")
        else:
            self.wifi_status_var.set("The robot's Wi-Fi isn't connected to anything right now.")

    def wifi_scan(self):
        rescan = not self.wifi_hotspot_mode  # rescanning can interrupt a hotspot the robot is hosting

        def work():
            return WifiManager(self.ssh_client).scan(rescan=rescan)

        self._wifi_task("Scanning for networks...", work, self._wifi_show_scan)

    def _wifi_show_scan(self, rows):
        self.wifi_scan_rows = rows
        self.wifi_scan_list.delete(0, tk.END)
        for row in rows:
            marker = "*" if row["in_use"] else " "
            self.wifi_scan_list.insert(tk.END, f"{marker} {row['ssid']:<24.24} {row['signal']:>3}% {row['security']}")
        if rows:
            self.wifi_status_var.set(f"Found {len(rows)} network{'s' if len(rows) != 1 else ''}.")
        elif self.wifi_hotspot_mode:
            self.wifi_status_var.set(
                "Nothing to show: the robot can't scan while it hosts its hotspot. Type the network's name below."
            )
        else:
            self.wifi_status_var.set("No networks found.")

    def _wifi_pick_scanned(self):
        selection = self.wifi_scan_list.curselection()
        if not selection or selection[0] >= len(self.wifi_scan_rows):
            return
        row = self.wifi_scan_rows[selection[0]]
        self.wifi_ssid_var.set(row["ssid"])
        if row["in_use"]:
            self.wifi_status_var.set(f"The robot is already on '{row['ssid']}'.")
            return
        if row["kind"] in (ENTERPRISE, UNSUPPORTED):
            self.wifi_status_var.set(f"'{row['ssid']}' uses {row['security']}, which isn't supported here.")
            return
        self.wifi_security_var.set(KIND_LABELS[row["kind"]])

    def _wifi_selected_profile(self):
        selection = self.wifi_saved_list.curselection()
        if not selection or selection[0] >= len(self.wifi_saved):
            messagebox.showinfo("Robot Wi-Fi", "Select a saved network first.")
            return None
        return self.wifi_saved[selection[0]]

    def _wifi_confirm_switch(self, name):
        return messagebox.askyesno(
            "Switch the robot's Wi-Fi?",
            f"Switch the robot to '{name}' now?\n\n"
            "The robot leaves its current network, so this app will lose its connection. Afterwards, join "
            f"'{name}' on your computer and click Connect again.\n\n"
            "If the robot can't join, it should fall back to its hotspot after a short wait.",
        )

    def wifi_connect_selected(self):
        profile = self._wifi_selected_profile()
        if profile is None:
            return
        if profile["active"]:
            messagebox.showinfo("Robot Wi-Fi", f"The robot is already connected to '{profile['name']}'.")
            return
        if not self._wifi_confirm_switch(profile["name"]):
            return

        def work():
            WifiManager(self.ssh_client).connect(profile)

        self._wifi_task(
            f"Switching to '{profile['name']}'...", work, lambda _: self._wifi_after_connect(profile),
            on_link_lost=lambda _: self._wifi_switched_away(profile["name"]),
        )

    def wifi_forget_selected(self):
        profile = self._wifi_selected_profile()
        if profile is None:
            return
        if profile["hotspot"]:
            messagebox.showinfo(
                "Robot Wi-Fi", "That's the robot's hotspot -- its way back in when no known network is in range. "
                "It can't be removed from here.",
            )
            return
        warning = (
            f"Forget '{profile['name']}'? The robot is connected to it right now, so it will disconnect."
            if profile["active"] else f"Forget '{profile['name']}'?"
        )
        if not messagebox.askyesno("Forget network", warning):
            return

        def work():
            WifiManager(self.ssh_client).forget(profile)

        def link_lost(_):
            if profile["active"]:
                self._wifi_switched_away(profile["name"])
            else:
                self._wifi_link_lost("")

        self._wifi_task(f"Forgetting '{profile['name']}'...", work, lambda _: self.wifi_refresh(), on_link_lost=link_lost)

    def wifi_save(self, connect_now):
        ssid = self.wifi_ssid_var.get().strip()
        password = self.wifi_password_var.get()
        kind = _KIND_BY_LABEL[self.wifi_security_var.get()]
        hidden = self.wifi_hidden_var.get()
        try:
            validate_network(ssid, password, kind)  # fail fast, before asking to confirm anything
        except WifiError as e:
            messagebox.showerror("Robot Wi-Fi", str(e))
            return
        if connect_now and not self._wifi_confirm_switch(ssid):
            return

        def work():
            manager = WifiManager(self.ssh_client)
            profile = manager.save_network(ssid, password, kind, hidden=hidden)
            if connect_now:
                try:
                    manager.connect(profile)
                except LinkLost:
                    raise  # expected: the robot moved networks
                except WifiError:
                    if profile["created"]:  # don't leave a broken profile behind (e.g. wrong password)
                        try:
                            manager.forget(profile)
                        except WifiError:
                            pass
                    raise
            return profile

        def done(profile):
            self.wifi_password_var.set("")
            if connect_now:
                self._wifi_after_connect(profile)
            else:
                self.wifi_refresh()
                self._set_status(f"Saved '{profile['name']}'. The robot will join it when it's in range.")

        self._wifi_task(
            f"Connecting to '{ssid}'..." if connect_now else f"Saving '{ssid}'...", work, done,
            on_link_lost=(lambda _: self._wifi_switched_away(ssid)) if connect_now else None,
        )

    def _wifi_after_connect(self, profile):
        """The command returned without dropping our link (e.g. we're reaching the robot another way)."""
        self.wifi_password_var.set("")
        self.wifi_refresh()
        self._set_status(f"Robot Wi-Fi switched to '{profile['name']}'.")

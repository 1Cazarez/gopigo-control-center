#!/usr/bin/env python3
"""GoPiGo Control Center -- entry point.

A desktop GUI for writing Python scripts (or snapping them together from
blocks), driving the robot manually (via buttons or a game controller),
watching live sensor readings, browsing files on the robot, launching
JupyterLab, watching a live camera stream, and streaming console output in
real time -- all over SSH.

Run:
    python3 main.py

See README.md for setup and a tour of each tab.
"""

import tkinter as tk

from app import GoPiGoApp


def main():
    root = tk.Tk()
    GoPiGoApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

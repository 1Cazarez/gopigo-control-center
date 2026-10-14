#!/usr/bin/env python3
"""GoPiGo Control Center -- entry point.

A desktop GUI for driving a GoPiGo3 robot (with on-screen buttons or a game
controller), watching its camera and sensors, setting its lights, launching
JupyterLab, managing its Wi-Fi and reading the course modules -- all over SSH.

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

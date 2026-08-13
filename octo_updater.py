"""Octo Updater — entry point.

The application lives in app.py (and the extracted service modules); this
file only wires the config store paths and starts the Tk mainloop, so the
PyInstaller build command (`pyinstaller --onefile ... octo_updater.py`)
keeps working unchanged.
"""

import sys

import config_store
from constants import (
    CACHE_FILE,
    CONFIG_FILE,
    LEGACY_CACHE_FILE,
    LEGACY_CONFIG_FILE,
)

config_store.configure(CONFIG_FILE, CACHE_FILE,
                       LEGACY_CONFIG_FILE, LEGACY_CACHE_FILE)


def main():
    try:
        from app import OctoUpdaterApp
    except ImportError as e:
        if "tkinter" in str(e) or "_tkinter" in str(e):
            sys.stderr.write(
                "Octo Updater needs Tk (tkinter) to run. Install it with your\n"
                "system package manager, e.g.:\n"
                "  Debian/Ubuntu:  sudo apt install python3-tk\n"
                "  Fedora:         sudo dnf install python3-tkinter\n"
                "  Arch:           sudo pacman -S tk\n"
                "  macOS:          brew install python-tk\n")
        else:
            sys.stderr.write(f"Failed to import the Octo Updater GUI: {e}\n")
        sys.exit(1)
    try:
        app = OctoUpdaterApp()
    except Exception as e:
        sys.stderr.write(
            f"Octo Updater could not start: {e}\n"
            "A graphical display (X11/Wayland) is required.\n")
        sys.exit(1)
    app.mainloop()


if __name__ == "__main__":
    main()

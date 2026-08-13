"""Octo Updater — entry point.

The application lives in app.py (and the extracted service modules); this
file only wires the config store paths, selects the GUI backend at runtime
and starts its mainloop, so the PyInstaller build command
(`pyinstaller --onefile ... octo_updater.py`) keeps working unchanged.
"""

import os
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

_TKINTER_HELP = (
    "Octo Updater needs Tk (tkinter) to run. Install it with your\n"
    "system package manager, e.g.:\n"
    "  Debian/Ubuntu:  sudo apt install python3-tk\n"
    "  Fedora:         sudo dnf install python3-tkinter\n"
    "  Arch:           sudo pacman -S tk\n"
    "  macOS:          brew install python-tk\n")

_QT_UNAVAILABLE = (
    "Octo Updater needs PySide6 (Qt) to run (Qt is the default backend). "
    "Install it with `uv sync` or `pip install PySide6`, or set "
    "OCTO_UI_BACKEND=tk to use the legacy Tk interface.\n")


def resolve_backend(name=None) -> type | None:
    """Return the app class for the selected GUI backend.

    Reads the OCTO_UI_BACKEND environment variable when ``name`` is None
    (``qt`` is the default). Raises ImportError when the backend's module
    cannot be imported; returns None for an unknown backend name.
    """
    if name is None:
        name = os.environ.get("OCTO_UI_BACKEND", "qt")
    if name == "tk":
        from app import OctoUpdaterApp
        return OctoUpdaterApp
    if name in ("qt", "pyside6"):
        from qt_app import QtOctoUpdaterApp
        return QtOctoUpdaterApp
    return None


def backend_error_message(name, exc) -> str:
    """Map a failed backend import to a user-facing stderr message."""
    if name in ("qt", "pyside6"):
        return _QT_UNAVAILABLE
    if "tkinter" in str(exc) or "_tkinter" in str(exc):
        return _TKINTER_HELP
    return f"Failed to import the Octo Updater GUI: {exc}\n"


def main():
    backend = os.environ.get("OCTO_UI_BACKEND", "qt")
    try:
        app_cls = resolve_backend(backend)
    except ImportError as e:
        sys.stderr.write(backend_error_message(backend, e))
        sys.exit(1)
    if app_cls is None:
        sys.stderr.write(f"Unknown OCTO_UI_BACKEND: {backend}\n")
        sys.exit(1)
    try:
        app = app_cls()
    except Exception as e:
        sys.stderr.write(
            f"Octo Updater could not start: {e}\n"
            "A graphical display (X11/Wayland) is required.\n")
        sys.exit(1)
    app.mainloop()


if __name__ == "__main__":
    main()

"""Octo Updater — command-line entry point.

Wires the config-store paths and starts the Qt interface. The console-script
entry (`octo-updater`), `python -m octo_updater` and the PyInstaller specs all
reach this module.
"""

import os
import sys

from .core import config_store
from .core.constants import (
    CACHE_FILE,
    CONFIG_FILE,
    LEGACY_CACHE_FILE,
    LEGACY_CONFIG_FILE,
)

_QT_UNAVAILABLE = (
    "Octo Updater needs PySide6 (Qt) to run. "
    "Install it with `uv sync` or `pip install PySide6`.\n")


def resolve_backend(name=None) -> type | None:
    """Return the Qt app class for the selected GUI backend.

    Reads the OCTO_UI_BACKEND environment variable when ``name`` is None
    (``qt`` is the default; ``pyside6`` is accepted as an alias). Raises
    ImportError when the Qt module cannot be imported; returns None for an
    unknown backend name.
    """
    if name is None:
        name = os.environ.get("OCTO_UI_BACKEND", "qt")
    if name in ("qt", "pyside6"):
        from .ui.qt.app import QtOctoUpdaterApp
        return QtOctoUpdaterApp
    return None


def backend_error_message(name, exc) -> str:
    """Map a failed backend import to a user-facing stderr message."""
    if name in ("qt", "pyside6"):
        return _QT_UNAVAILABLE
    return f"Failed to import the Octo Updater GUI: {exc}\n"


def main() -> int:
    config_store.configure(CONFIG_FILE, CACHE_FILE,
                           LEGACY_CONFIG_FILE, LEGACY_CACHE_FILE)
    backend = os.environ.get("OCTO_UI_BACKEND", "qt")
    try:
        app_cls = resolve_backend(backend)
    except ImportError as e:
        sys.stderr.write(backend_error_message(backend, e))
        return 1
    if app_cls is None:
        sys.stderr.write(f"Unknown OCTO_UI_BACKEND: {backend}\n")
        return 1
    try:
        app = app_cls()
    except Exception as e:
        sys.stderr.write(
            f"Octo Updater could not start: {e}\n"
            "A graphical display (X11/Wayland) is required.\n")
        return 1
    app.show()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())

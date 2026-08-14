"""Vanilla WoW Launcher — command-line entry point.

Wires the launcher configuration and the config-store paths, then starts the
Qt interface. The console-script entry (`vanilla-wow-launcher`), `python -m
vanilla_wow_launcher` and the PyInstaller specs all reach this module.
"""

import argparse
import os
import sys

from .core import config_store, launcher
from .core.constants import (
    CACHE_FILE,
    CONFIG_FILE,
    LEGACY_CACHE_FILE,
    LEGACY_CONFIG_FILE,
    LEGACY_USER_CACHE_FILE,
    LEGACY_USER_CONFIG_FILE,
)

_QT_UNAVAILABLE = (
    "Vanilla WoW Launcher needs PySide6 (Qt) to run. "
    "Install it with `uv sync` or `pip install PySide6`.\n")


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="vanilla-wow-launcher",
        description="Vanilla WoW Launcher — updater and mod manager for the Vanilla "
                    "WoW client.")
    parser.add_argument(
        "--launcher-config", metavar="PATH",
        help="Path to the vanilla_wow_launcher.json file that configures "
             "the server, endpoints and mirrors (auto-discovered next to "
             "the executable / in the repo root when omitted).")
    return parser.parse_args(argv)


def resolve_backend(name=None) -> type | None:
    """Return the Qt app class for the selected GUI backend.

    Reads the VANILLA_WOW_UI_BACKEND environment variable when ``name`` is None
    (``qt`` is the default; ``pyside6`` is accepted as an alias). Raises
    ImportError when the Qt module cannot be imported; returns None for an
    unknown backend name.
    """
    if name is None:
        name = os.environ.get("VANILLA_WOW_UI_BACKEND", "qt")
    if name in ("qt", "pyside6"):
        from .ui.qt.app import QtVanillaWoWLauncherApp
        return QtVanillaWoWLauncherApp
    return None


def backend_error_message(name, exc) -> str:
    """Map a failed backend import to a user-facing stderr message."""
    if name in ("qt", "pyside6"):
        return _QT_UNAVAILABLE
    return f"Failed to import the Vanilla WoW Launcher GUI: {exc}\n"


def main(argv=None) -> int:
    args = _parse_args(argv)
    _cfg, err = launcher.configure(args.launcher_config)
    if err:
        sys.stderr.write(f"{err}\n")
        return 1
    config_store.configure(
        CONFIG_FILE, CACHE_FILE,
        legacy_config=(LEGACY_CONFIG_FILE, LEGACY_USER_CONFIG_FILE),
        legacy_cache=(LEGACY_CACHE_FILE, LEGACY_USER_CACHE_FILE))
    backend = os.environ.get("VANILLA_WOW_UI_BACKEND", "qt")
    try:
        app_cls = resolve_backend(backend)
    except ImportError as e:
        sys.stderr.write(backend_error_message(backend, e))
        return 1
    if app_cls is None:
        sys.stderr.write(f"Unknown VANILLA_WOW_UI_BACKEND: {backend}\n")
        return 1
    try:
        app = app_cls()
    except Exception as e:
        sys.stderr.write(
            f"Vanilla WoW Launcher could not start: {e}\n"
            "A graphical display (X11/Wayland) is required.\n")
        return 1
    app.show()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())

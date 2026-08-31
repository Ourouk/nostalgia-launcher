"""Nostalgia Launcher — command-line entry point.

Wires the launcher configuration and the config-store paths, then starts the
Qt interface. The console-script entry (`nostalgia-launcher`), `python -m
nostalgia_launcher` and the PyInstaller specs all reach this module.

On first launch, when no `nostalgia_launcher.json` is found and no
``--launcher-config`` was given, a modal Qt wizard asks the user to pick one
instead of failing hard.
"""

import argparse
import os
import sys

from .core import (
    app_lock,
    config_store,
    launcher,
    log_sink,
    profiles,
)
from .core.constants import (
    LOG_FILE,
    UPDATER_VERSION,
)
from .core.log_sink import log
from .core.profiles import ProfileError

_QT_UNAVAILABLE = (
    "Nostalgia Launcher needs PySide6 (Qt) to run. "
    "Install it with `uv sync` or `pip install PySide6`.\n"
)

# Sentinel from the single-instance handshake: an existing instance
# answered, so main() must exit 0 immediately.
_GUARD_BUSY = object()

# The QLocalServer serving THIS instance's guard key; module-level so it
# stays referenced (a garbage-collected server stops listening).
_GUARD_SERVER_KEY: str | None = None


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nostalgia-launcher",
        description="Nostalgia Launcher — updater and mod manager for the Vanilla "
        "WoW client.",
    )
    parser.add_argument(
        "--launcher-config",
        metavar="PATH",
        help="Path to the nostalgia_launcher.json file that configures "
        "the server, endpoints and mirrors (auto-discovered next to "
        "the executable / in the repo root when omitted).",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help="Run within the named launcher profile (isolated server "
        "config, state, cache, catalogs and torrent metadata under "
        "<config dir>/profiles/NAME). Unknown names are a hard error.",
    )
    parser.add_argument(
        "--print-log",
        nargs="?",
        const=None,
        default=False,
        metavar="N",
        type=int,
        help="Print the retained session logs (rotated .old first, then "
        "current) to stdout and exit — only the last N lines when N is "
        "given. Never starts the GUI.",
    )
    parser.add_argument(
        "--show-log",
        action="store_true",
        help="Open the Session log window at startup (for debugging).",
    )
    return parser.parse_args(argv)


def resolve_backend(name=None) -> type | None:
    """Return the Qt app class for the selected GUI backend.

    Reads the NOSTALGIA_UI_BACKEND environment variable when ``name`` is None
    (``qt`` is the default; ``pyside6`` is accepted as an alias). Raises
    ImportError when the Qt module cannot be imported; returns None for an
    unknown backend name.
    """
    if name is None:
        name = os.environ.get("NOSTALGIA_UI_BACKEND", "qt")
    if name in ("qt", "pyside6"):
        from .ui.qt.app import QtNostalgiaLauncherApp

        return QtNostalgiaLauncherApp
    return None


def backend_error_message(name, exc) -> str:
    """Map a failed backend import to a user-facing stderr message."""
    if name in ("qt", "pyside6"):
        return _QT_UNAVAILABLE
    return f"Failed to import the Nostalgia Launcher GUI: {exc}\n"


def main(argv=None) -> int:
    args = _parse_args(argv)
    if args.print_log is not False:
        return _print_log(args.print_log)
    # Composition-root wiring: core's launch capability asks services/umu
    # through an injected probe (core must not import services).
    from .core import platform_support
    from .services import umu

    platform_support.set_umu_probe(umu.umu_available)
    try:
        prof = profiles.resolve(args.profile)
    except ProfileError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    profiles.activate(prof)
    if not args.launcher_config and not os.path.exists(prof.launcher_path()):
        # First launch for this profile: no launcher config in its scope —
        # the wizard below persists into the profile's own launcher.json.
        return _first_launch(args.show_log)
    explicit = bool(args.launcher_config)
    _cfg, err = launcher.configure(args.launcher_config)
    if err:
        if explicit:
            sys.stderr.write(f"{err}\n")
            return 1
        return _first_launch(args.show_log)
    return _run_backend(args.show_log)


def _print_log(tail) -> int:
    """--print-log: dump the retained session log to stdout. Runs before
    any launcher-config handling and never imports Qt."""
    lines = log_sink.read_lines(tail)
    if not lines:
        sys.stderr.write(
            f"No launcher log yet ({log_sink.current_log_path()} has not "
            "been created).\n"
        )
        return 0
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _first_launch(show_log: bool = False) -> int:
    """No launcher config and no --launcher-config: ask the user to import
    one (a local file or an https URL they supply), then persist it so
    future launches reuse it. The wizard also REQUIRES an install folder;
    it is recorded as the active profile's confirmed game folder
    (``out_dir`` in its own state store) so each profile installs its own
    client. No folder is ever assumed without that explicit wizard step —
    profiles configured otherwise stay unconfirmed until Settings."""
    try:
        chosen = _pick_launcher_config()
    except ImportError as e:
        sys.stderr.write(backend_error_message("qt", e))
        return 1
    if chosen is None:
        sys.stderr.write(
            "No launcher configuration selected. A nostalgia_launcher.json "
            "is required — launch again with --launcher-config.\n"
        )
        return 1
    if chosen["kind"] == "file":
        _cfg, err = launcher.configure(chosen["path"])
        if err:
            sys.stderr.write(f"{err}\n")
            return 1
        dest, err = launcher.persist(chosen["path"])
        if err:
            sys.stderr.write(f"{err}\n")
            return 1
        if os.path.normpath(dest) != os.path.normpath(chosen["path"]):
            _cfg, err = launcher.configure(dest)
            if err:
                sys.stderr.write(f"{err}\n")
                return 1
    else:  # url: the wizard already fetched and validated the config
        import json

        from .services import config_import

        # Reuse the config the wizard already fetched when possible,
        # otherwise fetch it now (e.g. a non-interactive selection).
        raw = chosen.get("raw")
        if not isinstance(raw, str) or not raw:
            _data, raw_fetched, err = config_import.fetch_config_url(
                chosen["config_url"]
            )
            if err:
                sys.stderr.write(f"{err}\n")
                return 1
            raw = raw_fetched
        assert isinstance(raw, str)
        _cfg = launcher.configure_from_dict(json.loads(raw))
        if _cfg is None:
            sys.stderr.write(
                f"Invalid launcher configuration: {launcher.config_error()}\n"
            )
            return 1
        dest, err = launcher.persist_text(raw)
        if err:
            sys.stderr.write(f"{err}\n")
            return 1
        _cfg, err = launcher.configure(dest)
        if err:
            sys.stderr.write(f"{err}\n")
            return 1
    # The wizard's required install folder becomes THIS profile's
    # confirmed game folder (its own state store; legacy top-level file
    # for the default profile).
    install_dir = (chosen.get("install_dir") or "").strip()
    if install_dir:
        config_store.apply_confirmed_out_dir(
            profiles.active().state_path(), install_dir
        )
    return _run_backend(show_log)


def _pick_launcher_config() -> dict | None:
    """Modal first-launch config import; returns the chosen selection dict
    (``{"kind": "file", "path", "raw"}`` or ``{"kind": "url",
    "config_url", "raw"}``) or None on cancel."""
    from PySide6.QtWidgets import QDialog

    from .ui.qt.app import create_qt_app
    from .ui.qt.launcher_config_dialog import LauncherConfigDialog

    create_qt_app()
    dlg = LauncherConfigDialog(initial_path=launcher.discover_path())
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.selection()


def _guard_enter(key, prof):
    """Single-instance handshake for the active profile.

    When another instance of this profile is already up: forward
    ``{"op": "raise"}`` so it focuses its window, print the notice, and
    return ``_GUARD_BUSY`` (caller exits 0). Otherwise start serving the
    key (raise messages are dispatched once our window exists) and return
    the message relay — or None when Qt is unavailable / listening failed,
    in which case the store lock alone still guards the profile.
    """
    try:
        from .ui.qt import app_lock_qt
    except ImportError:
        return None  # no Qt at all: lock-only protection
    global _GUARD_SERVER_KEY
    existing = app_lock_qt.try_connect_existing(key)
    if existing is not None:
        app_lock_qt.send_json_line(existing, {"op": "raise"})
        sys.stderr.write(
            f"Already running (profile {prof.name}) — focusing existing "
            "window.\n"
        )
        return _GUARD_BUSY
    try:
        server, relay = app_lock_qt.serve(key)
    except RuntimeError as e:
        # Degrade gracefully: the advisory store lock still protects us.
        log(f"single-instance server unavailable: {e}", "dim")
        return None
    _GUARD_SERVER_KEY = key
    return relay


def _make_raise_handler(app):
    """Dispatch single-instance messages to the live window. Unknown ops
    are ignored with a log line."""

    def _handle(msg):
        op = msg.get("op") if isinstance(msg, dict) else None
        if op == "raise":
            app.raise_to_front()
        else:
            log(f"Ignoring single-instance op: {op!r}")

    return _handle


def _run_backend(show_log: bool = False) -> int:
    """Config-store + log-sink setup, then Qt backend construction/run."""
    prof = profiles.active()
    state_path = prof.state_path()
    relay = _guard_enter(app_lock.state_key(state_path), prof)
    if relay is _GUARD_BUSY:
        return 0
    # Every early exit below must still release this instance's guard
    # server — a leaked QLocalServer/socket file would contradict the
    # single-instance contract (shutdown is idempotent, so the normal
    # path's call stays harmless).
    try:
        try:
            app_lock.acquire_with_grace(state_path)
        except app_lock.AcquireError:
            sys.stderr.write(
                "Another launcher instance already holds this profile's "
                "store.\n"
            )
            return 6
        config_store.configure(state_path, prof.cache_path())
        log_sink.configure_file(LOG_FILE)
        log(
            f"── Nostalgia Launcher {UPDATER_VERSION} · session start ──",
            "dim",
        )
        backend = os.environ.get("NOSTALGIA_UI_BACKEND", "qt")
        try:
            app_cls = resolve_backend(backend)
        except ImportError as e:
            sys.stderr.write(backend_error_message(backend, e))
            return 1
        if app_cls is None:
            sys.stderr.write(f"Unknown NOSTALGIA_UI_BACKEND: {backend}\n")
            return 1
        try:
            app = app_cls(open_log=show_log)
        except Exception as e:
            sys.stderr.write(
                f"Nostalgia Launcher could not start: {e}\n"
                "A graphical display (X11/Wayland) is required.\n"
            )
            return 1
        if relay is not None and relay is not _GUARD_BUSY:
            relay.message.connect(  # type: ignore[attr-defined]  # noqa: B009
                _make_raise_handler(app)
            )
        app.show()
        rc = app.run()
        return rc
    finally:
        _guard_shutdown()


def _guard_shutdown():
    """Close this instance's guard server (socket file removed so the
    next launch never sees a stale AddressInUse)."""
    global _GUARD_SERVER_KEY
    key = _GUARD_SERVER_KEY
    if key is None:
        return
    _GUARD_SERVER_KEY = None
    try:
        from .ui.qt import app_lock_qt

        app_lock_qt.stop_server(key)
    except ImportError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())

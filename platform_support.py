"""Platform detection and cross-platform helpers.

Octo Updater's core update/mod/addon/news features are generic, but a few
actions are inherently Windows-only (launching the Windows game client,
binary-patching WoW.exe, Windows Defender exclusions). On Linux/macOS those
are disabled and the app falls back to the generic features only.

Detection is done through functions (not module constants) so tests can
monkeypatch `sys.platform`.
"""

import os
import subprocess
import sys

CLIENT_EXE = "WoW.exe"


def system_name() -> str:
    return sys.platform


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def can_launch_client() -> bool:
    """The game client is a Windows binary — launch is Windows-only."""
    return is_windows()


def can_patch_client() -> bool:
    """Binary patching of WoW.exe targets Windows offsets only."""
    return is_windows()


def can_manage_antivirus() -> bool:
    """Windows Defender exclusions only exist on Windows."""
    return is_windows()


def config_dir() -> str:
    """OS-appropriate directory for the persistent JSON config file."""
    if is_windows():
        # Keep historical behavior: config lives next to the executable.
        return _app_dir()
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(home, "Library", "Application Support", "OctoUpdater")
    base = os.environ.get("XDG_CONFIG_HOME") \
        or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "octo-updater")


def cache_dir() -> str:
    """OS-appropriate directory for the SHA-1 hash cache."""
    if is_windows():
        return _app_dir()
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(home, "Library", "Caches", "OctoUpdater")
    base = os.environ.get("XDG_CACHE_HOME") \
        or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "octo-updater")


def default_out_dir() -> str:
    """First-run default game folder — always a user-writable location."""
    if is_windows():
        return os.path.join(_app_dir(), "OctoWoW")
    return os.path.join(os.path.expanduser("~"), "OctoWoW")


def open_folder(path: str):
    """Open a folder in the platform's file manager.

    Raises OSError (e.g. FileNotFoundError) when no opener is available.
    """
    if is_windows():
        # Explicit explorer.exe, not os.startfile: ShellExecute resolves
        # extensionless paths against PATHEXT/.lnk, so a Desktop shortcut
        # named like the folder (e.g. "OctoWoW.lnk") gets *executed* instead
        # of the folder being opened.
        subprocess.Popen(["explorer.exe", path], close_fds=True)
    elif is_macos():
        subprocess.Popen(["open", path], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", path], close_fds=True)


def _app_dir() -> str:
    """Directory of the executable when frozen, otherwise this file's dir."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

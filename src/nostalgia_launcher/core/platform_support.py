"""Platform detection and helpers — thin wrapper over platformdirs."""

import os
import subprocess
import sys

from platformdirs import PlatformDirs

_UMU_PROBE = None


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def set_umu_probe(probe) -> None:
    global _UMU_PROBE
    _UMU_PROBE = probe


def can_launch_client() -> bool:
    if is_windows():
        return True
    if is_linux():
        return bool(_UMU_PROBE and _UMU_PROBE())
    return False


def can_manage_antivirus() -> bool:
    return is_windows()


def _win_roaming() -> str:
    if b := os.environ.get("APPDATA"):
        return b
    p = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(p, "AppData", "Roaming")


def _win_local() -> str:
    if b := os.environ.get("LOCALAPPDATA"):
        return b
    if b := os.environ.get("APPDATA"):
        return b
    p = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(p, "AppData", "Local")


def config_dir() -> str:
    if is_windows():
        return os.path.join(_win_roaming(), "NostalgiaLauncher")
    if is_macos():
        h = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(
            h, "Library", "Application Support", "NostalgiaLauncher"
        )
    # Linux: platformdirs XDG, but preserve existing installs under the
    # legacy hidden dir (~/.nostalgia-launcher) until migrated.
    new = PlatformDirs(
        "nostalgia-launcher", appauthor=False, roaming=True
    ).user_config_dir
    legacy = os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"),
        ".nostalgia-launcher",
    )
    if os.path.exists(legacy) and not os.path.exists(new):
        return legacy
    return new


# Backward-compat aliases for previous private helpers
_windows_roaming_dir = _win_roaming
_windows_local_dir = _win_local


def cache_dir() -> str:
    if is_windows():
        return os.path.join(_win_local(), "NostalgiaLauncher")
    if is_macos():
        h = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(h, "Library", "Caches", "NostalgiaLauncher")
    return PlatformDirs(
        "nostalgia-launcher", appauthor=False, roaming=True
    ).user_cache_dir


def data_dir() -> str:
    if is_windows():
        return os.path.join(_win_local(), "NostalgiaLauncher")
    if is_macos():
        h = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(
            h, "Library", "Application Support", "NostalgiaLauncher"
        )
    return PlatformDirs(
        "nostalgia-launcher", appauthor=False, roaming=True
    ).user_data_dir


_ILLEGAL_DIR_CHARS = ':/\\*?"<>|'


def games_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Games")


def server_games_dir(name: str) -> str:
    safe = "".join(
        c for c in (name or "") if c not in _ILLEGAL_DIR_CHARS
    ).strip()
    return (
        os.path.join(games_dir(), safe)
        if safe
        else os.path.join(games_dir(), "VanillaWoW")
    )


def default_game_folder(server_name: str | None) -> str:
    return server_games_dir(server_name) if server_name else ""


def open_folder(path: str):
    if is_windows():
        subprocess.Popen(["explorer.exe", path], close_fds=True)
    elif is_macos():
        subprocess.Popen(["open", path], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", path], close_fds=True)

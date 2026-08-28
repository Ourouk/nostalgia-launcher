"""Platform detection and cross-platform helpers.

Nostalgia Launcher's core update/mod/addon/news features are generic, but a few
actions are platform-specific: launching the Windows game client (native on
Windows, via umu-launcher/Proton on Linux when ``umu-run`` is available) and
Windows Defender exclusions (Windows-only). On unsupported platforms those are
disabled and the app falls back to the generic features only.

Detection is done through functions (not module constants) so tests can
monkeypatch `sys.platform`.
"""

import os
import subprocess
import sys


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


# Whether the Linux game launch capability is available is decided by
# services/umu (umu-run on PATH). Core must not import services, so the
# composition root (cli) injects the probe at startup; unset → not capable.
_UMU_PROBE = None


def set_umu_probe(probe) -> None:
    """Register the Linux launch-capability probe (zero-arg callable)."""
    global _UMU_PROBE
    _UMU_PROBE = probe


def can_launch_client() -> bool:
    """The game client is a Windows binary — launched natively on Windows
    and via umu-launcher (Proton/Wine) on Linux when umu-run is available."""
    if is_windows():
        return True
    if is_linux():
        return bool(_UMU_PROBE and _UMU_PROBE())
    return False


def can_manage_antivirus() -> bool:
    """Windows Defender exclusions only exist on Windows."""
    return is_windows()


def config_dir() -> str:
    """OS-appropriate directory for the persistent JSON config file.

    User data never lives next to the executable (which may be read-only or
    shared between users): Linux uses a hidden per-user dir
    (~/.nostalgia-launcher), Windows uses the roaming %APPDATA% dir, macOS
    uses ~/Library/Application Support.
    """
    if is_windows():
        return os.path.join(_windows_roaming_dir(), "NostalgiaLauncher")
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(
            home, "Library", "Application Support", "NostalgiaLauncher"
        )
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".nostalgia-launcher")


def cache_dir() -> str:
    """OS-appropriate directory for the SHA-1 hash cache — disposable data,
    kept separate from the config. Windows uses %LOCALAPPDATA%, macOS uses
    ~/Library/Caches and Linux uses the XDG cache dir."""
    if is_windows():
        return os.path.join(_windows_local_dir(), "NostalgiaLauncher")
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(home, "Library", "Caches", "NostalgiaLauncher")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "nostalgia-launcher")


def data_dir() -> str:
    """OS-appropriate directory for persistent, non-config runtime data the
    launcher owns (e.g. the umu-launcher WINEPREFIX). Windows uses
    %LOCALAPPDATA%, macOS uses ~/Library/Application Support and Linux uses
    the XDG data dir."""
    if is_windows():
        return os.path.join(_windows_local_dir(), "NostalgiaLauncher")
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(
            home, "Library", "Application Support", "NostalgiaLauncher"
        )
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return os.path.join(base, "nostalgia-launcher")


def _windows_roaming_dir() -> str:
    """%APPDATA% (roaming), falling back to USERPROFILE\\AppData\\Roaming."""
    base = os.environ.get("APPDATA")
    if base:
        return base
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "AppData", "Roaming")


def _windows_local_dir() -> str:
    """%LOCALAPPDATA%, falling back to %APPDATA%, then
    USERPROFILE\\AppData\\Local."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return base
    base = os.environ.get("APPDATA")
    if base:
        return base
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "AppData", "Local")


# Characters illegal in a folder name on Windows/Linux/macOS — stripped when
# turning a server name into a Games/<name> subfolder.
_ILLEGAL_DIR_CHARS = ':/\\*?"<>|'


def games_dir() -> str:
    """Per-user 'Games' folder where each server's client is installed
    (e.g. ~/Games on every OS)."""
    return os.path.join(os.path.expanduser("~"), "Games")


def server_games_dir(name: str) -> str:
    """The game-client folder for a server: Games/<sanitized name>.

    Strips characters that are illegal in folder names while keeping letters,
    digits, spaces and dashes; falls back to 'VanillaWoW' for an empty/blank
    name."""
    safe = "".join(c for c in (name or "") if c not in _ILLEGAL_DIR_CHARS)
    safe = safe.strip()
    if not safe:
        safe = "VanillaWoW"
    return os.path.join(games_dir(), safe)


def default_game_folder(server_name: str | None) -> str:
    """The suggested client folder for a server: Games/<name> when a server
    is configured, else "" (unconfigured — the user must pick a folder; the
    launcher never downloads without an explicit game-folder confirmation)."""
    if not server_name:
        return ""
    return server_games_dir(server_name)


def open_folder(path: str):
    """Open a folder in the platform's file manager.

    Raises OSError (e.g. FileNotFoundError) when no opener is available.
    """
    if is_windows():
        # Explicit explorer.exe, not os.startfile: ShellExecute resolves
        # extensionless paths against PATHEXT/.lnk, so a Desktop shortcut
        # named like the folder (e.g. "VanillaWoW.lnk") gets *executed* instead
        # of the folder being opened.
        subprocess.Popen(["explorer.exe", path], close_fds=True)
    elif is_macos():
        subprocess.Popen(["open", path], close_fds=True)
    else:
        subprocess.Popen(["xdg-open", path], close_fds=True)

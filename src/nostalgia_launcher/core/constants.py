"""App-level constants and filesystem paths shared across modules.

Nostalgia Launcher: the executable dir (APP_DIR) is next to the .exe when
frozen (PyInstaller), otherwise next to this file — never the current working
directory. Persistent config/cache files live in OS-appropriate per-user
directories so the app works from read-only install locations: Linux config
in ~/.nostalgia-launcher, Windows config in %APPDATA%\\NostalgiaLauncher,
macOS config in ~/Library/Application Support; the hash cache is kept
separate (Linux XDG cache dir, %LOCALAPPDATA%, ~/Library/Caches).

Files from earlier versions are migrated on first run: the vanilla-era
per-user files (the app was named VanillaWoWLauncher through v1.4), the
superseded vanilla-era next-to-exe / XDG locations, and the octo-era
(``octo_updater_*`` / "octo-updater") files.

There is deliberately no hardcoded server here: every endpoint (client
updates, news, mod/addon catalogs, realm, mirrors) comes from the launcher
configuration (`core/launcher.py`).
"""

import os
import sys

from .platform_support import (
    cache_dir,
    config_dir,
    default_out_dir,
    is_macos,
    is_windows,
)

UPDATER_VERSION = "0.0.1"
UA = f"NostalgiaLauncher/{UPDATER_VERSION}"
DOWNLOAD_RETRY = 5
DOWNLOAD_TIMEOUT = 10  # seconds without any data before a transfer aborts

GITHUB_API = "https://api.github.com"
MOD_UA = f"NostalgiaLauncher/{UPDATER_VERSION}"

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # Running from source: the repo root (three levels up from
    # src/nostalgia_launcher/core/constants.py).
    APP_DIR = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

CONFIG_FILE = os.path.join(config_dir(), "nostalgia_launcher_config.json")
CACHE_FILE = os.path.join(cache_dir(), "nostalgia_launcher_hash_cache.json")

# Legacy locations, most recent first. The "vanilla" era (v1.0–1.4, when the
# app was named VanillaWoWLauncher) kept config/cache in per-user dirs;
# earlier still, the "octo" era (Octo Updater heritage) used next-to-exe
# files and "octo-updater" dirs. All are migrated to the current per-user
# location on first run.

# ── vanilla era (immediately preceding name) ────────────────────────────────


def _vanilla_config_dir() -> str:
    if is_windows():
        return os.path.join(_windows_roaming_dir_v(), "VanillaWoWLauncher")
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(
            home, "Library", "Application Support", "VanillaWoWLauncher"
        )
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "vanilla-wow-launcher")


def _vanilla_cache_dir() -> str:
    if is_windows():
        return os.path.join(_windows_local_dir_v(), "VanillaWoWLauncher")
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(home, "Library", "Caches", "VanillaWoWLauncher")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "vanilla-wow-launcher")


def _windows_roaming_dir_v() -> str:
    base = os.environ.get("APPDATA")
    if base:
        return base
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "AppData", "Roaming")


def _windows_local_dir_v() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return base
    return _windows_roaming_dir_v()


VANILLA_USER_CONFIG_FILE = os.path.join(
    _vanilla_config_dir(), "vanilla_wow_launcher_config.json"
)
VANILLA_USER_CACHE_FILE = os.path.join(
    _vanilla_cache_dir(), "vanilla_wow_launcher_hash_cache.json"
)

# Superseded vanilla-era locations: next to the executable (Windows) and the
# XDG dir (Linux) before the vanilla-era per-user move.
VANILLA_APP_CONFIG_FILE = os.path.join(
    APP_DIR, "vanilla_wow_launcher_config.json"
)
VANILLA_APP_CACHE_FILE = os.path.join(
    APP_DIR, "vanilla_wow_launcher_hash_cache.json"
)


def _vanilla_xdg_config_file() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(
        base, "vanilla-wow-launcher", "vanilla_wow_launcher_config.json"
    )


def _vanilla_xdg_cache_file() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(
        base, "vanilla-wow-launcher", "vanilla_wow_launcher_hash_cache.json"
    )


# ── octo era (Octo Updater heritage) ────────────────────────────────────────

# Legacy location: the config/cache used to live next to the executable
# under the old "octo_updater" names. Migrated on first use.
LEGACY_CONFIG_FILE = os.path.join(APP_DIR, "octo_updater_config.json")
LEGACY_CACHE_FILE = os.path.join(APP_DIR, "octo_updater_hash_cache.json")


def _legacy_user_config_dir() -> str:
    """The pre-rename per-user config directory (old "octo-updater")."""
    if is_windows():
        return APP_DIR
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(
            home, "Library", "Application Support", "OctoUpdater"
        )
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "octo-updater")


def _legacy_user_cache_dir() -> str:
    """The pre-rename per-user cache directory (old "octo-updater")."""
    if is_windows():
        return APP_DIR
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(home, "Library", "Caches", "OctoUpdater")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    return os.path.join(base, "octo-updater")


LEGACY_USER_CONFIG_FILE = os.path.join(
    _legacy_user_config_dir(), "octo_updater_config.json"
)
LEGACY_USER_CACHE_FILE = os.path.join(
    _legacy_user_cache_dir(), "octo_updater_hash_cache.json"
)


# Ordered migration candidates, most recent location first: the first file
# that exists on disk is copied to the new per-user location on first run.
LEGACY_CONFIG_FILES = (
    VANILLA_USER_CONFIG_FILE,
    VANILLA_APP_CONFIG_FILE,
    _vanilla_xdg_config_file(),
    LEGACY_CONFIG_FILE,
    LEGACY_USER_CONFIG_FILE,
)
LEGACY_CACHE_FILES = (
    VANILLA_USER_CACHE_FILE,
    VANILLA_APP_CACHE_FILE,
    _vanilla_xdg_cache_file(),
    LEGACY_CACHE_FILE,
    LEGACY_USER_CACHE_FILE,
)


def legacy_config_dir() -> str:
    """The config directory used by the previous (vanilla-era) version, for
    migrating files that share it (e.g. the custom catalog files). Empty
    when the directory would be identical to the current one."""
    old = _vanilla_config_dir()
    return "" if old == config_dir() else old


def legacy_custom_pairs() -> tuple:
    """(legacy, new) path pairs for the custom catalog files that moved with
    the config directory (empty when it didn't move)."""
    old = legacy_config_dir()
    if not old:
        return ()
    pairs = []
    for kind in ("mods", "addons"):
        legacy = os.path.join(old, f"vanilla_wow_launcher_{kind}_custom.json")
        new = os.path.join(
            config_dir(), f"nostalgia_launcher_{kind}_custom.json"
        )
        if legacy != new:
            pairs.append((legacy, new))
    return tuple(pairs)


# First-run default game folder — a user-writable location.
DEFAULT_OUT_DIR = default_out_dir()

# News feed timings (endpoints come from the launcher configuration).
NEWS_TIMEOUT = 8
NEWS_CACHE_TTL = 300

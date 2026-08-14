"""App-level constants and filesystem paths shared across modules.

Vanilla WoW Launcher: the executable dir (APP_DIR) is next to the .exe when
frozen (PyInstaller), otherwise next to this file — never the current working
directory. On non-Windows platforms the persistent config/cache files live in
the OS-appropriate per-user directories (XDG on Linux, Application Support on
macOS) so the app works from read-only install locations.

Files from earlier versions are migrated on first run: the old next-to-exe
``octo_updater_config.json`` / ``octo_updater_hash_cache.json`` (LEGACY_*) and
the old per-user files under the pre-rename directories (LEGACY_USER_*).

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

UPDATER_VERSION  = "1.2"
DOWNLOAD_VERSION = "latest"
UA               = f"VanillaWoWLauncher/{UPDATER_VERSION}"
DOWNLOAD_RETRY   = 5
DOWNLOAD_TIMEOUT = 10    # seconds without any data before a transfer aborts

GITHUB_API = "https://api.github.com"
MOD_UA     = f"VanillaWoWLauncher/{UPDATER_VERSION}"

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # Running from source: the repo root (three levels up from
    # src/vanilla_wow_launcher/core/constants.py).
    APP_DIR = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))

CONFIG_FILE = os.path.join(config_dir(), "vanilla_wow_launcher_config.json")
CACHE_FILE  = os.path.join(cache_dir(), "vanilla_wow_launcher_hash_cache.json")

# Legacy location: the config/cache used to live next to the executable
# under the old "octo_updater" names. On Windows that's still the case;
# elsewhere we migrate on first use.
LEGACY_CONFIG_FILE = os.path.join(APP_DIR, "octo_updater_config.json")
LEGACY_CACHE_FILE  = os.path.join(APP_DIR, "octo_updater_hash_cache.json")


def _legacy_user_config_dir() -> str:
    """The pre-rename per-user config directory (old "octo-updater")."""
    if is_windows():
        return APP_DIR
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(
            home, "Library", "Application Support", "OctoUpdater")
    base = os.environ.get("XDG_CONFIG_HOME") \
        or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "octo-updater")


def _legacy_user_cache_dir() -> str:
    """The pre-rename per-user cache directory (old "octo-updater")."""
    if is_windows():
        return APP_DIR
    if is_macos():
        home = os.environ.get("HOME") or os.path.expanduser("~")
        return os.path.join(home, "Library", "Caches", "OctoUpdater")
    base = os.environ.get("XDG_CACHE_HOME") \
        or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "octo-updater")


LEGACY_USER_CONFIG_FILE = os.path.join(
    _legacy_user_config_dir(), "octo_updater_config.json")
LEGACY_USER_CACHE_FILE = os.path.join(
    _legacy_user_cache_dir(), "octo_updater_hash_cache.json")

# First-run default game folder — a user-writable location.
DEFAULT_OUT_DIR  = default_out_dir()

# News feed timings (endpoints come from the launcher configuration).
NEWS_TIMEOUT   = 8
NEWS_CACHE_TTL = 300

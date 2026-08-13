"""App-level constants and filesystem paths shared across modules.

The executable dir (APP_DIR) is next to the .exe when frozen (PyInstaller),
otherwise next to this file — never the current working directory. On
non-Windows platforms the persistent config/cache files live in the
OS-appropriate per-user directories (XDG on Linux, Application Support on
macOS) so the app works from read-only install locations.
"""

import os
import sys

from .platform_support import config_dir, cache_dir, default_out_dir

UPDATER_VERSION  = "1.2"
SERVER           = "https://octowow.st"
DOWNLOAD_VERSION = "latest"
UA               = f"OctoUpdater/{UPDATER_VERSION}"
DOWNLOAD_RETRY   = 5
DOWNLOAD_TIMEOUT = 10    # seconds without any data before a transfer aborts

GITHUB_API = "https://api.github.com"
MOD_UA     = f"OctoUpdater/{UPDATER_VERSION}"

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # Running from source: the repo root (three levels up from
    # src/octo_updater/core/constants.py).
    APP_DIR = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))

# Legacy location: the config/cache used to live next to the executable.
# On Windows that's still the case; elsewhere we migrate on first use.
LEGACY_CONFIG_FILE = os.path.join(APP_DIR, "octo_updater_config.json")
LEGACY_CACHE_FILE  = os.path.join(APP_DIR, "octo_updater_hash_cache.json")

CONFIG_FILE = os.path.join(config_dir(), "octo_updater_config.json")
CACHE_FILE  = os.path.join(cache_dir(), "octo_updater_hash_cache.json")

# First-run default game folder — a user-writable location.
DEFAULT_OUT_DIR  = default_out_dir()

# News feed: announcements come from the forum list endpoint.
NEWS_URL          = f"{SERVER}/forum/octonews.php?mode=list&forum=2&limit=8"
NEWS_FEATURED_URL = f"{SERVER}/forum/octonews.php?forum=35&mode=full"
NEWS_TIMEOUT      = 8
NEWS_CACHE_TTL    = 300

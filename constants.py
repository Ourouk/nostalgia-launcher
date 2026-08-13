"""App-level constants and filesystem paths shared across modules.

Computed once at import time: the app dir is next to the .exe when frozen
(PyInstaller), otherwise next to this file — never the current working
directory, which varies with how the app was launched.
"""

import os
import sys

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
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE      = os.path.join(APP_DIR, "octo_updater_config.json")
CACHE_FILE       = os.path.join(APP_DIR, "octo_updater_hash_cache.json")

# First-run default game folder, anchored to the app dir (not the CWD).
DEFAULT_OUT_DIR  = os.path.join(APP_DIR, "OctoWoW")

# News feed: announcements come from the forum list endpoint.
NEWS_URL          = f"{SERVER}/forum/octonews.php?mode=list&forum=2&limit=8"
NEWS_FEATURED_URL = f"{SERVER}/forum/octonews.php?forum=35&mode=full"
NEWS_TIMEOUT      = 8
NEWS_CACHE_TTL    = 300

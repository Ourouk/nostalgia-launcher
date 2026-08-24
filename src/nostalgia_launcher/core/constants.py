"""App-level constants and filesystem paths shared across modules.

Nostalgia Launcher: the executable dir (APP_DIR) is next to the .exe when
frozen (PyInstaller), otherwise next to this file — never the current working
directory. Persistent config/cache files live in OS-appropriate per-user
directories so the app works from read-only install locations: Linux config
in ~/.nostalgia-launcher, Windows config in %APPDATA%\\NostalgiaLauncher,
macOS config in ~/Library/Application Support; the hash cache is kept
separate (Linux XDG cache dir, %LOCALAPPDATA%, ~/Library/Caches).

There is deliberately no hardcoded server here: every endpoint (client
updates, news, mod/addon catalogs, realm, mirrors) comes from the launcher
configuration (`core/launcher.py`).
"""

import os
import sys

from .platform_support import (
    cache_dir,
    config_dir,
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
# Session log (see core/log_sink.py): appended by every run, rotated to
# launcher.log.old when it outgrows the size cap. Lives next to the config
# so diagnostics survive cache clears and can be printed via the CLI.
LOG_FILE = os.path.join(config_dir(), "launcher.log")

# News feed timings (endpoints come from the launcher configuration).
NEWS_TIMEOUT = 8
NEWS_CACHE_TTL = 300

"""App-level constants and filesystem paths shared across modules.

Persistent config/cache/log files live in OS-appropriate per-user
directories so the app works from read-only install locations: Linux config
in ~/.nostalgia-launcher, Windows config in %APPDATA%\\NostalgiaLauncher,
macOS config in ~/Library/Application Support; the hash cache is kept
separate (Linux XDG cache dir, %LOCALAPPDATA%, ~/Library/Caches).

There is deliberately no hardcoded server here: every endpoint (client
updates, news, mod/addon catalogs, realm, mirrors) comes from the launcher
configuration (`core/launcher.py`).
"""

import os

from .platform_support import config_dir

UPDATER_VERSION = "0.0.2"
UA = f"NostalgiaLauncher/{UPDATER_VERSION}"
DOWNLOAD_RETRY = 5
DOWNLOAD_TIMEOUT = 10  # seconds without any data before a transfer aborts

GITHUB_API = "https://api.github.com"


# Session log (see core/log_sink.py): appended by every run, rotated to
# launcher.log.old when it outgrows the size cap. Lives next to the config
# so diagnostics survive cache clears and can be printed via the CLI.
def log_file() -> str:
    """Live session-log path — computed via ``config_dir()`` so a
    ``HOME``/``APPDATA`` redirection is always reflected."""
    return os.path.join(config_dir(), "launcher.log")


# Deprecated alias — import-time frozen, use ``log_file()`` instead.
LOG_FILE = log_file()

# News feed timings (endpoints come from the launcher configuration).
NEWS_TIMEOUT = 8
NEWS_CACHE_TTL = 300

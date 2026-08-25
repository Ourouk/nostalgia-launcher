"""Atomic JSON persistence for the updater's config and hash cache.

All config changes go through `update_config()` so the read-modify-write
cycle stays under a lock (concurrent worker threads can't clobber each
other) and writes are atomic (temp file + rename, so a crash can't leave a
truncated file).
"""

import json
import sys
import threading

from .filesystem import atomic_write_text as _atomic_write

_CONFIG_LOCK = threading.RLock()

# Set by configure() at import time.
config_file: str = ""
cache_file: str = ""


def configure(
    cfg_file: str,
    cache: str,
):
    """Point the store at the on-disk config and hash-cache files."""
    global config_file, cache_file
    config_file = cfg_file
    cache_file = cache


def load_config() -> dict:
    try:
        with open(config_file) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        sys.stderr.write(f"[config] failed to read {config_file}: {e}\n")
        return {}


def save_config(data: dict):
    with _CONFIG_LOCK:
        if not config_file:
            return
        try:
            _atomic_write(config_file, json.dumps(data, indent=2))
        except Exception as e:
            sys.stderr.write(f"[config] failed to write {config_file}: {e}\n")


def update_config(mutator):
    """Load the current on-disk config under the lock, apply `mutator(cfg)`,
    save atomically, and return the result. Every config change — main thread
    or worker — should go through this so no stale in-memory snapshot can
    overwrite keys another thread just persisted."""
    with _CONFIG_LOCK:
        cfg = load_config()
        mutator(cfg)
        save_config(cfg)
        return cfg


def load_cache() -> dict:
    try:
        with open(cache_file) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        sys.stderr.write(f"[cache] failed to read {cache_file}: {e}\n")
        return {}


def save_cache(cache: dict):
    with _CONFIG_LOCK:
        if not cache_file:
            return
        try:
            _atomic_write(cache_file, json.dumps(cache))
        except Exception as e:
            sys.stderr.write(f"[cache] failed to write {cache_file}: {e}\n")

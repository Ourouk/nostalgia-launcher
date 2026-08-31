"""Per-profile advisory process lock — single-instance belt-and-braces.

Uses :mod:`filelock` for cross-process mutual exclusion on the active
profile's store. The lock file sits next to the state file as
``<stem>.lock`` (e.g. ``state.json`` -> ``state.lock``) and is managed by
filelock's platform-appropriate primitives (fcntl on POSIX, msvcrt on
Windows). The OS releases the lock on process death.
"""

import atexit
import hashlib
import os

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

_LOCK: FileLock | None = None
_GRACE_STEP_S = 0.25
_GRACE_STEPS = 12


class AcquireError(Exception):
    """Another process already holds this profile's store lock."""


def state_key(state_path: str) -> str:
    digest = hashlib.sha1(state_path.encode("utf-8")).hexdigest()
    return f"nostalgia-launcher-{digest[:12]}"


def lock_file_for(state_path: str) -> str:
    return os.path.splitext(state_path)[0] + ".lock"


def _acquire(path: str, timeout: float) -> None:
    global _LOCK
    if _LOCK is not None and _LOCK.is_locked:
        return
    if _LOCK is not None:
        _LOCK = None
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lock = FileLock(path)
    try:
        lock.acquire(timeout=timeout)
    except FileLockTimeout as e:
        raise AcquireError(
            f"another instance holds this profile's store ({path}): {e}"
        ) from e
    _LOCK = lock
    atexit.register(release_store_lock)


def acquire_store_lock(state_path: str) -> None:
    _acquire(lock_file_for(state_path), 0)


def release_store_lock() -> None:
    global _LOCK
    lock = _LOCK
    if lock is None:
        return
    _LOCK = None
    try:
        lock.release()
    except Exception:
        pass


def acquire_with_grace(
    state_path: str, step_s: float = _GRACE_STEP_S, steps: int = _GRACE_STEPS
) -> None:
    if os.environ.get("NOSTALGIA_RELAUNCH") != "1":
        acquire_store_lock(state_path)
        return
    _acquire(lock_file_for(state_path), step_s * steps)

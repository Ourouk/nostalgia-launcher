"""Per-profile advisory process lock — single-instance belt-and-braces.

`config_store`'s RLock is thread-local to one process; this module adds a
process-level mutex on the active profile's store so a second launcher
instance can never silently race it (even via exotic launch paths that
bypass the Qt single-instance guard). The lock file sits next to the
state file as ``<stem>.lock`` (e.g. ``state.json`` ->
``nostalgia_launcher.lock``) and is NEVER written to — only its byte 0 is
locked. The OS releases both the flock and the handle on process death,
so there is no pidfile rot and no cleanup-file semantics.

Windows uses ``msvcrt.locking(LK_NBLCK)`` on byte 0; every other platform
uses ``fcntl.flock(LOCK_EX | LOCK_NB)``. UI-free and PySide6-free.
"""

import atexit
import hashlib
import os
import sys
import time

# Module-side fd reference: closing the descriptor would drop the lock.
_LOCK_FD = None

# Switch & Restart grace window: 250 ms x 12 ≈ 3 s.
_GRACE_STEP_S = 0.25
_GRACE_STEPS = 12


class AcquireError(Exception):
    """Another process already holds this profile's store lock."""


def state_key(state_path: str) -> str:
    """Qt-server name derived from the state path (per-profile key for
    the QLocalServer single-instance guard)."""
    digest = hashlib.sha1(state_path.encode("utf-8")).hexdigest()
    return f"nostalgia-launcher-{digest[:12]}"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def lock_file_for(state_path: str) -> str:
    """Path of the lock file guarding ``state_path``."""
    return os.path.splitext(state_path)[0] + ".lock"


def _try_lock(fd):
    if _is_windows():
        import msvcrt

        # Lock exactly one byte at the current position (0): the file is
        # never written, the region is purely a mutex token.
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_and_close(fd):
    try:
        if _is_windows():
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass  # best-effort: the OS drops everything at close/death anyway
    finally:
        os.close(fd)


def acquire_store_lock(state_path: str):
    """Take the exclusive non-blocking advisory lock for ``state_path``.

    Raises AcquireError when another process holds it. Idempotent within
    this process (the holder just re-enters)."""
    global _LOCK_FD
    if _LOCK_FD is not None:
        return
    path = lock_file_for(state_path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        _try_lock(fd)
    except OSError as e:
        os.close(fd)
        raise AcquireError(
            f"another instance holds this profile's store ({path}): {e}"
        ) from e
    _LOCK_FD = fd
    atexit.register(release_store_lock)


def release_store_lock():
    """Drop the lock (idempotent; also registered with atexit)."""
    global _LOCK_FD
    fd = _LOCK_FD
    if fd is None:
        return
    _LOCK_FD = None
    _unlock_and_close(fd)


def holds_store_lock() -> bool:
    """Whether THIS process currently holds the store lock."""
    return _LOCK_FD is not None


def acquire_with_grace(
    state_path: str, step_s: float = _GRACE_STEP_S, steps: int = _GRACE_STEPS
):
    """acquire_store_lock, with a bounded retry window (~250 ms x 12) but
    ONLY when ``NOSTALGIA_RELAUNCH=1`` — set by the Switch & Restart flow
    so the detached child tolerates the quitting parent's still-held
    socket/lock. Without that env var: fail fast."""
    grace = os.environ.get("NOSTALGIA_RELAUNCH") == "1"
    attempt = 0
    while True:
        try:
            acquire_store_lock(state_path)
            return
        except AcquireError:
            if not grace or attempt >= steps - 1:
                raise
            attempt += 1
            time.sleep(step_s)

"""One thread-safe log sink for the whole app.

Any function — worker thread or main — calls `log()`; the GUI drains
`_LOG_Q` on the main thread and renders each line. This keeps all UI access
on the main thread without threading a log_fn argument through every
function.

Two optional mirrors:
- stdout, when `NOSTALGIA_DEBUG` is set (any value except 0/false/no), so a
  terminal-launched run prints diagnostics;
- the session-log file, once the entry point called `configure_file()` —
  every line then also lands in launcher.log (rotated to .old past the
  size cap) so a crashed session can be read back with
  `nostalgia-launcher --print-log`. Library use and tests never configure
  it, so they never touch disk.
"""

import os
import queue
import sys
import threading

from .constants import LOG_FILE

_LOG_Q: queue.Queue = queue.Queue()

# File sink — disabled until configure_file() sets the target path.
_sink_path: str | None = None
_MAX_BYTES = 512 * 1024
_lock = threading.Lock()


def debug_enabled() -> bool:
    """Whether stdout log mirroring is on (`NOSTALGIA_DEBUG` set and not a
    falsey value)."""
    v = os.environ.get("NOSTALGIA_DEBUG", "").strip().lower()
    return v not in ("", "0", "false", "no", "off")


def debug_emit(msg: str):
    """Mirror one log line to stdout in debug mode (best-effort)."""
    if not debug_enabled():
        return
    try:
        sys.stdout.write(msg if msg.endswith("\n") else msg + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def configure_file(path: str):
    """Point the file sink at `path`: every subsequent log() line appends
    there. Called once at startup by the CLI entry point; failures merely
    disable the sink."""
    global _sink_path
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _sink_path = path
    except Exception:
        _sink_path = None


def current_log_path() -> str:
    """Where read_lines() looks: the configured sink, else the default
    LOG_FILE (e.g. --print-log in a fresh process that never configured)."""
    return _sink_path or LOG_FILE


def _append_file(msg: str):
    """Append one line to the sink file, rotating current→.old past the
    size cap. Open/append/close per line: crash-tolerant and simple.
    Serialized by _lock; best-effort — never raise into callers."""
    try:
        path = current_log_path()
        if os.path.isfile(path) and os.path.getsize(path) > _MAX_BYTES:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(msg if msg.endswith("\n") else msg + "\n")
    except Exception:
        pass


def log(msg: str, tag: str = ""):
    """Append a line to the app log. Thread-safe; safe to call before the
    GUI exists (the queue just buffers until it's drained) and before or
    without configure_file() (the file mirror is simply skipped)."""
    _LOG_Q.put((msg, tag))
    debug_emit(msg)
    if _sink_path is not None:
        with _lock:
            _append_file(msg)


def read_lines(n: int | None = None) -> list[str]:
    """Retained session-log lines: the rotated .old file first, then the
    current one — everything when n is None, otherwise just the last n
    lines. Missing files contribute nothing; never raises."""
    base = current_log_path()
    chunks: list[str] = []
    for path in (base + ".old", base):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                chunks.extend(fh.read().splitlines())
        except OSError:
            continue
    if n is not None:
        if n <= 0:
            return []
        chunks = chunks[-n:]
    return chunks

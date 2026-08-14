"""One thread-safe log sink for the whole app.

Any function — worker thread or main — calls `log()`; the GUI drains
`_LOG_Q` on the main thread and renders each line. This keeps all UI access
on the main thread without threading a log_fn argument through every
function.

When `VANILLA_WOW_DEBUG` is set (any value except 0/false/no), `log()` also
mirrors every line to stdout so a terminal-launched run prints diagnostics.
"""

import os
import queue
import sys

_LOG_Q: queue.Queue = queue.Queue()


def debug_enabled() -> bool:
    """Whether stdout log mirroring is on (`VANILLA_WOW_DEBUG` set and not a
    falsey value)."""
    v = os.environ.get("VANILLA_WOW_DEBUG", "").strip().lower()
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


def log(msg: str, tag: str = ""):
    """Append a line to the app log. Thread-safe; safe to call before the GUI
    exists (the queue just buffers until it's drained)."""
    _LOG_Q.put((msg, tag))
    debug_emit(msg)

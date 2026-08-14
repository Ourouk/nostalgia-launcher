"""One thread-safe log sink for the whole app.

Any function — worker thread or main — calls `log()`; the GUI drains
`_LOG_Q` on the main thread and renders each line. This keeps all UI access
on the main thread without threading a log_fn argument through every
function.
"""

import queue

_LOG_Q: queue.Queue = queue.Queue()


def log(msg: str, tag: str = ""):
    """Append a line to the app log. Thread-safe; safe to call before the GUI
    exists (the queue just buffers until it's drained)."""
    _LOG_Q.put((msg, tag))

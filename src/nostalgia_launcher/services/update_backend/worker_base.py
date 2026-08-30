"""Shared plumbing for the update backend workers.

`WorkerBase` owns the out-dir + log/progress-queue wiring and the cancel
flag, plus the shared "is this local file already up to date" check used by
both manifest verification and incremental download.

Workers can be wired either via the legacy queue pair (log_q/prog_q) polled
by UpdateController, or directly via an EventDispatcher. During the
consolidation phase both are supported: when a dispatcher is supplied
`log()`/`progress()` post structured events straight to it *and* to the
queues so existing poll() paths keep working. New code should pass only
the dispatcher.
"""

import os
import queue

from ...core.filesystem import cached_sha1


class WorkerBase:
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue | None = None,
        prog_q: queue.Queue | None = None,
        *,
        dispatcher=None,
    ):
        # Back-compat: `WorkerBase(out_dir, dispatcher)` was used in a
        # brief migration window where the second positional was the
        # dispatcher.
        if (
            log_q is not None
            and prog_q is None
            and dispatcher is None
            and hasattr(log_q, "post")
            and hasattr(log_q, "drain")
        ):
            dispatcher = log_q
            log_q = None

        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._dispatcher = dispatcher
        self._cancel = False
        # Verify/update hash cache; subclasses load the real store. Kept
        # here so WorkerBase methods never depend on subclass init order.
        self._cache: dict = {}

    def cancel(self):
        self._cancel = True

    def log(self, msg, tag=""):
        if self._dispatcher is not None:
            try:
                from ...state.events import LogMessage

                self._dispatcher.post(LogMessage(msg, tag))
            except Exception:
                pass
        if self.log_q is not None:
            self.log_q.put((msg, tag))

    def progress(self, value, label="", **details):
        if self._dispatcher is not None:
            try:
                from ...state.events import ProgressChanged

                self._dispatcher.post(
                    ProgressChanged(
                        value,
                        label,
                        phase=details.get("phase", ""),
                        transport=details.get("transport", ""),
                        current_file=details.get("current_file", ""),
                        downloaded=details.get("downloaded", 0),
                        total=details.get("total", 0),
                        speed=details.get("speed", 0.0),
                        peers=details.get("peers", 0),
                        verified_pieces=details.get("verified_pieces", 0),
                        total_pieces=details.get("total_pieces", 0),
                    )
                )
            except Exception:
                pass
        if self.prog_q is not None:
            item = (value, label, details) if details else (value, label)
            self.prog_q.put(item)

    def file_matches(self, dest: str, expected_sha1: str) -> bool:
        """Whether a file can be skipped because the local copy exists and
        its cached SHA-1 matches the expected one."""
        if not os.path.exists(dest):
            return False
        return cached_sha1(dest, self._cache) == expected_sha1

    def _raise_cancelled(self, h):
        """Best-effort cancel of the torrent handle, then abort the worker's
        loop. Shared by the verifier recheck and downloader pump tails."""
        try:
            h.cancel()
        except Exception:
            pass
        raise RuntimeError("Cancelled")

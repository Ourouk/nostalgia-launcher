"""Shared plumbing for the update backend workers.

`WorkerBase` owns the out-dir + log/progress-queue wiring and the cancel
flag, plus the shared "is this local file already up to date" check used by
both manifest verification and incremental download.
"""

import os
import queue

from ...core.filesystem import cached_sha1


class WorkerBase:
    def __init__(
        self,
        out_dir: str,
        log_q: queue.Queue,
        prog_q: queue.Queue,
    ):
        self.out_dir = out_dir
        self.log_q = log_q
        self.prog_q = prog_q
        self._cancel = False
        # Verify/update hash cache; subclasses load the real store. Kept
        # here so WorkerBase methods never depend on subclass init order.
        self._cache: dict = {}

    def cancel(self):
        self._cancel = True

    def log(self, msg, tag=""):
        self.log_q.put((msg, tag))

    def progress(self, value, label="", **details):
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

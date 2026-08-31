"""Shared plumbing for the update backend workers.

`WorkerBase` owns the out-dir + dispatcher wiring and the cancel flag, plus
the shared "is this local file already up to date" check used by both
manifest verification and incremental download.

Workers post LogMessage/ProgressChanged and typed lifecycle events directly
to the shared EventDispatcher; the controller subscribes.
"""

import os

from ...core.filesystem import cached_sha1


class WorkerBase:
    def __init__(self, out_dir: str, dispatcher):
        self.out_dir = out_dir
        self._dispatcher = dispatcher
        self._cancel = False
        # Verify/update hash cache; subclasses load the real store. Kept
        # here so WorkerBase methods never depend on subclass init order.
        self._cache: dict = {}

    def cancel(self):
        self._cancel = True

    def log(self, msg, tag=""):
        from ...state.events import LogMessage

        self._dispatcher.post(LogMessage(msg, tag))

    def progress(self, value, label="", **details):
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

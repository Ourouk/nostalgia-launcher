"""Shared plumbing for the update backend workers.

`WorkerBase` owns the out-dir + dispatcher wiring and the cancel flag, plus
the shared "is this local file already up to date" check used by both
manifest verification and incremental download.

Workers post LogMessage/ProgressChanged and typed lifecycle events directly
to the shared EventDispatcher; the controller subscribes.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ...core.filesystem import cached_sha1

if TYPE_CHECKING:
    from ...state.events import EventDispatcher


class WorkerBase:
    def __init__(self, out_dir: str, dispatcher: EventDispatcher) -> None:
        import threading

        self.out_dir: str = out_dir
        self._dispatcher: EventDispatcher = dispatcher
        self._cancel_event = threading.Event()
        self._cancel_flag: bool = False
        # Verify/update hash cache; subclasses load the real store. Kept
        # here so WorkerBase methods never depend on subclass init order.
        self._cache: dict[str, object] = {}

    @property
    def _cancel(self) -> bool:
        return self._cancel_flag or self._cancel_event.is_set()

    @_cancel.setter
    def _cancel(self, value: bool) -> None:
        self._cancel_flag = bool(value)
        if value:
            self._cancel_event.set()
        else:
            self._cancel_event.clear()

    def cancel(self) -> None:
        self._cancel = True

    def is_cancelled(self) -> bool:
        return bool(self._cancel)

    @property
    def cancelled(self) -> bool:
        return bool(self._cancel)

    def log(self, msg: str, tag: str = "") -> None:
        from ...state.events import LogMessage

        self._dispatcher.post(LogMessage(msg, tag))

    def progress(
        self,
        value: float,
        label: str = "",
        **details: object,
    ) -> None:
        from ...state.events import ProgressChanged

        def _str(key: str) -> str:
            v = details.get(key, "")
            return v if isinstance(v, str) else ""

        def _int(key: str) -> int:
            v = details.get(key, 0)
            return v if isinstance(v, int) and not isinstance(v, bool) else 0

        def _float(key: str) -> float:
            v = details.get(key, 0.0)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
            return 0.0

        self._dispatcher.post(
            ProgressChanged(
                value,
                label,
                phase=_str("phase"),
                transport=_str("transport"),
                current_file=_str("current_file"),
                downloaded=_int("downloaded"),
                total=_int("total"),
                speed=_float("speed"),
                peers=_int("peers"),
                verified_pieces=_int("verified_pieces"),
                total_pieces=_int("total_pieces"),
            )
        )

    def file_matches(self, dest: str, expected_sha1: str) -> bool:
        """Whether a file can be skipped because the local copy exists and
        its cached SHA-1 matches the expected one."""
        if not os.path.exists(dest):
            return False
        return cached_sha1(dest, self._cache) == expected_sha1

    def _raise_cancelled(self, h: object) -> None:
        """Best-effort cancel of the torrent handle, then abort the worker's
        loop. Shared by the verifier recheck and downloader pump tails."""
        cancel = getattr(h, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass
        raise RuntimeError("Cancelled")

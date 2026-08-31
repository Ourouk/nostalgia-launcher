"""Thread-safe UI event channel.

A small, toolkit-agnostic event bus that worker threads use to talk to the
interface. Standard library only — no GUI toolkit. The UI thread drains
events once per event-loop tick and forwards them to the registered
handlers; `ui.qt.bridge.ControllerBridge` converts them into Qt signals.
"""

from __future__ import annotations

import queue
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import AddonsState, AssetsState, ModsState, NewsResult


class Event:
    """Base class for every event the dispatcher carries."""


@dataclass
class StatusChanged(Event):
    """The footer status line shown in the main window."""

    text: str


@dataclass
class LogMessage(Event):
    """One session-log line (text, tag)."""

    text: str
    tag: str = ""


@dataclass
class ProgressChanged(Event):
    """Progress-bar value in 0..1 plus the label shown above it."""

    value: float
    label: str = ""
    phase: str = ""
    transport: str = ""
    current_file: str = ""
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0
    peers: int = 0
    verified_pieces: int = 0
    total_pieces: int = 0


@dataclass
class NewsLoaded(Event):
    """News-feed snapshot. kind is "featured" or "items"."""

    kind: str
    data: NewsResult | None = None


@dataclass
class ModsLoaded(Event):
    """MODS panel snapshot (ui_state.ModsState)."""

    state: ModsState | None = None


@dataclass
class AssetsLoaded(Event):
    """ASSETS panel snapshot (state.models.AssetsState)."""

    state: AssetsState | None = None


@dataclass
class AddonsLoaded(Event):
    """ADDONS panel snapshot (ui_state.AddonsState)."""

    state: AddonsState | None = None


@dataclass
class MirrorStatusChanged(Event):
    """Download-mirror reachability result (Settings modal label)."""

    ok: bool
    text: str


@dataclass
class OperationFinished(Event):
    """A worker operation completed successfully."""

    kind: str
    ok: bool
    message: str = ""


@dataclass
class OperationFailed(Event):
    """A worker operation raised before it could report success."""

    kind: str
    message: str = ""


@dataclass
class UpdateFilesList(Event):
    """List of files identified as updated (from diff tree or torrent stale set)."""

    files: list[str] = field(default_factory=list)


@dataclass
class GameLaunched(Event):
    """A game process was started by the launcher (umu on Linux)."""

    pid: int
    pgid: int


@dataclass
class GameExited(Event):
    """A game process launched by the launcher has ended."""

    pid: int
    exit_code: int | None = None


# ── Update lifecycle (torrent-primary, HTTP fallback) ─────
# Workers post these directly to the shared EventDispatcher; UpdateController
# subscribes and mutates UpdateState.


@dataclass
class VerificationUpToDate(Event):
    pass


@dataclass
class UpdateRequired(Event):
    pass


@dataclass
class TorrentReachable(Event):
    """BitTorrent snapshot is reachable."""


@dataclass
class TorrentUnavailable(Event):
    """BitTorrent snapshot cannot be fetched."""

    message: str = ""


@dataclass
class TorrentCorrupt(Event):
    """BitTorrent snapshot is malformed."""

    message: str = ""


@dataclass
class TorrentStalled(Event):
    """BitTorrent verification stalled (no/low peers)."""

    message: str = ""


@dataclass
class TorrentSessionError(Event):
    """libtorrent session creation failed."""

    message: str = ""


@dataclass
class TorrentDiskError(Event):
    """Disk I/O error during torrent verify/download."""

    message: str = ""


@dataclass
class TorrentVerifyFailed(Event):
    """Generic torrent verification failure."""

    message: str = ""


@dataclass
class TorrentDiffReady(Event):
    """Torrent verify found stale files (replaces DIFF + TORRENT_DIFF)."""

    stale: list[str] = field(default_factory=list)


@dataclass
class TorrentUpToDate(Event):
    """Client matches BitTorrent snapshot."""


@dataclass
class TorrentRecoveryDone(Event):
    """Manifest-less BitTorrent recovery completed."""


@dataclass
class ClientVersionReady(Event):
    """WoW.exe version read after update."""

    version: str = ""


@dataclass
class UpdateCompleted(Event):
    """Incremental update or recovery completed."""

    version: str | None = None


@dataclass
class UpdateFailed(Event):
    """Update/verify/download failed or cancelled."""

    message: str = ""
    op: str = "update"


class EventDispatcher:
    """Thread-safe, non-blocking event bus.

    Workers call post() from any thread; the UI thread calls drain() (or
    dispatch_all()) once per event-loop tick. A single lock guards both the
    queue and the handler list, so concurrent post/drain/subscribe calls can
    never lose an event or corrupt the handler set.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[Event] = queue.Queue()
        self._handlers: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()

    def post(self, event: Event) -> None:
        """Enqueue an event. Never blocks; safe from any thread."""
        self._queue.put_nowait(event)

    def drain(self) -> list[Event]:
        """Return every pending event, in post order, without blocking."""
        events: list[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return events

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        """Register a handler to receive events via dispatch_all().
        Duplicate registrations of the same handler are ignored."""
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def unsubscribe(self, handler: Callable[[Event], None]) -> None:
        """Remove a handler; an unknown handler is a no-op."""
        with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def dispatch_all(
        self, handler: Callable[[Event], None] | None = None
    ) -> list[Event]:
        """Drain the pending events and deliver each to `handler`, or to
        every subscribed handler when omitted. Events posted during delivery
        stay queued for the next call. Returns the dispatched events. A
        raising handler never strands its siblings: each event/handler pair
        is delivered independently and the failure is reported on stderr
        without posting back into the dispatcher (avoids a self-sustaining
        LogMessage loop when the log handler itself is broken)."""
        import sys

        events = self.drain()
        if not events:
            return events

        handlers: list[Callable[[Event], None]] = []
        if handler is None:
            with self._lock:
                handlers = list(self._handlers)

        def _safe_call(fn: Callable[[Event], None], event: Event) -> None:
            try:
                fn(event)
            except Exception as e:
                print(
                    f"Event handler failed for {event!r}: {e}",
                    file=sys.stderr,
                )
                traceback.print_exc()

        for event in events:
            if handler is not None:
                _safe_call(handler, event)
            else:
                for h in handlers:
                    _safe_call(h, event)
        return events

    def __len__(self) -> int:
        return self._queue.qsize()

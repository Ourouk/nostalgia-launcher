"""News feed controller.

Phase 1b of the PySide6 migration: owns the news-feed fetch logic that
OctoUpdaterApp used to drive directly — TTL caching (NEWS_CACHE_TTL), the
background fetch threads, and the "Couldn't reach the news feed." error path.
Publishes snapshots as NewsLoaded events on the shared EventDispatcher; the
Tk/Qt panels render them. No tkinter, no Qt.
"""

import threading
import time
from dataclasses import dataclass

from constants import NEWS_CACHE_TTL
from news import fetch_featured_post, fetch_news_items
from ui_events import EventDispatcher, NewsLoaded
from ui_state import NewsState


@dataclass
class NewsResult:
    """One render snapshot carried by a NewsLoaded event.

    `data` is the post dict (featured) or items list (announcements) — None
    while still loading or when the fetch failed. `loading` and `error` let
    the renderer show the same placeholder/error states as the Tk app.
    """
    data: object = None
    loading: bool = False
    error: str = ""


class NewsController:
    """Owns the news-feed lifecycle; speaks to the UI only through events."""

    def __init__(self, dispatcher: EventDispatcher):
        self._dispatcher = dispatcher
        self.state = NewsState()

    def load(self, force: bool = False):
        self.refresh_featured(force)
        self.refresh_announcements(force)

    def refresh_featured(self, force: bool = False):
        now = time.time()
        if (not force and self.state.featured is not None
                and (now - self.state.feat_ts) < NEWS_CACHE_TTL):
            return
        self._dispatcher.post(NewsLoaded("featured", NewsResult(loading=True)))

        def worker():
            feat, err = None, ""
            try:
                feat = fetch_featured_post()
            except Exception:
                err = "Couldn't reach the news feed."
            self.state.feat_ts = time.time()
            self.state.featured = feat
            self._dispatcher.post(
                NewsLoaded("featured", NewsResult(data=feat, error=err)))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_announcements(self, force: bool = False):
        now = time.time()
        if (not force and self.state.items is not None
                and (now - self.state.news_ts) < NEWS_CACHE_TTL):
            return
        self._dispatcher.post(NewsLoaded("items", NewsResult(loading=True)))

        def worker():
            items, err = None, ""
            try:
                items = fetch_news_items()
            except Exception:
                err = "Couldn't reach the news feed."
            self.state.news_ts = time.time()
            self.state.items = items
            self._dispatcher.post(
                NewsLoaded("items", NewsResult(data=items, error=err)))

        threading.Thread(target=worker, daemon=True).start()

    def invalidate(self):
        """Drop the TTL timestamps (game folder changed) so the next load
        refetches; the cached data stays visible until then."""
        self.state.feat_ts = 0.0
        self.state.news_ts = 0.0

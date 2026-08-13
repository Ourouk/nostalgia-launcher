"""Unit tests for the news controller (news_controller).

No Tk involved: fetch_featured_post/fetch_news_items are swapped for fakes via
monkeypatch and the controller's effects are read from the shared
EventDispatcher and its NewsState.
"""

import threading
import time

import pytest

import news_controller as nc
from news_controller import NewsController
from ui_events import EventDispatcher, LogMessage, NewsLoaded


class FakeFeed:
    """Injected news fetchers with a per-kind call counter.

    A shared `gate` Event lets tests hold the fetchers mid-flight so the
    loading placeholder can be observed before the result lands.
    """

    calls = {"featured": 0, "items": 0}
    featured = {"id": 1, "title": "Post", "html": "<p>hi</p>", "date": "2026-01-01"}
    items = [{"id": 1, "title": "Item", "date": "2026-01-01T00:00:00+01:00"}]
    fail = False
    gate = threading.Event()


@pytest.fixture
def feed(monkeypatch):
    FakeFeed.calls = {"featured": 0, "items": 0}
    FakeFeed.fail = False
    FakeFeed.gate = threading.Event()

    def fake_featured():
        FakeFeed.calls["featured"] += 1
        FakeFeed.gate.wait(2.0)
        if FakeFeed.fail:
            raise ConnectionError("offline")
        return FakeFeed.featured

    def fake_items():
        FakeFeed.calls["items"] += 1
        FakeFeed.gate.wait(2.0)
        if FakeFeed.fail:
            raise ConnectionError("offline")
        return FakeFeed.items

    monkeypatch.setattr(nc, "fetch_featured_post", fake_featured)
    monkeypatch.setattr(nc, "fetch_news_items", fake_items)
    return FakeFeed


@pytest.fixture
def controller(feed):
    return NewsController(EventDispatcher())


def _collect(controller, kinds=("featured", "items"), timeout=2.0, release=True):
    """Release the fetch gate (unless release=False), then drain until a
    non-loading NewsLoaded has arrived for every `kind`. Returns every event
    collected along the way (loading + results)."""
    if release:
        FakeFeed.gate.set()
    collected = []
    deadline = time.monotonic() + timeout
    while True:
        collected.extend(controller._dispatcher.drain())
        got = {e.kind for e in collected
               if isinstance(e, NewsLoaded) and not e.data.loading}
        if all(k in got for k in kinds):
            return collected
        if time.monotonic() > deadline:
            raise AssertionError(
                f"news fetches never completed; got {got!r}")
        time.sleep(0.005)


def _wait_len(dispatcher, n, timeout=2.0):
    deadline = time.monotonic() + timeout
    while len(dispatcher) < n:
        if time.monotonic() > deadline:
            raise AssertionError("expected events never arrived")
        time.sleep(0.005)


def _results(collected):
    return {e.kind: e.data for e in collected
            if isinstance(e, NewsLoaded) and not e.data.loading}


# ── load flow ───────────────────────────────────────────────────────────

def test_load_posts_loading_events_first(controller, feed):
    controller.load()
    events = controller._dispatcher.drain()
    assert sorted(e.kind for e in events) == ["featured", "items"]
    assert all(e.data.loading for e in events)
    feed.gate.set()
    _collect(controller, release=False)


def test_load_posts_result_events_and_state(controller, feed):
    controller.load()
    collected = _collect(controller)
    results = _results(collected)
    assert results["featured"].data == feed.featured
    assert results["featured"].loading is False
    assert results["featured"].error == ""
    assert results["items"].data == feed.items
    assert controller.state.featured == feed.featured
    assert controller.state.items == feed.items
    assert controller.state.feat_ts > 0.0
    assert controller.state.news_ts > 0.0


# ── TTL caching ─────────────────────────────────────────────────────────

def test_refresh_within_ttl_uses_cache(controller, feed):
    controller.load()
    _collect(controller)
    assert feed.calls["featured"] == 1
    assert feed.calls["items"] == 1

    controller.refresh_featured()
    controller.refresh_announcements()
    assert feed.calls["featured"] == 1
    assert feed.calls["items"] == 1
    assert controller._dispatcher.drain() == []


def test_force_bypasses_cache(controller, feed):
    controller.load()
    _collect(controller)
    assert feed.calls["featured"] == 1

    feed.gate.clear()
    controller.refresh_featured(force=True)
    events = controller._dispatcher.drain()
    assert [e.data.loading for e in events if e.kind == "featured"] == [True]

    collected = _collect(controller, kinds=("featured",))
    assert _results(collected)["featured"].data == feed.featured
    assert feed.calls["featured"] == 2


def test_invalidate_resets_ttl(controller, feed):
    controller.load()
    _collect(controller)
    assert feed.calls["featured"] == 1

    controller.invalidate()
    controller.refresh_featured()
    controller.refresh_announcements()
    _collect(controller)
    assert feed.calls["featured"] == 2
    assert feed.calls["items"] == 2


# ── error path / offline behavior ───────────────────────────────────────

def test_error_sets_failure_state(controller, feed):
    feed.fail = True
    controller.load()
    collected = _collect(controller)
    results = _results(collected)
    assert results["featured"].data is None
    assert results["featured"].error == "Couldn't reach the news feed."
    assert results["featured"].loading is False
    assert results["items"].data is None
    assert results["items"].error == "Couldn't reach the news feed."
    assert controller.state.featured is None
    assert controller.state.items is None


def test_error_path_only_posts_news_events(controller, feed):
    """app.py never logged news failures, so the controller must not either."""
    feed.fail = True
    controller.load()
    collected = _collect(controller)
    assert all(isinstance(e, NewsLoaded) for e in collected)
    assert not any(isinstance(e, LogMessage) for e in collected)


def test_none_result_is_not_loading(controller, feed):
    feed.featured = None
    controller.refresh_featured()
    collected = _collect(controller, kinds=("featured",))
    res = _results(collected)["featured"]
    assert res.data is None
    assert res.loading is False
    assert res.error == ""


# ── event delivery ──────────────────────────────────────────────────────

def test_events_delivered_to_subscribers(controller, feed):
    got = []
    controller._dispatcher.subscribe(got.append)
    controller.refresh_featured()
    feed.gate.set()
    _wait_len(controller._dispatcher, 2)
    controller._dispatcher.dispatch_all()
    featured = [e for e in got if isinstance(e, NewsLoaded) and e.kind == "featured"]
    assert len(featured) == 2
    assert featured[0].data.loading is True
    assert featured[1].data.loading is False
    assert featured[1].data.data == feed.featured

"""Unit tests for the thread-safe UI event dispatcher (ui_events)."""

import threading

from ui_events import (
    AddonsLoaded,
    Event,
    EventDispatcher,
    LogMessage,
    ModsLoaded,
    NewsLoaded,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)


# ── post / drain ─────────────────────────────────────────────────────────

def test_drain_empty_when_nothing_posted():
    d = EventDispatcher()
    assert d.drain() == []
    assert len(d) == 0


def test_post_drain_preserves_order():
    d = EventDispatcher()
    d.post(StatusChanged("Verifying…"))
    d.post(LogMessage("line", "ok"))
    d.post(ProgressChanged(0.5, "Downloading…"))
    events = d.drain()
    assert len(events) == 3
    assert events[0] == StatusChanged("Verifying…")
    assert events[1] == LogMessage("line", "ok")
    assert isinstance(events[2], ProgressChanged)
    assert events[2].value == 0.5
    assert events[2].label == "Downloading…"
    assert d.drain() == []
    assert len(d) == 0


def test_typed_events_carry_payloads():
    assert isinstance(StatusChanged("x"), Event)
    assert isinstance(LogMessage("x", "ok"), Event)
    assert isinstance(ProgressChanged(0.1, "y"), Event)

    news = NewsLoaded("featured", {"id": 1})
    assert news.kind == "featured"
    assert news.data == {"id": 1}

    mods = ModsLoaded(state=None)
    addons = AddonsLoaded(state=None)
    assert isinstance(mods, Event) and isinstance(addons, Event)

    fin = OperationFinished("verify", True, "up to date")
    assert fin.kind == "verify"
    assert fin.ok is True
    assert fin.message == "up to date"
    fail = OperationFailed("update", "hash mismatch")
    assert fail.kind == "update"
    assert fail.message == "hash mismatch"
    assert isinstance(fin, Event) and isinstance(fail, Event)


# ── thread safety ─────────────────────────────────────────────────────────

def test_post_from_many_threads_then_drain_sees_all():
    d = EventDispatcher()

    def post_batch(n):
        for i in range(n):
            d.post(LogMessage(f"line {i}"))

    threads = [threading.Thread(target=post_batch, args=(100,))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events = d.drain()
    assert len(events) == 800
    assert all(isinstance(e, LogMessage) for e in events)
    assert d.drain() == []


def test_concurrent_post_and_drain_every_event_accounted_for():
    d = EventDispatcher()
    n = 1000
    consumed = []
    stop = threading.Event()

    def consumer():
        while not stop.is_set():
            consumed.extend(d.drain())

    cons = threading.Thread(target=consumer)
    cons.start()
    for i in range(n):
        d.post(LogMessage(f"m{i}"))
    stop.set()
    cons.join()
    remaining = d.drain()
    # Nothing is ever lost: each event is either drained by the consumer
    # thread or still sitting in the queue.
    assert len(consumed) + len(remaining) == n


def test_concurrent_post_and_drain_no_deadlock():
    d = EventDispatcher()
    n = 500
    errors = []

    def producer():
        try:
            for i in range(n):
                d.post(ProgressChanged(i / n, f"p{i}"))
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    def consumer():
        try:
            for _ in range(50):
                d.drain()
        except Exception as e:  # pragma: no cover - failure path
            errors.append(e)

    prods = [threading.Thread(target=producer) for _ in range(4)]
    cons = [threading.Thread(target=consumer) for _ in range(4)]
    for t in prods + cons:
        t.start()
    for t in prods + cons:
        t.join()
    assert errors == []


# ── subscribe / unsubscribe ──────────────────────────────────────────────

def test_subscribe_and_dispatch_all():
    d = EventDispatcher()
    got = []
    d.subscribe(lambda e: got.append(e))
    d.post(StatusChanged("a"))
    d.post(StatusChanged("b"))
    returned = d.dispatch_all()
    assert got == [StatusChanged("a"), StatusChanged("b")]
    assert returned == [StatusChanged("a"), StatusChanged("b")]


def test_subscribe_twice_dispatches_once():
    d = EventDispatcher()
    got = []
    handler = lambda e: got.append(e)
    d.subscribe(handler)
    d.subscribe(handler)
    d.post(StatusChanged("a"))
    d.dispatch_all()
    assert got == [StatusChanged("a")]


def test_unsubscribe_stops_delivery():
    d = EventDispatcher()
    got = []
    handler = lambda e: got.append(e)
    d.subscribe(handler)
    d.post(StatusChanged("a"))
    d.dispatch_all()
    assert got == [StatusChanged("a")]
    d.unsubscribe(handler)
    d.post(StatusChanged("b"))
    d.dispatch_all()
    assert got == [StatusChanged("a")]
    d.unsubscribe(handler)   # unknown handler is a no-op


def test_dispatch_all_with_explicit_handler_ignores_subscribers():
    d = EventDispatcher()
    subscribed = []
    d.subscribe(lambda e: subscribed.append(e))
    direct = []
    d.post(LogMessage("x", "ok"))
    d.dispatch_all(lambda e: direct.append(e))
    assert direct == [LogMessage("x", "ok")]
    assert subscribed == []
    d.post(LogMessage("y"))
    d.dispatch_all()
    assert subscribed == [LogMessage("y")]


def test_dispatch_all_with_no_pending_events():
    d = EventDispatcher()
    got = []
    d.subscribe(lambda e: got.append(e))
    assert d.dispatch_all() == []
    assert got == []


def test_event_posted_during_dispatch_waits_for_next_round():
    d = EventDispatcher()
    got = []

    def handler(e):
        got.append(e)
        if isinstance(e, StatusChanged):
            d.post(LogMessage("nested"))

    d.subscribe(handler)
    d.post(StatusChanged("first"))
    d.dispatch_all()
    assert got == [StatusChanged("first")]
    d.dispatch_all()
    assert got == [StatusChanged("first"), LogMessage("nested")]

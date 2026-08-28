"""Unit tests for the news feed module."""

import json

import nostalgia_launcher.core.launcher as launcher
import nostalgia_launcher.services.news as news


def _fake_resp(payload):
    class R:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=-1):
            data = self._data
            self._data = b""
            return data

    return R(payload)


def _mock_explicit(monkeypatch, explicit=True):
    """Mock the explicit URL check functions."""
    monkeypatch.setattr(launcher, "news_url_explicit", lambda: explicit)
    monkeypatch.setattr(
        launcher, "featured_news_url_explicit", lambda: explicit
    )


def test_fetch_news_items_sorts_newest_first(monkeypatch):
    _mock_explicit(monkeypatch, True)
    payload = json.dumps(
        {
            "items": [
                {"id": 2, "date": "2026-01-02T00:00:00+00:00", "title": "old"},
                {"id": 1, "date": "2026-01-03T00:00:00+00:00", "title": "new"},
            ]
        }
    ).encode()
    monkeypatch.setattr(
        news, "secure_urlopen", lambda *a, **k: _fake_resp(payload)
    )
    items = news.fetch_news_items()
    assert [i["id"] for i in items] == [1, 2]


def test_fetch_featured_post_returns_dict_with_id(monkeypatch):
    _mock_explicit(monkeypatch, True)
    payload = json.dumps({"id": 42, "title": "T"}).encode()
    monkeypatch.setattr(
        news, "secure_urlopen", lambda *a, **k: _fake_resp(payload)
    )
    assert news.fetch_featured_post() == {"id": 42, "title": "T"}


def test_fetch_featured_post_none_when_no_id(monkeypatch):
    _mock_explicit(monkeypatch, True)
    payload = json.dumps({"title": "no id"}).encode()
    monkeypatch.setattr(
        news, "secure_urlopen", lambda *a, **k: _fake_resp(payload)
    )
    assert news.fetch_featured_post() is None


def test_fetch_news_items_empty_when_not_explicit(monkeypatch):
    """When news_url is not explicit, fetch_news_items returns empty list
    without calling the network."""
    _mock_explicit(monkeypatch, False)
    called = []

    def fail(*a, **k):
        called.append(True)
        raise AssertionError("Should not call secure_urlopen")

    monkeypatch.setattr(news, "secure_urlopen", fail)
    items = news.fetch_news_items()
    assert items == []
    assert not called


def test_fetch_featured_post_none_when_not_explicit(monkeypatch):
    """When featured_news_url is not explicit, fetch_featured_post returns
    None without calling the network."""
    _mock_explicit(monkeypatch, False)
    called = []

    def fail(*a, **k):
        called.append(True)
        raise AssertionError("Should not call secure_urlopen")

    monkeypatch.setattr(news, "secure_urlopen", fail)
    result = news.fetch_featured_post()
    assert result is None
    assert not called

"""Unit tests for the news feed module."""

import json

import news


def _fake_resp(payload):
    return type("R", (), {
        "__enter__": lambda s: s,
        "__exit__": lambda *a: False,
        "read": lambda s=0: payload,
    })()


def test_fetch_news_items_sorts_newest_first(monkeypatch):
    payload = json.dumps({"items": [
        {"id": 2, "date": "2026-01-02T00:00:00+00:00", "title": "old"},
        {"id": 1, "date": "2026-01-03T00:00:00+00:00", "title": "new"},
    ]}).encode()
    monkeypatch.setattr(news, "secure_urlopen",
                        lambda *a, **k: _fake_resp(payload))
    items = news.fetch_news_items()
    assert [i["id"] for i in items] == [1, 2]


def test_fetch_featured_post_returns_dict_with_id(monkeypatch):
    payload = json.dumps({"id": 42, "title": "T"}).encode()
    monkeypatch.setattr(news, "secure_urlopen",
                        lambda *a, **k: _fake_resp(payload))
    assert news.fetch_featured_post() == {"id": 42, "title": "T"}


def test_fetch_featured_post_none_when_no_id(monkeypatch):
    payload = json.dumps({"title": "no id"}).encode()
    monkeypatch.setattr(news, "secure_urlopen",
                        lambda *a, **k: _fake_resp(payload))
    assert news.fetch_featured_post() is None

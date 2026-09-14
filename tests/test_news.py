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


RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>ChromieCraft</title>
<item>
  <title>Older post</title>
  <link>https://chromiecraft.com/2026/08/old/</link>
  <pubDate>Mon, 03 Aug 2026 10:00:00 +0000</pubDate>
  <dc:creator>Admin</dc:creator>
  <guid>https://chromiecraft.com/?p=1</guid>
  <description><![CDATA[<p>Old body</p>]]></description>
</item>
<item>
  <title>Top Contributors of August 2026</title>
  <link>https://chromiecraft.com/2026/09/top/</link>
  <pubDate>Thu, 03 Sep 2026 12:00:00 +0000</pubDate>
  <dc:creator>Herby</dc:creator>
  <guid>https://chromiecraft.com/?p=2</guid>
  <content:encoded><![CDATA[<p>New body</p>]]></content:encoded>
</item>
</channel></rss>"""


def test_fetch_news_items_rss_fallback(monkeypatch):
    """A WordPress RSS feed (e.g. ChromieCraft's /en/feed/) parses into
    news-shaped items, newest first — no JSONDecodeError."""
    _mock_explicit(monkeypatch, True)
    monkeypatch.setattr(
        news, "secure_urlopen", lambda *a, **k: _fake_resp(RSS_SAMPLE)
    )
    items = news.fetch_news_items()
    assert len(items) == 2
    assert items[0]["title"] == "Top Contributors of August 2026"
    assert items[0]["author"] == "Herby"
    assert items[0]["date"].startswith("2026-09-03")
    assert items[0]["url"] == "https://chromiecraft.com/2026/09/top/"
    assert "New body" in items[0]["body"]
    assert "<p>" not in items[0]["body"]


def test_fetch_featured_post_rss_fallback(monkeypatch):
    """An RSS featured URL yields the newest item with raw html."""
    _mock_explicit(monkeypatch, True)
    monkeypatch.setattr(
        news, "secure_urlopen", lambda *a, **k: _fake_resp(RSS_SAMPLE)
    )
    post = news.fetch_featured_post()
    assert post is not None
    assert post["title"] == "Top Contributors of August 2026"
    assert post["id"] == "https://chromiecraft.com/?p=2"
    assert "<p>New body</p>" in post["html"]
    assert post["url"] == "https://chromiecraft.com/2026/09/top/"


def test_fetch_news_items_garbage_stays_empty(monkeypatch):
    """A non-JSON, non-XML body still degrades to empty, never a crash."""
    _mock_explicit(monkeypatch, True)
    monkeypatch.setattr(
        news, "secure_urlopen", lambda *a, **k: _fake_resp(b"<html>nope")
    )
    assert news.fetch_news_items() == []
    assert news.fetch_featured_post() is None

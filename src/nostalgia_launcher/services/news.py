"""News feed: announcements list and featured forum post.

Endpoints come from the launcher configuration (`core/launcher.py`).
JSON is the primary contract, but a WordPress-style RSS/Atom feed is
accepted too (auto-detected) — several communities only publish RSS.
"""

import json
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ..core import launcher
from ..core.constants import NEWS_TIMEOUT, UA
from ..core.helpers import strip_html
from ..core.log_sink import log
from ..core.security_http import read_capped, secure_urlopen


def _local(tag: str) -> str:
    """An ElementTree tag without its namespace."""
    return tag.rpartition("}")[2] if "}" in tag else tag


def _child_text(entry, *names: str) -> str:
    """First matching direct-child text (namespace-insensitive)."""
    for child in entry:
        if _local(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _rss_date(text: str) -> str:
    """An RSS pubDate/updated value as ISO8601; the raw string on failure
    so sorting never crashes on an unparseable date."""
    if not text:
        return ""
    try:
        return parsedate_to_datetime(text.strip()).isoformat()
    except (TypeError, ValueError):
        return text.strip()


def _parse_rss_items(raw: bytes) -> list:
    """RSS 2.0 <item> / Atom <entry> elements → news-shaped dicts
    [{id, title, date, body, url, author}, …]. stdlib ElementTree never
    resolves external entities, and the payload is already size-capped."""
    root = ET.fromstring(raw)
    entries = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    items = []
    for entry in entries:
        link = _child_text(entry, "link")
        if not link:
            # Atom links live in href="…" instead of text.
            for child in entry:
                if _local(child.tag) == "link" and child.get("href"):
                    link = child.get("href", "").strip()
                    break
        guid = _child_text(entry, "guid", "id")
        html = _child_text(entry, "encoded", "content") or _child_text(
            entry, "description", "summary"
        )
        items.append(
            {
                "id": guid or link,
                "title": _child_text(entry, "title"),
                "date": _rss_date(
                    _child_text(entry, "pubDate", "published", "updated")
                ),
                "body": strip_html(html),
                "url": link,
                "author": _child_text(entry, "creator", "author", "name"),
                "html": html,
            }
        )
    return items


def _is_rss(raw: bytes) -> bool:
    """Sniff an RSS/Atom payload: XML declaration or a feed root tag."""
    head = raw[:512].lstrip()
    return (
        head.startswith(b"<?xml")
        or head.startswith(b"<rss")
        or (head.startswith(b"<feed"))
    )


def _fetch_payload(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with secure_urlopen(req, timeout=NEWS_TIMEOUT) as r:
        return read_capped(r, 1 * 1024 * 1024)


def _decode_items(raw: bytes, url: str) -> list:
    """Payload bytes → news-shaped items, JSON first with RSS fallback."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict):
        # A shape-broken feed degrades to "no news" — never a crash in
        # the fetch path that the controller would misreport as network
        # failure.
        items = [it for it in data.get("items", []) if isinstance(it, dict)]
    elif _is_rss(raw):
        log(f"news: RSS fallback for {url}", "dim")
        items = _parse_rss_items(raw)
    else:
        items = []
    # Newest first (ISO dates with a fixed offset sort correctly as
    # strings; non-string dates are normalized so mixed types can't blow
    # up mid-sort).
    items.sort(key=lambda it: str(it.get("date", "")), reverse=True)
    return items


def fetch_news_items() -> list:
    """news.json (or an RSS/Atom feed) → [{id, title, date, body, …}, …]

    Returns empty list when the news URL was not explicitly configured
    (i.e., only the derived default exists).
    """
    if not launcher.news_url_explicit():
        return []
    return _decode_items(
        _fetch_payload(launcher.news_url()), launcher.news_url()
    )


def fetch_featured_post() -> dict | None:
    """Latest announcements-forum post → {id, title, author?, date, url,
    html}. An RSS feed yields its newest <item>.

    Returns None when the featured news URL was not explicitly configured
    (i.e., only the derived default exists).
    """
    if not launcher.featured_news_url_explicit():
        return None
    url = launcher.featured_news_url()
    raw = _fetch_payload(url)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("id"):
        return data
    if _is_rss(raw):
        log(f"news: RSS fallback for {url}", "dim")
        items = _parse_rss_items(raw)
        if not items:
            return None
        items.sort(key=lambda it: str(it.get("date", "")), reverse=True)
        first = items[0]
        return {
            "id": first.get("id"),
            "title": first.get("title"),
            "author": first.get("author"),
            "date": first.get("date"),
            "url": first.get("url"),
            "html": first.get("html"),
        }
    return None

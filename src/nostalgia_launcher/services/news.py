"""News feed: announcements list and featured forum post.

Endpoints come from the launcher configuration (`core/launcher.py`).
"""

import json
import urllib.request

from ..core import launcher
from ..core.constants import NEWS_TIMEOUT, UA
from ..core.security_http import read_capped, secure_urlopen


def fetch_news_items() -> list:
    """news.json → [{id, title, date, body, url?, author?}, …]"""
    req = urllib.request.Request(
        launcher.news_url(), headers={"User-Agent": UA}
    )
    with secure_urlopen(req, timeout=NEWS_TIMEOUT) as r:
        data = json.loads(read_capped(r, 1 * 1024 * 1024))
    items = data.get("items", [])
    # news.json lists topics in forum order — show newest first (ISO dates
    # with a fixed offset sort correctly as strings).
    items.sort(key=lambda it: it.get("date", ""), reverse=True)
    return items


def fetch_featured_post() -> dict | None:
    """Latest announcements-forum post → {id, title, author?, date, url, html}"""
    req = urllib.request.Request(
        launcher.featured_news_url(), headers={"User-Agent": UA}
    )
    with secure_urlopen(req, timeout=NEWS_TIMEOUT) as r:
        data = json.loads(read_capped(r, 1 * 1024 * 1024))
    return data if isinstance(data, dict) and data.get("id") else None

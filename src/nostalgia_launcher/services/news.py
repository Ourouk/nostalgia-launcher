"""News feed: announcements list and featured forum post.

Endpoints come from the launcher configuration (`core/launcher.py`).
"""

import json
import urllib.request

from ..core import launcher
from ..core.constants import NEWS_TIMEOUT, UA
from ..core.security_http import read_capped, secure_urlopen


def fetch_news_items() -> list:
    """news.json → [{id, title, date, body, url?, author?}, …]

    Returns empty list when the news URL was not explicitly configured
    (i.e., only the derived default exists).
    """
    if not launcher.news_url_explicit():
        return []
    req = urllib.request.Request(
        launcher.news_url(), headers={"User-Agent": UA}
    )
    with secure_urlopen(req, timeout=NEWS_TIMEOUT) as r:
        data = json.loads(read_capped(r, 1 * 1024 * 1024))
    # A shape-broken feed degrades to "no news" — never a crash in the
    # fetch path that the controller would misreport as a network failure.
    items = (
        [it for it in data.get("items", []) if isinstance(it, dict)]
        if isinstance(data, dict)
        else []
    )
    # news.json lists topics in forum order — show newest first (ISO dates
    # with a fixed offset sort correctly as strings; non-string dates are
    # normalized so mixed types can't blow up mid-sort).
    items.sort(key=lambda it: str(it.get("date", "")), reverse=True)
    return items


def fetch_featured_post() -> dict | None:
    """Latest announcements-forum post → {id, title, author?, date, url, html}

    Returns None when the featured news URL was not explicitly configured
    (i.e., only the derived default exists).
    """
    if not launcher.featured_news_url_explicit():
        return None
    req = urllib.request.Request(
        launcher.featured_news_url(), headers={"User-Agent": UA}
    )
    with secure_urlopen(req, timeout=NEWS_TIMEOUT) as r:
        data = json.loads(read_capped(r, 1 * 1024 * 1024))
    return data if isinstance(data, dict) and data.get("id") else None

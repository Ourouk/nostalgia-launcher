"""News feed: announcements list and featured forum post.

Endpoints come from the launcher configuration (`core/launcher.py`).
"""

import json

from ..core import launcher
from ..core.constants import NEWS_TIMEOUT
from ..core.security_http import _check_url, make_secure_client


def fetch_news_items() -> list:
    """news.json → [{id, title, date, body, url?, author?}, …]

    Returns empty list when the news URL was not explicitly configured
    (i.e., only the derived default exists).
    """
    if not launcher.news_url_explicit():
        return []
    url = launcher.news_url()
    _check_url(url, None)
    with make_secure_client(timeout=NEWS_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if len(resp.content) > 1 * 1024 * 1024:
            raise RuntimeError("Response exceeded the 1024 KiB limit.")
        data = json.loads(resp.content)
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
    url = launcher.featured_news_url()
    _check_url(url, None)
    with make_secure_client(timeout=NEWS_TIMEOUT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if len(resp.content) > 1 * 1024 * 1024:
            raise RuntimeError("Response exceeded the 1024 KiB limit.")
        data = json.loads(resp.content)
    return data if isinstance(data, dict) and data.get("id") else None

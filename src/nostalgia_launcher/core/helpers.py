"""Pure, dependency-free helpers shared across Nostalgia Launcher modules.

No global state, no I/O — deterministic and easy to test in isolation.
"""

import html as html_mod
import re
from datetime import datetime
from urllib.parse import urlsplit


def fmt_size(num_bytes: float) -> str:
    """Human-readable size: KB under a megabyte, MB otherwise."""
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / 1024 / 1024:.1f} MB"


def fmt_speed(bytes_per_sec: float) -> str:
    if bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    return f"{bytes_per_sec / 1024 / 1024:.1f} MB/s"


def parse_version(v: str) -> tuple:
    """'v1.2.0' → (1, 2, 0); each dot-part contributes its LEADING digit run
    ('2rc1' → 2, 'rc1' → 0) so qualifiers can't splice digits together."""
    parts = []
    for p in (v or "").strip().lstrip("vV").split("."):
        m = re.match(r"\d+", p)
        parts.append(int(m.group(0)) if m else 0)
    return tuple(parts) or (0,)


def same_git_repo(a, b) -> bool:
    """Compare git URLs ignoring a trailing '.git' / slash and case."""

    def norm(u):
        u = (u or "").rstrip("/")
        return (u[:-4] if u.endswith(".git") else u).lower()

    return norm(a) == norm(b)


def redact_url(url: str) -> str:
    """Mask any userinfo (user:token@) in a URL before it reaches the
    session log — credentials embedded in configured endpoints must not be
    persisted to disk or shown in the GUI log window."""
    if not isinstance(url, str) or "@" not in url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.username:
        return url
    netloc = f"***@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return url.replace(f"{parts.netloc}@", netloc, 1)


def parse_wow_colored(text: str):
    """Split a string containing WoW colour escapes (|cAARRGGBB … |r) into
    [(segment, "#rrggbb" | None), …] for rendering."""
    segments = []
    color = None
    pos = 0
    for m in re.finditer(r"\|c[0-9a-fA-F]{8}|\|r", text):
        if m.start() > pos:
            segments.append((text[pos : m.start()], color))
        tok = m.group(0)
        color = f"#{tok[4:]}" if tok.startswith("|c") else None
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], color))
    return [(t, c) for t, c in segments if t]


def strip_wow_colors(text: str) -> str:
    return "".join(t for t, _c in parse_wow_colored(text))


def strip_html(raw: str) -> str:
    """Reduce forum HTML to readable plain text."""
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", raw)
    txt = re.sub(r"(?i)<br\s*/?>", "\n", txt)
    txt = re.sub(r"(?i)<li[^>]*>", "\n• ", txt)
    txt = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr|blockquote)>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html_mod.unescape(txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r" ?\n ?", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def format_news_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y")
    except Exception:
        return iso


def relative_age(ts: float | None, now: float | None = None) -> str:
    """Human age of an epoch timestamp: "just now", "5m ago", "3h ago",
    "3d ago". Empty string when there is no timestamp (never fetched)."""
    if not ts:
        return ""
    now = now if now is not None else datetime.now().timestamp()
    delta = max(0, int(now - ts))
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"

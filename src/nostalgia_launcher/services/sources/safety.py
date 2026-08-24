"""Generic safety validators shared by the content services and backends.

Lives outside `services/catalog.py` so the download backends
(`services/sources/*`) can use them without importing the catalog module
(which imports the registry — importing back here would be circular).
`catalog.py` re-exports these under their historical names.
"""

from urllib.parse import urlsplit


def safe_folder(name) -> bool:
    """A directory name we are willing to install into (no separators, no
    traversal, no NUL)."""
    if not isinstance(name, str):
        return False
    name = name.strip()
    return (
        bool(name)
        and name not in (".", "..")
        and not any(ch in name for ch in "/\\")
        and "\x00" not in name
    )


def safe_relpath(p) -> bool:
    """A relative destination path: not absolute, no traversal, no NUL."""
    if not isinstance(p, str) or not p:
        return False
    if p.startswith(("/", "\\")) or p[1:2] == ":":
        return False
    parts = p.replace("\\", "/").split("/")
    return (
        all(part and part not in (".", "..") for part in parts)
        and "\x00" not in p
    )


def https_url(u) -> str | None:
    """A well-formed https:// URL string, else None."""
    if not isinstance(u, str):
        return None
    u = u.strip()
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return u


def safe_slug(s) -> str | None:
    """A repo owner/repo slug: printable ASCII letters, digits, . _ -."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s or len(s) > 100 or any(ch.isspace() for ch in s):
        return None
    if not all(ch.isalnum() or ch in "._-" for ch in s):
        return None
    return s


def valid_extract_map(emap) -> dict | None:
    """Sanitize an extract_map {zip/tar entry pattern: dest} into a dict of
    valid relative destinations. None when not a dict, or when every entry
    failed validation (a map the installer could never honour)."""
    if emap is None:
        return None
    if not isinstance(emap, dict):
        return None
    out = {}
    for pattern, dest in emap.items():
        if (
            isinstance(pattern, str)
            and pattern
            and isinstance(dest, str)
            and safe_relpath(dest)
        ):
            out[pattern] = dest
    return out or None

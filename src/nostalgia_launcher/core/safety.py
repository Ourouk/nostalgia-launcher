"""Canonical security primitives for path validation, URL checking,
and archive extraction safety.

All external data (manifests, catalogs, release assets, archive
entries, filenames) must pass through these before any filesystem
operation. Existing callers use the legacy names; the canonical
names are the target for all new code.
"""

from urllib.parse import urlsplit

# ── Path validation ─────────────────────────────────────────────


def safe_relative_path(p) -> bool:
    """A relative destination path: not absolute, no traversal, no NUL.
    Canonical name for the path-checking primitive.
    """
    if not isinstance(p, str) or not p:
        return False
    if p.startswith(("/", "\\")) or p[1:2] == ":":
        return False
    parts = p.replace("\\", "/").split("/")
    return (
        all(part and part not in (".", "..") for part in parts)
        and "\x00" not in p
    )


safe_relpath = safe_relative_path  # legacy alias


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


# ── URL validation ──────────────────────────────────────────────


def validate_download_url(u) -> str | None:
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


https_url = validate_download_url  # legacy alias


# ── Extract map / SHA-1 validation ──────────────────────────────


def validate_extract_map(emap) -> dict | None:
    """Sanitize an extract_map {zip/tar entry pattern: dest} into a dict
    of valid relative destinations. None when not a dict, or when every
    entry failed validation (a map the installer could never honour)."""
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
            and safe_relative_path(dest)
        ):
            out[pattern] = dest
    return out or None


valid_extract_map = validate_extract_map  # legacy alias


def validate_sha1(value) -> str | None:
    """A lowercase 40-hex SHA-1 digest, or None when absent/invalid."""
    if value is None or not isinstance(value, str):
        return None
    v = value.strip().lower()
    if len(v) != 40 or any(c not in "0123456789abcdef" for c in v):
        return None
    return v


valid_sha1 = validate_sha1  # legacy alias


# ── Destination containment ─────────────────────────────────────


def safe_destination(path: str, base: str) -> bool:
    """Return True when ``path`` resolves inside ``base`` (no traversal,
    no absolute escape). Both paths use forward slashes."""
    base = base.replace("\\", "/")
    path = path.replace("\\", "/")
    if not path.startswith(base + "/"):
        return False
    parts = path[len(base) + 1 :].split("/")
    return all(part not in ("..",) for part in parts)


def safe_slug(s) -> str | None:
    """A repo owner/repo slug: printable ASCII letters, digits, . _ -."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s or len(s) > 100 or any(ch.isspace() for ch in s):
        return None
    if not s.isascii() or not all(ch.isalnum() or ch in "._-" for ch in s):
        return None
    return s

"""Canonical security primitives for path validation, URL checking,
and archive extraction safety.

All external data (manifests, catalogs, release assets, archive
entries, filenames) must pass through these before any filesystem
operation. Existing callers use the legacy names; the canonical
names are the target for all new code.
"""

import os
from urllib.parse import urlsplit

# Windows reserved device names — even with an extension they are unsafe.
_WIN_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def _is_windows_reserved(part: str) -> bool:
    # ``CON.txt`` etc. are reserved as well — check the stem before the dot.
    stem = part.split(".")[0].upper()
    return stem in _WIN_RESERVED


# ── Path validation ─────────────────────────────────────────────


def safe_relative_path(p) -> bool:
    """A relative destination path: not absolute, no traversal, no NUL,
    no empty segments, no Windows reserved names.
    Canonical name for the path-checking primitive.
    """
    if not isinstance(p, str) or not p:
        return False
    if "\x00" in p:
        return False
    if p.startswith(("/", "\\")) or p[1:2] == ":":
        return False
    parts = p.replace("\\", "/").split("/")
    for part in parts:
        if not part or part in (".", ".."):
            return False
        if _is_windows_reserved(part):
            return False
        if ":" in part:
            return False
    return True


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
    """Return True when ``path`` resolves strictly inside ``base``.

    Uses resolved filesystem containment (realpath/commonpath) so a
    pre-existing symlink cannot be used to escape, and syntactic checks
    (NUL, drive letter, empty segments, ``..``, Windows reserved names)
    via :func:`safe_relative_path` on the relative portion.
    """
    if not isinstance(path, str) or not isinstance(base, str):
        return False
    if not path or not base:
        return False
    if "\x00" in path or "\x00" in base:
        return False
    try:
        base_abs = os.path.abspath(base)
        path_abs = os.path.abspath(path)
        base_real = os.path.realpath(base_abs)
        path_real = os.path.realpath(path_abs)
        if os.name == "nt":
            common = os.path.commonpath([base_real.lower(), path_real.lower()])
            if common != base_real.lower():
                return False
        else:
            common = os.path.commonpath([base_real, path_real])
            if common != base_real:
                return False
    except (ValueError, OSError):
        return False
    try:
        rel = os.path.relpath(path_abs, base_abs)
    except ValueError:
        return False
    if rel == ".":
        return False
    rel_slash = rel.replace("\\", "/")
    if rel_slash.startswith("../") or rel_slash == "..":
        return False
    # ``rel`` of ``base``+``/``+``member`` with ``member`` absolute (e.g.
    # ``/etc/passwd``) yields ``../../etc/passwd`` — already rejected above.
    # Otherwise it must be a safe relative path (no ``..``, no empty, no
    # reserved, no drive).
    if not safe_relative_path(rel_slash):
        return False
    return True


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

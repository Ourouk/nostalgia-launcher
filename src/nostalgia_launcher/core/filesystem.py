"""Filesystem and hashing helpers shared across the updater.

Pure filesystem operations (hashing, atomic-ish cleanup, version reads) that
don't belong to any single engine module.
"""

import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

from .log_sink import log

# Version/build fields inside WoW.exe, at offsets specific to the 1.12.1
# client build. Any other build decodes to garbage here, which
# get_client_version reports as "" rather than a bogus string.
_BUILD_OFFSET = 0x00437BFC
_VERSION_OFFSET = 0x00437C04

_VERSIONISH_RE = re.compile(r"\d+(?:\.\d+)*")


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def atomic_write_bytes(path: str, data: bytes):
    """Write via a temp file + atomic rename so a crash mid-write can never
    leave a truncated/corrupt file at `path`. Creates the parent directory
    so the per-user data dirs work on first write."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def atomic_write_text(path: str, text: str):
    """atomic_write_bytes for str payloads (UTF-8 encoded)."""
    atomic_write_bytes(path, text.encode("utf-8"))


def sha1_file(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def cached_sha1(path_str: str, cache: dict) -> str:
    try:
        mtime = os.path.getmtime(path_str)
        entry = cache.get(path_str)
        if entry and entry[1] == mtime:
            return entry[0]
        h = sha1_file(path_str)
        cache[path_str] = [h, mtime]
        return h
    except Exception:
        return ""


def remove_wdb(client_dir: str):
    """Delete the client's WDB folder (server-data cache, safe to drop)."""
    wdb = os.path.join(client_dir, "WDB")
    if not os.path.isdir(wdb):
        return
    try:
        shutil.rmtree(wdb)
        log("WDB cache cleared.", "dim")
    except Exception as e:
        log(f"Could not clear WDB: {e}", "err")


def get_client_version(out_dir: str) -> str:
    """Read version + build from fixed offsets in the client's WoW.exe.

    The offsets are 1.12.1-build-specific; a missing exe or any other
    client build yields "" instead of a misread label.
    """
    exe_path = os.path.join(out_dir, "WoW.exe")
    if not os.path.exists(exe_path):
        return ""
    try:
        # Read only the two small fields, not the whole ~5 MB binary.
        with open(exe_path, "rb") as f:
            f.seek(_BUILD_OFFSET)
            build = f.read(4).decode("utf-8", errors="replace").rstrip("\x00")
            f.seek(_VERSION_OFFSET)
            version = (
                f.read(6).decode("utf-8", errors="replace").rstrip("\x00")
            )
        if not _VERSIONISH_RE.fullmatch(
            version
        ) or not _VERSIONISH_RE.fullmatch(build):
            return ""
        return f"{version} ({build})"
    except Exception:
        return ""


def pick_game_executable(
    client_dir: str, external_executables: list[str] | None = None
) -> tuple[str, str]:
    """Which binary to launch from the game folder.

    Prefers the first external-launcher executable (declared by an
    installed catalog mod and passed in by the caller) that exists on disk,
    falling back to WoW.exe. Returns ``(absolute_path, label)``.
    """
    for name in external_executables or []:
        candidate = os.path.join(client_dir, name)
        if os.path.exists(candidate):
            return candidate, name
    return os.path.join(client_dir, "WoW.exe"), "WoW.exe"


def rmtree_force(path):
    """Like shutil.rmtree, but also removes read-only files. Plain rmtree
    raises PermissionError on Windows when it meets a read-only file (e.g. a
    .git object store from a manual clone, or a read-only addon shipped in an
    old zip); this clears the read-only bit and retries."""

    def handler(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    shutil.rmtree(path, onexc=handler)

"""Filesystem and hashing helpers shared across the updater.

Pure filesystem operations (hashing, atomic-ish cleanup, version reads) that
don't belong to any single engine module.
"""

import hashlib
import os
import shutil
import stat
from pathlib import Path

from .log_sink import log


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


def get_client_version(out_dir: str) -> str:  # noqa: ARG001
    """Client version — declarative (server-pinned).

    The legacy binary-offset reader (1.12.1 WoW.exe at 0x00437BFC/0x00437C04)
    is removed. The declared ``server.client_version`` from
    ``nostalgia_launcher.json`` (``1.12.1`` / ``2.4.3`` / ``3.3.5a``) is now
    the single source of truth (one client per profile). ``out_dir`` is kept
    for call-site compatibility.

    Kept as a thin shim so ``controllers/update`` and
    ``services/update/workflow`` stay importable without cycles; the real
    value is read via ``launcher.client_version()``.
    """
    try:
        from . import launcher

        return launcher.client_version() or ""
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

"""Payload extraction for first-time client acquisition.

The client a server distributes is described by ``server.download.content.type``:
``folder`` means the source already delivers extracted files; ``zip`` / ``rar``
means the acquired payload is an archive the launcher must extract into the game
folder before the client becomes playable. This module is invoked after a
successful client download (BitTorrent recovery or HTTP update) so the
extraction step is uniform regardless of transport.
"""

import os
import zipfile

from ...core.log_sink import log


def _root_archives(out_dir: str, ext: str) -> list[str]:
    """Archive files (matching ``ext``) sitting directly in ``out_dir``."""
    found: list[str] = []
    try:
        names = os.listdir(out_dir)
    except OSError:
        return found
    for name in names:
        if name.lower().endswith(ext) and os.path.isfile(
            os.path.join(out_dir, name)
        ):
            found.append(os.path.join(out_dir, name))
    return found


def _is_within(base: str, target: str) -> bool:
    base = os.path.abspath(base)
    return os.path.abspath(target).startswith(base + os.sep)


def _extract_zip(archive: str, dest: str) -> None:
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            if not _is_within(dest, os.path.join(dest, member)):
                raise RuntimeError(f"unsafe zip entry (zip-slip): {member}")
        zf.extractall(dest)


def _extract_rar(archive: str, dest: str) -> None:
    try:
        import rarfile
    except ImportError:
        raise RuntimeError(
            "rarfile is not installed; cannot extract .rar"
        ) from None
    with rarfile.RarFile(archive) as rf:
        for member in rf.namelist():
            if not _is_within(dest, os.path.join(dest, member)):
                raise RuntimeError(f"unsafe rar entry (rar-slip): {member}")
        rf.extractall(dest)


def extract_client_payload(out_dir: str, content_type: str) -> bool:
    """Extract the client payload in ``out_dir`` per ``content_type``.

    ``folder`` → nothing to do (returns True). ``zip`` / ``rar`` → extract every
    matching archive found at the root of ``out_dir`` into ``out_dir`` and remove
    the archive. Returns True when extraction ran (or was a no-op), False when an
    archive was expected but none was found (caller may warn)."""
    if content_type == "folder":
        return True
    if content_type not in ("zip", "rar"):
        return True
    ext = ".zip" if content_type == "zip" else ".rar"
    archives = _root_archives(out_dir, ext)
    if not archives:
        log(
            f"[extract] content.type is '{content_type}' but no {ext} "
            "archive was found in the game folder.",
            "err",
        )
        return False
    for archive in archives:
        log(
            f"[extract] Extracting {os.path.basename(archive)} "
            f"into {out_dir}…",
            "acct",
        )
        try:
            if content_type == "zip":
                _extract_zip(archive, out_dir)
            else:
                _extract_rar(archive, out_dir)
        except Exception as e:
            log(f"[extract] Failed to extract {archive}: {e}", "err")
            return False
        try:
            os.remove(archive)
        except OSError:
            pass
        log(f"[extract] Extracted {os.path.basename(archive)}.", "ok")
    return True

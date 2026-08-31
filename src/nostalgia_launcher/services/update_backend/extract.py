"""Payload extraction for first-time client acquisition.

The client a server distributes is described by ``server.download.content.type``:
``folder`` means the source already delivers extracted files; ``zip`` / ``rar``
means the acquired payload is an archive the launcher must extract into the game
folder before the client becomes playable. This module is invoked after a
successful client download (BitTorrent recovery or HTTP update) so the
extraction step is uniform regardless of transport.
"""

import os
import shutil
import stat
import zipfile

from ...core.log_sink import log
from ...core.safety import safe_destination, safe_relative_path


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


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o777777
    if mode != 0 and stat.S_ISLNK(mode):
        return True
    # Some zips store symlink target as regular file content but with
    # symlink mode bits zero; fallback: treat entries whose filename is a
    # symlink-like path is handled via safe_destination, but we also check
    # the is_symlink heuristic via external_attr symlink bit.
    return False


def _is_zip_unsafe_type(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o777777
    if mode == 0:
        return False
    # Zip entries created without explicit unix mode have no file-type
    # bits (e.g. 0o600) — treat them as regular files, not special.
    if (mode & 0o170000) == 0:
        return False
    # Only regular files and directories are allowed.
    if stat.S_ISREG(mode) or stat.S_ISDIR(mode):
        return False
    # symlinks already handled, everything else (fifo, socket, char/block)
    return True


def _extract_zip(archive: str, dest: str) -> None:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            member = info.filename
            if "\x00" in member:
                raise RuntimeError(f"unsafe zip entry (zip-slip): {member}")
            if _is_zip_symlink(info):
                raise RuntimeError(f"unsafe zip entry (symlink): {member}")
            if _is_zip_unsafe_type(info):
                raise RuntimeError(
                    f"unsafe zip entry (special file): {member}"
                )
            # Syntactic checks on the member name itself (empty segments,
            # reserved names, drive letters) before filesystem containment.
            norm = member.replace("\\", "/")
            stripped = norm.rstrip("/")
            if stripped == "" and norm != "":
                raise RuntimeError(f"unsafe zip entry (zip-slip): {member}")
            if stripped and not safe_relative_path(stripped):
                raise RuntimeError(f"unsafe zip entry (zip-slip): {member}")
            # ``ZipInfo.is_dir()`` is authoritative for directory entries.
            is_dir = info.is_dir()
            # Directory entries have trailing slash; strip for destination
            # check but keep directory creation path.
            target = os.path.join(dest, norm)
            if not safe_destination(target, dest):
                raise RuntimeError(f"unsafe zip entry (zip-slip): {member}")
            # Pre-existing symlink parent escape is caught by safe_destination
            # (realpath/commonpath) — also reject symlink/hardlink members
            # above. Now extract manually without following symlinks.
            # TOCTOU mitigation: verify containment again after creating
            # parent dirs / file (parent could have been swapped between
            # pre-check and write).
            if is_dir:
                os.makedirs(target, exist_ok=True)
                # Post-create check for directory symlink escape
                if not safe_destination(os.path.realpath(target), dest):
                    try:
                        os.rmdir(target)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"unsafe zip entry (post-check): {member}"
                    )
            elif norm.endswith("/") or not norm.strip():
                os.makedirs(target, exist_ok=True)
                if not safe_destination(os.path.realpath(target), dest):
                    try:
                        os.rmdir(target)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"unsafe zip entry (post-check): {member}"
                    )
            else:
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                    if not safe_destination(os.path.realpath(parent), dest):
                        raise RuntimeError(
                            f"unsafe zip entry (post-check parent): {member}"
                        )
                # Use O_NOFOLLOW where available to avoid following symlink
                try:
                    fd = os.open(
                        target,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_TRUNC
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o644,
                    )
                    with os.fdopen(fd, "wb") as dst, zf.open(info) as src:
                        shutil.copyfileobj(src, dst)
                except OSError:
                    # Fallback if O_NOFOLLOW not supported
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                if not safe_destination(os.path.realpath(target), dest):
                    try:
                        os.remove(target)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"unsafe zip entry (post-check file): {member}"
                    )


def _extract_rar(archive: str, dest: str) -> None:
    try:
        import rarfile  # type: ignore[import-not-found]
    except ImportError:
        raise RuntimeError(
            "rarfile is not installed; cannot extract .rar"
        ) from None
    with rarfile.RarFile(archive) as rf:  # type: ignore[attr-defined]
        for info in rf.infolist():
            member = info.filename
            if "\x00" in member:
                raise RuntimeError(f"unsafe rar entry (rar-slip): {member}")
            # Syntactic member check (empty segments, reserved, drive)
            norm = member.replace("\\", "/")
            stripped = norm.rstrip("/")
            if stripped == "" and norm != "":
                raise RuntimeError(f"unsafe rar entry (rar-slip): {member}")
            if stripped and not safe_relative_path(stripped):
                raise RuntimeError(f"unsafe rar entry (rar-slip): {member}")
            # RarInfo symlink/hardlink detection
            try:
                if info.is_symlink():
                    raise RuntimeError(f"unsafe rar entry (symlink): {member}")
            except RuntimeError:
                raise
            except Exception:
                pass
            try:
                is_hardlink = False
                if hasattr(info, "is_hardlink"):
                    is_hardlink = info.is_hardlink()
                elif hasattr(info, "is_hard_link"):
                    is_hardlink = info.is_hard_link()
                if is_hardlink:
                    raise RuntimeError(
                        f"unsafe rar entry (hardlink): {member}"
                    )
            except RuntimeError:
                raise
            except Exception:
                pass
            if info.is_dir():
                target = os.path.join(dest, norm)
                if not safe_destination(target, dest):
                    raise RuntimeError(
                        f"unsafe rar entry (rar-slip): {member}"
                    )
                os.makedirs(target, exist_ok=True)
                if not safe_destination(os.path.realpath(target), dest):
                    try:
                        os.rmdir(target)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"unsafe rar entry (post-check): {member}"
                    )
                continue
            target = os.path.join(dest, norm)
            if not safe_destination(target, dest):
                raise RuntimeError(f"unsafe rar entry (rar-slip): {member}")
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
                if not safe_destination(os.path.realpath(parent), dest):
                    raise RuntimeError(
                        f"unsafe rar entry (post-check parent): {member}"
                    )
            try:
                fd = os.open(
                    target,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_TRUNC
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o644,
                )
                with os.fdopen(fd, "wb") as dst, rf.open(info) as src:
                    shutil.copyfileobj(src, dst)
            except OSError:
                with rf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            if not safe_destination(os.path.realpath(target), dest):
                try:
                    os.remove(target)
                except Exception:
                    pass
                raise RuntimeError(
                    f"unsafe rar entry (post-check file): {member}"
                )


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

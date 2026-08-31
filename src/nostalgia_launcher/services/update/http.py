"""HTTP transport: per-file HTTPS download with resume/retry.

No policy: just fetches bytes, reports progress, verifies transfer length.
Hash verification of the final file is the caller's responsibility.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
import urllib.request
from typing import TYPE_CHECKING

from ...core.constants import DOWNLOAD_RETRY, DOWNLOAD_TIMEOUT, UA
from ...core.helpers import fmt_size, fmt_speed, redact_url
from ...core.security_http import allowed_download_hosts as _allowed_hosts_impl
from ...core.security_http import secure_urlopen as _secure_impl
from .manifest import checked_node_size


def _hu_attr(name: str, fallback):
    try:
        import sys

        m = sys.modules.get(
            "nostalgia_launcher.services.update_backend.http_update"
        )
        if m is not None and hasattr(m, name):
            return getattr(m, name)
    except Exception:
        pass
    return fallback


def _get_secure_urlopen():
    return _hu_attr("secure_urlopen", _secure_impl)


def _get_allowed_hosts():
    return _hu_attr("allowed_download_hosts", _allowed_hosts_impl)


if TYPE_CHECKING:
    from ...state.events import EventDispatcher


def fetch_manifest(url: str, dispatcher=None):
    """Fetch and parse manifest from URL, returning parsed object."""
    import json

    from ...core.security_http import read_capped
    from ...state.manifest import parse_manifest

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _get_secure_urlopen()(
        req,
        timeout=DOWNLOAD_TIMEOUT,
        allowed_hosts=_get_allowed_hosts()(),
    ) as r:
        raw = json.loads(read_capped(r, 16 * 1024 * 1024))
        return parse_manifest(raw)


def download_file(
    url: str,
    dest: str,
    size: object,
    name: str = "",
    *,
    dispatcher: EventDispatcher | None = None,
    cache: dict | None = None,
    is_cancelled=None,
    log=None,
    progress=None,
    total_ref: dict | None = None,
    counted: dict | None = None,
) -> str | None:
    """Download one file over HTTPS with resume/retry and progress.

    Returns SHA-1 hex when hashed from byte 0, else None (caller must hash).
    Updates cache entry for the destination when possible.
    """
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    name = name or os.path.basename(dest)
    size = checked_node_size(size)
    total_str = fmt_size(size) if size else "?"
    cache = cache if cache is not None else {}
    counted = counted if counted is not None else {}
    total_val = 0
    downloaded_val = 0
    if total_ref is not None:
        total_val = total_ref.get("total", 0)
        downloaded_val = total_ref.get("downloaded", 0)

    def _log(msg: str, tag: str = "") -> None:
        if log:
            log(msg, tag)
        elif dispatcher:
            from ...state.events import LogMessage

            dispatcher.post(LogMessage(msg, tag))

    def _progress(value: float, label: str = "", **kw) -> None:
        if progress:
            progress(value, label, **kw)
        elif dispatcher:
            from ...state.events import ProgressChanged

            def _str(k: str) -> str:
                v = kw.get(k, "")
                return v if isinstance(v, str) else ""

            def _int(k: str) -> int:
                v = kw.get(k, 0)
                return (
                    v if isinstance(v, int) and not isinstance(v, bool) else 0
                )

            def _float(k: str) -> float:
                v = kw.get(k, 0.0)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
                return 0.0

            dispatcher.post(
                ProgressChanged(
                    value,
                    label,
                    phase=_str("phase"),
                    transport=_str("transport"),
                    current_file=_str("current_file"),
                    downloaded=_int("downloaded"),
                    total=_int("total"),
                    speed=_float("speed"),
                    peers=_int("peers"),
                    verified_pieces=_int("verified_pieces"),
                    total_pieces=_int("total_pieces"),
                )
            )

    for attempt in range(1, DOWNLOAD_RETRY + 1):
        if is_cancelled and is_cancelled():
            raise RuntimeError("Cancelled")
        try:
            got = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            if size and got >= size:
                os.remove(tmp)
                got = 0
            headers = {"User-Agent": UA}
            mode = "wb"
            if got:
                headers["Range"] = f"bytes={got}-"
                mode = "ab"
                _log(f"  Resuming ({fmt_size(got)} / {total_str})…")
            else:
                _log(f"  Downloading ({total_str})…")
            req = urllib.request.Request(url, headers=headers)
            downloaded = got
            hasher = hashlib.sha1() if not got else None
            t0 = time.monotonic()
            bytes_at_t0 = downloaded
            speed_str = ""
            with _get_secure_urlopen()(
                req,
                timeout=DOWNLOAD_TIMEOUT,
                allowed_hosts=_get_allowed_hosts()(),
            ) as r:
                status = getattr(r, "status", None) or r.getcode()
                if got and status != 206:
                    downloaded, mode = 0, "wb"
                    hasher = hashlib.sha1()
                    bytes_at_t0 = 0
                with open(tmp, mode) as f:
                    while True:
                        if is_cancelled and is_cancelled():
                            raise RuntimeError("Cancelled")
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        if hasher is not None:
                            hasher.update(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        dt = now - t0
                        if dt >= 0.5:
                            speed_str = "   •   " + fmt_speed(
                                (downloaded - bytes_at_t0) / dt
                            )
                            t0, bytes_at_t0 = now, downloaded
                        if size:
                            if total_val:
                                agg = downloaded_val + downloaded
                                _progress(
                                    agg / total_val,
                                    f"{name}   •   "
                                    f"{fmt_size(downloaded)}"
                                    f" / {total_str}{speed_str}",
                                    phase="Downloading",
                                    transport="HTTP",
                                    current_file=name,
                                    downloaded=agg,
                                    total=total_val,
                                    speed=(downloaded - bytes_at_t0) / dt
                                    if dt > 0
                                    else 0.0,
                                )
                            else:
                                _progress(
                                    downloaded / size,
                                    f"{name}   •   "
                                    f"{fmt_size(downloaded)}"
                                    f" / {total_str}{speed_str}",
                                    phase="Downloading",
                                    transport="HTTP",
                                    current_file=name,
                                    downloaded=downloaded,
                                    total=size,
                                    speed=(downloaded - bytes_at_t0) / dt
                                    if dt > 0
                                    else 0.0,
                                )
            if size and downloaded != size:
                raise OSError(
                    f"connection lost at {fmt_size(downloaded)} / {total_str}"
                )
            shutil.move(tmp, dest)
            if total_ref is not None and total_val:
                prev = counted.get(dest, 0)
                counted[dest] = size
                total_ref["downloaded"] = downloaded_val + size - prev
                downloaded_val = total_ref["downloaded"]
                _progress(
                    min(1.0, downloaded_val / total_val),
                    "Downloading…",
                    phase="Downloading",
                    transport="HTTP",
                    downloaded=downloaded_val,
                    total=total_val,
                )
            if hasher is not None:
                digest = hasher.hexdigest().upper()
                try:
                    cache[dest] = [digest, os.path.getmtime(dest)]
                except OSError:
                    cache.pop(dest, None)
                return digest
            cache.pop(dest, None)
            return None
        except Exception as e:
            if is_cancelled and is_cancelled():
                raise RuntimeError("Cancelled") from None
            _log(f"  Attempt {attempt} failed: {e}", "err")
            if attempt < DOWNLOAD_RETRY:
                wait = min(2**attempt, 10)
                part = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                _progress(
                    (part / size) if size else 0.0,
                    f"{name} — retrying ({attempt}/{DOWNLOAD_RETRY})…",
                    phase="Retrying",
                    transport="HTTP",
                    current_file=name,
                    downloaded=part,
                    total=size,
                )
                _log(f"  Retrying in {wait} s…", "dim")
                time.sleep(wait)
    raise RuntimeError(
        f"Download failed after {DOWNLOAD_RETRY} attempts: {redact_url(url)}"
    )

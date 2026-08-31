"""HTTP transport: per-file HTTPS download with resume/retry."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from typing import TYPE_CHECKING

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...core.constants import DOWNLOAD_RETRY, DOWNLOAD_TIMEOUT, UA
from ...core.helpers import fmt_size, fmt_speed, redact_url
from ...core.security_http import SSL_CTX, _check_url, allowed_download_hosts
from ...core.security_http import secure_urlopen as _secure_impl
from .manifest import checked_node_size


def _hu_attr(name, fallback):
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


def _get_allowed_hosts():
    return _hu_attr("allowed_download_hosts", allowed_download_hosts)


def _get_secure_urlopen():
    return _hu_attr("secure_urlopen", _secure_impl)


if TYPE_CHECKING:
    pass


def fetch_manifest(url: str, dispatcher=None):
    import json
    import urllib.request

    from ...core.security_http import read_capped
    from ...state.manifest import parse_manifest

    sec = _get_secure_urlopen()
    if sec is not _secure_impl:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with sec(
            req, timeout=DOWNLOAD_TIMEOUT, allowed_hosts=_get_allowed_hosts()()
        ) as r:
            return parse_manifest(json.loads(read_capped(r, 16 * 1024 * 1024)))
    _check_url(url, _get_allowed_hosts()())
    with httpx.Client(
        verify=SSL_CTX,
        timeout=httpx.Timeout(DOWNLOAD_TIMEOUT),
        follow_redirects=True,
    ) as client:
        resp = client.get(url, headers={"User-Agent": UA})
        resp.raise_for_status()
        for hist in resp.history:
            _check_url(str(hist.url), _get_allowed_hosts()())
        _check_url(str(resp.url), _get_allowed_hosts()())
        return parse_manifest(json.loads(read_capped(resp, 16 * 1024 * 1024)))


def download_file(
    url,
    dest,
    size,
    name="",
    *,
    dispatcher=None,
    cache=None,
    is_cancelled=None,
    log=None,
    progress=None,
    total_ref=None,
    counted=None,
):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    name = name or os.path.basename(dest)
    size = checked_node_size(size)
    total_str = fmt_size(size) if size else "?"
    cache = cache if cache is not None else {}
    counted = counted if counted is not None else {}
    total_val = total_ref.get("total", 0) if total_ref else 0
    downloaded_val = total_ref.get("downloaded", 0) if total_ref else 0

    def _log(msg, tag=""):
        if log:
            log(msg, tag)
        elif dispatcher:
            from ...state.events import LogMessage

            dispatcher.post(LogMessage(msg, tag))

    def _progress(value, label="", **kw):
        if progress:
            progress(value, label, **kw)
        elif dispatcher:
            from ...state.events import ProgressChanged

            def _s(k):
                v = kw.get(k, "")
                return v if isinstance(v, str) else ""

            def _i(k):
                v = kw.get(k, 0)
                return (
                    v if isinstance(v, int) and not isinstance(v, bool) else 0
                )

            def _f(k):
                v = kw.get(k, 0.0)
                return (
                    float(v)
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                    else 0.0
                )

            dispatcher.post(
                ProgressChanged(
                    value,
                    label,
                    phase=_s("phase"),
                    transport=_s("transport"),
                    current_file=_s("current_file"),
                    downloaded=_i("downloaded"),
                    total=_i("total"),
                    speed=_f("speed"),
                    peers=_i("peers"),
                    verified_pieces=_i("verified_pieces"),
                    total_pieces=_i("total_pieces"),
                )
            )

    retrying = Retrying(
        stop=stop_after_attempt(DOWNLOAD_RETRY),
        wait=wait_exponential(multiplier=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    last_exc = None
    for attempt in retrying:
        attempt_no = attempt.retry_state.attempt_number
        if is_cancelled and is_cancelled():
            raise RuntimeError("Cancelled")
        with attempt:
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
                downloaded, hasher = got, hashlib.sha1() if not got else None
                t0, bytes_at_t0, speed_str = time.monotonic(), got, ""
                sec = _get_secure_urlopen()
                client = None
                if sec is not _secure_impl:
                    import urllib.request

                    req = urllib.request.Request(url, headers=headers)
                    ctx = sec(
                        req,
                        timeout=DOWNLOAD_TIMEOUT,
                        allowed_hosts=_get_allowed_hosts()(),
                    )
                    resp_obj, is_httpx = ctx, False
                else:
                    _check_url(url, _get_allowed_hosts()())
                    client = httpx.Client(
                        verify=SSL_CTX,
                        timeout=httpx.Timeout(DOWNLOAD_TIMEOUT),
                        follow_redirects=True,
                    )
                    resp_obj, is_httpx = (
                        client.stream("GET", url, headers=headers),
                        True,
                    )
                resp = resp_obj.__enter__()
                close_ctx = resp_obj
                if is_httpx:
                    for hist in resp.history:
                        _check_url(str(hist.url), _get_allowed_hosts()())
                    _check_url(str(resp.url), _get_allowed_hosts()())
                try:
                    status = (
                        resp.status_code
                        if is_httpx
                        else (getattr(resp, "status", None) or resp.getcode())  # type: ignore[attr-defined, call-arg]
                    )  # type: ignore[attr-defined]
                    if got and status != 206:
                        downloaded, mode, hasher, bytes_at_t0 = (
                            0,
                            "wb",
                            hashlib.sha1(),
                            0,
                        )
                    chunks = (
                        resp.iter_bytes(256 * 1024)
                        if is_httpx
                        else iter(lambda r=resp: r.read(256 * 1024), b"")  # type: ignore
                    )
                    with open(tmp, mode) as f:
                        for chunk in chunks:
                            if is_cancelled and is_cancelled():
                                raise RuntimeError("Cancelled")
                            if not chunk:
                                break
                            f.write(chunk)
                            if hasher is not None:
                                hasher.update(chunk)
                            downloaded += len(chunk)
                            now, dt = time.monotonic(), time.monotonic() - t0
                            if dt >= 0.5:
                                speed_str = "   •   " + fmt_speed(
                                    (downloaded - bytes_at_t0) / dt
                                )
                                t0, bytes_at_t0 = now, downloaded
                            if size:
                                agg = (
                                    downloaded_val + downloaded
                                    if total_val
                                    else downloaded
                                )
                                _progress(
                                    (
                                        agg / total_val
                                        if total_val
                                        else downloaded / size
                                    ),
                                    f"{name}   •   {fmt_size(downloaded)} / {total_str}{speed_str}",
                                    phase="Downloading",
                                    transport="HTTP",
                                    current_file=name,
                                    downloaded=agg
                                    if total_val
                                    else downloaded,
                                    total=total_val if total_val else size,
                                    speed=(downloaded - bytes_at_t0) / dt
                                    if dt > 0
                                    else 0.0,
                                )
                finally:
                    try:
                        close_ctx.__exit__(None, None, None)
                    except Exception:
                        pass
                    if is_httpx and client is not None:
                        try:
                            client.close()
                        except Exception:
                            pass
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
                last_exc = e
                _log(f"  Attempt {attempt_no} failed: {e}", "err")
                if attempt_no < DOWNLOAD_RETRY:
                    part = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                    _progress(
                        (part / size if size else 0.0),
                        f"{name} — retrying ({attempt_no}/{DOWNLOAD_RETRY})…",
                        phase="Retrying",
                        transport="HTTP",
                        current_file=name,
                        downloaded=part,
                        total=size,
                    )
                    _log(
                        f"  Retrying in {attempt.retry_state.idle_for:.1f} s…",
                        "dim",
                    )
                raise
    raise RuntimeError(
        f"Download failed after {DOWNLOAD_RETRY} attempts: {redact_url(url)}"
    ) from last_exc

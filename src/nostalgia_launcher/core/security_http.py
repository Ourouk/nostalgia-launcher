"""Hardened HTTP via httpx: HTTPS-only, capped reads.

All network I/O goes through :func:`make_secure_client` / :func:`secure_urlopen`
which refuse non-HTTPS URLs (including every redirect hop) and use a
shared TLS context that verifies against the system trust store (plus
``certifi`` roots when bundled). ``httpx`` manages redirect following;
we validate the resulting ``response.history`` stays HTTPS.

The legacy ``urllib`` opener has been replaced with :mod:`httpx` + a
centralised :mod:`tenacity` retry policy. ``secure_urlopen`` is retained as
a compatibility shim that delegates to :mod:`httpx` so existing callers and
test monkeypatches keep working while new code should prefer
:func:`make_secure_client`.

Trust model: any HTTPS URL declared by the launcher config (or its
catalogs) is trusted — there is no host allowlist.
"""

from __future__ import annotations

import logging
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import httpx
import tenacity

from .constants import UA

_log = logging.getLogger(__name__)

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = True
SSL_CTX.verify_mode = ssl.CERT_REQUIRED
try:
    import certifi

    SSL_CTX.load_verify_locations(certifi.where())
except Exception:
    pass
try:
    SSL_CTX.minimum_version = ssl.TLSVersion.TLSv1_2
except (AttributeError, ValueError):
    pass


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise RuntimeError(f"Refusing non-HTTPS URL: {url}")


def _check_redirect_chain(resp: httpx.Response) -> None:
    for hist in resp.history:
        _check_url(str(hist.url))
        loc = str(hist.headers.get("location", ""))
        if loc and "://" in loc:
            _check_url(loc)
        # Handle protocol-relative redirects (//evil.com/path)
        elif loc.startswith("//"):
            _check_url(f"https:{loc}")
    _check_url(str(resp.url))


def _validate(resp: httpx.Response) -> None:
    for hist in resp.history:
        _check_url(str(hist.url))
    _check_url(str(resp.url))


def _enforce_https_request(request: httpx.Request) -> None:
    """httpx request hook — every request (including redirects) must stay HTTPS."""
    if request.url.scheme != "https":
        raise RuntimeError(f"Refusing non-HTTPS redirect: {request.url}")


def make_secure_client(
    *,
    timeout: float | httpx.Timeout = 10.0,
    follow_redirects: bool = True,
) -> httpx.Client:
    """Canonical secure HTTP client.

    Centralises TLS, certificate, redirect, UA, and timeout policy. Any
    HTTPS URL is trusted — hosts are never allowlisted.
    """
    return httpx.Client(
        verify=SSL_CTX,
        follow_redirects=follow_redirects,
        timeout=timeout,
        headers={"User-Agent": UA},
        event_hooks={"request": [_enforce_https_request]},
        trust_env=False,
    )


# ---------------------------------------------------------------------------
# Tenacity retry policy — transient transport/5xx only, never security failures
# ---------------------------------------------------------------------------


def _is_retryable(exc: BaseException) -> bool:
    """Whether *exc* is a transient failure worth retrying."""
    if isinstance(exc, RuntimeError) and "Refusing" in str(exc):
        return False
    if isinstance(exc, RuntimeError) and "Cancelled" in str(exc):
        return False
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.NetworkError):
        return True
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600  # type: ignore[attr-defined]
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return 500 <= exc.code < 600
    if isinstance(exc, OSError):
        return True
    return False


httpx_retry = tenacity.retry(
    stop=tenacity.stop_after_attempt(5),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10)
    + tenacity.wait_random(0, 1),
    retry=tenacity.retry_if_exception(_is_retryable),
    reraise=True,
    before_sleep=tenacity.before_sleep_log(_log, logging.WARNING),
)


def _httpx_to_http_error(url: str, response: httpx.Response) -> None:
    """Raise a :class:`urllib.error.HTTPError` mirroring httpx's 4xx/5xx."""
    raise urllib.error.HTTPError(
        url,
        response.status_code,
        response.reason_phrase,
        dict(response.headers),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )


def read_capped(r, max_bytes: int) -> bytes:
    if hasattr(r, "iter_bytes"):
        chunks, total = [], 0
        for chunk in r.iter_bytes(65536):  # type: ignore[attr-defined]
            total += len(chunk)
            if total > max_bytes:
                raise RuntimeError(
                    f"Response exceeded the {max_bytes // 1024} KiB limit."
                )
            chunks.append(chunk)
        return b"".join(chunks)
    chunks, total = [], 0
    while True:
        chunk = r.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(
                f"Response exceeded the {max_bytes // 1024} KiB limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _request(
    method: str,
    url: str,
    *,
    timeout: float,
    headers: dict | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    _check_url(url)
    with make_secure_client(timeout=timeout, follow_redirects=True) as client:
        req = client.build_request(
            method, url, headers=headers or {}, content=content
        )
        resp = client.send(req)
        _validate(resp)
        return resp


def secure_urlopen(req, timeout, **_ignored):
    import urllib.request

    if isinstance(req, urllib.request.Request):
        url = req.full_url  # type: ignore[attr-defined]
        headers = dict(req.headers)
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        data = getattr(req, "data", None)
    else:
        url, headers, method, data = req, {}, "GET", None
    resp = _request(
        method,
        url,
        timeout=timeout,
        headers=headers,
        content=data,
    )
    return _HttpxResponseWrapper(resp)


class _HttpxResponseWrapper:
    def __init__(self, resp: httpx.Response) -> None:
        self._resp = resp
        self.headers = resp.headers
        self.status = resp.status_code
        self._content: bytes | None = None
        self._pos = 0
        self._consumed = False

    def getcode(self):
        return self._resp.status_code

    def read(self, amt: int | None = None) -> bytes:
        if self._content is None:
            self._content = self._resp.content if not self._consumed else b""
            self._consumed = True
        if amt is None:
            data = self._content[self._pos :]
            self._pos = len(self._content)
            return data
        data = self._content[self._pos : self._pos + amt]
        self._pos += len(data)
        return data

    def iter_bytes(self, chunk_size=65536):
        if self._content is not None:
            for i in range(0, len(self._content), chunk_size):
                yield self._content[i : i + chunk_size]
            return
        yield from self._resp.iter_bytes(chunk_size)

    def close(self):
        self._resp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def secure_get(
    url: str, *, timeout=10.0, headers=None, max_bytes=None, **_ignored
) -> httpx.Response:
    resp = _request(
        "GET",
        url,
        timeout=timeout,
        headers=headers,
    )
    if max_bytes is not None and len(resp.content) > max_bytes:
        raise RuntimeError(
            f"Response exceeded the {max_bytes // 1024} KiB limit."
        )
    return resp


def secure_head(
    url: str, *, timeout=10.0, headers=None, **_ignored
) -> httpx.Response:
    return _request(
        "HEAD",
        url,
        timeout=timeout,
        headers=headers,
    )

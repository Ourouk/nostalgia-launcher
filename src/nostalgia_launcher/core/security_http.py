"""Hardened HTTP via httpx: HTTPS-only, host allowlist, capped reads.

All network I/O goes through :func:`make_secure_client` / :func:`secure_urlopen`
which refuse non-HTTPS URLs, optionally enforce a host allowlist, validate
every redirect hop, and use a shared TLS context that verifies against the
system trust store (plus ``certifi`` roots when bundled). ``httpx`` manages
redirect following; we validate the resulting ``response.history``.

The legacy ``urllib`` opener has been replaced with :mod:`httpx` + a
centralised :mod:`tenacity` retry policy. ``secure_urlopen`` is retained as
a compatibility shim that delegates to :mod:`httpx` so existing callers and
test monkeypatches keep working while new code should prefer
:func:`make_secure_client`.
"""

from __future__ import annotations

import io
import logging
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import httpx
import tenacity

from . import launcher
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

ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "gitlab.com",
    "codeberg.org",
}


def allowed_download_hosts() -> set[str]:
    hosts = set(ALLOWED_DOWNLOAD_HOSTS)
    c = launcher.config()
    if c is not None:
        hosts |= c.download_hosts()
    return hosts


def _check_url(url: str, allowed_hosts) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise RuntimeError(f"Refusing non-HTTPS URL: {url}")
    if allowed_hosts is not None:
        host = (parts.hostname or "").lower()
        if host not in {h.lower() for h in allowed_hosts}:
            raise RuntimeError(
                f"Refusing download from unexpected host: {host}"
            )


def _check_redirect_chain(resp: httpx.Response, allowed_hosts) -> None:
    allowed = (
        {h.lower() for h in allowed_hosts}
        if allowed_hosts is not None
        else None
    )
    for hist in resp.history:
        _check_url(str(hist.url), allowed)
        loc = str(hist.headers.get("location", ""))
        if loc and "://" in loc:
            _check_url(loc, allowed)
        # Handle protocol-relative redirects (//evil.com/path)
        elif loc.startswith("//"):
            _check_url(f"https:{loc}", allowed)
    _check_url(str(resp.url), allowed)


def _validate(resp: httpx.Response, allowed_hosts) -> None:
    if allowed_hosts is not None:
        _check_redirect_chain(resp, allowed_hosts)
    else:
        for hist in resp.history:
            _check_url(str(hist.url), None)
        _check_url(str(resp.url), None)


# Deprecated alias for backward compat (old urllib handler)
class _HttpsOnlyRedirectHandler:  # type: ignore[no-redef]
    def __init__(self, allowed_hosts=None):
        self.allowed_hosts = (
            {h.lower() for h in allowed_hosts} if allowed_hosts else None
        )

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        import urllib.request

        _check_url(newurl, self.allowed_hosts)
        return urllib.request.Request(newurl, headers=dict(req.headers))


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

    Centralises TLS, certificate, redirect, UA, and timeout policy. Host
    allowlist validation remains per-request via :func:`_check_url` /
    :func:`_validate` so different call sites can supply different
    allowlists while sharing the same TLS configuration.
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
    allowed_hosts,
    headers: dict | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    _check_url(url, allowed_hosts)
    with make_secure_client(timeout=timeout, follow_redirects=True) as client:
        req = client.build_request(
            method, url, headers=headers or {}, content=content
        )
        resp = client.send(req)
        _validate(resp, allowed_hosts)
        return resp


def secure_urlopen(req, timeout, allowed_hosts=None):
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
        allowed_hosts=allowed_hosts,
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


# Compatibility alias for refactor branch imports
_HttpxCompatResponse = _HttpxResponseWrapper


class _CompatBytesIO(io.BytesIO):
    """Alias for tests that import io.BytesIO wrapper."""

    pass


def secure_get(
    url: str, *, timeout=10.0, allowed_hosts=None, headers=None, max_bytes=None
) -> httpx.Response:
    resp = _request(
        "GET",
        url,
        timeout=timeout,
        allowed_hosts=allowed_hosts,
        headers=headers,
    )
    if max_bytes is not None and len(resp.content) > max_bytes:
        raise RuntimeError(
            f"Response exceeded the {max_bytes // 1024} KiB limit."
        )
    return resp


def secure_head(
    url: str, *, timeout=10.0, allowed_hosts=None, headers=None
) -> httpx.Response:
    return _request(
        "HEAD",
        url,
        timeout=timeout,
        allowed_hosts=allowed_hosts,
        headers=headers,
    )

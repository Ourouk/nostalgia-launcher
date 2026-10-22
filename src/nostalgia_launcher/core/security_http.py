"""Hardened HTTP via httpx: HTTPS-only, capped reads.

All network I/O goes through :func:`make_secure_client` / :func:`secure_urlopen`
which refuse non-HTTPS URLs (including every redirect hop) and use a
shared TLS context that verifies against the system trust store (plus
``certifi`` roots when bundled). ``httpx`` manages redirect following;
we validate the resulting ``response.history`` stays HTTPS.

``secure_urlopen`` is retained as a compatibility shim that delegates to
:mod:`httpx` so existing callers and test monkeypatches keep working
while new code should prefer :func:`make_secure_client`.

Trust model: any HTTPS URL declared by the launcher config (or its
catalogs) is trusted — there is no host allowlist.
"""

from __future__ import annotations

import ssl
from urllib.parse import urlsplit

import httpx

from .constants import UA

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


def secure_urlopen(req, timeout, **_ignored):
    import urllib.request

    if isinstance(req, urllib.request.Request):
        url = req.full_url  # type: ignore[attr-defined]
        headers = dict(req.headers)
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        data = getattr(req, "data", None)
    else:
        url, headers, method, data = req, {}, "GET", None
    _check_url(url)
    # Stream the body (send with stream=True) so size caps stop the
    # transfer early instead of trimming an already-buffered body.
    client = make_secure_client(timeout=timeout, follow_redirects=True)
    try:
        creq = client.build_request(
            method, url, headers=headers or {}, content=data
        )
        resp = client.send(creq, stream=True)
    except Exception:
        client.close()
        raise
    try:
        _validate(resp)
    except Exception:
        resp.close()
        client.close()
        raise
    return _HttpxResponseWrapper(resp, client)


class _HttpxResponseWrapper:
    """Streaming response wrapper: body bytes are pulled from the live"
    connection in chunks, so the callers' size caps bound memory too."""

    def __init__(self, resp, client=None) -> None:
        self._resp = resp
        self._client = client
        self.headers = resp.headers
        self.status = resp.status_code
        self._chunks = None
        self._buf = bytearray()
        self._eof = False

    def getcode(self):
        return self._resp.status_code

    def _fill(self, want: int) -> None:
        if self._chunks is None:
            self._chunks = self._resp.iter_bytes(65536)
        while len(self._buf) < want and not self._eof:
            try:
                piece = next(self._chunks)
            except StopIteration:
                self._eof = True
                break
            if piece:
                self._buf.extend(piece)

    def read(self, amt: int | None = None) -> bytes:
        if amt is None:
            out = [bytes(self._buf)]
            self._buf.clear()
            for piece in self.iter_bytes():
                out.append(piece)
            return b"".join(out)
        self._fill(amt)
        data = bytes(self._buf[:amt])
        del self._buf[:amt]
        return data

    def iter_bytes(self, chunk_size=65536):
        if self._buf:
            yield bytes(self._buf)
            self._buf.clear()
        if self._chunks is None:
            self._chunks = self._resp.iter_bytes(chunk_size)
        yield from self._chunks
        self._eof = True

    def close(self):
        try:
            self._resp.close()
        finally:
            if self._client is not None:
                self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

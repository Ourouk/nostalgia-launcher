"""Hardened HTTP: TLS verification, HTTPS-only enforcement, host allowlists.

All network I/O goes through :func:`make_secure_client` / :func:`secure_urlopen`
which refuse non-HTTPS URLs, optionally enforce a host allowlist on the
*initial* URL, keep every redirect on HTTPS, and use a shared TLS context
that verifies against the system trust store (plus ``certifi`` roots when
bundled).

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

# Hardened TLS: verify the server certificate against the system trust store,
# require the hostname to match, and refuse anything below TLS 1.2. This is
# the primary defence against a man-in-the-middle tampering with downloads.
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = True
SSL_CTX.verify_mode = ssl.CERT_REQUIRED
# Trust certifi's curated roots *in addition to* the system store, so a stale
# or incomplete Windows root store (Python's ssl uses a static snapshot and
# never triggers Windows' on-demand root update) can't break verification.
# If certifi isn't bundled, fall back to the system store alone.
try:
    import certifi

    SSL_CTX.load_verify_locations(certifi.where())
except Exception:
    pass
try:
    SSL_CTX.minimum_version = ssl.TLSVersion.TLSv1_2
except (AttributeError, ValueError):
    pass

# Binaries may only be fetched from these hosts. TLS already stops a MITM
# from impersonating them; this additionally stops a tampered API response from
# redirecting a download (e.g. a mod DLL) to an unexpected host. The base set
# covers the git hosts; the configured server/mirror hosts are added at call
# time via allowed_download_hosts().
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "gitlab.com",
    "codeberg.org",
}


def allowed_download_hosts() -> set[str]:
    """The full download allowlist: base git hosts + launcher server hosts."""
    hosts = set(ALLOWED_DOWNLOAD_HOSTS)
    c = launcher.config()
    if c is not None:
        hosts |= c.download_hosts()
    return hosts


def _check_url(
    url: str, allowed_hosts: set[str] | frozenset[str] | None
) -> None:
    """Enforce HTTPS and (optionally) an allowlist on a URL."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise RuntimeError(f"Refusing non-HTTPS URL: {url}")
    if allowed_hosts is not None:
        host = (parts.hostname or "").lower()
        if host not in allowed_hosts:
            raise RuntimeError(
                f"Refusing download from unexpected host: {host}"
            )


def _enforce_https_request(request: httpx.Request) -> None:
    """httpx request hook — every request (including redirects) must stay
    HTTPS. Host allowlist is intentionally *not* re-applied on redirects:
    an allowlisted host controls its own redirects to its CDN and TLS protects
    wherever it lands. The allowlist vets the *initial* URL before the first
    request; this hook only enforces the scheme."""
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
    :func:`secure_request` so different call sites can supply different
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
    # Security / validation failures must never be retried.
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
    # Legacy urllib errors that may surface via the compat shim.
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return 500 <= exc.code < 600
    # File/Disk transient (e.g. short read, connection lost) is retryable
    # for the download path — handled centrally so callers need not duplicate.
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


# ---------------------------------------------------------------------------
# Compatibility shim — preserves the old ``secure_urlopen`` API via httpx
# ---------------------------------------------------------------------------


class _HttpxCompatResponse:
    """Minimal ``urllib``-compatible response wrapper around :class:`httpx.Response`.

    Only the surface used by the codebase is implemented: ``read(n)``,
    ``getcode()`` / ``status``, ``headers``, context-manager protocol, and
    ``close()``. The entire body is buffered in memory; callers that need
    streaming for large downloads should use :func:`make_secure_client`
    directly with ``client.stream()``.
    """

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._buf = io.BytesIO(response.content)
        self.headers = response.headers
        self.status = response.status_code

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def getcode(self) -> int:
        return self._response.status_code

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> _HttpxCompatResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        self.close()
        return False


def _httpx_to_http_error(url: str, response: httpx.Response) -> None:
    """Raise a :class:`urllib.error.HTTPError` mirroring httpx's 4xx/5xx.

    Preserves the ``e.code`` contract expected by callers such as
    ``self_update.fetch_updater_latest_tag`` which branches on 404.
    """
    raise urllib.error.HTTPError(
        url,
        response.status_code,
        response.reason_phrase,
        dict(response.headers),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )


def read_capped(r: object, max_bytes: int) -> bytes:
    """Read *r* (httpx compat response or any file-like with ``read``) in
    chunks, refusing responses longer than *max_bytes*. Raises
    RuntimeError on overflow."""
    # Fast path for httpx compat wrapper or urllib response: delegate to
    # chunked read loop so large responses are not buffered twice.
    read = getattr(r, "read", None)
    if not callable(read):
        raise TypeError("read_capped expects a response with .read()")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk: bytes = read(65536)  # type: ignore[assignment,operator]
        if not chunk:
            break
        total += len(chunk)  # type: ignore[arg-type]
        if total > max_bytes:
            raise RuntimeError(
                f"Response exceeded the {max_bytes // 1024} KiB limit."
            )
        chunks.append(chunk)  # type: ignore[arg-type]
    return b"".join(chunks)


def secure_urlopen(
    req: str | urllib.request.Request,
    timeout: float = 10.0,
    allowed_hosts: set[str] | frozenset[str] | None = None,
) -> _HttpxCompatResponse:
    """Compatibility wrapper that enforces HTTPS + host allowlist and uses
    :mod:`httpx` under the hood.

    ``req`` may be a URL string or a :class:`urllib.request.Request`
    (headers from the Request are forwarded). Redirects are followed but each
    hop is re-validated to stay HTTPS. 4xx/5xx are surfaced as
    :class:`urllib.error.HTTPError` to preserve existing error handling.
    """
    if isinstance(req, urllib.request.Request):
        url = req.full_url
        # urllib Request stores headers in ``headers`` and
        # ``unredirected_hdrs``; ``header_items()`` is not public.
        headers: dict[str, str] = {}
        try:
            headers.update(dict(req.header_items()))  # type: ignore[attr-defined]
        except Exception:
            pass
        # Fallback for Requests built with explicit headers dict.
        if not headers and hasattr(req, "headers"):
            try:
                headers.update(dict(req.headers))  # type: ignore[arg-type]
            except Exception:
                pass
    else:
        url = req
        headers = {}

    _check_url(url, allowed_hosts)

    # Per-request client so timeout/headers don't leak across calls. The
    # shared SSL_CTX is reused; connection pooling is not critical for the
    # launcher's low-concurrency workload and simplifies mocking in tests
    # (callers monkeypatch this function directly).
    with make_secure_client(timeout=timeout) as client:
        # Use get() for the compat path — callers expect a buffered response.
        # Redirect history is available via ``response.history``.
        response = client.get(url, headers=headers)
        # Enforce HTTPS on every redirect hop (request hook already checked
        # the final request, but be explicit for the history).
        for hist in response.history:
            loc = hist.headers.get("location")
            if loc:
                # ``loc`` may be relative; resolve against history URL.
                try:
                    # Absolute URL required for validation.
                    if loc.startswith("http"):
                        _check_url(loc, None)
                    elif not loc.startswith("/"):
                        _check_url(str(hist.url), None)
                except RuntimeError:
                    response.close()
                    raise
        # Final URL must still be HTTPS (hook guarantees it, double-check).
        if response.url.scheme != "https":
            response.close()
            raise RuntimeError(f"Refusing non-HTTPS URL: {response.url}")

        if response.status_code >= 400:
            # Preserve urllib contract for callers that branch on HTTPError.
            url_str = str(response.url)
            # Close before raising to avoid leaking the connection.
            # Re-raise as HTTPError with the original response closed.
            status = response.status_code
            reason = response.reason_phrase
            hdrs = dict(response.headers)
            response.close()
            raise urllib.error.HTTPError(url_str, status, reason, hdrs, None)  # type: ignore[arg-type]

        # Buffer into compat wrapper; caller will read via read_capped().
        return _HttpxCompatResponse(response)

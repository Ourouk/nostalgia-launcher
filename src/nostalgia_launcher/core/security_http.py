"""Hardened HTTP: TLS verification, HTTPS-only enforcement, host allowlists.

Downloads and API calls go through `secure_urlopen()`, which refuses
non-HTTPS URLs, optionally enforces a host allowlist on the *initial* URL,
and keeps every redirect on HTTPS using a shared TLS context that verifies
against the system trust store (plus certifi's roots when bundled).
"""

import ssl
import urllib.request
from urllib.parse import urlsplit

from . import launcher

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

# Binaries may only be fetched from these hosts. TLS already stops a MITM from
# impersonating them; this additionally stops a tampered API response from
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


def allowed_download_hosts() -> set:
    """The full download allowlist: the base git hosts plus every host the
    launcher configuration's server and mirrors may serve from."""
    hosts = set(ALLOWED_DOWNLOAD_HOSTS)
    c = launcher.config()
    if c is not None:
        hosts |= c.download_hosts()
    return hosts


def _check_url(url: str, allowed_hosts):
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


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Require every redirect target to stay HTTPS (blocks an https→http
    downgrade). The host allowlist is deliberately *not* re-applied on
    redirects: an allowlisted host controls its own redirects — legitimately
    to its CDN (e.g. the server→its dl host, github.com→codeload) — and TLS
    protects wherever it lands. The allowlist's job is to vet the *initial*
    URL (against a tampered API response), which secure_urlopen still does."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl, None)  # HTTPS-only, no host check
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Shared opener with the hardened TLS context and the HTTPS-only redirect
# guard, built once.
_SECURE_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=SSL_CTX), _HttpsOnlyRedirectHandler()
)


def read_capped(r, max_bytes: int) -> bytes:
    """Read `r` (an http response) in chunks, refusing responses longer than
    `max_bytes`. Raises RuntimeError on overflow."""
    chunks = []
    total = 0
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


def secure_urlopen(req, timeout, allowed_hosts=None):
    """urlopen wrapper that enforces HTTPS + an optional host allowlist on the
    initial URL, keeps redirects on HTTPS, and uses the hardened TLS context.
    `req` may be a URL string or a urllib Request."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    _check_url(url, allowed_hosts)
    return _SECURE_OPENER.open(req, timeout=timeout)

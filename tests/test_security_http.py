"""Unit tests for the hardened HTTP layer (httpx-backed).

Trust model: any HTTPS URL declared by the launcher config (or its
catalogs) is trusted — there is no host allowlist. These tests pin the
HTTPS-only enforcement (initial URL + every redirect hop) and the
capped-read behavior.
"""

import pytest

import nostalgia_launcher.core.security_http as security_http


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/file",
        "ftp://example.com/file",
    ],
)
def test_check_url_rejects_non_https(url):
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http._check_url(url)


def test_check_url_allows_any_https_host():
    # No allowlist: every HTTPS host is trusted.
    security_http._check_url("https://example.com/x")
    security_http._check_url("https://anywhere.example.com/x")
    security_http._check_url("https://EXAMPLE.COM/y")
    security_http._check_url("https://example.com:443/y")


def test_secure_urlopen_rejects_plain_http():
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http.secure_urlopen("http://example.com/x", timeout=5)


def test_secure_urlopen_ignores_legacy_allowed_hosts_kwarg():
    """`allowed_hosts` is accepted but ignored (backward compat for
    monkeypatched callers) — an unlisted HTTPS host is fetched, not
    refused."""

    class _Resp:
        status_code = 200
        headers = {}
        history = []
        url = "https://evil.example.com/x"
        reason_phrase = "OK"

        @property
        def content(self):
            return b"ok"

        def close(self):
            pass

        def iter_bytes(self, n=65536):
            yield b"ok"

    client = _FakeClient(_Resp())

    import nostalgia_launcher.core.security_http as sh

    orig = sh.make_secure_client
    sh.make_secure_client = lambda **kw: client
    try:
        with sh.secure_urlopen(
            "https://evil.example.com/x",
            timeout=5,
            allowed_hosts={"example.com"},
        ) as r:
            assert r.read() == b"ok"
    finally:
        sh.make_secure_client = orig


class _FakeClient:
    """Minimal ``httpx.Client`` stand-in for transport tests."""

    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def build_request(self, method, url, headers=None, content=None):
        import httpx

        return httpx.Request(method, url, headers=headers or {})

    def send(self, req):
        return self._resp


# redirect checks are now via _check_url / _check_redirect_chain
def test_redirect_https_downgrade_blocked():
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http._check_url("http://b.example.com/y")


def test_redirect_to_any_https_host_allowed():
    security_http._check_url("https://b.example.com/y")
    security_http._check_url("https://evil.example.com/y")


class _FakeResp:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_read_capped_returns_joined_when_under_limit():
    resp = _FakeResp([b"abc", b"de", b"f"])
    assert security_http.read_capped(resp, 1024) == b"abcdef"


def test_read_capped_raises_when_over_limit():
    resp = _FakeResp([b"abc", b"xyz"])
    with pytest.raises(RuntimeError, match="limit"):
        security_http.read_capped(resp, 4)


class _FakeResponse:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_read_capped_returns_full_body_when_under_limit():
    body = b"hello world"
    r = _FakeResponse([body[i : i + 3] for i in range(0, len(body), 3)])
    assert security_http.read_capped(r, 16 * 1024 * 1024) == body


def test_read_capped_raises_on_overflow():
    big = b"x" * (8 * 1024 * 1024)
    r = _FakeResponse([big, big, big])
    with pytest.raises(RuntimeError, match="limit"):
        security_http.read_capped(r, 16 * 1024 * 1024)


def test_read_capped_empty_body():
    assert security_http.read_capped(_FakeResponse([]), 1024) == b""


def test_check_redirect_chain_blocks_http_downgrade_in_history():
    import httpx

    req = httpx.Request("GET", "https://a.example.com/x")
    hist_req = httpx.Request("GET", "http://a.example.com/y")
    hist_resp = httpx.Response(
        302, request=hist_req, headers={"location": "https://a.example.com/x"}
    )
    final_resp = httpx.Response(200, request=req)
    final_resp.history = [hist_resp]  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http._check_redirect_chain(final_resp)


def test_check_redirect_chain_allows_cross_host_https_chain():
    import httpx

    req1 = httpx.Request("GET", "https://a.example.com/x")
    hist = httpx.Response(
        302, request=req1, headers={"location": "https://cdn.example.com/y"}
    )
    req2 = httpx.Request("GET", "https://cdn.example.com/y")
    final = httpx.Response(200, request=req2)
    final.history = [hist]  # type: ignore[attr-defined]
    security_http._check_redirect_chain(final)  # no raise

"""Unit tests for the hardened HTTP layer (httpx-backed)."""

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
        security_http._check_url(url, None)


def test_check_url_rejects_disallowed_host():
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http._check_url("https://evil.example.com/x", {"example.com"})


def test_check_url_allows_https_and_allowlisted_host():
    security_http._check_url("https://example.com/x", {"example.com"})


def test_check_url_allowlist_none_permits_any_https():
    security_http._check_url("https://anywhere.example.com/x", None)


def test_secure_urlopen_rejects_plain_http():
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http.secure_urlopen("http://example.com/x", timeout=5)


def test_secure_urlopen_rejects_bad_initial_host():
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http.secure_urlopen(
            "https://evil.example.com/x",
            timeout=5,
            allowed_hosts={"example.com"},
        )


def test_allowed_download_hosts_include_launcher_and_git_hosts():
    hosts = security_http.allowed_download_hosts()
    assert "launcher.test" in hosts
    assert "github.com" in hosts
    assert "gitlab.com" in hosts
    assert "codeberg.org" in hosts


# redirect checks are now via _check_url / _check_redirect_chain
def test_redirect_https_downgrade_blocked():
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http._check_url("http://b.example.com/y", {"a.example.com"})


def test_redirect_allowlist_enforced():
    security_http._check_url(
        "https://b.example.com/y", {"a.example.com", "b.example.com"}
    )
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http._check_url(
            "https://evil.example.com/y", {"a.example.com"}
        )


def test_redirect_case_insensitive_and_port():
    security_http._check_url("https://EXAMPLE.COM/y", {"example.com"})
    security_http._check_url("https://example.com:443/y", {"example.com"})


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.com.evil.example.com/x",
        "https://example.com.evil.com/x",
        "https://example.com@evil.com/x",
    ],
)
def test_redirect_hostname_edge_cases_blocked(evil):
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http._check_url(evil, {"example.com"})


def test_redirect_subdomain_not_allowed():
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http._check_url("https://sub.example.com/y", {"example.com"})


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


def test_check_redirect_chain_blocks_evil_history():
    import httpx

    req = httpx.Request("GET", "https://a.example.com/x")
    hist_req = httpx.Request("GET", "https://evil.example.com/y")
    hist_resp = httpx.Response(
        302, request=hist_req, headers={"location": "https://a.example.com/x"}
    )
    final_resp = httpx.Response(200, request=req)
    final_resp.history = [hist_resp]  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http._check_redirect_chain(final_resp, {"a.example.com"})


def test_check_redirect_chain_allows_same_host_chain():
    import httpx

    req1 = httpx.Request("GET", "https://a.example.com/x")
    hist = httpx.Response(
        302, request=req1, headers={"location": "https://a.example.com/y"}
    )
    req2 = httpx.Request("GET", "https://a.example.com/y")
    final = httpx.Response(200, request=req2)
    final.history = [hist]  # type: ignore[attr-defined]
    security_http._check_redirect_chain(final, {"a.example.com"})  # no raise

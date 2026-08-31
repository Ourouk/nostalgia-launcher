"""Unit tests for the hardened HTTP layer."""

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
    # The launcher-configured server (from the test conftest) joins the git
    # hosts in the runtime allowlist.
    assert "launcher.test" in hosts
    assert "github.com" in hosts
    assert "gitlab.com" in hosts
    assert "codeberg.org" in hosts


def test_enforce_https_request_forbids_http():
    import httpx

    req = httpx.Request("GET", "http://b.example.com/y")
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http._enforce_https_request(req)


def test_enforce_https_request_allows_https():
    import httpx

    req = httpx.Request("GET", "https://cdn.example.com/y")
    # Should not raise
    security_http._enforce_https_request(req)


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
    """A response whose ``read`` returns canned chunks, then EOF."""

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
    # Each chunk is 8 MiB; two of them already exceed the 16 MiB cap.
    big = b"x" * (8 * 1024 * 1024)
    r = _FakeResponse([big, big, big])
    with pytest.raises(RuntimeError, match="limit"):
        security_http.read_capped(r, 16 * 1024 * 1024)


def test_read_capped_empty_body():
    assert security_http.read_capped(_FakeResponse([]), 1024) == b""

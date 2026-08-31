"""Unit tests for the hardened HTTP layer."""

import urllib.request

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


def test_redirect_handler_forbids_https_downgrade():
    handler = security_http._HttpsOnlyRedirectHandler()
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        handler.redirect_request(
            urllib.request.Request("https://a.example.com/x"),
            fp=None,
            code=302,
            msg="Found",
            headers={"Location": "http://b.example.com/y"},
            newurl="http://b.example.com/y",
        )


def test_redirect_handler_allows_https_redirect_target():
    handler = security_http._HttpsOnlyRedirectHandler()
    req = handler.redirect_request(
        urllib.request.Request("https://example.com/x"),
        fp=None,
        code=302,
        msg="Found",
        headers={"Location": "https://cdn.example.com/y"},
        newurl="https://cdn.example.com/y",
    )
    assert req.full_url == "https://cdn.example.com/y"


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


# ── redirect host enforcement (regression: Track 4) ──────────────────────────


def test_redirect_allowed_to_allowed():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"a.example.com", "b.example.com"}
    )
    req = handler.redirect_request(
        urllib.request.Request("https://a.example.com/x"),
        fp=None,
        code=302,
        msg="Found",
        headers={"Location": "https://b.example.com/y"},
        newurl="https://b.example.com/y",
    )
    assert req.full_url == "https://b.example.com/y"


def test_redirect_allowed_to_disallowed_blocks():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"a.example.com"}
    )
    with pytest.raises(RuntimeError, match="unexpected host"):
        handler.redirect_request(
            urllib.request.Request("https://a.example.com/x"),
            fp=None,
            code=302,
            msg="Found",
            headers={"Location": "https://evil.example.com/y"},
            newurl="https://evil.example.com/y",
        )


def test_redirect_allowed_to_http_blocks():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"a.example.com"}
    )
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        handler.redirect_request(
            urllib.request.Request("https://a.example.com/x"),
            fp=None,
            code=302,
            msg="Found",
            headers={"Location": "http://a.example.com/y"},
            newurl="http://a.example.com/y",
        )


def test_redirect_multiple_redirects_last_blocks():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"a.example.com", "b.example.com"}
    )
    req1 = handler.redirect_request(
        urllib.request.Request("https://a.example.com/x"),
        fp=None,
        code=302,
        msg="Found",
        headers={"Location": "https://b.example.com/y"},
        newurl="https://b.example.com/y",
    )
    assert req1.full_url == "https://b.example.com/y"
    with pytest.raises(RuntimeError, match="unexpected host"):
        handler.redirect_request(
            req1,
            fp=None,
            code=302,
            msg="Found",
            headers={"Location": "https://evil.test/z"},
            newurl="https://evil.test/z",
        )


def test_redirect_attacker_https_cannot_escape():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"launcher.test"}
    )
    with pytest.raises(RuntimeError, match="unexpected host"):
        handler.redirect_request(
            urllib.request.Request("https://launcher.test/x"),
            fp=None,
            code=302,
            msg="Found",
            headers={"Location": "https://attacker.test/evil"},
            newurl="https://attacker.test/evil",
        )


def test_redirect_case_insensitive_host():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"example.com"}
    )
    req = handler.redirect_request(
        urllib.request.Request("https://example.com/x"),
        fp=None,
        code=302,
        msg="Found",
        headers={"Location": "https://EXAMPLE.COM/y"},
        newurl="https://EXAMPLE.COM/y",
    )
    assert req.full_url == "https://EXAMPLE.COM/y"


def test_redirect_subdomain_not_allowed():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"example.com"}
    )
    with pytest.raises(RuntimeError, match="unexpected host"):
        handler.redirect_request(
            urllib.request.Request("https://example.com/x"),
            fp=None,
            code=302,
            msg="Found",
            headers={"Location": "https://sub.example.com/y"},
            newurl="https://sub.example.com/y",
        )


def test_redirect_port_stripped_allows_with_port():
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"example.com"}
    )
    req = handler.redirect_request(
        urllib.request.Request("https://example.com/x"),
        fp=None,
        code=302,
        msg="Found",
        headers={"Location": "https://example.com:443/y"},
        newurl="https://example.com:443/y",
    )
    assert req.full_url == "https://example.com:443/y"


@pytest.mark.parametrize(
    "evil",
    [
        "https://evil.com.evil.example.com/x",
        "https://example.com.evil.com/x",
        "https://example.com@evil.com/x",
    ],
)
def test_redirect_hostname_edge_cases_blocked(evil):
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"example.com"}
    )
    with pytest.raises(RuntimeError, match="unexpected host"):
        handler.redirect_request(
            urllib.request.Request("https://example.com/x"),
            fp=None,
            code=302,
            msg="Found",
            headers={"Location": evil},
            newurl=evil,
        )


def test_secure_urlopen_allows_redirect_within_allowlist(monkeypatch):
    # Simulate a redirect via the handler directly: secure_urlopen
    # checks initial host, then handler checks redirect host.
    # We test the handler path, as secure_urlopen's opener uses it.
    handler = security_http._HttpsOnlyRedirectHandler(
        allowed_hosts={"a.example.com", "b.example.com"}
    )
    # initial check passes
    security_http._check_url(
        "https://a.example.com/x", {"a.example.com", "b.example.com"}
    )
    req = handler.redirect_request(
        urllib.request.Request("https://a.example.com/x"),
        fp=None,
        code=302,
        msg="Found",
        headers={"Location": "https://b.example.com/y"},
        newurl="https://b.example.com/y",
    )
    assert req.full_url == "https://b.example.com/y"

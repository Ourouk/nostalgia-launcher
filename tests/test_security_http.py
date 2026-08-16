"""Unit tests for the hardened HTTP layer."""

import urllib.request

import pytest

import vanilla_wow_launcher.core.security_http as security_http


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

"""Unit tests for the hardened HTTP layer."""

import pytest
import urllib.request

import octo_updater.core.security_http as security_http


@pytest.mark.parametrize("url", [
    "http://octowow.st/file",
    "ftp://octowow.st/file",
])
def test_check_url_rejects_non_https(url):
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http._check_url(url, None)


def test_check_url_rejects_disallowed_host():
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http._check_url("https://evil.example.com/x", {"octowow.st"})


def test_check_url_allows_https_and_allowlisted_host():
    security_http._check_url("https://octowow.st/x", {"octowow.st"})


def test_check_url_allowlist_none_permits_any_https():
    security_http._check_url("https://anywhere.example.com/x", None)


def test_secure_urlopen_rejects_plain_http():
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        security_http.secure_urlopen("http://octowow.st/x", timeout=5)


def test_secure_urlopen_rejects_bad_initial_host():
    with pytest.raises(RuntimeError, match="unexpected host"):
        security_http.secure_urlopen(
            "https://evil.example.com/x", timeout=5,
            allowed_hosts={"octowow.st"})


def test_allowed_download_hosts_are_all_https_and_common():
    hosts = security_http.ALLOWED_DOWNLOAD_HOSTS
    assert "octowow.st" in hosts
    assert "github.com" in hosts


def test_redirect_handler_forbids_https_downgrade():
    handler = security_http._HttpsOnlyRedirectHandler()
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        handler.redirect_request(
            urllib.request.Request("https://a.example.com/x"),
            fp=None, code=302, msg="Found",
            headers={"Location": "http://b.example.com/y"},
            newurl="http://b.example.com/y")


def test_redirect_handler_allows_https_redirect_target():
    handler = security_http._HttpsOnlyRedirectHandler()
    req = handler.redirect_request(
        urllib.request.Request("https://octowow.st/x"),
        fp=None, code=302, msg="Found",
        headers={"Location": "https://dl.octowow.st/y"},
        newurl="https://dl.octowow.st/y")
    assert req.full_url == "https://dl.octowow.st/y"

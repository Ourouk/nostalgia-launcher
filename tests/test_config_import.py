"""Unit tests for config-import URL fetching."""

import io

import pytest

from nostalgia_launcher.services import config_import


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Returns a canned body of `size` bytes for any urlopen."""

    def __init__(self, body: bytes):
        self._body = body

    def __call__(self, req, timeout=0):
        return _FakeResponse(self._body)


def _install(monkeypatch, body: bytes):
    monkeypatch.setattr(config_import, "secure_urlopen", _FakeOpener(body))


def test_check_config_url_rejects_non_https():
    for bad in ("http://x/c.json", "ftp://x/c.json", "x", ""):
        try:
            config_import.check_config_url(bad)
            pytest.fail(f"expected rejection of {bad!r}")
        except config_import.ConfigUrlError:
            pass


def test_fetch_config_url_parses_valid(monkeypatch):
    payload = '{"server": {"base_url": "https://x.example"}}'
    _install(monkeypatch, payload.encode("utf-8"))
    data, raw, err = config_import.fetch_config_url("https://x.example/c.json")
    assert err == ""
    assert data == {"server": {"base_url": "https://x.example"}}
    assert raw == payload


def test_fetch_config_url_rejects_oversized(monkeypatch):
    # One byte over the cap must be refused without buffering the whole body.
    body = b"x" * (config_import.CONFIG_FETCH_MAX_BYTES + 1)
    _install(monkeypatch, body)
    data, raw, err = config_import.fetch_config_url(
        "https://x.example/big.json"
    )
    assert data is None and raw is None
    assert "larger than" in err


def test_fetch_config_url_rejects_non_object(monkeypatch):
    _install(monkeypatch, b"[1, 2, 3]")
    data, raw, err = config_import.fetch_config_url(
        "https://x.example/list.json"
    )
    assert data is None and raw is None
    assert "not a JSON object" in err


def test_fetch_config_url_rejects_bad_json(monkeypatch):
    _install(monkeypatch, b"not json")
    data, raw, err = config_import.fetch_config_url(
        "https://x.example/bad.json"
    )
    assert data is None and raw is None
    assert err


def test_check_config_url_malformed_raises_config_url_error():
    """A URL urllib itself can't parse (broken IPv6 literal) surfaces as
    ConfigUrlError — the type every caller's except-clause catches —
    never as a raw ValueError."""
    import pytest

    with pytest.raises(config_import.ConfigUrlError):
        config_import.check_config_url("https://[::1")

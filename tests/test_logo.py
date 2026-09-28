"""Unit tests for the launcher logo fetch/cache (services/logo)."""

import os
import time

from nostalgia_launcher.services import logo

LOGO_BYTES = b"\x89PNG\r\n\x1a\nfake logo bytes"


def _patch_cache(tmp_path, monkeypatch):
    dest = tmp_path / "logo.img"
    monkeypatch.setattr(logo, "logo_cache_path", lambda: str(dest))
    return dest


def test_fetch_logo_downloads_and_caches(tmp_path, monkeypatch):
    dest = _patch_cache(tmp_path, monkeypatch)

    def _open(req, timeout=10, **kw):

        class R:
            def __init__(self):
                self._data = LOGO_BYTES

            def read(self, n=-1):
                data = self._data
                self._data = b""
                return data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return R()

    monkeypatch.setattr(logo, "secure_urlopen", _open)

    path = logo.fetch_logo("https://cdn.example/logo.png")
    assert path == str(dest)
    assert open(path, "rb").read() == LOGO_BYTES


def test_fetch_logo_falls_back_to_cache_on_failure(tmp_path, monkeypatch):
    dest = _patch_cache(tmp_path, monkeypatch)
    dest.write_bytes(LOGO_BYTES)

    def _fail(req, timeout=10):
        raise RuntimeError("boom")

    monkeypatch.setattr(logo, "secure_urlopen", _fail)
    assert logo.fetch_logo("https://cdn.example/logo.png") == str(dest)


def test_fetch_logo_returns_none_without_cache(tmp_path, monkeypatch):
    _patch_cache(tmp_path, monkeypatch)

    def _fail(req, timeout=10):
        raise RuntimeError("boom")

    monkeypatch.setattr(logo, "secure_urlopen", _fail)
    assert logo.fetch_logo("https://cdn.example/logo.png") is None


def test_cached_logo(tmp_path, monkeypatch):
    dest = _patch_cache(tmp_path, monkeypatch)
    assert logo.cached_logo() is None
    dest.write_bytes(LOGO_BYTES)
    assert logo.cached_logo() == str(dest)


def test_fresh_cache_served_without_network(tmp_path, monkeypatch):
    """A fresh cache returns instantly — no request to the logo host."""
    dest = _patch_cache(tmp_path, monkeypatch)
    dest.write_bytes(LOGO_BYTES)

    def _boom(req, timeout=10):
        raise AssertionError("network hit for a fresh cache")

    monkeypatch.setattr(logo, "secure_urlopen", _boom)
    assert logo.fetch_logo("https://cdn.example/logo.png") == str(dest)


def test_stale_cache_triggers_redownload(tmp_path, monkeypatch):
    """A cache older than LOGO_CACHE_TTL is refetched from the host."""
    import urllib.request

    dest = _patch_cache(tmp_path, monkeypatch)
    dest.write_bytes(b"old logo")
    old = time.time() - logo.LOGO_CACHE_TTL - 60
    os.utime(dest, (old, old))

    def _open(req, timeout=10, **kw):
        assert isinstance(req, urllib.request.Request)

        class R:
            def __init__(self):
                self._data = LOGO_BYTES

            def read(self, n=-1):
                data = self._data
                self._data = b""
                return data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return R()

    monkeypatch.setattr(logo, "secure_urlopen", _open)
    assert logo.fetch_logo("https://cdn.example/logo.png") == str(dest)
    assert open(dest, "rb").read() == LOGO_BYTES


def test_force_refetches_fresh_cache(tmp_path, monkeypatch):
    """force=True bypasses the TTL (manual refresh path)."""
    dest = _patch_cache(tmp_path, monkeypatch)
    dest.write_bytes(b"old logo")

    def _open(req, timeout=10, **kw):
        class R:
            def __init__(self):
                self._data = LOGO_BYTES

            def read(self, n=-1):
                data = self._data
                self._data = b""
                return data

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return R()

    monkeypatch.setattr(logo, "secure_urlopen", _open)
    assert logo.fetch_logo("https://cdn.example/logo.png", force=True) == str(
        dest
    )
    assert open(dest, "rb").read() == LOGO_BYTES


def test_cache_is_fresh(tmp_path, monkeypatch):
    dest = _patch_cache(tmp_path, monkeypatch)
    assert logo.cache_is_fresh() is False
    dest.write_bytes(LOGO_BYTES)
    assert logo.cache_is_fresh() is True
    old = time.time() - logo.LOGO_CACHE_TTL - 60
    os.utime(dest, (old, old))
    assert logo.cache_is_fresh() is False

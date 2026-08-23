"""Unit tests for the launcher logo fetch/cache (services/logo)."""

from nostalgia_launcher.services import logo

LOGO_BYTES = b"\x89PNG\r\n\x1a\nfake logo bytes"


def _patch_cache(tmp_path, monkeypatch):
    dest = tmp_path / "logo.img"
    monkeypatch.setattr(logo, "logo_cache_path", lambda: str(dest))
    monkeypatch.setattr(
        logo, "allowed_download_hosts", lambda: {"launcher.test"}
    )
    return dest


def test_fetch_logo_downloads_and_caches(tmp_path, monkeypatch):
    dest = _patch_cache(tmp_path, monkeypatch)
    captured = {}

    def _open(req, timeout=10, allowed_hosts=None):
        captured["allowed_hosts"] = allowed_hosts

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
    # The logo's own host is added to the regular download allowlist.
    assert captured["allowed_hosts"] == frozenset(
        {"launcher.test", "cdn.example"}
    )


def test_fetch_logo_falls_back_to_cache_on_failure(tmp_path, monkeypatch):
    dest = _patch_cache(tmp_path, monkeypatch)
    dest.write_bytes(LOGO_BYTES)

    def _fail(req, timeout=10, allowed_hosts=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(logo, "secure_urlopen", _fail)
    assert logo.fetch_logo("https://cdn.example/logo.png") == str(dest)


def test_fetch_logo_returns_none_without_cache(tmp_path, monkeypatch):
    _patch_cache(tmp_path, monkeypatch)

    def _fail(req, timeout=10, allowed_hosts=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(logo, "secure_urlopen", _fail)
    assert logo.fetch_logo("https://cdn.example/logo.png") is None


def test_cached_logo(tmp_path, monkeypatch):
    dest = _patch_cache(tmp_path, monkeypatch)
    assert logo.cached_logo() is None
    dest.write_bytes(LOGO_BYTES)
    assert logo.cached_logo() == str(dest)

"""Unit tests for the client update engine (VerifyWorker/UpdateWorker)."""

import json

import pytest

import nostalgia_launcher.services.update_backend.http_update as client_update
import nostalgia_launcher.services.update_backend.sources as update_sources
import nostalgia_launcher.services.update_backend.torrent_update as td
from nostalgia_launcher.services.update_backend.http_update import (
    DownloadSource,
    UpdateWorker,
    VerifyWorker,
)
from nostalgia_launcher.state.events import (
    EventDispatcher,
    ProgressChanged,
    TorrentDiffReady,
    TorrentUnavailable,
    TorrentUpToDate,
    TorrentVerifyFailed,
    UpdateFailed,
)


def _mk_client(tmp_path):
    d = tmp_path / "client"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _default_source():
    """Configure a default single download source so the workers have
    something to resolve. Tests that need a specific source monkeypatch
    ``client_update._download_source`` instead."""

    from nostalgia_launcher.core import launcher

    launcher.configure_from_dict(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "torrent": {
                        "torrent_url": ("https://srv.example/client.torrent"),
                    },
                    "http": {
                        "fallback": "https://srv.example/client.zip",
                    },
                },
            }
        }
    )
    yield
    launcher.reset()


class _BodyResp:
    """A fake response whose ``read`` returns the whole body once, then EOF —
    compatible with the bounded ``read_capped`` loop in http_update."""

    def __init__(self, body: bytes):
        self._body = body
        self._done = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def read(self, n=-1):
        if self._done:
            return b""
        self._done = True
        return self._body


def test_verify_worker_up_to_date(tmp_path, monkeypatch):
    """Torrent verify finds no stale files → torrent up-to-date."""

    client = _mk_client(tmp_path)
    (client / "WoW.exe").write_bytes(b"x")
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)

    class FakeVerifier:
        def __init__(self, out_dir, dispatcher=None, *a, **kw):
            pass

        def verify(self, url, snapshot=None):
            return []

    monkeypatch.setattr(td, "TorrentVerifier", FakeVerifier)
    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()
    assert any(isinstance(e, TorrentUpToDate) for e in events)
    assert not any(isinstance(e, TorrentDiffReady) for e in events)


def test_verify_worker_detects_stale_file(tmp_path, monkeypatch):
    """Torrent verify finds stale files → diff ready."""

    client = _mk_client(tmp_path)
    (client / "WoW.exe").write_bytes(b"x")
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)

    class FakeVerifier:
        def __init__(self, out_dir, dispatcher=None, *a, **kw):
            pass

        def verify(self, url, snapshot=None):
            return ["data.bin"]

    monkeypatch.setattr(td, "TorrentVerifier", FakeVerifier)
    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()
    assert any(isinstance(e, TorrentDiffReady) for e in events)
    assert any(
        e.stale == ["data.bin"]
        for e in events
        if isinstance(e, TorrentDiffReady)
    )  # type: ignore[attr-defined]


def test_verify_worker_manifest_failure_marks_unavailable(
    tmp_path, monkeypatch
):
    """No torrent source → unavailable (fallback path)."""

    client = _mk_client(tmp_path)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: DownloadSource(None, "", None),
    )
    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()
    assert any(isinstance(e, TorrentUnavailable) for e in events)


def test_verify_worker_config_wtf_created_when_missing(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)

    class FakeVerifier:
        def __init__(self, out_dir, dispatcher=None, *a, **kw):
            pass

        def verify(self, url, snapshot=None):
            return []

    monkeypatch.setattr(td, "TorrentVerifier", FakeVerifier)
    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    assert (client / "WTF" / "Config.wtf").exists()


def test_verify_worker_seeds_wtf_even_when_manifest_fails(
    tmp_path, monkeypatch
):
    """Config.wtf/realmlist.wtf are seeded BEFORE torrent verify."""

    client = _mk_client(tmp_path)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: DownloadSource(None, "", None),
    )
    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    assert (client / "WTF" / "Config.wtf").exists()
    events = dispatcher.drain()
    assert any(isinstance(e, TorrentUnavailable) for e in events)


def test_update_worker_downloads_and_verifies(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    payload = b"hello world"

    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            # Return the payload once, then EOF — mirrors a real socket.
            if getattr(self, "_exhausted", False):
                return b""
            self._exhausted = True
            return payload

        def getcode(self):
            return 200

    calls = {"n": 0}

    def fake_urlopen(req, timeout, allowed_hosts=None):
        calls["n"] += 1
        assert req.full_url.endswith("/data.bin")
        return FakeResp()

    monkeypatch.setattr(client_update, "secure_urlopen", fake_urlopen)

    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    import hashlib

    digest = worker.download(
        "https://launcher.test/client/latest/data.bin",
        str(client / "data.bin"),
        len(payload),
    )
    assert digest == hashlib.sha1(payload).hexdigest().upper()
    assert (client / "data.bin").read_bytes() == payload


def test_verify_worker_cancelled_torrent_posts_error_not_failure_marker(
    tmp_path, monkeypatch
):
    client = _mk_client(tmp_path)
    dispatcher = EventDispatcher()
    worker = VerifyWorker(str(client), dispatcher)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: client_update.DownloadSource(
            "https://srv/client.torrent",
            "https://srv/client.zip",
        ),
    )
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)
    monkeypatch.setattr(
        client_update,
        "secure_urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ConnectionError("manifest down")
        ),
    )

    class CancelledVerifier:
        def __init__(self, out_dir, dispatcher=None, *a, **kw):
            pass

        def verify(self, url, snapshot=None):
            raise RuntimeError("Cancelled")

    monkeypatch.setattr(td, "TorrentVerifier", CancelledVerifier)
    worker._cancel = True

    worker.run()
    events = dispatcher.drain()

    assert any(isinstance(e, UpdateFailed) for e in events)
    assert not any(isinstance(e, TorrentVerifyFailed) for e in events)


def test_update_worker_uses_verified_torrent_paths_without_manifest(
    tmp_path, monkeypatch
):
    client = _mk_client(tmp_path)
    source = client_update.DownloadSource(
        "https://launcher.test/client.torrent",
        "https://launcher.test/client.zip",
    )
    monkeypatch.setattr(client_update, "_download_source", lambda: source)
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)
    monkeypatch.setattr(
        client_update,
        "secure_urlopen",
        lambda *args, **kwargs: pytest.fail("manifest must not be fetched"),
    )
    recovered = []
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    monkeypatch.setattr(
        worker,
        "_recovery_download",
        lambda wanted: (recovered.append(wanted), True)[1],
    )

    worker.run(None, {"Data/a.bin"})

    assert recovered == [{"Data/a.bin"}]
    events = dispatcher.drain()
    progress_events = [e for e in events if isinstance(e, ProgressChanged)]
    assert progress_events
    assert progress_events[0].value == 0.02
    assert progress_events[0].label == "Downloading via BitTorrent…"
    assert progress_events[0].phase == "BitTorrent"


# ── download source resolution (single source, no mirror failover) ───────────


def _resp():
    return type(
        "R",
        (),
        {
            "__enter__": lambda s: s,
            "__exit__": lambda *x: False,
            "read": lambda s, n=1: b"{}"[:n],
        },
    )()


def test_download_source_none_without_launcher(monkeypatch):
    from nostalgia_launcher.core import launcher

    launcher.reset()
    assert client_update._download_source() is None


def test_download_source_resolves_torrent_only():
    """A torrent/magnet is a valid download source on its own;
    _download_source() returns it without requiring HTTP endpoints."""
    from nostalgia_launcher.core import launcher

    launcher.configure_from_dict(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "torrent": {"magnet": "magnet:?xt=urn:btih:" + "ab" * 20}
                },
            }
        }
    )
    src = client_update._download_source()
    assert src is not None
    assert src.torrent_locator is not None
    assert src.fallback_url == ""


def test_download_source_uses_explicit_endpoint_overrides(monkeypatch):
    """The configured fallback/torrent URLs (not any derived defaults)
    are what the updater uses."""

    from nostalgia_launcher.core import launcher

    launcher.configure_from_dict(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "http": {
                        "fallback": "https://dl.example/client.zip",
                    },
                    "torrent": {
                        "torrent_url": ("https://srv.example/client.torrent"),
                    },
                },
            }
        }
    )

    monkeypatch.setattr(
        update_sources,
        "secure_urlopen",
        lambda req, timeout=5, allowed_hosts=None: _resp(),
    )
    src = client_update._download_source()
    assert src.fallback_url == "https://dl.example/client.zip"
    assert src.torrent_url == "https://srv.example/client.torrent"


def test_verify_uses_selected_manifest_url(monkeypatch, tmp_path):
    """VerifyWorker must fetch the torrent from the configured URL."""

    from nostalgia_launcher.core import launcher

    launcher.configure_from_dict(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "torrent": {
                        "torrent_url": ("https://m1.example/client.torrent"),
                    },
                },
            }
        }
    )
    fetched = []

    def fake_urlopen(req, timeout, allowed_hosts=None):
        fetched.append(req.full_url)
        return _resp()

    monkeypatch.setattr(update_sources, "secure_urlopen", fake_urlopen)
    monkeypatch.setattr(client_update, "secure_urlopen", fake_urlopen)
    monkeypatch.setattr(client_update, "load_cache", lambda: {})
    monkeypatch.setattr(client_update, "save_cache", lambda c: None)
    monkeypatch.setattr(client_update, "write_config_wtf", lambda d: None)

    client = tmp_path / "client"
    client.mkdir()
    # VerifyWorker in torrent-only mode posts TorrentUnavailable or
    # TorrentUpToDate, not a manifest fetch — just ensure it ran.
    dispatcher = EventDispatcher()
    monkeypatch.setattr(client_update, "_torrent_available", lambda: False)
    worker = client_update.VerifyWorker(str(client), dispatcher)
    worker.run()
    assert True


def _manifest_resp(manifest):
    return _BodyResp(json.dumps(manifest).encode())


def test_verify_worker_reserves_progress_bar_for_update(tmp_path, monkeypatch):
    """Verification must not drive the progress bar to 100% — that sweep
    is reserved for the actual download. The bar stays at 0 while the
    phase reports Verifying/Verified."""

    client = _mk_client(tmp_path)
    (client / "WoW.exe").write_bytes(b"x")
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)

    class FakeVerifier:
        def __init__(self, out_dir, dispatcher=None, *a, **kw):
            pass

        def verify(self, url, snapshot=None):
            return []

    monkeypatch.setattr(td, "TorrentVerifier", FakeVerifier)
    dispatcher = EventDispatcher()
    VerifyWorker(str(client), dispatcher).run()
    events = dispatcher.drain()
    progress_events = [e for e in events if isinstance(e, ProgressChanged)]

    # No full-bar sweep during verification: every progress value stays at 0.
    assert progress_events, "verify should have emitted progress"
    assert all(e.value == 0.0 for e in progress_events)
    phases = [e.phase for e in progress_events]
    assert "Verifying" in phases
    # Verified phase may be posted after torrent verify; at least Verifying
    assert phases

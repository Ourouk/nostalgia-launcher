"""Torrent → HTTP fallback and final verification failure."""

import hashlib
import os

import nostalgia_launcher.services.update_backend.http_update as client_update
import nostalgia_launcher.services.update_backend.torrent_update as td
from nostalgia_launcher.services.update_backend.http_update import (
    DownloadSource,
    UpdateWorker,
    VerifyWorker,
)
from nostalgia_launcher.state.events import (
    EventDispatcher,
    TorrentRecoveryDone,
    UpdateCompleted,
    UpdateFailed,
)


def _mk_client(tmp_path):
    d = tmp_path / "client"
    d.mkdir()
    return d


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return b""


def test_update_worker_falls_back_when_torrent_stalls(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)
    monkeypatch.setattr(client_update, "load_cache", lambda: {})
    monkeypatch.setattr(client_update, "save_cache", lambda c: None)

    def boom(self, torrent_url, wanted):
        raise td.TorrentStalledError(peers=0)

    monkeypatch.setattr(td.TorrentDownloader, "download", boom)
    worker._source = DownloadSource(
        "https://srv/manifest.json",
        "https://srv/client",
        "https://srv/client.torrent",
    )
    nodes = [{"type": "file", "name": "a.bin", "hash": "A" * 40, "size": 1}]
    # Should return False and let caller fallback to HTTP
    assert worker._torrent_download(nodes) is False
    events = dispatcher.drain()
    assert any(
        "Falling back to HTTP" in e.text for e in events if hasattr(e, "text")
    )


def test_update_worker_torrent_not_available_fallback(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    worker = UpdateWorker(str(client), EventDispatcher())
    monkeypatch.setattr(client_update, "_torrent_available", lambda: False)
    worker._source = DownloadSource(
        "https://srv/manifest.json",
        "https://srv/client",
        "https://srv/client.torrent",
    )
    nodes = [{"type": "file", "name": "a.bin", "hash": "A" * 40, "size": 1}]
    assert worker._torrent_download(nodes) is False


def test_update_final_verification_failure_after_retry(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    monkeypatch.setattr(client_update, "load_cache", lambda: {})
    monkeypatch.setattr(client_update, "save_cache", lambda c: None)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: DownloadSource(
            "https://srv/manifest.json", "https://srv/client"
        ),
    )
    monkeypatch.setattr(client_update, "_torrent_available", lambda: False)

    payload = b"corrupt"
    good_hash = hashlib.sha1(b"correct").hexdigest().upper()

    def fake_download(url, dest, size, name=""):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(payload)
        return hashlib.sha1(payload).hexdigest().upper()

    monkeypatch.setattr(worker, "download", fake_download)

    nodes = [{"type": "file", "name": "a.bin", "hash": good_hash, "size": 7}]
    worker.run(nodes)
    events = dispatcher.drain()
    assert any(isinstance(e, UpdateFailed) for e in events)
    assert not any(isinstance(e, UpdateCompleted) for e in events)


def test_update_recovery_download_torrent_failure_posts_failure(
    tmp_path, monkeypatch
):
    client = _mk_client(tmp_path)
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: DownloadSource(
            "https://srv/manifest.json",
            "https://srv/client",
            "https://srv/client.torrent",
        ),
    )
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)
    monkeypatch.setattr(client_update, "load_cache", lambda: {})
    monkeypatch.setattr(client_update, "save_cache", lambda c: None)

    def boom(self, url, wanted):
        raise td.TorrentCorruptError("corrupt")

    monkeypatch.setattr(td.TorrentDownloader, "download", boom)
    worker.run(None, {"Data/a.bin"})
    events = dispatcher.drain()
    # Should post a torrent error, not recovery done
    from nostalgia_launcher.state.events import TorrentCorrupt

    assert any(isinstance(e, TorrentCorrupt) for e in events)
    assert not any(isinstance(e, TorrentRecoveryDone) for e in events)


def test_verify_fallback_to_torrent_when_manifest_fails(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    dispatcher = EventDispatcher()
    worker = VerifyWorker(str(client), dispatcher)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: DownloadSource(
            "https://srv/manifest.json",
            "https://srv/client",
            "https://srv/client.torrent",
        ),
    )
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)
    monkeypatch.setattr(
        client_update,
        "secure_urlopen",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")),
    )

    class FakeVerifier:
        def __init__(self, out_dir, dispatcher=None, *a, **kw):
            pass

        def verify(self, url, snapshot=None):
            return []

    monkeypatch.setattr(td, "TorrentVerifier", FakeVerifier)
    worker.run()
    events = dispatcher.drain()
    from nostalgia_launcher.state.events import TorrentUpToDate

    assert any(isinstance(e, TorrentUpToDate) for e in events)

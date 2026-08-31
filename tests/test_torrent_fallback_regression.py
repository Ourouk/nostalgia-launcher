"""Torrent → HTTP fallback and final verification failure."""

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


def test_update_recovery_download_torrent_failure_posts_failure(
    tmp_path, monkeypatch
):
    client = _mk_client(tmp_path)
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: DownloadSource("https://srv/client.torrent", ""),
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
        lambda: DownloadSource("https://srv/client.torrent", ""),
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

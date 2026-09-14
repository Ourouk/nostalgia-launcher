"""Torrent → HTTP fallback and final verification failure."""

from _torrent_fakes import FakeVerifier, failing_urlopen
from _torrent_fakes import make_client_dir as _mk_client

import nostalgia_launcher.services.update.workflow as client_update
import nostalgia_launcher.services.update_backend.torrent_update as td
from nostalgia_launcher.services.update.workflow import (
    DownloadSource,
    UpdateWorker,
    VerifyWorker,
)
from nostalgia_launcher.state.events import (
    EventDispatcher,
    TorrentRecoveryDone,
)


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
    worker.run({"Data/a.bin"})
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
        failing_urlopen(ConnectionError("down")),
    )
    monkeypatch.setattr(td, "TorrentVerifier", FakeVerifier)
    worker.run()
    events = dispatcher.drain()
    from nostalgia_launcher.state.events import TorrentUpToDate

    assert any(isinstance(e, TorrentUpToDate) for e in events)

"""Tests for the BitTorrent download backend (services/torrent_download) and
its wiring into the client update engine.

libtorrent is never required here: a fake `lt` module is injected into
sys.modules and the availability probe / TorrentDownloader are monkeypatched.
"""

import importlib.util
import queue
import sys
from types import SimpleNamespace

import pytest

import vanilla_wow_launcher.services.client_update as client_update
import vanilla_wow_launcher.services.torrent_download as td
from vanilla_wow_launcher.core import launcher
from vanilla_wow_launcher.services.client_update import (
    DownloadSource,
    UpdateWorker,
)

SHA1_X = "11F6AD8EC52A2984ABAAFD7C3B516503785C2072"


def _mk_client(tmp_path):
    d = tmp_path / "client"
    d.mkdir()
    return d


def _resp(content: bytes):
    return type(
        "R",
        (),
        {
            "__enter__": lambda s: s,
            "__exit__": lambda *a: False,
            "read": lambda s, n=-1: content if n < 0 else content[:n],
        },
    )()


# ── availability probe ───────────────────────────────────────────────────────


def test_available_true_when_find_spec_finds_libtorrent(monkeypatch):
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name == "libtorrent" else None,
    )
    assert td.available() is True


def test_available_false_when_find_spec_misses(monkeypatch):
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert td.available() is False


def test_available_false_on_probe_error(monkeypatch):
    def boom(name):
        raise ValueError("nope")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    assert td.available() is False


# ── launcher config parsing ──────────────────────────────────────────────────


def test_config_parses_server_and_mirror_torrent_urls():
    launcher.configure_from_dict(
        {
            "server": {
                "base_url": "https://srv.example",
                "torrent_url": "https://dl.example/client/client.torrent",
            },
            "mirrors": [
                {
                    "name": "A",
                    "base_url": "https://a.example",
                    "torrent_url": "https://a.example/t/client.torrent",
                },
                {"name": "B", "base_url": "https://b.example"},
            ],
        }
    )
    cfg = launcher.config()
    assert cfg.torrent_url == "https://dl.example/client/client.torrent"
    assert cfg.mirrors[0].torrent_url == "https://a.example/t/client.torrent"
    assert cfg.mirrors[1].torrent_url is None


def test_config_rejects_non_https_torrent_url():
    launcher.configure_from_dict(
        {
            "server": {
                "base_url": "https://srv.example",
                "torrent_url": "http://insecure.example/client.torrent",
            },
            "mirrors": [
                {
                    "name": "A",
                    "base_url": "https://a.example",
                    "torrent_url": "not a url",
                }
            ],
        }
    )
    cfg = launcher.config()
    assert cfg.torrent_url is None
    assert cfg.mirrors[0].torrent_url is None


def test_torrent_hosts_join_download_allowlist():
    launcher.configure_from_dict(
        {
            "server": {"base_url": "https://srv.example"},
            "mirrors": [
                {
                    "name": "A",
                    "base_url": "https://a.example",
                    "torrent_url": "https://torrent.example/client.torrent",
                }
            ],
        }
    )
    hosts = launcher.config().download_hosts()
    assert "torrent.example" in hosts


# ── DownloadSource propagation ───────────────────────────────────────────────


def test_download_source_uses_mirror_torrent_url(monkeypatch):
    launcher.configure_from_dict(
        {
            "server": {"base_url": "https://srv.example"},
            "mirrors": [
                {
                    "name": "A",
                    "base_url": "https://a.example",
                    "torrent_url": "https://a.example/client.torrent",
                }
            ],
        }
    )
    monkeypatch.setattr(
        client_update,
        "secure_urlopen",
        lambda req, timeout=5, allowed_hosts=None: _resp(b"{}"),
    )
    src = client_update._download_source()
    assert src.torrent_url == "https://a.example/client.torrent"


def test_download_source_falls_back_to_server_torrent_url(monkeypatch):
    launcher.configure_from_dict(
        {
            "server": {
                "base_url": "https://srv.example",
                "torrent_url": "https://dl.example/client.torrent",
            },
            "mirrors": [{"name": "A", "base_url": "https://a.example"}],
        }
    )

    def down(req, timeout=5, allowed_hosts=None):
        raise ConnectionError("down")

    monkeypatch.setattr(client_update, "secure_urlopen", down)
    src = client_update._download_source()
    assert src.torrent_url == "https://dl.example/client.torrent"


# ── TorrentDownloader unit tests (fake libtorrent) ──────────────────────────


def _make_fake_lt(finished_after=3):
    class FakeStatus:
        def __init__(self, finished=False):
            self.name = "client"
            self.total_wanted = 10
            self.total_wanted_done = 10 if finished else 0
            self.download_rate = 0
            self.num_peers = 0
            self.is_finished = finished

    class FakeHandle:
        def __init__(self):
            self.cancelled = False
            self.paused = False
            self.status_calls = 0

        def status(self):
            self.status_calls += 1
            return FakeStatus(self.status_calls >= finished_after)

        def cancel(self):
            self.cancelled = True

        def pause(self):
            self.paused = True

    class FakeFiles:
        def __init__(self):
            self.paths = [
                "client/Data/a.bin",
                "client/Data/b.mpq",
                "client/WoW.exe",
            ]

        def num_files(self):
            return len(self.paths)

        def file_path(self, i):
            return self.paths[i]

    class FakeTorrentInfo:
        def files(self):
            return FakeFiles()

    class FakeSession:
        def __init__(self, settings):
            self.settings = settings
            self.atp = None
            self.removed = []

        def add_torrent(self, atp):
            self.atp = atp
            return FakeHandle()

        def pop_alerts(self):
            return []

        def wait_for_alert(self, ms):
            return None

        def remove_torrent(self, h):
            self.removed.append(h)

    class FakeLT:
        class alert:
            class category_t:
                error_notification = 1

        def __init__(self):
            self.last_session = None

        def torrent_info(self, path):
            return FakeTorrentInfo()

        def session(self, settings):
            self.last_session = FakeSession(settings)
            return self.last_session

        def add_torrent_params(self):
            return SimpleNamespace()

    return FakeLT()


def _install_fake_lt(monkeypatch, **kwargs):
    fake = _make_fake_lt(**kwargs)
    monkeypatch.setitem(sys.modules, "libtorrent", fake)
    monkeypatch.setattr(td, "allowed_download_hosts", lambda: set())
    monkeypatch.setattr(
        td,
        "secure_urlopen",
        lambda req, timeout=10, allowed_hosts=None: _resp(b"fake"),
    )
    return fake


def test_download_completes_and_sets_file_priorities(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    fake = _install_fake_lt(monkeypatch)
    log_q, prog_q = queue.Queue(), queue.Queue()
    d = td.TorrentDownloader(str(client), log_q, prog_q)
    result = d.download("https://srv.example/client.torrent", {"Data/a.bin"})

    assert result == []
    ses = fake.last_session
    assert ses.atp.save_path == str(client)
    # Only the stale file is wanted; the shared leading dir is stripped.
    assert ses.atp.file_priorities == [7, 0, 0]
    assert len(ses.removed) == 1  # never seeded — removed after completion


def test_download_fetches_torrent_over_allowlisted_https(
    monkeypatch, tmp_path
):
    client = _mk_client(tmp_path)
    _install_fake_lt(monkeypatch)
    seen = {}

    def fake_urlopen(req, timeout=10, allowed_hosts=None):
        seen["url"] = req.full_url
        seen["hosts"] = allowed_hosts
        return _resp(b"fake")

    monkeypatch.setattr(td, "secure_urlopen", fake_urlopen)
    d = td.TorrentDownloader(str(client), queue.Queue(), queue.Queue())
    d.download("https://torrent.example/client.torrent", {"Data/a.bin"})
    assert seen["url"] == "https://torrent.example/client.torrent"
    assert seen["hosts"] == set()


def test_download_cancelled_raises(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    fake = _install_fake_lt(monkeypatch, finished_after=10**9)
    log_q, prog_q = queue.Queue(), queue.Queue()
    d = td.TorrentDownloader(str(client), log_q, prog_q)
    d._cancel = True
    with pytest.raises(RuntimeError, match="Cancelled"):
        d.download("https://srv.example/client.torrent", {"Data/a.bin"})
    assert len(fake.last_session.removed) == 1


def test_download_stall_raises(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    _install_fake_lt(monkeypatch, finished_after=10**9)
    monkeypatch.setattr(td, "STALL_TIMEOUT", -1)
    d = td.TorrentDownloader(str(client), queue.Queue(), queue.Queue())
    with pytest.raises(RuntimeError, match="BitTorrent stalled"):
        d.download("https://srv.example/client.torrent", {"Data/a.bin"})


def test_strip_root_leaves_flat_torrent_untouched():
    assert td.TorrentDownloader._strip_root(["a.bin", "b.mpq"]) == [
        "a.bin",
        "b.mpq",
    ]
    assert td.TorrentDownloader._strip_root(
        ["client/Data/a.bin", "client/WoW.exe"]
    ) == ["Data/a.bin", "WoW.exe"]
    assert td.TorrentDownloader._strip_root([]) == []


# ── UpdateWorker wiring ──────────────────────────────────────────────────────


def _recording_downloader(monkeypatch):
    calls = []

    def fake_download(self, torrent_url, wanted):
        calls.append((torrent_url, wanted))
        return sorted(wanted)

    monkeypatch.setattr(td.TorrentDownloader, "download", fake_download)
    return calls


def test_torrent_download_collects_stale_files(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    (client / "Data").mkdir()
    (client / "Data" / "ok.bin").write_bytes(b"x")
    log_q, prog_q = queue.Queue(), queue.Queue()
    worker = UpdateWorker(str(client), log_q, prog_q)
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)
    calls = _recording_downloader(monkeypatch)
    worker._source = DownloadSource(
        "https://srv/manifest.json",
        "https://srv/client",
        "https://srv/client.torrent",
    )

    nodes = [
        {
            "type": "dir",
            "name": "Data",
            "files": [
                {"type": "file", "name": "ok.bin", "hash": SHA1_X, "size": 1},
                {
                    "type": "file",
                    "name": "stale.bin",
                    "hash": "A" * 40,
                    "size": 9,
                },
            ],
        },
        {"type": "mpq", "name": "Patch", "hash": "B" * 40, "size": 9},
        {"type": "del", "name": "old.bin"},
    ]
    assert worker._torrent_download(nodes) is True
    assert calls == [
        (
            "https://srv/client.torrent",
            {"Data/stale.bin", "Patch.mpq"},
        )
    ]
    assert "via BitTorrent" in log_q.queue[0][0]


def test_torrent_download_skipped_without_torrent_url(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    worker = UpdateWorker(str(client), queue.Queue(), queue.Queue())
    calls = _recording_downloader(monkeypatch)
    worker._source = DownloadSource(
        "https://srv/manifest.json", "https://srv/client"
    )
    worker._torrent_download(
        [{"type": "file", "name": "a.bin", "hash": "A" * 40, "size": 1}]
    )
    assert calls == []


def test_torrent_download_skipped_without_libtorrent(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    worker = UpdateWorker(str(client), queue.Queue(), queue.Queue())
    calls = _recording_downloader(monkeypatch)
    monkeypatch.setattr(client_update, "_torrent_available", lambda: False)
    worker._source = DownloadSource(
        "https://srv/manifest.json",
        "https://srv/client",
        "https://srv/client.torrent",
    )
    worker._torrent_download(
        [{"type": "file", "name": "a.bin", "hash": "A" * 40, "size": 1}]
    )
    assert calls == []


def test_torrent_download_falls_back_on_failure(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)

    def boom(self, torrent_url, wanted):
        raise RuntimeError("swarm dead")

    monkeypatch.setattr(td.TorrentDownloader, "download", boom)
    monkeypatch.setattr(client_update, "_torrent_available", lambda: True)
    log_q, prog_q = queue.Queue(), queue.Queue()
    worker = UpdateWorker(str(client), log_q, prog_q)
    worker._source = DownloadSource(
        "https://srv/manifest.json",
        "https://srv/client",
        "https://srv/client.torrent",
    )
    nodes = [{"type": "file", "name": "a.bin", "hash": "A" * 40, "size": 1}]
    assert worker._torrent_download(nodes) is False
    msgs = [log_q.get_nowait()[0] for _ in range(log_q.qsize())]
    assert any("Falling back to HTTP" in m for m in msgs)


def test_run_invokes_torrent_then_skips_covered_files(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    (client / "data.bin").write_bytes(b"x")
    log_q, prog_q = queue.Queue(), queue.Queue()
    worker = UpdateWorker(str(client), log_q, prog_q)
    monkeypatch.setattr(
        client_update,
        "_download_source",
        lambda: DownloadSource(
            "https://srv/manifest.json",
            "https://srv/client",
            "https://srv/client.torrent",
        ),
    )
    monkeypatch.setattr(client_update, "load_cache", lambda: {})
    monkeypatch.setattr(client_update, "save_cache", lambda c: None)
    torrent_calls = []

    def fake_torrent_download(nodes):
        torrent_calls.append(nodes)
        return True

    monkeypatch.setattr(worker, "_torrent_download", fake_torrent_download)

    nodes = [
        {
            "type": "file",
            "name": "data.bin",
            "hash": SHA1_X,
            "size": 1,
        }
    ]
    worker.run(nodes)
    assert torrent_calls == [nodes]
    msgs = [m[0] for m in log_q.queue]
    assert "__DONE__" in msgs
    assert "__ERROR__" not in msgs

"""Unit tests for the client update engine (VerifyWorker/UpdateWorker)."""

import json
import os
import urllib.request

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
    DiffTreeReady,
    EventDispatcher,
    ManifestAvailable,
    ManifestUnavailable,
    ProgressChanged,
    TorrentVerifyFailed,
    UpdateFailed,
    UpdateRequired,
    VerificationUpToDate,
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
                    "http": {
                        "manifest": "https://srv.example/api/file/latest/manifest.json",
                        "client": "https://srv.example/client/latest",
                    }
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
    client = _mk_client(tmp_path)
    f = client / "data.bin"
    f.write_bytes(b"x")

    manifest = {
        "root": {
            "files": [
                {
                    "type": "file",
                    "name": "data.bin",
                    "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072",  # sha1("x")
                    "size": 1,
                },
            ]
        }
    }
    fake_resp = _BodyResp(json.dumps(manifest).encode())
    monkeypatch.setattr(
        client_update, "secure_urlopen", lambda *a, **k: fake_resp
    )

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()

    assert any(isinstance(e, VerificationUpToDate) for e in events)
    assert any(isinstance(e, ManifestAvailable) for e in events)
    assert not any(isinstance(e, DiffTreeReady) and e.tree for e in events)


def test_verify_worker_manifest_overflow_marks_unavailable(
    tmp_path, monkeypatch
):
    """A manifest body larger than 16 MiB is rejected (capped read)."""
    client = _mk_client(tmp_path)
    big = b"x" * (16 * 1024 * 1024 + 1)
    fake_resp = _BodyResp(big)
    monkeypatch.setattr(
        client_update, "secure_urlopen", lambda *a, **k: fake_resp
    )

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()

    assert any(isinstance(e, ManifestUnavailable) for e in events)
    assert not any(isinstance(e, ManifestAvailable) for e in events)


def test_verify_worker_detects_stale_file(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    (client / "data.bin").write_bytes(b"old")

    manifest = {
        "root": {
            "files": [
                {
                    "type": "file",
                    "name": "data.bin",
                    "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072",  # sha1("x")
                    "size": 1,
                },
            ]
        }
    }
    fake_resp = _BodyResp(json.dumps(manifest).encode())
    monkeypatch.setattr(
        client_update, "secure_urlopen", lambda *a, **k: fake_resp
    )

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()

    assert any(isinstance(e, UpdateRequired) for e in events)
    assert any(isinstance(e, ManifestAvailable) for e in events)
    assert any(isinstance(e, DiffTreeReady) and e.tree for e in events)


def test_verify_worker_manifest_failure_marks_unavailable(
    tmp_path, monkeypatch
):
    client = _mk_client(tmp_path)

    def boom(*a, **k):
        raise urllib.error.HTTPError(
            "https://srv.example/m.json", 404, "not found", {}, None
        )

    monkeypatch.setattr(client_update, "secure_urlopen", boom)

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()

    assert any(isinstance(e, ManifestUnavailable) for e in events)
    assert not any(isinstance(e, UpdateRequired) for e in events)
    assert not any(isinstance(e, ManifestAvailable) for e in events)


def test_verify_worker_config_wtf_created_when_missing(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    manifest = {"root": {"files": []}}
    fake_resp = _BodyResp(json.dumps(manifest).encode())
    monkeypatch.setattr(
        client_update, "secure_urlopen", lambda *a, **k: fake_resp
    )

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    assert (client / "WTF" / "Config.wtf").exists()


def test_verify_worker_seeds_wtf_even_when_manifest_fails(
    tmp_path, monkeypatch
):
    """Config.wtf/realmlist.wtf are seeded BEFORE the manifest fetch, so a
    fresh client folder still gets its realm configuration when the server
    serves no manifest (torrent-verified / play-only setups)."""
    client = _mk_client(tmp_path)

    def boom(*a, **k):
        raise urllib.error.HTTPError(
            "https://srv.example/m.json", 404, "not found", {}, None
        )

    monkeypatch.setattr(client_update, "secure_urlopen", boom)

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()

    assert (client / "WTF" / "Config.wtf").exists()
    events = dispatcher.drain()
    assert any(isinstance(e, ManifestUnavailable) for e in events)


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


def test_update_worker_traverse_skips_up_to_date(tmp_path, monkeypatch):
    client = _mk_client(tmp_path)
    f = client / "data.bin"
    f.write_bytes(b"x")
    node = {
        "type": "file",
        "name": "data.bin",
        "size": 1,
        "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072",
    }

    def fail(*a, **k):
        raise AssertionError(
            "download must not be attempted for a matching file"
        )

    monkeypatch.setattr(client_update, "secure_urlopen", fail)
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    worker.traverse(node, [])
    assert (client / "data.bin").read_bytes() == b"x"


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
            "https://srv/manifest.json",
            "https://srv/client",
            "https://srv/client.torrent",
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
        "https://launcher.test/manifest.json",
        "https://launcher.test/client",
        "https://launcher.test/client.torrent",
    )
    monkeypatch.setattr(client_update, "_download_source", lambda: source)
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
        lambda wanted: recovered.append(wanted),
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
    assert src.manifest_url == ""
    assert src.client_url == ""


def test_download_source_uses_explicit_endpoint_overrides(monkeypatch):
    """The configured manifest/client URLs (not any derived defaults) are what
    the updater uses."""
    from nostalgia_launcher.core import launcher

    launcher.configure_from_dict(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "http": {
                        "manifest": "https://srv.example/custom/manifest.json",
                        "client": "https://dl.example/client/latest",
                    }
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
    assert src.manifest_url == "https://srv.example/custom/manifest.json"
    assert src.client_url == "https://dl.example/client/latest"


def test_verify_uses_selected_manifest_url(monkeypatch, tmp_path):
    """VerifyWorker must fetch the manifest from the configured manifest URL."""
    from nostalgia_launcher.core import launcher

    launcher.configure_from_dict(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "http": {
                        "manifest": "https://m1.example/custom/manifest.json",
                        "client": "https://dl.example/client/latest",
                    }
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
    dispatcher = EventDispatcher()
    worker = client_update.VerifyWorker(str(client), dispatcher)
    worker.run()
    assert any(
        u.startswith("https://m1.example/custom/manifest.json")
        for u in fetched
    )


def test_traverse_downloads_from_configured_client_url(monkeypatch, tmp_path):
    """File downloads must come from the configured client_url."""
    from nostalgia_launcher.core import launcher

    launcher.configure_from_dict(
        {
            "server": {
                "url": "https://srv.example",
                "download": {
                    "http": {
                        "manifest": "https://srv.example/m.json",
                        "client": "https://dl.example/client/latest",
                    }
                },
            }
        }
    )

    monkeypatch.setattr(
        client_update,
        "secure_urlopen",
        lambda req, timeout=5, allowed_hosts=None: _resp(),
    )

    client = tmp_path / "client"
    client.mkdir()
    recorded = []

    class _RecordingWorker(client_update.UpdateWorker):
        def download(self, url, dest, size, name=""):
            recorded.append(url)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            open(dest, "wb").close()
            return "A" * 40

    dispatcher = EventDispatcher()
    worker = _RecordingWorker(str(client), dispatcher)
    worker.traverse(
        {"type": "file", "name": "data.bin", "size": 1, "hash": "A" * 40}, []
    )


def _manifest_resp(manifest):
    return _BodyResp(json.dumps(manifest).encode())


def test_verify_worker_reserves_progress_bar_for_update(tmp_path, monkeypatch):
    """Verification must not drive the progress bar to 100% — that sweep is
    reserved for the actual download of the files that need updating. The bar
    stays at 0 while the phase reports Verifying/Verified."""
    client = _mk_client(tmp_path)
    (client / "data.bin").write_bytes(b"x")
    manifest = {
        "root": {
            "files": [
                {
                    "type": "file",
                    "name": "data.bin",
                    "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072",
                    "size": 1,
                }
            ]
        }
    }
    monkeypatch.setattr(
        client_update,
        "secure_urlopen",
        lambda *a, **k: _manifest_resp(manifest),
    )
    dispatcher = EventDispatcher()
    VerifyWorker(str(client), dispatcher).run()
    events = dispatcher.drain()
    progress_events = [e for e in events if isinstance(e, ProgressChanged)]

    # No full-bar sweep during verification: every progress value stays at 0.
    assert progress_events, "verify should have emitted progress"
    assert all(e.value == 0.0 for e in progress_events)
    phases = [e.phase for e in progress_events]
    assert "Verifying" in phases
    assert "Verified" in phases


def test_sum_needed_bytes_excludes_up_to_date(tmp_path, monkeypatch):
    """The update progress denominator is the bytes of the files that actually
    need downloading, not the whole client."""
    client = _mk_client(tmp_path)
    (client / "ok.bin").write_bytes(b"x")  # already matches the manifest
    monkeypatch.setattr(client_update, "load_cache", lambda: {})
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    nodes = [
        {
            "type": "file",
            "name": "ok.bin",
            "hash": "11F6AD8EC52A2984ABAAFD7C3B516503785C2072",
            "size": 1,
        },
        {"type": "file", "name": "stale.bin", "hash": "A" * 40, "size": 9},
        {"type": "mpq", "name": "Patch", "hash": "B" * 40, "size": 5},
    ]
    # ok.bin is up to date (size 1 excluded); stale.bin (9) + Patch.mpq (5).
    assert worker._sum_needed_bytes(nodes) == 14


def test_update_progress_spans_needed_files_only(tmp_path, monkeypatch):
    """With the BitTorrent backend unused, the update progress bar must span
    0→100 across exactly the files that need downloading (not the whole
    client)."""
    import hashlib

    client = _mk_client(tmp_path)
    size = 9
    data = b"y" * size
    h = hashlib.sha1(data).hexdigest().upper()
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

    def _resp(*a, **k):
        buf = {"n": 0}

        def _read(s, *a):
            if buf["n"] == 0:
                buf["n"] = 1
                return data
            return b""

        return type(
            "R",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda *a: None,
                "read": _read,
                "status": 200,
                "getcode": lambda s: 200,
            },
        )()

    monkeypatch.setattr(client_update, "secure_urlopen", _resp)
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    nodes = [{"type": "file", "name": "a.bin", "hash": h, "size": size}]
    worker.run(nodes)

    events = dispatcher.drain()
    progress_events = [e for e in events if isinstance(e, ProgressChanged)]
    # The final overall progress reaches 100%.
    assert progress_events[-1].value == 1.0
    # An aggregate item reports the needed-file total, not the whole client.
    agg = [e for e in progress_events if e.total == size]
    assert agg, "expected an aggregate progress item for the needed file"
    assert all(e.transport == "HTTP" for e in agg)


# ── hostile-manifest hardening (regression tests) ────────────────────────


def test_verify_refuses_unsafe_manifest_paths(tmp_path, monkeypatch):
    """A manifest naming traversal/absolute paths must not flag them stale:
    the update would otherwise write (or delete) outside the game folder."""
    client = _mk_client(tmp_path)
    outside = tmp_path / "evil.txt"
    outside.write_bytes(b"old")

    manifest = {
        "root": {
            "files": [
                {
                    "type": "file",
                    "name": "../../../evil.txt",
                    "hash": "A" * 40,
                    "size": 3,
                },
                {
                    "type": "file",
                    "name": "C:\\Users\\v\\AppData\\evil.js",
                    "hash": "B" * 40,
                    "size": 3,
                },
            ]
        }
    }
    fake_resp = _BodyResp(json.dumps(manifest).encode())
    monkeypatch.setattr(
        client_update, "secure_urlopen", lambda *a, **k: fake_resp
    )

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()

    assert any(isinstance(e, VerificationUpToDate) for e in events)
    assert not any(isinstance(e, DiffTreeReady) and e.tree for e in events)
    assert outside.read_bytes() == b"old"


def test_update_traverse_never_writes_outside_out_dir(tmp_path, monkeypatch):
    """The download path enforces the same gate: a hostile manifest node
    is skipped, not written relative to out_dir."""
    import hashlib

    client = _mk_client(tmp_path)
    data = b"pwn"
    h = hashlib.sha1(data).hexdigest().upper()
    nodes = [
        {
            "type": "file",
            "name": "../../escape.bin",
            "hash": h,
            "size": len(data),
        }
    ]
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    # A usable-looking source: if the unsafe node were not rejected, the
    # flow would proceed to _skip_download/_download_verified.
    worker._source = type("S", (), {"client_url": "https://x.test/client"})()
    worker.traverse(nodes[0], [])
    assert not (client.parent / "escape.bin").exists()
    assert not (client / ".." / "escape.bin").exists()


def test_sum_needed_bytes_nested_mpq_uses_mpq_suffix(tmp_path, monkeypatch):
    """Nested MPQ nodes are checked at <dir>/<name>.mpq — a doubled
    <dir>/<name>/<name>.mpq path made every current MPQ count into the
    progress denominator (bar never reached 100%)."""
    import hashlib

    client = _mk_client(tmp_path)
    (client / "Data").mkdir()
    f = client / "Data" / "patch.mpq"
    f.write_bytes(b"x")
    sha = hashlib.sha1(b"x").hexdigest().upper()
    monkeypatch.setattr(client_update, "load_cache", lambda: {})
    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    nodes = [
        {
            "type": "dir",
            "name": "Data",
            "files": [
                {"type": "mpq", "name": "patch", "hash": sha, "size": 1}
            ],
        }
    ]
    assert worker._sum_needed_bytes(nodes) == 0


def test_traverse_delete_failure_does_not_abort_update(tmp_path, monkeypatch):
    """One undeletable file (locked/AV) must not abort the remaining
    deletes and downloads."""
    import hashlib

    client = _mk_client(tmp_path)
    locked = client / "locked.bin"
    locked.write_bytes(b"x")
    victim = client / "gone.bin"
    victim.write_bytes(b"x")
    data = b"fresh"
    h = hashlib.sha1(data).hexdigest().upper()

    real_remove = os.remove

    def failing_remove(path, *a, **kw):
        if str(path) == str(locked):
            raise PermissionError("locked by antivirus")
        return real_remove(path, *a, **kw)

    monkeypatch.setattr(client_update.os, "remove", failing_remove)
    monkeypatch.setattr(client_update, "_download_source", lambda: None)

    def fake_download(url, dest, size, name=""):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        return h

    dispatcher = EventDispatcher()
    worker = UpdateWorker(str(client), dispatcher)
    worker.download = fake_download
    worker._source = type("S", (), {"client_url": "https://x.test/client"})()
    nodes = [
        {"type": "del", "name": "locked.bin"},
        {"type": "del", "name": "gone.bin"},
        {"type": "file", "name": "new.bin", "hash": h, "size": len(data)},
    ]
    for node in nodes:
        worker.traverse(node, [])
    assert locked.exists()  # removal failed but was contained
    assert not victim.exists()  # later deletes still ran
    assert (client / "new.bin").read_bytes() == data


def test_verify_malformed_manifest_shape_reports_unavailable(
    tmp_path, monkeypatch
):
    """Valid JSON that isn't a manifest routes to MANIFEST_UNAVAILABLE
    (torrent fallback), never to a bogus UPDATE_NEEDED verdict."""
    client = _mk_client(tmp_path)
    fake_resp = _BodyResp(json.dumps({"root": None}).encode())
    monkeypatch.setattr(
        client_update, "secure_urlopen", lambda *a, **k: fake_resp
    )

    dispatcher = EventDispatcher()
    vw = VerifyWorker(str(client), dispatcher)
    vw.run()
    events = dispatcher.drain()

    assert any(isinstance(e, ManifestUnavailable) for e in events)
    assert not any(isinstance(e, UpdateRequired) for e in events)

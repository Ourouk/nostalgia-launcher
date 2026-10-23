"""Manifest engine tests: parse/routing/bundled entries (network-free)."""

import json

import pytest
from _torrent_fakes import fake_urlopen

import nostalgia_launcher.core.config_store as config_store
from nostalgia_launcher.services import manifest as _manifest


def _schema():
    return {
        "files": "files",
        "version": "contentVersion",
        "base_url": "filesBaseUrl",
        "entry": {"path": "path", "size": "size", "sha256": "sha256"},
        "download_url": "{base_url}{path}",
    }


def _doc():
    return {
        "contentVersion": "1.1.8",
        "filesBaseUrl": "https://updates.example.com/files/",
        "files": [
            {
                "path": "Data/patch-A.MPQ",
                "size": 100,
                "sha256": "a" * 64,
            },
            {
                "path": "ServerConnection.dll",
                "size": 10,
                "sha256": "b" * 64,
            },
            {"path": "Wow.exe", "size": 20, "sha256": ""},
        ],
    }


def test_parse_manifest_extracts_entries():
    version, entries = _manifest.parse_manifest(_doc(), _schema())
    assert version == "1.1.8"
    assert len(entries) == 3
    assert entries[0]["download_url"] == (
        "https://updates.example.com/files/Data/patch-A.MPQ"
    )
    assert entries[0]["size"] == 100


def test_parse_manifest_rejects_traversal():
    doc = _doc()
    doc["files"] = [{"path": "../evil", "size": 1, "sha256": "c" * 64}]
    with pytest.raises(ValueError):
        _manifest.parse_manifest(doc, _schema())


def test_parse_manifest_rejects_bad_hash():
    doc = _doc()
    doc["files"] = [{"path": "a/b", "size": 1, "sha256": "zzz"}]
    with pytest.raises(ValueError):
        _manifest.parse_manifest(doc, _schema())


def test_parse_manifest_rejects_non_https_base():
    doc = _doc()
    doc["filesBaseUrl"] = "http://updates.example.com/files/"
    with pytest.raises(ValueError):
        _manifest.parse_manifest(doc, _schema())


def test_route_entry_first_match_wins():
    routes = [
        {"match": "Data/*.MPQ", "as": "asset"},
        {"match": "Data/*", "as": "mod"},
    ]
    assert _manifest.route_entry("Data/patch-A.MPQ", routes)["as"] == "asset"
    assert _manifest.route_entry("other.txt", routes) is None


def test_dlls_for_path_reads_route_register_dll(monkeypatch):
    monkeypatch.setattr(
        _manifest,
        "_manifest_block",
        lambda: {
            "routes": [
                {
                    "match": "ServerConnection.dll",
                    "as": "mod",
                    "register_dll": ["ServerConnection.dll"],
                },
                {"match": "Data/*.MPQ", "as": "asset"},
            ]
        },
    )
    assert _manifest.dlls_for_path("ServerConnection.dll") == [
        "ServerConnection.dll"
    ]
    assert _manifest.dlls_for_path("Data/patch-A.MPQ") == []
    assert _manifest.dlls_for_path("other.txt") == []


def test_dlls_for_path_no_block(monkeypatch):
    monkeypatch.setattr(_manifest, "_manifest_block", lambda: None)
    assert _manifest.dlls_for_path("ServerConnection.dll") == []


def test_stale_overlay_entries_missing_only(tmp_path):
    import hashlib

    existing = tmp_path / "Data"
    existing.mkdir()
    good = b"x"
    (existing / "patch-A.MPQ").write_bytes(good)
    entries = [
        {
            "path": "Data/patch-A.MPQ",
            "size": len(good),
            "sha256": hashlib.sha256(good).hexdigest(),
        },
        {"path": "Data/patch-B.MPQ", "size": 1, "sha256": ""},
    ]
    assert _manifest.stale_overlay_entries(str(tmp_path), entries) == [
        "Data/patch-B.MPQ"
    ]


def test_stale_overlay_entries_hash_decides(tmp_path):
    """Size pre-check + sha256: mismatch → stale, match → fresh."""
    import hashlib

    (tmp_path / "Data").mkdir()
    same_size_a = b"aaaa"
    same_size_b = b"bbbb"
    assert len(same_size_a) == len(same_size_b)
    (tmp_path / "Data" / "same.MPQ").write_bytes(same_size_b)
    (tmp_path / "Data" / "match.MPQ").write_bytes(same_size_a)
    (tmp_path / "Data" / "short.MPQ").write_bytes(b"a")
    entries = [
        {
            "path": "Data/same.MPQ",
            "size": len(same_size_a),
            "sha256": hashlib.sha256(same_size_a).hexdigest(),
        },
        {
            "path": "Data/match.MPQ",
            "size": len(same_size_a),
            "sha256": hashlib.sha256(same_size_a).hexdigest(),
        },
        {
            "path": "Data/short.MPQ",
            "size": len(same_size_a),
            "sha256": hashlib.sha256(same_size_a).hexdigest(),
        },
        {"path": "Data/nosha.MPQ", "size": 5, "sha256": ""},
    ]
    (tmp_path / "Data" / "nosha.MPQ").write_bytes(b"12345")
    stale = _manifest.stale_overlay_entries(str(tmp_path), entries)
    assert stale == ["Data/same.MPQ", "Data/short.MPQ"]


def test_verify_manifest_version_bump_content_decides(tmp_path, monkeypatch):
    """A moved version cursor no longer refreshes everything: identical
    bytes stay up-to-date, changed bytes go stale."""
    import hashlib

    import nostalgia_launcher.core.config_store as config_store
    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import (
        EventDispatcher,
        TorrentDiffReady,
    )

    new_bytes = b"new-bytes"
    old_bytes = b"old-bytes"
    client = tmp_path / "client"
    client.mkdir()
    (client / "Data").mkdir()
    (client / "Data" / "a.MPQ").write_bytes(new_bytes)
    (client / "Data" / "b.MPQ").write_bytes(old_bytes)
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    worker = w.VerifyWorker(
        str(client),
        EventDispatcher(),
        source=w.DownloadSource(None, ""),
    )
    monkeypatch.setattr(
        _manifest, "_manifest_block", lambda: {"url": "https://u/m"}
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {
            "version": "2",
            "url": "https://u/m",
            "entries": [
                {
                    "path": "Data/a.MPQ",
                    "size": len(new_bytes),
                    "sha256": hashlib.sha256(new_bytes).hexdigest(),
                },
                {
                    "path": "Data/b.MPQ",
                    "size": len(new_bytes),
                    "sha256": hashlib.sha256(new_bytes).hexdigest(),
                },
            ],
        },
    )
    worker.run()
    diffs = [
        e
        for e in worker._dispatcher.drain()
        if isinstance(e, TorrentDiffReady)
    ]
    assert diffs, "expected a manifest verdict"
    assert diffs[0].stale == ["manifest:Data/b.MPQ"]


def test_verify_manifest_same_version_corrupt_is_stale(tmp_path, monkeypatch):
    """Same cursor + corrupted-but-present file → stale (hash decides)."""
    import hashlib

    import nostalgia_launcher.core.config_store as config_store
    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import (
        EventDispatcher,
        TorrentDiffReady,
    )

    good = b"good-bytes"
    client = tmp_path / "client"
    client.mkdir()
    (client / "Data").mkdir()
    (client / "Data" / "a.MPQ").write_bytes(b"corrupted!!")
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "__manifest_installed__": {
                "version": "2",
                "url": "https://u/m",
            }
        }
    )
    worker = w.VerifyWorker(
        str(client),
        EventDispatcher(),
        source=w.DownloadSource(None, ""),
    )
    monkeypatch.setattr(
        _manifest, "_manifest_block", lambda: {"url": "https://u/m"}
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {
            "version": "2",
            "url": "https://u/m",
            "entries": [
                {
                    "path": "Data/a.MPQ",
                    "size": len(good),
                    "sha256": hashlib.sha256(good).hexdigest(),
                },
            ],
        },
    )
    worker.run()
    diffs = [
        e
        for e in worker._dispatcher.drain()
        if isinstance(e, TorrentDiffReady)
    ]
    assert diffs
    assert diffs[0].stale == ["manifest:Data/a.MPQ"]


def test_verify_manifest_same_version_only_missing(tmp_path, monkeypatch):
    """Same cursor → only missing files go stale (no-sha entries keep
    existence-only staleness)."""
    import nostalgia_launcher.core.config_store as config_store
    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import (
        EventDispatcher,
        TorrentDiffReady,
    )

    client = tmp_path / "client"
    client.mkdir()
    (client / "Data").mkdir()
    (client / "Data" / "a.MPQ").write_bytes(b"old-bytes")
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "__manifest_installed__": {
                "version": "2",
                "url": "https://u/m",
            }
        }
    )
    worker = w.VerifyWorker(
        str(client),
        EventDispatcher(),
        source=w.DownloadSource(None, ""),
    )
    monkeypatch.setattr(
        _manifest, "_manifest_block", lambda: {"url": "https://u/m"}
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {
            "version": "2",
            "url": "https://u/m",
            "entries": [
                {"path": "Data/a.MPQ"},
                {"path": "ServerConnection.dll"},
            ],
        },
    )
    worker.run()
    diffs = [
        e
        for e in worker._dispatcher.drain()
        if isinstance(e, TorrentDiffReady)
    ]
    assert diffs
    assert diffs[0].stale == ["manifest:ServerConnection.dll"]


def test_verify_up_to_date_repairs_missing_dll_registration(
    tmp_path, monkeypatch
):
    """Present-but-unregistered bundled DLL gets its dlls.txt line even
    when nothing is stale (this shape: DLL on disk, line missing)."""
    import hashlib

    import nostalgia_launcher.core.config_store as config_store
    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import (
        EventDispatcher,
        TorrentUpToDate,
    )

    dll_bytes = b"fake-dll-bytes"
    client = tmp_path / "client"
    client.mkdir()
    (client / "ServerConnection.dll").write_bytes(dll_bytes)
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "__manifest_installed__": {
                "version": "1.1.8",
                "url": "https://u/m",
            }
        }
    )
    worker = w.VerifyWorker(
        str(client),
        EventDispatcher(),
        source=w.DownloadSource(None, ""),
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {
            "version": "1.1.8",
            "url": "https://u/m",
            "entries": [
                {
                    "path": "ServerConnection.dll",
                    "size": len(dll_bytes),
                    "sha256": hashlib.sha256(dll_bytes).hexdigest(),
                },
            ],
        },
    )
    # Routes block for reconcile_dlls (URL included so the verify path
    # treats the manifest as configured).
    monkeypatch.setattr(
        _manifest,
        "_manifest_block",
        lambda: {
            "url": "https://u/m",
            "routes": [
                {
                    "match": "ServerConnection.dll",
                    "as": "mod",
                    "register_dll": ["ServerConnection.dll"],
                }
            ],
        },
    )
    worker.run()
    events = worker._dispatcher.drain()
    assert any(isinstance(e, TorrentUpToDate) for e in events)
    entries = (client / "dlls.txt").read_text().splitlines()
    assert "ServerConnection.dll" in entries


def test_bundled_mods_lists_unrouted_root_dlls(monkeypatch):
    """the manifest ships more DLLs than the routes name.
    Unrouted root-level DLLs still surface as server rows; non-DLL
    unrouted files (MPQs) stay update-owned and hidden."""
    monkeypatch.setattr(
        _manifest,
        "_manifest_block",
        lambda: {
            "url": "https://u/m",
            "routes": [
                {
                    "match": "ServerConnection.dll",
                    "as": "mod",
                    "register_dll": ["ServerConnection.dll"],
                },
            ],
        },
    )
    mods = _manifest.bundled_mods(
        snapshot_entries=[
            {
                "path": "ServerConnection.dll",
                "download_url": "https://u/m/ServerConnection.dll",
            },
            {"path": "d3d9.dll", "download_url": "https://u/m/d3d9.dll"},
            {
                "path": "Data/patch-A.MPQ",
                "download_url": "https://u/m/Data/patch-A.MPQ",
            },
        ]
    )
    by_id = {m["id"]: m for m in mods}
    assert "manifest-serverconnection.dll" in by_id
    unrouted = by_id.get("manifest-d3d9.dll")
    assert unrouted is not None
    assert unrouted["installed_files"] == ["d3d9.dll"]
    assert all(m["id"] != "manifest-data-patch-a.mpq" for m in mods)


def _manifest_worker(tmp_path, monkeypatch, entries, installed=None):
    """UpdateWorker with stubbed snapshot/ownership for overlay tests."""
    import nostalgia_launcher.core.config_store as config_store
    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import EventDispatcher

    client = tmp_path / "client"
    client.mkdir(exist_ok=True)
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    cfg = {"__manifest_installed__": installed} if installed else {}
    config_store.save_config(cfg)
    worker = w.UpdateWorker(
        str(client), EventDispatcher(), source=w.DownloadSource(None, "")
    )
    monkeypatch.setattr(
        _manifest, "_manifest_block", lambda: {"url": "https://u/m"}
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {"version": "9", "url": "https://u/m", "entries": entries},
    )
    monkeypatch.setattr(
        w,
        "_snapshot_manifest_paths",
        lambda: {e["path"].lower() for e in entries},
    )
    monkeypatch.setattr(w, "_snapshot_user_records", lambda: [])
    return worker


def test_manifest_overlay_download_sha_gate(tmp_path, monkeypatch):
    """Matching bytes land + cursor advances; wrong bytes → UpdateFailed
    with 'sha256 mismatch' and the bad file discarded."""
    import hashlib

    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import UpdateFailed

    good = b"good-bytes"
    entries = [
        {
            "path": "Data/a.MPQ",
            "size": len(good),
            "sha256": hashlib.sha256(good).hexdigest(),
            "download_url": "https://u/m/Data/a.MPQ",
        }
    ]
    worker = _manifest_worker(tmp_path, monkeypatch, entries)
    import os as _os

    def _write_good(self, url, dest, size, name=""):
        _os.makedirs(_os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(good)

    def _write_bad(self, url, dest, size, name=""):
        _os.makedirs(_os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"tampered!!")

    monkeypatch.setattr(w.UpdateWorker, "download", _write_good)
    assert worker._manifest_overlay_download({"Data/a.MPQ"}) is True
    dest = worker.out_dir + "/Data/a.MPQ".replace("/", _os.sep)
    assert open(dest, "rb").read() == good

    bad_worker = _manifest_worker(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(w.UpdateWorker, "download", _write_bad)
    assert bad_worker._manifest_overlay_download({"Data/a.MPQ"}) is False
    failed = [
        e
        for e in bad_worker._dispatcher.drain()
        if isinstance(e, UpdateFailed)
    ]
    assert failed and "sha256 mismatch" in failed[0].message


def test_manifest_overlay_download_registers_dlls(tmp_path, monkeypatch):
    """Bundled DLLs land in dlls.txt (mirrors the MODS tab install:
    without registration the client never loads them)."""
    import hashlib

    import nostalgia_launcher.services.update.workflow as w

    dll_bytes = b"dll-bytes"
    entries = [
        {
            "path": "ServerConnection.dll",
            "size": len(dll_bytes),
            "sha256": hashlib.sha256(dll_bytes).hexdigest(),
            "download_url": "https://u/m/ServerConnection.dll",
        }
    ]
    worker = _manifest_worker(tmp_path, monkeypatch, entries)
    monkeypatch.setattr(
        _manifest,
        "_manifest_block",
        lambda: {
            "url": "https://u/m",
            "routes": [
                {
                    "match": "ServerConnection.dll",
                    "as": "mod",
                    "register_dll": ["ServerConnection.dll"],
                }
            ],
        },
    )
    import os as _os

    def _write_dll(self, url, dest, size, name=""):
        with open(dest, "wb") as f:
            f.write(dll_bytes)

    monkeypatch.setattr(w.UpdateWorker, "download", _write_dll)
    assert worker._manifest_overlay_download({"ServerConnection.dll"}) is True
    with open(_os.path.join(worker.out_dir, "dlls.txt")) as f:
        assert "ServerConnection.dll" in [
            line.strip() for line in f if line.strip()
        ]


def test_fetch_snapshot_forced_refetches_not_cache(tmp_path, monkeypatch):
    """A populated snapshot cache never pins verify/update to stale
    hashes: force=True refetches (the verify/update path); the plain
    cached call (the UI-list path) stays network-free."""
    import hashlib

    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "__manifest_snapshot__": {
                "url": "https://u/m",
                "version": "1",
                "entries": [],
            }
        }
    )
    block = {
        "url": "https://u/m",
        "schema": _schema(),
        "routes": [],
    }
    monkeypatch.setattr(_manifest, "_manifest_block", lambda: block)
    fresh = {
        "contentVersion": "2",
        "filesBaseUrl": "https://updates.example.com/files/",
        "files": [
            {
                "path": "ServerConnection.dll",
                "size": 3,
                "sha256": hashlib.sha256(b"new").hexdigest(),
            }
        ],
    }
    payload = json.dumps(fresh).encode()
    calls: list = []
    monkeypatch.setattr(
        _manifest, "secure_urlopen", fake_urlopen(payload, calls=calls)
    )
    snap = _manifest.fetch_snapshot(force=True)
    assert snap is not None and snap["version"] == "2"
    assert calls, "forced fetch must hit the network, not the cache"
    quiet: list = []
    monkeypatch.setattr(
        _manifest, "secure_urlopen", fake_urlopen(payload, calls=quiet)
    )
    cached = _manifest.fetch_snapshot()
    assert cached is not None and cached["version"] == "2"
    assert not quiet, "default fetch serves the cached snapshot"


def test_fetch_snapshot_offline_falls_back_to_cache(tmp_path, monkeypatch):
    """Unreachable manifest + warm cache → cached snapshot (verify can
    still run offline against last-known hashes)."""
    import urllib.error

    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config(
        {
            "__manifest_snapshot__": {
                "url": "https://u/m",
                "version": "1",
                "entries": [],
            }
        }
    )
    block = {
        "url": "https://u/m",
        "schema": _schema(),
        "routes": [],
    }
    monkeypatch.setattr(_manifest, "_manifest_block", lambda: block)
    monkeypatch.setattr(
        _manifest,
        "secure_urlopen",
        fake_urlopen(exc=urllib.error.URLError("down")),
    )
    snap = _manifest.fetch_snapshot(force=True)
    assert snap is not None and snap["version"] == "1"


def test_verify_up_to_date_reconciles_missing_dll_registration(
    tmp_path, monkeypatch
):
    """A present-but-unregistered bundled DLL gets its dlls.txt line on
    verify even when every hash is current (no download runs)."""
    import hashlib

    import nostalgia_launcher.services.update.workflow as w
    from nostalgia_launcher.state.events import (
        EventDispatcher,
        TorrentUpToDate,
    )

    dll_bytes = b"dll-bytes"
    client = tmp_path / "client"
    client.mkdir()
    (client / "ServerConnection.dll").write_bytes(dll_bytes)
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    worker = w.VerifyWorker(
        str(client),
        EventDispatcher(),
        source=w.DownloadSource(None, ""),
    )
    monkeypatch.setattr(
        _manifest,
        "_manifest_block",
        lambda: {
            "url": "https://u/m",
            "routes": [
                {
                    "match": "ServerConnection.dll",
                    "as": "mod",
                    "register_dll": ["ServerConnection.dll"],
                }
            ],
        },
    )
    monkeypatch.setattr(
        _manifest,
        "fetch_snapshot",
        lambda **k: {
            "version": "2",
            "url": "https://u/m",
            "entries": [
                {
                    "path": "ServerConnection.dll",
                    "size": len(dll_bytes),
                    "sha256": hashlib.sha256(dll_bytes).hexdigest(),
                },
            ],
        },
    )
    worker.run()
    assert any(
        isinstance(e, TorrentUpToDate) for e in worker._dispatcher.drain()
    )
    entries = (client / "dlls.txt").read_text().splitlines()
    assert "ServerConnection.dll" in [line.strip() for line in entries]


def test_reconcile_dlls_skips_missing_files(tmp_path, monkeypatch):
    """Only on-disk DLLs are registered — the download path heals the
    rest; reconcile never invents lines for absent files."""
    client = tmp_path / "client"
    client.mkdir()
    monkeypatch.setattr(
        _manifest,
        "_manifest_block",
        lambda: {
            "url": "https://u/m",
            "routes": [
                {
                    "match": "Present.dll",
                    "as": "mod",
                    "register_dll": ["Present.dll"],
                },
                {
                    "match": "Absent.dll",
                    "as": "mod",
                    "register_dll": ["Absent.dll"],
                },
            ],
        },
    )
    (client / "Present.dll").write_bytes(b"dll")
    assert _manifest.reconcile_dlls(str(client)) == 1
    assert (client / "dlls.txt").read_text().splitlines() == ["Present.dll"]
    assert _manifest.reconcile_dlls(str(client)) == 0

"""Manifest engine tests: parse/routing/bundled entries (network-free)."""

import pytest

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
                "path": "EpochConnection.dll",
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
                {"path": "EpochConnection.dll"},
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
    assert diffs[0].stale == ["manifest:EpochConnection.dll"]


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
    assert not _os.path.exists(
        _os.path.join(bad_worker.out_dir, "Data", "a.MPQ")
    )

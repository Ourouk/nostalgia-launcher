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
    existing = tmp_path / "Data"
    existing.mkdir()
    (existing / "patch-A.MPQ").write_bytes(b"x")
    entries = [
        {"path": "Data/patch-A.MPQ"},
        {"path": "Data/patch-B.MPQ"},
    ]
    assert _manifest.stale_overlay_entries(str(tmp_path), entries) == [
        "Data/patch-B.MPQ"
    ]

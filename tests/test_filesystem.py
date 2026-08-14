"""Unit tests for the filesystem/hashing helpers."""

import os
import stat

import pytest

import vanilla_wow_launcher.core.filesystem as filesystem


def test_sha1_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    assert filesystem.sha1_file(str(p)) == "AAF4C61DDCC5E8A2DABEDE0F3B482CD9AEA9434D"


def test_sha1_file_matches_hashlib(tmp_path):
    import hashlib
    p = tmp_path / "f.bin"
    p.write_bytes(b"the quick brown fox jumps over the lazy dog")
    assert filesystem.sha1_file(str(p)) == hashlib.sha1(p.read_bytes()).hexdigest().upper()


def test_cached_sha1_populates_and_hits(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    cache = {}
    h = filesystem.cached_sha1(str(p), cache)
    assert h == filesystem.sha1_file(str(p))
    assert str(p) in cache
    # mtime unchanged -> served from cache
    assert filesystem.cached_sha1(str(p), cache) == h


def test_cached_sha1_invalidates_on_change(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"a")
    cache = {}
    h1 = filesystem.cached_sha1(str(p), cache)
    p.write_bytes(b"b")
    h2 = filesystem.cached_sha1(str(p), cache)
    assert h1 != h2


def test_cached_sha1_missing_file_returns_empty(tmp_path):
    assert filesystem.cached_sha1(str(tmp_path / "nope"), {}) == ""


def test_already_updated(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"x")
    assert filesystem.already_updated(str(p), filesystem.sha1_file(str(p)))
    assert not filesystem.already_updated(str(p), "A" * 40)
    assert not filesystem.already_updated(str(tmp_path / "nope"), "A" * 40)


def test_ensure_dir_creates_nested(tmp_path):
    d = tmp_path / "a" / "b"
    filesystem.ensure_dir(d)
    assert d.is_dir()
    filesystem.ensure_dir(d)  # idempotent


def test_remove_wdb(tmp_path):
    wdb = tmp_path / "WDB"
    wdb.mkdir()
    (wdb / "cache.bin").write_bytes(b"x")
    filesystem.remove_wdb(str(tmp_path))
    assert not wdb.exists()
    filesystem.remove_wdb(str(tmp_path))  # no-op when absent


def test_get_client_version(tmp_path):
    assert filesystem.get_client_version(str(tmp_path)) == ""


def test_get_client_version_reads_offsets(tmp_path):
    exe = tmp_path / "WoW.exe"
    data = bytearray(0x00437c10)
    data[0x00437bfc:0x00437bfc + 4] = b"1.17"
    data[0x00437c04:0x00437c04 + 6] = b"60000\x00"
    exe.write_bytes(bytes(data))
    assert filesystem.get_client_version(str(tmp_path)) == "60000 (1.17)"


def test_rmtree_force_removes_readonly(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    p = d / "file"
    p.write_bytes(b"x")
    os.chmod(p, stat.S_IREAD)
    filesystem.rmtree_force(str(d))
    assert not d.exists()

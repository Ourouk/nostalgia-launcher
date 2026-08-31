"""Regression tests for malicious archive extraction.

Covers absolute paths, traversal, symlinks, hardlinks, Windows
peculiarities and pre-existing symlink objects for both zip and rar
paths in services/update_backend/extract.py and sources/deploy.py.
"""

import io
import stat
import sys
import zipfile

import pytest

from nostalgia_launcher.services.sources import deploy
from nostalgia_launcher.services.update_backend import extract as extract_mod


def _make_zip(members):
    """Build a zip bytes with given {name: data_or_ZipInfo}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            if isinstance(data, zipfile.ZipInfo):
                zf.writestr(data, b"evil")
            else:
                zf.writestr(name, data)
    return buf.getvalue()


def _write_zip_file(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            if isinstance(data, zipfile.ZipInfo):
                zf.writestr(data, b"evil")
            else:
                zf.writestr(name, data)


def test_extract_zip_absolute_path_blocked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "a.zip"
    _write_zip_file(str(archive), {"/etc/passwd": b"evil"})
    # extract should log failure and return False, not write outside
    # Use internal _extract_zip for direct exception
    with pytest.raises(RuntimeError, match="unsafe zip"):
        extract_mod._extract_zip(str(archive), str(out))
    assert not (tmp_path / "etc" / "passwd").exists()


def test_extract_zip_traversal_blocked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "b.zip"
    _write_zip_file(str(archive), {"../evil.txt": b"evil"})
    with pytest.raises(RuntimeError, match="unsafe zip"):
        extract_mod._extract_zip(str(archive), str(out))
    assert not (tmp_path / "evil.txt").exists()
    assert not (out.parent / "evil.txt").exists()


def test_extract_zip_traversal_nested(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "c.zip"
    _write_zip_file(str(archive), {"a/../../evil.txt": b"evil"})
    with pytest.raises(RuntimeError, match="unsafe zip"):
        extract_mod._extract_zip(str(archive), str(out))


def test_extract_zip_symlink_blocked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "d.zip"
    info = zipfile.ZipInfo("symlink")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    _write_zip_file(str(archive), {info: b"target"})
    with pytest.raises(RuntimeError, match="symlink"):
        extract_mod._extract_zip(str(archive), str(out))


def test_extract_zip_special_file_blocked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "e.zip"
    info = zipfile.ZipInfo("fifo")
    info.create_system = 3
    info.external_attr = (stat.S_IFIFO | 0o644) << 16
    _write_zip_file(str(archive), {info: b""})
    with pytest.raises(RuntimeError, match="special file"):
        extract_mod._extract_zip(str(archive), str(out))


def test_extract_zip_windows_reserved_blocked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    for name in ["CON", "NUL.txt", "COM1", "LPT1/file.txt"]:
        archive = tmp_path / "out" / f"res_{name.replace('/', '_')}.zip"
        _write_zip_file(str(archive), {name: b"evil"})
        with pytest.raises(RuntimeError, match="unsafe zip"):
            extract_mod._extract_zip(str(archive), str(out))


def test_extract_zip_windows_drive_blocked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "drive.zip"
    _write_zip_file(str(archive), {"C:\\Windows\\evil.dll": b"evil"})
    with pytest.raises(RuntimeError, match="unsafe zip"):
        extract_mod._extract_zip(str(archive), str(out))


def test_extract_zip_empty_segment_blocked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "empty.zip"
    _write_zip_file(str(archive), {"a//b.txt": b"evil"})
    with pytest.raises(RuntimeError, match="unsafe zip"):
        extract_mod._extract_zip(str(archive), str(out))


def test_extract_zip_pre_existing_symlink_parent(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # Create a symlink directory inside out that points outside
    link = out / "a"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not supported")
    archive = tmp_path / "out" / "sym.zip"
    _write_zip_file(str(archive), {"a/b.txt": b"evil"})
    # Should be blocked via safe_destination realpath check
    with pytest.raises(RuntimeError, match="unsafe zip"):
        extract_mod._extract_zip(str(archive), str(out))
    assert not (outside / "b.txt").exists()


def test_extract_zip_normal_file_succeeds(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = tmp_path / "out" / "ok.zip"
    _write_zip_file(str(archive), {"Data/a.txt": b"hello"})
    extract_mod._extract_zip(str(archive), str(out))
    assert (out / "Data" / "a.txt").read_bytes() == b"hello"


def test_extract_client_payload_zip_traversal(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    archive = out / "payload.zip"
    _write_zip_file(str(archive), {"../evil": b"evil"})
    # extract_client_payload returns False on failure and does not
    # create outside file
    result = extract_mod.extract_client_payload(str(out), "zip")
    assert result is False
    assert not (tmp_path / "evil").exists()


def test_extract_rar_absolute_blocked(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()

    class FakeInfo:
        def __init__(self, name):
            self.filename = name

        def is_dir(self):
            return False

        def is_symlink(self):
            return False

    class FakeRar:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def infolist(self):
            return [FakeInfo("/etc/passwd")]

        def open(self, info):
            return io.BytesIO(b"evil")

    fake_mod = type("Mod", (), {"RarFile": FakeRar})
    monkeypatch.setitem(sys.modules, "rarfile", fake_mod)
    with pytest.raises(RuntimeError, match="rar-slip"):
        extract_mod._extract_rar(str(out / "x.rar"), str(out))


def test_extract_rar_symlink_blocked(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()

    class FakeInfo:
        filename = "link"

        def is_dir(self):
            return False

        def is_symlink(self):
            return True

    class FakeRar:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def infolist(self):
            return [FakeInfo()]

        def open(self, info):
            return io.BytesIO(b"target")

    fake_mod = type("Mod", (), {"RarFile": FakeRar})
    monkeypatch.setitem(sys.modules, "rarfile", fake_mod)
    with pytest.raises(RuntimeError, match="symlink"):
        extract_mod._extract_rar(str(out / "y.rar"), str(out))


def test_extract_rar_hardlink_blocked(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()

    class FakeInfo:
        filename = "hard"

        def is_dir(self):
            return False

        def is_symlink(self):
            return False

        def is_hardlink(self):
            return True

    class FakeRar:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def infolist(self):
            return [FakeInfo()]

        def open(self, info):
            return io.BytesIO(b"evil")

    fake_mod = type("Mod", (), {"RarFile": FakeRar})
    monkeypatch.setitem(sys.modules, "rarfile", fake_mod)
    with pytest.raises(RuntimeError, match="hardlink"):
        extract_mod._extract_rar(str(out / "z.rar"), str(out))


def test_deploy_extract_zip_map_traversal_refused(tmp_path):
    client = tmp_path / "client"
    client.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
    # deploy layer also validates dest
    with pytest.raises(RuntimeError, match="unsafe install path"):
        deploy.extract_zip_map(
            str(client), buf.getvalue(), "t", {"a.txt": "../evil"}
        )


def test_deploy_unpack_folder_hardened(tmp_path):
    import zipfile

    dest_root = tmp_path / "Interface" / "AddOns" / "TestAddon"
    dest_root.mkdir(parents=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("TestAddon-master/pwn.txt", "evil")
        zf.writestr("TestAddon-master/../escape.txt", "evil")
    deploy.unpack_folder(buf.getvalue(), str(dest_root))
    assert (dest_root / "pwn.txt").exists()
    assert not (tmp_path / "escape.txt").exists()

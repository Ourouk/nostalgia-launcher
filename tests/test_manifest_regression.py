"""Manifest parsing/validation regression tests (manifest removed —
torrent-only)."""

import pytest

pytestmark = pytest.mark.skip(reason="manifest removed — torrent-only")

try:
    from nostalgia_launcher.state.manifest import (
        parse_manifest,  # type: ignore  # noqa: F401
    )
except ImportError:
    parse_manifest = None  # type: ignore


def test_parse_valid_manifest():
    data = {
        "root": {
            "files": [
                {
                    "type": "file",
                    "name": "a.bin",
                    "hash": "A" * 40,
                    "size": 123,
                },
                {"type": "dir", "name": "Data", "files": []},
            ]
        }
    }
    m = parse_manifest(data)
    assert m["root"]["files"][0]["name"] == "a.bin"


def test_parse_malformed_top_level_not_dict():
    with pytest.raises(ValueError, match="top level"):
        parse_manifest([])


def test_parse_malformed_root_not_dict():
    with pytest.raises(ValueError, match="'root'"):
        parse_manifest({"root": None})


def test_parse_malformed_root_files_not_list():
    with pytest.raises(ValueError, match="root.files"):
        parse_manifest({"root": {"files": "nope"}})


def test_parse_malformed_node_type_invalid():
    with pytest.raises(ValueError, match="type must be one of"):
        parse_manifest({"root": {"files": [{"type": "bogus", "name": "x"}]}})


def test_parse_malformed_name_missing():
    with pytest.raises(ValueError, match="name must be"):
        parse_manifest({"root": {"files": [{"type": "file"}]}})


def test_parse_malformed_file_hash_missing():
    with pytest.raises(ValueError, match="hash must be"):
        parse_manifest(
            {"root": {"files": [{"type": "file", "name": "a", "size": 1}]}}  # noqa
        )


def test_parse_malformed_size_not_int():
    with pytest.raises(ValueError, match="size must be"):
        parse_manifest(
            {
                "root": {
                    "files": [
                        {
                            "type": "file",
                            "name": "a",
                            "hash": "A" * 40,
                            "size": "huge",
                        }
                    ]
                }
            }
        )


def test_parse_malformed_size_bool_rejected():
    with pytest.raises(ValueError, match="size must be"):
        parse_manifest(
            {
                "root": {
                    "files": [
                        {
                            "type": "file",
                            "name": "a",
                            "hash": "A" * 40,
                            "size": True,
                        }
                    ]
                }
            }
        )


def test_parse_malformed_size_negative():
    with pytest.raises(ValueError, match="non-negative"):
        parse_manifest(
            {
                "root": {
                    "files": [
                        {
                            "type": "file",
                            "name": "a",
                            "hash": "A" * 40,
                            "size": -1,
                        }
                    ]
                }
            }
        )


def test_parse_malformed_mpq_hash_missing():
    with pytest.raises(ValueError, match="hash must be"):
        parse_manifest(
            {"root": {"files": [{"type": "mpq", "name": "Patch", "size": 5}]}}
        )


def test_parse_malformed_dir_files_not_list():
    with pytest.raises(ValueError, match="files must be a list"):
        parse_manifest(
            {
                "root": {
                    "files": [{"type": "dir", "name": "Data", "files": "nope"}]
                }
            }
        )


def test_parse_malformed_dir_files_invalid_child():
    with pytest.raises(ValueError, match="must be an object"):
        parse_manifest(
            {
                "root": {
                    "files": [{"type": "dir", "name": "Data", "files": [1]}]
                }
            }
        )


def test_parse_typed_models_preserved():
    data = {
        "root": {
            "files": [
                {"type": "del", "name": "old.txt"},
                {"type": "mpq", "name": "patch", "hash": "B" * 40, "size": 9},
            ]
        }
    }
    m = parse_manifest(data)
    assert m["root"]["files"][0]["type"] == "del"
    assert m["root"]["files"][1]["type"] == "mpq"


def test_parse_nested_dir():
    data = {
        "root": {
            "files": [
                {
                    "type": "dir",
                    "name": "Data",
                    "files": [
                        {
                            "type": "file",
                            "name": "a.bin",
                            "hash": "A" * 40,
                            "size": 1,
                        }
                    ],
                }
            ]
        }
    }
    m = parse_manifest(data)
    assert m["root"]["files"][0]["name"] == "Data"
    assert m["root"]["files"][0]["files"][0]["name"] == "a.bin"  # type: ignore

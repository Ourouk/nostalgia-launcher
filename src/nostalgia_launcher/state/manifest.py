"""Typed manifest domain models.

The manifest is untrusted JSON fetched over HTTPS. It is parsed once into
explicit TypedDict models with validation — downstream code handles typed
models, not raw dicts. Validation errors are explicit (ValueError) and not
silently coerced.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class FileNode(TypedDict):
    type: Literal["file"]
    name: str
    hash: str
    size: int


class MPQNode(TypedDict):
    type: Literal["mpq"]
    name: str
    hash: str
    size: int


class DeleteNode(TypedDict):
    type: Literal["del"]
    name: str


class DirectoryNode(TypedDict):
    type: Literal["dir"]
    name: str
    files: list[ManifestNode]


ManifestNode = FileNode | MPQNode | DeleteNode | DirectoryNode


class ManifestRoot(TypedDict):
    files: list[ManifestNode]


class Manifest(TypedDict):
    root: ManifestRoot


def parse_manifest(data: object) -> Manifest:
    if not isinstance(data, dict):
        raise ValueError("malformed manifest: top level must be an object")
    root_raw = data.get("root")
    if not isinstance(root_raw, dict):
        raise ValueError("malformed manifest: 'root' is not an object")
    files_raw = root_raw.get("files", [])
    if not isinstance(files_raw, list):
        raise ValueError("malformed manifest: 'root.files' must be a list")
    files: list[ManifestNode] = []
    for idx, item in enumerate(files_raw):
        files.append(_parse_node(item, f"root.files[{idx}]"))
    return Manifest(root=ManifestRoot(files=files))


def _parse_node(data: object, path: str) -> ManifestNode:
    if not isinstance(data, dict):
        raise ValueError(f"malformed manifest: {path} must be an object")
    t = data.get("type")
    if t not in ("file", "dir", "del", "mpq"):
        raise ValueError(
            f"malformed manifest: {path}.type must be one of file/dir/del/mpq"
        )
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(
            f"malformed manifest: {path}.name must be a non-empty string"
        )
    if t == "file":
        return _parse_file_node(data, path)
    if t == "mpq":
        return _parse_mpq_node(data, path)
    if t == "del":
        # del has only name; extra keys ignored
        return DeleteNode(type="del", name=name)
    # dir
    files_raw = data.get("files", [])
    if not isinstance(files_raw, list):
        raise ValueError(f"malformed manifest: {path}.files must be a list")
    files: list[ManifestNode] = []
    for i, child in enumerate(files_raw):
        files.append(_parse_node(child, f"{path}.files[{i}]"))
    return DirectoryNode(type="dir", name=name, files=files)


def _parse_file_node(data: dict[str, object], path: str) -> FileNode:
    name = data["name"]  # validated above
    assert isinstance(name, str)
    h = data.get("hash")
    if not isinstance(h, str) or not h:
        raise ValueError(
            f"malformed manifest: {path}.hash must be a non-empty string"
        )
    size_raw = data.get("size")
    if not isinstance(size_raw, int) or isinstance(size_raw, bool):
        raise ValueError(f"malformed manifest: {path}.size must be an integer")
    if size_raw < 0:
        raise ValueError(
            f"malformed manifest: {path}.size must be non-negative"
        )
    return FileNode(type="file", name=name, hash=h, size=size_raw)


def _parse_mpq_node(data: dict[str, object], path: str) -> MPQNode:
    name = data["name"]
    assert isinstance(name, str)
    h = data.get("hash")
    if not isinstance(h, str) or not h:
        raise ValueError(
            f"malformed manifest: {path}.hash must be a non-empty string"
        )
    size_raw = data.get("size")
    if not isinstance(size_raw, int) or isinstance(size_raw, bool):
        raise ValueError(f"malformed manifest: {path}.size must be an integer")
    if size_raw < 0:
        raise ValueError(
            f"malformed manifest: {path}.size must be non-negative"
        )
    return MPQNode(type="mpq", name=name, hash=h, size=size_raw)

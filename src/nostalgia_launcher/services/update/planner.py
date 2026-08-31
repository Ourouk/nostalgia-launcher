"""Update planning: which files need updating.

Pure manifest-tree walks that answer "what needs updating" without touching
transport. Callers inject a ``file_ok(path, hash)`` predicate so the planner
stays filesystem-agnostic and testable.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from .manifest import ManifestNode, checked_node_rel, checked_node_size


def stale_tree(
    nodes: list[ManifestNode],
    file_ok: Callable[[str, str], bool],
    out_dir: str,
    log=None,
    is_cancelled=None,
) -> list[ManifestNode]:
    """Diff tree of manifest nodes whose local copy is missing or stale."""
    out: list[ManifestNode] = []
    for child in nodes:
        c = _traverse(child, [], file_ok, out_dir, log, is_cancelled)
        if c is not None:
            out.append(c)
    return out


def _traverse(
    node: ManifestNode,
    path_parts: list[str],
    file_ok: Callable[[str, str], bool],
    out_dir: str,
    log,
    is_cancelled,
) -> ManifestNode | None:
    if is_cancelled and is_cancelled():
        return None
    if node["type"] == "dir":
        cur = path_parts + [node["name"]]
        stale: list[ManifestNode] = []
        for child in node["files"]:
            c = _traverse(child, cur, file_ok, out_dir, log, is_cancelled)
            if c is not None:
                stale.append(c)
        if stale:
            return {"type": "dir", "name": node["name"], "files": stale}
        return None
    if node["type"] == "del":
        rel = checked_node_rel(path_parts, node["name"])
        if rel is None:
            if log:
                log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
            return None
        dest = os.path.join(out_dir, rel)
        return node if os.path.exists(dest) else None
    if node["type"] == "file":
        rel = checked_node_rel(path_parts, node["name"])
        if rel is None:
            if log:
                log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
            return None
        dest = os.path.join(out_dir, rel)
        return None if file_ok(dest, node["hash"]) else node
    if node["type"] == "mpq":
        rel = checked_node_rel(path_parts, node["name"], ".mpq")
        if rel is None:
            if log:
                log(
                    f"  Refusing unsafe manifest path: {node['name']!r}", "err"
                )
            return None
        mpq_dest = os.path.join(out_dir, rel)
        return None if file_ok(mpq_dest, node["hash"]) else node
    return None


def sum_needed_bytes(
    nodes: list[ManifestNode],
    file_ok: Callable[[str, str], bool],
    out_dir: str,
    is_cancelled=None,
) -> int:
    """Total bytes of files that actually need downloading."""
    total = 0

    def walk(node: ManifestNode, parts: list[str]) -> None:
        nonlocal total
        if is_cancelled and is_cancelled():
            return
        if node["type"] == "dir":
            cur = parts + [node["name"]]
            for child in node["files"]:
                walk(child, cur)
        elif node["type"] == "file":
            rel = checked_node_rel(parts, node["name"])
            if rel is None:
                return
            dest = os.path.join(out_dir, rel)
            if not file_ok(dest, node["hash"]):
                total += checked_node_size(node["size"])
        elif node["type"] == "mpq":
            rel = checked_node_rel(parts, node["name"], ".mpq")
            if rel is None:
                return
            dest = os.path.join(out_dir, rel)
            if not file_ok(dest, node["hash"]):
                total += checked_node_size(node["size"])

    for n in nodes:
        walk(n, [])
    return total


def collect_wanted(
    nodes: list[ManifestNode],
    file_ok: Callable[[str, str], bool],
    out_dir: str,
    log=None,
    is_cancelled=None,
) -> set[str]:
    """Stale file/mpq relative paths for the torrent backend."""
    wanted: set[str] = set()

    def walk(node: ManifestNode, parts: list[str]) -> None:
        if is_cancelled and is_cancelled():
            return
        if node["type"] == "dir":
            cur = parts + [node["name"]]
            for child in node["files"]:
                walk(child, cur)
        elif node["type"] == "file":
            rel = checked_node_rel(parts, node["name"])
            if rel is None:
                if log:
                    log(
                        f"  Refusing unsafe manifest path: {node['name']!r}",
                        "err",
                    )
                return
            dest = os.path.join(out_dir, rel)
            if not file_ok(dest, node["hash"]):
                assert rel is not None
                wanted.add(rel)
        elif node["type"] == "mpq":
            rel = checked_node_rel(parts, node["name"], ".mpq")
            if rel is None:
                if log:
                    log(
                        f"  Refusing unsafe manifest path: {node['name']!r}",
                        "err",
                    )
                return
            dest = os.path.join(out_dir, rel)
            if not file_ok(dest, node["hash"]):
                assert rel is not None
                wanted.add(rel)

    for child in nodes:
        walk(child, [])
    return wanted


def flatten_diff_tree(
    nodes: list[ManifestNode] | None,
    prefix: tuple[str, ...] = (),
) -> list[str]:
    """Flatten a diff tree into relative file paths."""
    if not nodes:
        return []
    paths: list[str] = []
    for node in nodes:
        if node["type"] == "dir":
            paths.extend(
                flatten_diff_tree(node["files"], prefix + (node["name"],))
            )
        elif node["type"] == "file" or node["type"] == "del":
            paths.append("/".join(prefix + (node["name"],)))
        elif node["type"] == "mpq":
            paths.append("/".join(prefix + (node["name"],)) + ".mpq")
    return paths

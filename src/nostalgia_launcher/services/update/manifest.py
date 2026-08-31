"""Manifest helpers for the update workflow.

Re-exports the typed manifest domain (``state/manifest``) and adds the
path-safety gates that guard every filesystem touch from a hostile manifest.
"""

from __future__ import annotations

from ...core.safety import safe_relative_path
from ...state.manifest import (
    DirectoryNode,
    FileNode,
    Manifest,
    ManifestNode,
    ManifestRoot,
    MPQNode,
    parse_manifest,
)

__all__ = [
    "DirectoryNode",
    "FileNode",
    "MPQNode",
    "Manifest",
    "ManifestNode",
    "ManifestRoot",
    "parse_manifest",
    "checked_node_rel",
    "checked_node_size",
]

_MAX_NODE_SIZE = 64 * 1024 * 1024 * 1024


def checked_node_rel(parts: list[str], name: str, ext: str = "") -> str | None:
    """Client-relative path of a manifest node, or None when unsafe."""
    rel = "/".join([*parts, name + ext])
    return rel if safe_relative_path(rel) else None


def checked_node_size(size: object) -> int:
    """Node size coerced to a sane non-negative int (0 when unusable)."""
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        return 0
    assert isinstance(size, (int, float))
    size_int = int(size)
    if size_int < 0:
        return 0
    return min(size_int, _MAX_NODE_SIZE)

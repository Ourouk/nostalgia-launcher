"""Manifest stage: server-owned overlay over the base client.

Checks the upstream manifest's listed files by size pre-check + sha256
and downloads stale ones over HTTPS from the manifest's own URLs (each
download sha256-gated). Additions only — never deletes, never touches
user-managed paths. Owns the verify verdict when reachable (the torrent
base is a third-party client the manifest publisher does not version).
"""

from __future__ import annotations


def split_manifest_paths(wanted: set[str]) -> tuple[set[str], set[str]]:
    """Split a stale set into (manifest-owned, torrent-owned) paths."""
    manifest_paths = {
        p[len("manifest:") :] for p in wanted if p.startswith("manifest:")
    }
    rest = {p for p in wanted if not p.startswith("manifest:")}
    return manifest_paths, rest


def tag_manifest_paths(paths: list[str]) -> list[str]:
    """Prefix manifest-owned stale paths with the ``manifest:`` namespace."""
    return sorted(f"manifest:{p}" for p in paths)

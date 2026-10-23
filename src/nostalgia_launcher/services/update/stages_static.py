"""Static stage: server-declared bundled entries (Briefing §4.B Static).

Probes the server-owned bundled entries surfaced through the
mods/assets layered registries (manifest-routed entries included) and
updates stale ones via their fixed URLs. Whole-file HTTPS fetch —
no folder recheck, no torrent participation. Additions and in-place
updates only; never deletes.
"""

from __future__ import annotations


def bundled_entry_paths(registry: list) -> set[str]:
    """Client-relative paths owned by server-bundled registry entries."""
    from .ownership import server_paths_from_list

    paths: list[str] = []
    for entry in registry or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id", "")).startswith("manifest-"):
            dest = entry.get("dest")
            if isinstance(dest, str) and dest:
                paths.append(dest)
            for rel in entry.get("installed_files", []) or []:
                if isinstance(rel, str) and rel:
                    paths.append(rel)
    return server_paths_from_list(paths)

"""Static stage: server-declared bundled entries (Briefing §4.B Static).

Probes the server-owned bundled entries surfaced through the
mods/assets layered registries (manifest-routed entries included) and
updates stale ones via their fixed URLs. Whole-file HTTPS fetch —
no folder recheck, no torrent participation. Additions and in-place
updates only; never deletes.
"""

from __future__ import annotations

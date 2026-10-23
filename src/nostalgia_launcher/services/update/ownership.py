"""Server-base vs community-overlay file ownership.

Answers one question: "is this client-relative path owned by the
server base or by the user's community layer?" Server-owned paths
(torrent file list, manifest entries) are authoritative — the server
wins collisions, with a log warning so the overwrite is never silent.
User-owned paths (mod/asset ``installed_files`` records, ``dlls.txt``,
``Interface/``) must never be touched by server syncs.

Network-free: torrent membership requires the caller to pass the
already-fetched file list; the manifest snapshot comes from the
config-store cache (offline serves cache).
"""

from __future__ import annotations

from ...core.safety import safe_relpath

#: User-layer directories the server syncs must never descend into.
#: Addon folders live here; no server pipeline writes below it.
USER_ADDON_ROOT = "Interface"

#: The client-side loader list — user-managed (mods register lines).
USER_LOADER_LIST = "dlls.txt"


def _norm(path: str) -> str:
    """Normalize a client-relative path for ownership comparison.

    Case-insensitive only where the OS is (Windows/macOS) —
    ``os.path.normcase`` semantics; Linux keeps exact compare so
    ``Data/x`` and ``Data/X`` stay distinct owners."""
    norm = path.replace("\\", "/").strip()
    import sys

    if sys.platform in ("win32", "darwin"):
        return norm.lower()
    return norm


def _valid(path: object) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return None
    if not safe_relpath(path.strip()):
        return None
    return _norm(path.strip())


def server_paths_from_list(paths: list) -> set[str]:
    """Normalized server-owned set from a raw path list."""
    owned: set[str] = set()
    for p in paths or []:
        norm = _valid(p)
        if norm is not None:
            owned.add(norm)
    return owned


def server_paths_from_manifest(entries: list) -> set[str]:
    """Normalized server-owned set from manifest snapshot entries."""
    owned: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        norm = _valid(entry.get("path", ""))
        if norm is not None:
            owned.add(norm)
    return owned


def user_paths_from_records(records: list[dict]) -> set[str]:
    """Normalized user-owned set from mod/asset state ``installed_files``."""
    owned: set[str] = set()
    for record in records or []:
        if not isinstance(record, dict):
            continue
        files = record.get("installed_files", [])
        if not isinstance(files, list):
            continue
        for rel in files:
            norm = _valid(rel)
            if norm is not None:
                owned.add(norm)
    return owned


def snapshot_manifest_paths() -> set[str]:
    """Server-owned manifest paths from the cached snapshot (network-free)."""
    from ...services import manifest as _manifest

    block = _manifest._manifest_block()
    if block is None:
        return set()
    try:
        from ...core import config_store as _store

        stored = _store.load_config().get(_manifest.MANIFEST_CACHE_KEY)
    except Exception:
        return set()
    if not isinstance(stored, dict):
        return set()
    entries = stored.get("entries")
    if not isinstance(entries, list):
        return set()
    return server_paths_from_manifest(entries)


def snapshot_user_records() -> list[dict]:
    """Raw mod/asset state records holding ``installed_files``."""
    try:
        from ...core.config_store import load_config
    except Exception:
        return []
    try:
        cfg = load_config()
    except Exception:
        return []
    records: list[dict] = []
    for section in ("mods", "assets"):
        bucket = cfg.get(section)
        if not isinstance(bucket, dict):
            continue
        for record in bucket.values():
            if isinstance(record, dict):
                records.append(record)
    return records


def is_user_managed(path: str, user_owned: set[str]) -> bool:
    """Whether a client-relative path belongs to the community layer."""
    norm = _valid(path)
    if norm is None:
        return False
    if norm in user_owned:
        return True
    if norm == _norm(USER_LOADER_LIST):
        return True
    return norm == _norm(USER_ADDON_ROOT) or norm.startswith(
        _norm(USER_ADDON_ROOT) + "/"
    )


def collisions(server_owned: set[str], user_owned: set[str]) -> list[str]:
    """Paths claimed by both layers — server wins, caller must warn."""
    return sorted(server_owned & user_owned)


def check_server_write(
    path: str,
    server_owned: set[str],
    user_owned: set[str],
    log=None,
) -> bool:
    """Gate a server-sync write: True = proceed (server wins), False = skip.

    Logs a warning on collisions (server overwrites a user file) and on
    writes outside both sets into user-managed areas (refused). Writes to
    unknown-but-not-user-managed paths proceed silently — they are plain
    server-base content.
    """
    norm = _valid(path)
    if norm is None:
        return False
    emit = log if log is not None else (lambda *a: None)
    if norm in server_owned and norm in user_owned:
        try:
            emit(f"[ownership] Server overwrites user file: {path}", "err")
        except TypeError:
            emit(f"[ownership] Server overwrites user file: {path}")
        return True
    if norm not in server_owned and is_user_managed(path, user_owned):
        try:
            emit(f"[ownership] Refusing server write to user file: {path}")
        except TypeError:
            emit(f"[ownership] Refusing server write to user file: {path}")
        return False
    return True

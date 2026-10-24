"""Addons panel controller.

Owns the ADDONS-panel business logic: the catalog fetch with its offline
fallback, the Interface/AddOns scan with .toc parsing, the cached remote-sha
verification (TTL-gated, cache-only on demand) and the sequential
install/update/remove worker. Publishes snapshots as AddonsLoaded
and worker outcomes as OperationFinished on the shared EventDispatcher; the Qt
Addons panel renders them. No GUI toolkit.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import replace

from ..core import config_store
from ..core.errors import describe_install_error
from ..core.helpers import same_git_repo
from ..core.log_sink import log
from ..services import addons, catalog
from ..state.events import (
    AddonsLoaded,
    EventDispatcher,
    LogMessage,
    OperationFinished,
    StatusChanged,
)
from ..state.models import AddonError, AddonsState, AddonState

# Status message when the remote could not be reached to compare SHAs — a
# transient state (rate limit, outage), distinct from a broken addon folder.
C_COULD_NOT_CHECK = "Couldn't check for updates"

# Serializes index read-modify-write cycles: the install worker
# (_install_group/_install_direct), the uninstall worker
# (_apply_pending_worker) and the scan rebuild (_reconcile_index) all
# load → patch → store. Without this, an install landing mid-scan can be
# clobbered by the scan's store.
_INDEX_LOCK = threading.Lock()


def _norm_folder(name) -> str:
    """Comparison key for addon folder names: strip, NFC-normalize and
    casefold. Comparison-only — installed identity, config keys and TOC
    file names always keep their exact disk case (on Linux ``Foo`` and
    ``foo`` can be two distinct installs). Empty for non-string input so
    malformed rows never collide with a real folder."""
    if not isinstance(name, str):
        return ""
    try:
        import unicodedata

        name = unicodedata.normalize("NFC", name)
    except Exception:
        pass
    key = name.strip().casefold()
    return key if key else ""


def _gits_differ(a, b) -> bool:
    """Whether two git URLs name different repos (both present and not the
    same repo per same_git_repo). Missing URLs never differ — an unknown
    source is compatible with anything."""
    if not a or not b:
        return False
    try:
        return not same_git_repo(a, b)
    except Exception:
        return a != b


def _index_available(available: list) -> tuple[dict, dict]:
    """Exact plus norm-keyed folder indexes over AVAILABLE rows.

    Exact match always wins at lookup time; the norm index is only the
    fallback for variant spellings (case/whitespace/unicode). A row's
    primary folder always beats another row's learned-set hit on the
    same spelling (see _available_from_catalog) — set membership
    fills gaps, never shadows primaries. Last row wins, mirroring
    merge_addons override order."""
    by_folder: dict = {}
    by_norm: dict = {}
    # Pass 1: primary slots. Pass 2: learned-set members that do not
    # shadow a primary claim from another row.
    for row in available:
        by_folder.setdefault(row["folder"], row)
    for row in available:
        for sub in row.get("folders") or []:
            if isinstance(sub, str) and sub and sub not in by_folder:
                by_folder[sub] = row
    for row in available:
        key = _norm_folder(row.get("folder"))
        if key:
            by_norm[key] = row
    return by_folder, by_norm


def _scan_disk_dirs(addons_dir: str) -> tuple[set, list]:
    """Addon folder names on disk: (name set, sorted list).

    Skips Blizzard/Turtle shipped folders and dotfiles. Empty pair when
    the folder is missing or unreadable. Never raises."""
    if not addons_dir:
        return set(), []
    try:
        with os.scandir(addons_dir) as it:
            seen = []
            for entry in it:
                try:
                    is_dir = entry.is_dir(follow_symlinks=True)
                except OSError:
                    continue
                if not is_dir:
                    continue
                name = entry.name
                if name.startswith(("Blizzard_", "Turtle_", ".")):
                    continue
                seen.append(name)
        return set(seen), sorted(seen)
    except OSError:
        return set(), []


def _lookup_available(
    name, saved_git, by_folder, by_norm, siblings, siblings_by_norm
):
    """The catalog/sibling row a disk folder reconciles against.

    Exact folder match first; then a norm match only when the saved
    source agrees (a norm match across *different* repos is a different
    addon sharing a folder spelling, not this install); then a sibling
    of a multi-addon repo, adopted under the repo's catalog entry.
    None when the folder is untracked."""
    avail = by_folder.get(name)
    if avail is not None and avail.get("folder") != name:
        # Learned-set hit on a sub-folder: keep it only when the saved
        # source agrees with the entry's repo — a variant fork of a
        # sub-folder falls through to the variant logic below instead
        # of adopting into the wrong unit.
        if _gits_differ(saved_git, avail.get("git")):
            avail = None
    if avail is None:
        # Variant-spelling fallback: only associate when the sources
        # agree — a norm match across *different* repos is a different
        # addon sharing a folder spelling, not this install (exact
        # matches keep the legacy catalog-authoritative migration).
        cand = by_norm.get(_norm_folder(name))
        if cand is not None and not _gits_differ(saved_git, cand.get("git")):
            avail = cand
    if avail is None:
        entry = siblings.get(name)
        if entry is None:
            hit = siblings_by_norm.get(_norm_folder(name))
            entry = siblings[hit] if hit else None
        if entry is not None and _gits_differ(saved_git, entry.get("git")):
            entry = None
        if entry is not None:
            # Multi-addon repo sibling (e.g. AtlasLoot's modules):
            # adopt it under the repo's catalog entry like a primary
            # folder instead of leaving it "Not tracked".
            avail = {
                "folder": name,
                "status": "available",
                "git": entry.get("git"),
                "branch": entry.get("branch"),
                "ref": entry.get("ref"),
                "toc": {},
                "description": entry.get("description"),
                "error": None,
            }
    return avail


def _suppress_shadowed(available: list, installed_gits: dict) -> list:
    """Drop AVAILABLE rows that denote an installed addon under a
    textually different spelling (case/whitespace/unicode variant).

    The git URL differentiates: a same-norm row pointing at the same repo
    (or with an unknown source on either side) is one addon shown twice —
    drop the available-side shadow. A same-norm row pointing at a
    *different* repo is a different addon (fork/custom) sharing a folder
    spelling — keep it visible, with a conflict hint in `description`
    naming the installed unit it would replace. Rows whose learned
    `folders` set overlaps an installed folder are treated like a
    primary overlap (one unit per folder — install = replace).
    Producers spell names from the catalog side while ``installed``
    keys come from the disk side. Exact installed identity is
    untouched. Logs what it drops (observability for variant
    spellings in the wild). Never raises."""
    try:
        norms: dict = {}
        for folder, git in installed_gits.items():
            key = _norm_folder(folder)
            if key:
                norms[key] = git
    except Exception:
        return available
    kept = []
    for row in available:
        try:
            folder = row.get("folder") if isinstance(row, dict) else None
            key = _norm_folder(folder)
            git = row.get("git") if isinstance(row, dict) else None
            folders = row.get("folders") if isinstance(row, dict) else None
            if not isinstance(folders, list):
                folders = [folder] if folder else []
            hit = key and key in norms
            if not hit:
                try:
                    hit = any(
                        _norm_folder(f) in norms
                        for f in folders
                        if isinstance(f, str)
                    )
                except Exception:
                    hit = False
        except Exception:
            kept.append(row)
            continue
        # First overlapping installed folder decides (one unit per
        # folder): its repo differentiates shadow from alternative.
        other_git = None
        other_folder = None
        try:
            for f, g in installed_gits.items():
                if _norm_folder(f) == key or any(
                    _norm_folder(s) == _norm_folder(f)
                    for s in folders
                    if isinstance(s, str)
                ):
                    other_git = g
                    other_folder = f
                    break
        except Exception:
            other_git = None
        if other_folder is None:
            kept.append(row)
            continue
        if not _gits_differ(other_git, git):
            try:
                log(
                    f"  Addon {folder!r} shadows an installed folder — hiding."
                )
            except Exception:
                pass
            continue
        if isinstance(row, dict):
            # Different repo, overlapping set: install alternative —
            # say what it replaces (description renders today).
            try:
                base = row.get("description") or ""
                hint = f"Installs over {other_folder} — replaces it."
                row["description"] = f"{base} {hint}".strip() if base else hint
            except Exception:
                pass
        kept.append(row)
    return kept


class AddonsController:
    """Owns the addons lifecycle; speaks to the UI only through events.

    `get_out_dir` is an optional zero-arg callable returning the current game
    folder (the Qt UI supplies its path field's getter). When omitted the
    controller reads ``out_dir`` from the on-disk config, mirroring the
    UI's default.
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        get_out_dir: Callable[[], str] | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self.state = AddonsState()
        if get_out_dir is None:

            def _default_get_out_dir() -> str:
                val = config_store.load_config().get("out_dir", "")
                return val if isinstance(val, str) else ""

            get_out_dir = _default_get_out_dir

        self._get_out_dir = get_out_dir

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def updates_count(self) -> int:
        return self.state.updates_count

    @property
    def installing(self) -> bool:
        return self.state.installing

    def verify(self, force=False, remote_checks=True) -> bool:
        """Scan Interface/AddOns, match against the catalog and check every
        tracked addon's remote commit sha (config-cached). With
        remote_checks=False the scan is guaranteed network-free: shas come
        from the cache only (used for post-install/update refreshes).
        Returns True when a background scan actually started."""
        if self.state.busy:
            return False
        # A recent verify result is already rendered — plain tab switches
        # within the TTL don't need a rescan or a rebuild at all.
        if (
            not force
            and self.state.state == "done"
            and (time.time() - self.state.verified_ts)
            < addons.ADDONS_VERIFY_TTL
        ):
            return False
        client = (self._get_out_dir() or "").strip()
        self.state.busy = True
        self.state.state = "verifying"

        # Instant paint: build a preview snapshot from the persisted catalog
        # cache (network-free) so the AVAILABLE section shows immediately
        # while the full scan (filesystem + SHA checks) runs in background.
        try:
            cached = addons.catalog_from_cache()
        except Exception:
            cached = []
        if cached:
            preview_rows = self._available_from_catalog(cached)
            self._overlay_errors(preview_rows)
            preview_rows = _suppress_shadowed(
                preview_rows,
                {
                    f: (s.git if isinstance(s, AddonState) else None)
                    for f, s in self.state.addons.items()
                },
            )
            # A copy: the live state object keeps being mutated by the scan,
            # so both events must not share one reference.
            snapshot = replace(self.state)
            snapshot.available = [
                AddonState.from_dict(rec) for rec in preview_rows
            ]
            try:
                fast_installed = self._fast_paint_installed(
                    client, preview_rows
                )
            except Exception:
                fast_installed = {}
            if fast_installed:
                # One row per install unit, keyed on entry id — the same
                # collapse the scan publishes (per-folder toc optional).
                fast_installed = self._collapse_units(fast_installed)
                snapshot.addons = {
                    folder: AddonState.from_dict(rec)
                    for folder, rec in fast_installed.items()
                }
                snapshot.updates_count = sum(
                    1
                    for rec in fast_installed.values()
                    if rec.get("status") == "outOfDate"
                )
            self._dispatcher.post(AddonsLoaded(snapshot))
        else:
            self._dispatcher.post(AddonsLoaded(self.state))

        def worker():
            try:
                catalog = addons.addons_catalog(force=force)
            except Exception:
                # offline — fall back to whatever the config still holds
                try:
                    catalog = addons.catalog_from_cache()
                except Exception:
                    catalog = []

            try:
                self._verify_worker_body(catalog, client, force, remote_checks)
            except Exception as e:
                # A crashed scan thread would leave state.busy stuck True
                # forever (ADDONS wedged on "Checking…" until restart) —
                # always report the outcome instead.
                log(f"Add-on verification failed: {e}", "err")
                self.state.busy = False
                self.state.installing = False
                self._dispatcher.post(OperationFinished("addons", False))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _fast_paint_installed(self, client: str, preview_rows: list) -> dict:
        """Paint INSTALLED synchronously from index × AVAILABLE rows.

        Network-free: index folder sets × the cached catalog (entry id →
        row). Update status comes from the index entry's own sha against
        the cached remote sha — never a live lookup, never a config
        re-read. A missing/unconfirmed sha paints "unknown" (retryable),
        never "up to date": the background scan is the only thing that
        may claim up-to-date. Disk existence is assumed — drift
        (hand-deleted folders, changed toc) is reconciled by the
        background scan, which stays authoritative. Returns {} when there
        is nothing to paint from."""
        try:
            index = addons.load_addons_index()
        except Exception:
            return {}
        try:
            units = index.get("units")
            orphans = index.get("orphans")
        except Exception:
            return {}
        if not isinstance(units, dict) or not units:
            return {}
        installed: dict = {}
        by_id: dict = {}
        try:
            for row in preview_rows or []:
                if not isinstance(row, dict):
                    continue
                eid = row.get("id")
                if isinstance(eid, str) and eid:
                    by_id[eid] = row
        except Exception:
            pass
        for eid, unit in units.items():
            if not isinstance(eid, str) or not eid:
                continue
            if not isinstance(unit, dict):
                continue
            folders = unit.get("folders")
            if not isinstance(folders, list) or not folders:
                continue
            row = by_id.get(eid)
            if row is None:
                continue
            source_type = row.get("source_type") or "git"
            for folder in folders:
                if not isinstance(folder, str) or not folder:
                    continue
                if folder in installed:
                    continue
                rec = {
                    "folder": folder,
                    "status": "unknown",
                    "git": None,
                    "branch": None,
                    "ref": None,
                    "source_type": source_type,
                    "download_url": None,
                    "toc": {},
                    "description": None,
                    "error": None,
                    "id": eid,
                    "folders": list(folders),
                }
                rec.update(
                    git=row.get("git"),
                    branch=row.get("branch"),
                    ref=row.get("ref"),
                    download_url=row.get("download_url"),
                    description=row.get("description"),
                    name=row.get("name") or None,
                )
                toc_snap = unit.get("toc")
                title = ""
                try:
                    if isinstance(toc_snap, dict):
                        title = (toc_snap.get(folder) or {}).get("title", "")
                except Exception:
                    title = ""
                if isinstance(title, str) and title:
                    rec["toc"] = {"Title": title}
                indexed_sha = unit.get("sha")
                if source_type == "direct":
                    rec["status"] = "upToDate"
                elif not isinstance(indexed_sha, str) or not indexed_sha:
                    # No confirmed version on record — retryable, never
                    # "up to date". Only the background scan may claim
                    # up-to-date.
                    rec["status"] = "unknown"
                    rec["error"] = C_COULD_NOT_CHECK
                else:
                    git_url = rec.get("git")
                    try:
                        cached_sha = (
                            addons.addon_cached_sha(
                                git_url,
                                rec.get("branch"),
                                rec.get("ref"),
                            )
                            if isinstance(git_url, str) and git_url
                            else None
                        )
                    except Exception:
                        cached_sha = None
                    if cached_sha is None:
                        rec["status"] = "unknown"
                        rec["error"] = C_COULD_NOT_CHECK
                    elif cached_sha == indexed_sha:
                        rec["status"] = "upToDate"
                    else:
                        rec["status"] = "outOfDate"
                installed[folder] = rec
        # Orphan folders the index knew but no group claims: list them as
        # untracked-but-present so removal from a group never hides them.
        try:
            if isinstance(orphans, list):
                for folder in orphans:
                    if (
                        not isinstance(folder, str)
                        or not folder
                        or folder in installed
                    ):
                        continue
                    installed[folder] = {
                        "folder": folder,
                        "status": "unknown",
                        "git": None,
                        "branch": None,
                        "ref": None,
                        "source_type": "git",
                        "download_url": None,
                        "toc": {},
                        "description": None,
                        "error": None,
                    }
        except Exception:
            pass
        return installed

    def _discover_siblings(
        self, catalog_list, disk_names, records, force, addons_dir=""
    ) -> dict:
        """Map sibling folder name → catalog entry for multi-addon repos.

        One Git repo often ships several addons (AtlasLoot's seven
        modules): only the catalog's primary folder would otherwise match
        and the rest would stay "Not tracked" forever. The tracked-install
        index names the folder set per entry id (no archive fetch on the
        update path); discovery still fetches an archive for a
        *relevant but untracked* repo (its catalog folder is on disk, or a
        snapjaw clone of it sits in `.snapjaw_cache`) and only when its
        sha moved (cached otherwise), so unrelated catalog entries cost
        nothing. Entries sharing one repo (git#branch/ref) resolve once.
        Never raises.
        """
        by_folder: dict = {}
        saved_gits = set()
        try:
            for rec in records.values():
                if isinstance(rec, dict) and rec.get("git"):
                    saved_gits.add(rec["git"])
        except Exception:
            pass
        try:
            snapjaw_urls = addons.snapjaw_cache_repo_urls(addons_dir)
        except Exception:
            snapjaw_urls = set()
        disk_norms = set()
        try:
            disk_norms = {_norm_folder(n) for n in disk_names}
            disk_norms.discard("")
        except Exception:
            pass
        # Group by repo key so multi-folder repos resolve once. Units
        # tracked in the index (by entry id) never hit the network —
        # their folder sets are contributed below; only relevant but
        # untracked repos fetch an archive.
        indexed: dict = {}
        indexed_ids = set()
        try:
            position = {
                r.get("id"): r
                for r in catalog_list
                if isinstance(r, dict) and r.get("id")
            }
            index = addons.load_addons_index()
            units = index.get("units")
            if isinstance(units, dict):
                for eid, unit in units.items():
                    if not isinstance(eid, str) or not eid:
                        continue
                    if not isinstance(unit, dict):
                        continue
                    catalog_row = position.get(eid)
                    if catalog_row is None:
                        continue
                    indexed_ids.add(eid)
                    folders = unit.get("folders")
                    if not isinstance(folders, list):
                        continue
                    for folder in folders:
                        if (
                            isinstance(folder, str)
                            and folder
                            and folder not in indexed
                        ):
                            indexed[folder] = catalog_row
        except Exception:
            indexed = {}
            indexed_ids = set()
        groups: dict = {}
        order: list = []
        for entry in catalog_list:
            if entry.get("id") in indexed_ids:
                continue
            git = entry.get("git")
            if not git:
                continue
            key = (git, entry.get("branch"), entry.get("ref"))
            relevant = (
                _norm_folder(entry.get("name")) in disk_norms
                or any(same_git_repo(saved, git) for saved in saved_gits)
                or any(same_git_repo(snap, git) for snap in snapjaw_urls)
            )
            if not relevant:
                continue
            if key not in groups:
                groups[key] = entry
                order.append(key)
        # Tracked groups name their siblings without any fetch.
        try:
            for folder, entry in indexed.items():
                by_folder.setdefault(folder, entry)
        except Exception:
            pass
        for key in order:
            entry = groups[key]
            try:
                siblings = addons.addon_repo_names(
                    entry.get("git"),
                    entry.get("branch"),
                    entry.get("ref"),
                    force=force,
                )
            except Exception:
                continue
            for sibling in siblings:
                by_folder.setdefault(sibling, entry)
        return by_folder

    def _verify_worker_body(self, catalog_list, client, force, remote_checks):
        """The filesystem/catalog scan of verify()'s worker thread; runs
        inside the caller's exception guard."""
        available = self._available_from_catalog(catalog_list)
        by_folder, by_norm = _index_available(available)
        installed = {}
        records = config_store.load_config().get("addons", {})
        if not isinstance(records, dict):
            records = {}
        ap = addons.addons_path(client) if client else ""
        disk_names, dir_names = _scan_disk_dirs(ap)
        # Serial per-verify memo: folders sharing one repo (git#branch/ref)
        # resolve the remote sha once instead of once per folder.
        sha_memo: dict = {}
        siblings, siblings_by_norm = self._adopt_sibling_rows(
            available,
            by_folder,
            by_norm,
            catalog_list,
            disk_names,
            records,
            force,
            ap,
            remote_checks,
        )
        for name in dir_names:
            rec = {
                "folder": name,
                "status": "unknown",
                "git": None,
                "branch": None,
                "ref": None,
                "toc": {},
                "description": None,
                "error": None,
            }
            toc_dir = os.path.join(ap, name)
            toc_path, toc_kind = addons.find_toc_for_folder(toc_dir, name)
            if toc_path is None:
                rec.update(status="invalid", error="Missing .toc file")
                installed[name] = rec
                continue
            rec["toc"] = addons.read_toc_file_cached(toc_path)
            if toc_kind == "single":
                stem = os.path.basename(toc_path)[:-4]
                rec.update(
                    status="invalid",
                    error=(
                        f"Found {os.path.basename(toc_path)} — rename "
                        f"folder to {stem} to load in-game"
                    ),
                )
                installed[name] = rec
                continue
            saved = records.get(name)
            saved_git = saved.get("git") if isinstance(saved, dict) else None
            avail = _lookup_available(
                name,
                saved_git,
                by_folder,
                by_norm,
                siblings,
                siblings_by_norm,
            )
            if avail:
                rec["description"] = avail["description"]
                rec["name"] = avail.get("name") or None
                rec["id"] = avail.get("id") or None
            else:
                # No catalog hit: the folder may still belong to a
                # tracked unit (hand-dropped sub-folder). The index
                # keyed by entry id is the membership source; when the
                # index has no entry, fall back to a catalog entry whose
                # git matches the saved record so manually-tracked or
                # pre-existing installs still collapse into their unit.
                try:
                    eid, _folders = addons.unit_for_folder(
                        addons.load_addons_index(), name
                    )
                    rec["id"] = eid
                except Exception:
                    pass
                if not rec.get("id"):
                    # Fall back: a catalog entry whose git matches the
                    # saved record owns this folder's unit.
                    try:
                        saved_git = (
                            saved.get("git")
                            if isinstance(saved, dict)
                            else None
                        )
                        if saved_git:
                            for arow in available:
                                if (
                                    isinstance(arow, dict)
                                    and arow.get("id")
                                    and arow.get("git")
                                    and same_git_repo(arow["git"], saved_git)
                                ):
                                    rec["id"] = arow["id"]
                                    break
                            if (
                                not rec.get("id")
                                and isinstance(saved, dict)
                                and saved.get("git")
                            ):
                                # Custom entry (saved source, no catalog
                                # row): the folder name is the unit key.
                                rec["id"] = name
                    except Exception:
                        pass
            self._reconcile_rec(
                rec, name, saved, avail, force, remote_checks, sha_memo
            )
            installed[name] = rec

        self._reconcile_index(installed, records, ap, sha_memo)
        self._finish_scan(installed, available)

    def _reconcile_index(
        self, installed: dict, records: dict, addons_dir: str, sha_memo: dict
    ) -> None:
        """Patch the tracked-install index from scan truth, persist once.

        Units are keyed by catalog entry id: scanned folders with a
        resolved entry id join that id's unit; folders with no id pass
        through (untracked). Drop: index folders absent from disk leave
        the unit (except when the scan still claims them on disk —
        the orphan independence win: custom/removed-upstream installs
        stay listed + update-checkable against saved git). Refresh:
        changed toc snapshots are re-read. Never raises — the index is
        a cache, the disk scan stays authoritative. Serialized with
        the install/uninstall writers via _INDEX_LOCK."""
        try:
            with _INDEX_LOCK:
                index = addons.load_addons_index()
                units = index.get("units")
                if not isinstance(units, dict):
                    units = {}
                    index["units"] = units
                disk_names = set(installed)
                # Map every scanned folder to its owning entry id: the
                # scan's id (catalog hit, sibling adopt, or the index's
                # own unit for hand-dropped sub-folders).
                owner_of: dict = {}
                for name, rec in installed.items():
                    if not isinstance(rec, dict):
                        continue
                    eid = rec.get("id")
                    if isinstance(eid, str) and eid:
                        owner_of[name] = eid
                # Adopt / refresh scanned folders into their owning
                # unit: the scan's set is truth, so the unit keeps
                # exactly the folders currently on disk (a hand-deleted
                # folder drops out instead of lingering as a phantom).
                grouped: dict = {}
                for name, eid in owner_of.items():
                    grouped.setdefault(eid, []).append(name)
                for eid, names in grouped.items():
                    unit = units.get(eid)
                    if not isinstance(unit, dict):
                        unit = {}
                        units[eid] = unit
                    folders = unit.get("folders")
                    if not isinstance(folders, list):
                        folders = []
                    prev = [f for f in folders if f not in names]
                    unit["folders"] = sorted(set(names))
                    # shortcut: shipped set replaces the want (the archive
                    # is truth); the previous set survives one scan as
                    # prev_folders so a vanished-upstream sub-addon flags
                    # instead of silently shrinking.
                    if prev:
                        unit["prev_folders"] = sorted(set(prev) - set(names))
                    rec0 = installed.get(names[0]) or {}
                    if isinstance(rec0, dict):
                        if rec0.get("git"):
                            unit["sha"] = None
                            saved = (
                                records.get(names[0])
                                if isinstance(records, dict)
                                else None
                            )
                            saved_sha = (
                                saved.get("sha")
                                if isinstance(saved, dict)
                                else None
                            )
                            if isinstance(saved_sha, str) and saved_sha:
                                unit["sha"] = saved_sha
                    try:
                        claimed = unit.get("claimed_at")
                        if not isinstance(claimed, dict):
                            claimed = {}
                            unit["claimed_at"] = claimed
                        import time as _time

                        now = _time.time()
                        for name in names:
                            claimed[name] = now
                        unit.setdefault("installed_at", now)
                    except Exception:
                        pass
                    try:
                        toc = unit.get("toc")
                        if not isinstance(toc, dict):
                            toc = {}
                            unit["toc"] = toc
                        for name in names:
                            snap = toc.get(name)
                            if addons.toc_snapshot_changed(
                                addons_dir, name, snap
                            ):
                                toc.update(
                                    addons.snapshot_toc_dir(addons_dir, name)
                                )
                    except Exception:
                        pass
                # Drop index folders missing from disk (hand-deleted).
                # Adopt runs first so a unit the scan still claims keeps
                # its scanned set; anything left over with no folders
                # on disk is pruned.
                for eid in list(units):
                    unit = units.get(eid)
                    if not isinstance(unit, dict):
                        units.pop(eid, None)
                        continue
                    folders = unit.get("folders")
                    if not isinstance(folders, list):
                        unit["folders"] = []
                        folders = []
                    if not folders:
                        continue
                    if any(f in disk_names for f in folders):
                        continue
                    if any(
                        owner_of.get(f) == eid and f in disk_names
                        for f in folders
                    ):
                        continue
                    units.pop(eid, None)
                addons.store_addons_index(index)
        except Exception:
            return

    def _adopt_sibling_rows(
        self,
        available,
        by_folder,
        by_norm,
        catalog_list,
        disk_names,
        records,
        force,
        addons_dir,
        remote_checks,
    ) -> tuple[dict, dict]:
        """Discover multi-addon repo siblings and list the uninstalled
        ones as AVAILABLE rows (mutates ``available`` and its indexes).

        A sibling variant-spelling an installed folder is adopted in the
        scan loop, never listed as available. Returns (siblings,
        siblings_by_norm). Empty pair when discovery is off."""
        if not (remote_checks and disk_names):
            return {}, {}
        siblings = self._discover_siblings(
            catalog_list, disk_names, records, force, addons_dir
        )
        siblings_by_norm = {
            _norm_folder(s): s for s in siblings if _norm_folder(s)
        }
        # Discovered-but-not-installed siblings are installable too.
        known = {_norm_folder(a["folder"]) for a in available}
        known |= {_norm_folder(n) for n in disk_names}
        known.discard("")
        for sibling, entry in sorted(siblings.items()):
            if _norm_folder(sibling) in known:
                continue
            row = {
                "folder": sibling,
                "id": entry.get("id") or sibling,
                "name": entry.get("name") or None,
                "status": "available",
                "folders": [sibling],
                "git": entry.get("git"),
                "source_type": entry.get("source_type") or "git",
                "download_url": entry.get("download_url"),
                "branch": entry.get("branch"),
                "ref": entry.get("ref"),
                "toc": entry.get("toc") or {},
                "description": entry.get("description"),
                "depends": list(entry.get("depends") or []),
                "error": None,
            }
            available.append(row)
            by_folder.setdefault(sibling, row)
            key = _norm_folder(sibling)
            if key:
                by_norm.setdefault(key, row)
            known.add(_norm_folder(sibling))
        return siblings, siblings_by_norm

    def _reconcile_rec(
        self, rec, name, saved, avail, force, remote_checks, sha_memo
    ) -> None:
        """Set a scanned folder's source and status (mutates ``rec``).

        Six cases: the curated fork overrides the saved repo, the saved
        repo migrates to the catalog's repo, a direct download migrates
        (URL change ⇒ out-of-date), a custom entry keeps its saved
        source, or a first-seen folder is adopted silently with the
        resolved remote sha as its baseline. Direct entries have no
        commit sha — presence on disk is up-to-date, absence is
        available."""
        avail_source = (avail or {}).get("source_type") or "git"
        override = addons.RECOMMENDED_ADDONS.get(name)
        if (
            saved
            and saved.get("git")
            and override
            and not same_git_repo(saved["git"], override)
        ):
            # Installed from a different repo than the curated
            # fork — offer an update that migrates to the fork.
            rec.update(
                git=override,
                source_type="git",
                download_url=None,
                branch=None,
                ref=None,
                status="outOfDate",
            )
        elif saved and avail and avail_source == "direct":
            rec.update(
                git=avail.get("git"),
                source_type="direct",
                download_url=avail.get("download_url"),
                branch=avail.get("branch"),
                ref=avail.get("ref"),
            )
            saved_url = saved.get("download_url")
            if saved.get("source_type") != "direct" or (
                saved_url and saved_url != avail.get("download_url")
            ):
                # Source switch (git→direct or a new direct URL) —
                # reinstall on next apply.
                rec["status"] = "outOfDate"
            else:
                rec["status"] = "upToDate"
        elif saved and saved.get("git") and avail:
            # The launcher catalog is authoritative for the addon's
            # source. A saved repo that differs means the addon was
            # installed from elsewhere — offer an update that
            # migrates to the catalog's repo. Even when the repos
            # match, verify against the catalog's branch/ref (the
            # saved record may predate a catalog branch change).
            if not same_git_repo(saved["git"], avail["git"]):
                rec.update(
                    git=avail["git"],
                    source_type=avail_source,
                    download_url=avail.get("download_url"),
                    branch=avail["branch"],
                    ref=avail["ref"],
                    status="outOfDate",
                )
            else:
                rec.update(
                    git=avail["git"],
                    source_type=avail_source,
                    download_url=avail.get("download_url"),
                    branch=avail["branch"],
                    ref=avail["ref"],
                )
                remote = self._resolve_remote_sha(
                    rec["git"],
                    rec["branch"],
                    rec["ref"],
                    force=force,
                    remote_checks=remote_checks,
                    fallback_sha=saved.get("sha"),
                    memo=sha_memo,
                )
                self._apply_remote_status(rec, remote, saved.get("sha"))
        elif saved and saved.get("git"):
            # Installed addon not in the catalog (a custom entry) —
            # keep tracking the saved source.
            rec.update(
                git=saved.get("git"),
                source_type=saved.get("source_type") or "git",
                download_url=saved.get("download_url"),
                branch=saved.get("branch"),
                ref=saved.get("ref"),
            )
            if rec.get("source_type") == "direct":
                # No remote version to check — presence is up-to-date.
                rec["status"] = "upToDate"
                return
            remote = self._resolve_remote_sha(
                rec["git"],
                rec["branch"],
                rec["ref"],
                force=force,
                remote_checks=remote_checks,
                fallback_sha=saved.get("sha"),
                memo=sha_memo,
            )
            self._apply_remote_status(rec, remote, saved.get("sha"))
        elif avail:
            # Installed addon the launcher has never recorded (a
            # folder set up elsewhere, or addons pre-dating the
            # launcher). Adopt it silently: record its catalog
            # source and resolve its current remote sha as the
            # baseline, so it's tracked like any launcher-installed
            # addon from now on — no re-download and no "update"
            # flag on every pre-existing addon. Offline/rate-limited
            # resolution leaves it retryable, never "out of date".
            # Direct entries have no sha — presence is the baseline.
            rec.update(
                git=avail["git"],
                source_type=avail_source,
                download_url=avail.get("download_url"),
                branch=avail["branch"],
                ref=avail["ref"],
            )
            if avail_source == "direct":
                rec["status"] = "upToDate"
                config_store.update_config(
                    lambda c, f=name, a=avail: c.setdefault(
                        "addons", {}
                    ).__setitem__(
                        f,
                        {
                            "git": a.get("git"),
                            "source_type": "direct",
                            "download_url": a.get("download_url"),
                        },
                    )
                )
                return
            remote = self._resolve_remote_sha(
                avail["git"],
                avail["branch"],
                avail["ref"],
                force=force,
                remote_checks=remote_checks,
                memo=sha_memo,
            )
            if remote:
                rec["status"] = "upToDate"
                config_store.update_config(
                    lambda c, f=name, g=avail["git"], b=avail["branch"], r=avail["ref"], s=remote: (
                        c.setdefault("addons", {}).__setitem__(
                            f,
                            {
                                "git": g,
                                "branch": b,
                                "ref": r,
                                "sha": s,
                            },
                        )
                    )
                )
            else:
                rec.update(status="unknown", error=C_COULD_NOT_CHECK)

    def _finish_scan(self, installed: dict, available: list) -> None:
        """Close a verify scan: re-attach session install errors,
        hide available rows shadowing installed folders, collapse
        multi-folder units to one row per entry, and publish the
        snapshot. The collapse runs AFTER the suppress so the
        suppress still sees the pre-collapse per-disk-folder map."""
        # Overlay install failures from this session: the rescan drops
        # them (a failed install leaves no folder on disk), so re-attach
        # errors to the matching available row — or synthesize one for a
        # failed custom addon. Errors for now-installed folders are stale
        # and dropped.
        for folder in [f for f in self.state.errors if f in installed]:
            self.state.errors.pop(folder, None)
        self._overlay_errors(available)
        # Never show an AVAILABLE row that denotes an installed addon under
        # a variant spelling (same repo or unknown source); the panel
        # partitions sections by exact match, so such rows render twice.
        # A same-norm row from a *different* repo is a different addon and
        # stays visible.
        # Id-agnostic access: rows arrive as dicts (scan) or AddonState
        # objects (mid-scan state) — one lookup covers both.
        installed_gits = {
            name: (rec.get("git") if isinstance(rec, dict) else rec.git)
            for name, rec in installed.items()
        }
        available = _suppress_shadowed(available, installed_gits)
        installed = self._collapse_units(installed)
        self._finish_verify(installed, available)

    _UNIT_STATUS_RANK = {
        "invalid": 0,
        "outOfDate": 1,
        "unknown": 2,
        "upToDate": 3,
    }

    def _collapse_units(self, installed: dict) -> dict:
        """Merge per-disk-folder scan rows into one row per install unit.

        Folders whose rows share one entry id (learned set) collapse to
        the primary folder's row: `folders` lists the set, status is
        worst-of (invalid > outOfDate > unknown > upToDate), and a
        partially-present set reports outOfDate + "missing: X" so
        Repair reinstalls the unit. Rows without a shared id
        (untracked, single-error rows) pass through untouched.
        Keys on entry id — per-folder toc optional, so the
        fast paint reuses this without disk data. Never raises."""
        try:
            groups: dict = {}
            order: list = []
            passthrough: dict = {}
            for name, rec in installed.items():
                eid = None
                try:
                    if isinstance(rec, dict):
                        eid = rec.get("id")
                except Exception:
                    eid = None
                if not eid or not isinstance(rec, dict):
                    passthrough[name] = rec
                    continue
                if eid not in groups:
                    groups[eid] = []
                    order.append(eid)
                groups[eid].append((name, rec))
            if not any(len(g) > 1 for g in groups.values()):
                return installed
            collapsed: dict = dict(passthrough)
            for eid in order:
                members = groups[eid]
                if len(members) == 1:
                    name, rec = members[0]
                    collapsed[name] = rec
                    continue
                # Primary is the row whose folder is the catalog entry's
                # own folder (or matches the entry id for folder-keyed
                # customs); alphabetical order must never promote a
                # helper folder like !Libs to the unit title.
                primary = members[0][0]
                for mname, mrec in members:
                    slot = mrec.get("folder") or mname
                    if slot == eid or mname == eid:
                        primary = mname
                        break
                else:
                    cat = self._available_row_for(eid) or {}
                    try:
                        want = (
                            cat.get("folder")
                            if isinstance(cat, dict)
                            else None
                        )
                    except Exception:
                        want = None
                    if isinstance(want, str) and want:
                        for mname, _mrec in members:
                            if mname == want:
                                primary = mname
                                break
                members.sort(
                    key=lambda m: (
                        0 if (m[1].get("folder") or m[0]) == primary else 1,
                        m[0],
                    )
                )
                rank = 3
                merged_toc: dict = {}
                for mname, mrec in members:
                    st = mrec.get("status") or "upToDate"
                    rank = min(rank, self._UNIT_STATUS_RANK.get(st, 3))
                    try:
                        if isinstance(mrec.get("toc"), dict):
                            merged_toc[mname] = mrec["toc"]
                    except Exception:
                        pass
                status = next(
                    s for s, r in self._UNIT_STATUS_RANK.items() if r == rank
                )
                base = dict(members[0][1])
                base["status"] = status
                base["folders"] = [m[0] for m in members]
                base["toc"] = merged_toc
                # shortcut: missing-subfolder list is best-effort (a
                # hand-deleted folder simply absent from the scan); the
                # outOfDate flag is what triggers Repair.
                if status == "outOfDate":
                    base["description"] = (
                        (base.get("description") or "")
                        + " Missing sub-folders? Reinstall to repair."
                    ).strip()
                collapsed[primary] = base
            return collapsed
        except Exception:
            return installed

    def _available_from_catalog(self, catalog_list: list) -> list:
        """Map catalog entries to AVAILABLE row dicts: curated blocked
        flags plus any flags the catalog carries, with curated git
        overrides applied (and synthesized rows for renamed entries)."""
        blocked = set(addons.BLOCKED_ADDONS)
        blocked_norms = {_norm_folder(n) for n in blocked}
        blocked_norms.discard("")
        available = []
        by_id: dict = {}
        by_folder: dict = {}
        try:
            _idx = addons.load_addons_index()
            _iu = _idx.get("units")
            learned_units = _iu if isinstance(_iu, dict) else {}
        except Exception:
            learned_units = {}
        for a in catalog_list:
            folder = a.get("folder") or a.get("name")
            if not folder:
                continue
            eid = a.get("id") or folder
            if a.get("blocked"):
                blocked.add(folder)
                blocked_norms.add(_norm_folder(folder))
                blocked_norms.discard("")
            if _norm_folder(folder) in blocked_norms:
                # A blocked folder stays hidden even under a variant
                # spelling; drop any earlier rows for it too.
                available = [
                    r
                    for r in available
                    if _norm_folder(r["folder"]) not in blocked_norms
                ]
                by_id = {
                    k: v
                    for k, v in by_id.items()
                    if _norm_folder(v["folder"]) not in blocked_norms
                }
                by_folder = {
                    k: v
                    for k, v in by_folder.items()
                    if _norm_folder(v["folder"]) not in blocked_norms
                }
                continue
            # Identity is the entry id (later layers win, mirroring
            # merge_addons override order). Sharing one folder declares
            # a variant group — both rows are kept, install = replace.
            # The row carries the index-learned folder set when known
            # (multi-folder unit, keyed by entry id), else the single
            # primary slot.
            folders = self._learned_folders_for(eid, learned_units) or [folder]
            rec = {
                "folder": folder,
                "id": eid,
                "name": a.get("name") or None,
                "status": "available",
                "folders": list(folders),
                "git": a.get("git"),
                "source_type": a.get("source_type") or "git",
                "download_url": a.get("download_url"),
                "branch": a.get("branch"),
                "ref": a.get("ref"),
                "toc": a.get("toc") or {},
                "description": a.get("description"),
                "depends": list(a.get("depends") or []),
                "error": None,
            }
            # Identity is the entry id (later layers win, mirroring
            # merge_addons override order). Sharing one folder declares
            # a variant group — both rows are kept, install = replace.
            old = by_id.get(eid)
            if old is not None:
                available = [r for r in available if r is not old]
                for k, v in list(by_folder.items()):
                    if v is old:
                        del by_folder[k]
            available.append(rec)
            by_id[eid] = rec
            by_folder[folder] = rec
        # Curated git overrides for renamed entries.
        for name, override in addons.RECOMMENDED_ADDONS.items():
            rec = by_folder.get(name)
            if rec is None:
                available.append(
                    {
                        "folder": name,
                        "id": name,
                        "name": None,
                        "status": "available",
                        "git": override,
                        "branch": None,
                        "ref": None,
                        "toc": {},
                        "description": None,
                        "depends": [],
                        "error": None,
                    }
                )
            elif rec.get("git") != override:
                rec.update(git=override, branch=None, ref=None)
        return available

    def _learned_folders_for(self, eid: str, units=None) -> list:
        """The folder set an AVAILABLE row installs as a unit: the
        tracked-install index unit for the entry id contributes its
        learned `folders`; unknown ids yield [] (caller falls back to
        the row's primary slot). Never raises."""
        try:
            if units is None:
                index = addons.load_addons_index()
                units = index.get("units")
            unit = units.get(eid) if isinstance(units, dict) else None
            folders = unit.get("folders") if isinstance(unit, dict) else None
            if isinstance(folders, list):
                cleaned = sorted(
                    {f for f in folders if isinstance(f, str) and f}
                )
                if cleaned:
                    return cleaned
        except Exception:
            pass
        return []

    def _overlay_errors(self, available: list):
        """Re-attach session install errors to their AVAILABLE rows,
        synthesizing a row for failed custom addons."""
        by_name = {a["folder"]: a for a in available}
        for folder, info in self.state.errors.items():
            rec = by_name.get(folder)
            if rec is None:
                rec = {
                    "folder": folder,
                    "id": folder,
                    "name": None,
                    "status": "available",
                    "git": info.git,
                    "branch": None,
                    "ref": None,
                    "toc": {},
                    "description": None,
                    "depends": [],
                    "error": None,
                }
                available.append(rec)
            rec["error"] = info.error

    def _resolve_remote_sha(
        self,
        git,
        branch,
        ref,
        *,
        force,
        remote_checks,
        fallback_sha=None,
        memo=None,
    ):
        """The effective remote sha for a tracked source: the live lookup
        when remote checks are on, otherwise the config cache with an
        optional saved-sha fallback (assume current rather than hit the
        network). ``memo`` dedupes repeat lookups of one repo key within
        a single verify (multi-folder repos resolve once)."""
        key = (git, branch, ref, bool(force), bool(remote_checks))
        if memo is not None and key in memo:
            return memo[key]
        if remote_checks:
            remote = addons.addon_remote_sha(git, branch, ref, force=force)
        else:
            remote = addons.addon_cached_sha(git, branch, ref)
            if remote is None:
                remote = fallback_sha
        if memo is not None:
            memo[key] = remote
        return remote

    def _apply_remote_status(self, rec: dict, remote, saved_sha):
        """Classify a resolved remote sha onto a row: unknown (retryable)
        when unresolved, else up-to-date vs out-of-date against the saved
        sha."""
        if remote is None:
            rec.update(status="unknown", error=C_COULD_NOT_CHECK)
        elif remote == saved_sha:
            rec["status"] = "upToDate"
        else:
            rec["status"] = "outOfDate"

    def toggle(self, folder: str, enabled: bool):
        """Record a pending checkbox change for the addon.

        The manage tab is select-to-uninstall: a tick passes
        ``enabled=False`` (staged for removal); unticking clears the
        stage via ``pending.pop`` in the tab glue.
        """
        from ..state.models import AddonPending

        self.state.pending[folder] = AddonPending(enabled=enabled)

    def add_custom_entry(self, entry: dict) -> str | None:
        """Validate a user-built addon entry and persist it into the local
        addons repo's "custom" list so it survives restarts. Returns an
        error message, or None on success."""
        if addons.validate_custom_entry(entry) is None:
            return "This addon entry is not usable."
        return catalog.add_custom_entry("addons", entry)

    def apply_pending(self) -> bool:
        """Uninstall addons staged (ticked) for removal.

        Installs never flow through pending: the Browse dialog installs
        directly via ``apply()``. Only ``pending[folder] is False``
        (enabled-off) entries are acted on; anything else is dropped.
        Returns True when the worker actually started."""
        staged = [
            folder
            for folder, pend in (self.state.pending or {}).items()
            if getattr(pend, "enabled", pend) is False
        ]
        if self.state.busy or not staged:
            return False
        client = (self._get_out_dir() or "").strip()
        if not client:
            self._dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return False
        self.state.busy = True
        self.state.installing = True
        self._dispatcher.post(StatusChanged("Uninstalling addons…"))
        threading.Thread(
            target=self._apply_pending_worker, args=(client,), daemon=True
        ).start()
        return True

    def apply(self, recs) -> bool:
        """Install/update the given addon records sequentially, pulling in
        any ``depends: addon:<id>`` prerequisites (resolved against the
        current AVAILABLE snapshot by id; mod:/file: kinds stay
        advisory). Returns True when the worker actually started."""
        client = (self._get_out_dir() or "").strip()
        if not client or self.state.busy or not recs:
            return False
        recs = self._with_addon_dependencies(list(recs))
        if not recs:
            return False
        self.state.busy = True
        self.state.installing = True
        for rec in recs:
            rec["status"] = "downloading"
            rec_state = self.state.addons.get(rec["folder"])
            if rec_state is None:
                rec_state = AddonState.from_dict(rec)
                self.state.addons[rec["folder"]] = rec_state
            else:
                rec_state.status = "downloading"
                rec_state.error = None
        self._dispatcher.post(StatusChanged("Downloading addons…"))
        threading.Thread(
            target=self._apply_worker, args=(client, list(recs)), daemon=True
        ).start()
        return True

    def _with_addon_dependencies(self, recs: list) -> list:
        """Expand ``recs`` with their transitive ``addon:<id>`` prerequisites.

        Resolved by id against the current AVAILABLE snapshot; folders
        already installed on disk (or already requested) are skipped.
        ``mod:``/``file:`` kinds stay advisory — the launcher neither
        installs mods from here nor checks client-relative files.
        Cycles terminate on the seen-set. Never raises: an unresolvable
        prerequisite logs instead of failing the requested install."""
        try:
            from ..services import catalog as _catalog

            disk: set = set()
            try:
                disk = set(self.state.addons or {})
            except Exception:
                disk = set()
            ap = None
            try:
                ap = addons.addons_path((self._get_out_dir() or "").strip())
            except Exception:
                ap = None
            if ap:
                try:
                    _names, dirs = _scan_disk_dirs(ap)
                    disk |= set(dirs)
                except Exception:
                    pass
            by_id = {}
            for row in self.state.available or []:
                try:
                    if isinstance(row, dict):
                        rid = row.get("id")
                        rfolder = row.get("folder")
                    else:
                        rid = getattr(row, "id", None)
                        rfolder = getattr(row, "folder", None)
                    if rid and rfolder:
                        by_id.setdefault(rid, row)
                except Exception:
                    continue
            seen = set()
            for rec in recs:
                try:
                    seen.add(
                        rec.get("folder")
                        if isinstance(rec, dict)
                        else getattr(rec, "folder", None)
                    )
                except Exception:
                    pass
            out = list(recs)
            queue = list(recs)
            while queue:
                cur = queue.pop(0)
                deps = []
                try:
                    deps = (
                        cur.get("depends")
                        if isinstance(cur, dict)
                        else getattr(cur, "depends", [])
                    ) or []
                except Exception:
                    deps = []
                for dep in deps:
                    parsed = None
                    try:
                        parsed = _catalog.parse_depends_dep(dep)
                    except Exception:
                        continue
                    if parsed is None:
                        continue
                    kind, ref = parsed
                    if kind != "addon" or not ref:
                        continue
                    target = by_id.get(ref)
                    if target is None:
                        try:
                            log(
                                f"  Addon dependency {dep!r} not in catalog — skipping."
                            )
                        except Exception:
                            pass
                        continue
                    try:
                        tfolder = (
                            target.get("folder")
                            if isinstance(target, dict)
                            else getattr(target, "folder", None)
                        )
                    except Exception:
                        tfolder = None
                    if not tfolder or tfolder in seen:
                        continue
                    if tfolder in disk:
                        continue
                    seen.add(tfolder)
                    dep_rec = (
                        dict(target)
                        if isinstance(target, dict)
                        else target.to_dict()
                    )
                    out.append(dep_rec)
                    queue.append(dep_rec)
                    try:
                        # Pull the dep's whole folder set: installing the
                        # id installs the unit (same index-learned set
                        # the row would install from the panel).
                        for f in dep_rec.get("folders") or []:
                            if isinstance(f, str) and f:
                                seen.add(f)
                    except Exception:
                        pass
            return out
        except Exception:
            return list(recs)

    def reset(self):
        """Drop the session verify TTL/content (called when the game folder
        changes)."""
        try:
            addons.prune_toc_cache()
        except Exception:
            pass
        self.state.verified_ts = 0.0
        self.state.state = "idle"
        self.state.addons = {}
        self.state.available = []
        self.state.errors = {}
        self.state.updates_count = 0

    def invalidate(self):
        """Drop the verify TTL so the next verify() rescans and rebuilds the
        list (used after a catalog reload)."""
        self.state.verified_ts = 0.0
        self.state.state = "idle"

    # ── internals ───────────────────────────────────────────────────────────

    # ── tracked-install index (keyed by catalog entry id) ─────────────

    def _index_record_install(
        self,
        sha: str | None,
        folders: list,
        group: list | None = None,
    ) -> None:
        """Record a successful install into the tracked-install index,
        keyed by catalog entry id: ``{id: {folders, sha, toc, ...}}``.
        The catalog gives the id; install time teaches the index which
        folders that id ships. The shipped set REPLACES the want (the
        archive is truth); the previous set survives one scan as
        ``prev_folders`` so a vanished-upstream sub-addon flags instead
        of silently shrinking. A folder owned by another unit (variant
        switch) is dropped from the old unit — folder → single owning
        unit invariant — and the loser's saved records for moved folders
        are removed so no phantom survives. Serialized with the scan
        rebuild via _INDEX_LOCK so an install landing mid-scan
        survives."""
        try:
            with _INDEX_LOCK:
                index = addons.load_addons_index()
                units = index.get("units")
                if not isinstance(units, dict):
                    units = {}
                    index["units"] = units
                eid = None
                try:
                    for src in group or []:
                        if isinstance(src, dict) and src.get("id"):
                            eid = str(src["id"])
                            break
                    if eid is None:
                        for f in folders:
                            r = self._available_row_for(f)
                            if (
                                isinstance(r, dict)
                                and r.get("id")
                                and r.get("folder") == f
                            ):
                                eid = str(r["id"])
                                break
                except Exception:
                    eid = None
                if not eid:
                    return
                client = (self._get_out_dir() or "").strip()
                ap = addons.addons_path(client) if client else ""
                try:
                    import time as _time

                    now = _time.time()
                except Exception:
                    now = 0.0
                prev = units.get(eid)
                prev_folders = (
                    prev.get("folders") if isinstance(prev, dict) else None
                )
                if not isinstance(prev_folders, list):
                    prev_folders = []
                unit: dict = {
                    "folders": sorted(set(folders)),
                    "prev_folders": sorted(
                        {f for f in prev_folders if isinstance(f, str) and f}
                        - set(folders)
                    ),
                    "sha": sha,
                    "toc": addons.snapshot_toc_folders(ap, folders),
                    "claimed_at": {
                        f: now for f in folders if isinstance(f, str)
                    },
                }
                try:
                    unit["installed_at"] = now
                except Exception:
                    pass
                owned: set = set(folders)
                for other_id in list(units):
                    if other_id == eid:
                        continue
                    other = units.get(other_id)
                    if not isinstance(other, dict):
                        continue
                    other_folders = other.get("folders")
                    if not isinstance(other_folders, list):
                        continue
                    kept = [f for f in other_folders if f not in owned]
                    if len(kept) != len(other_folders):
                        moved = [f for f in other_folders if f in owned]
                        try:
                            claimed = other.get("claimed_at")
                            if isinstance(claimed, dict):
                                for f in moved:
                                    claimed.pop(f, None)
                        except Exception:
                            pass
                        try:
                            moved_t = tuple(moved)

                            def _drop_moved(cfg, ms=moved_t):
                                recs = cfg.get("addons")
                                if isinstance(recs, dict):
                                    for m in ms:
                                        recs.pop(m, None)
                                return cfg

                            config_store.update_config(_drop_moved)
                        except Exception:
                            pass
                        if kept:
                            other["folders"] = sorted(set(kept))
                        else:
                            units.pop(other_id, None)
                units[eid] = unit
                orphans = index.get("orphans")
                if isinstance(orphans, list) and owned:
                    index["orphans"] = [o for o in orphans if o not in owned]
                addons.store_addons_index(index)
        except Exception:
            return

    def _index_drop_folder(self, folder: str) -> None:
        """Drop one folder from the tracked-install index (uninstall)."""
        try:
            with _INDEX_LOCK:
                index = addons.load_addons_index()
                units = index.get("units")
                if not isinstance(units, dict):
                    return
                changed = False
                for k in list(units):
                    entry = units.get(k)
                    if not isinstance(entry, dict):
                        continue
                    folders = entry.get("folders")
                    if not isinstance(folders, list) or folder not in folders:
                        continue
                    kept = [f for f in folders if f != folder]
                    if kept:
                        entry["folders"] = kept
                    else:
                        units.pop(k, None)
                    changed = True
                if changed:
                    addons.store_addons_index(index)
        except Exception:
            return

    def _available_row_for(self, folder: str) -> dict | None:
        """The current AVAILABLE row whose PRIMARY folder matches,
        as a dict, else None. Primary-only: a sub-folder hit must
        never resolve here (variant forks fall through elsewhere)."""
        try:
            for row in self.state.available or []:
                try:
                    name = (
                        row.get("folder")
                        if isinstance(row, dict)
                        else getattr(row, "folder", None)
                    )
                except Exception:
                    continue
                if name == folder:
                    if isinstance(row, dict):
                        return row
                    try:
                        return row.to_dict()
                    except Exception:
                        return None
        except Exception:
            return None
        return None

    def _finish_verify(self, installed: dict, available: list):
        self.state.state = "done"
        self.state.addons = {
            folder: AddonState.from_dict(rec)
            for folder, rec in installed.items()
        }
        self.state.available = [AddonState.from_dict(rec) for rec in available]
        self.state.verified_ts = time.time()
        self.state.busy = False
        self.state.updates_count = sum(
            1 for rec in installed.values() if rec["status"] == "outOfDate"
        )
        self._dispatcher.post(AddonsLoaded(self.state))

    def _install_direct(
        self, client: str, url: str, folders: list, group: list | None = None
    ) -> list[str]:
        """Fetch one prebuilt archive and install every requested folder
        from it. Records each installed folder against its URL and flips
        its row up-to-date; returns the installed folder names."""
        installed = addons.install_addon_direct(
            client, folders[0], url, wanted=list(folders)
        )
        record = {"source_type": "direct", "download_url": url}
        for folder in installed:
            config_store.update_config(
                lambda c, f=folder, r=record: c.setdefault(
                    "addons", {}
                ).__setitem__(f, r)
            )
            self.state.errors.pop(folder, None)
            st_rec = self.state.addons.get(folder)
            if st_rec is None:
                st_rec = AddonState.from_dict(
                    {
                        "folder": folder,
                        "status": "upToDate",
                        "source_type": "direct",
                        "download_url": url,
                        "toc": {},
                        "description": None,
                        "error": None,
                    }
                )
                self.state.addons[folder] = st_rec
            else:
                st_rec.status = "upToDate"
                st_rec.error = None
                st_rec.source_type = "direct"
                st_rec.download_url = url
            self._dispatcher.post(LogMessage(f"  ✓ Addon {folder} installed."))
        for folder in folders:
            if folder not in installed:
                log(f"  {folder} is not shipped by {url} — left as is.", "dim")
                st_rec = self.state.addons.get(folder)
                if st_rec is not None and st_rec.status == "downloading":
                    st_rec.status = "outOfDate"
        self.state.updates_count = sum(
            1
            for rec in self.state.addons.values()
            if rec.status == "outOfDate"
        )
        if installed:
            self._index_record_install(None, installed, group)
        return installed

    def _install_group(
        self,
        client: str,
        git: str,
        branch,
        ref,
        folders: list,
        group: list | None = None,
    ) -> list[str]:
        """Fetch one repo archive and install every requested folder from
        it. Records each installed folder against the repo's sha and flips
        its row up-to-date; returns the installed folder names. Raises on
        failure (caller marks the group failed)."""
        sha = addons.addon_remote_sha(
            git, branch, ref, force=True, raise_errors=True
        )
        if not sha:
            raise RuntimeError("Could not resolve remote commit")
        installed = addons.install_addon_files(
            client, folders[0], git, sha, wanted=list(folders)
        )
        record = {"git": git, "branch": branch, "ref": ref, "sha": sha}
        for folder in installed:
            config_store.update_config(
                lambda c, f=folder, r=record: c.setdefault(
                    "addons", {}
                ).__setitem__(f, r)
            )
            self.state.errors.pop(folder, None)
            st_rec = self.state.addons.get(folder)
            if st_rec is None:
                # A newly installed sibling from a multi-addon repo has no
                # row yet — create it up-to-date now instead of waiting for
                # the next verify to reconcile it.
                st_rec = AddonState.from_dict(
                    {
                        "folder": folder,
                        "status": "upToDate",
                        "git": git,
                        "branch": branch,
                        "ref": ref,
                        "toc": {},
                        "description": None,
                        "error": None,
                    }
                )
                self.state.addons[folder] = st_rec
            else:
                # Instant feedback: the row flips to up-to-date now
                # instead of staying on the stale status until the
                # post-install verify reconciles it.
                st_rec.status = "upToDate"
                st_rec.error = None
            self._dispatcher.post(LogMessage(f"  ✓ Addon {folder} installed."))
        for folder in folders:
            if folder not in installed:
                log(
                    f"  {folder} is not shipped by {git} — left as is.",
                    "dim",
                )
                st_rec = self.state.addons.get(folder)
                if st_rec is not None and st_rec.status == "downloading":
                    st_rec.status = "outOfDate"
        self.state.updates_count = sum(
            1
            for rec in self.state.addons.values()
            if rec.status == "outOfDate"
        )
        if installed:
            self._index_record_install(sha, installed, group)
        return installed

    def _fail_group(self, folders: list, git, err: str) -> None:
        for folder in folders:
            self._dispatcher.post(LogMessage(f"  ✗ Addon {folder}: {err}"))
            st_rec = self.state.addons.get(folder)
            if st_rec is not None:
                st_rec.status = "invalid"
                st_rec.error = err
            self.state.errors[folder] = AddonError(err, git)

    def _group_recs(self, recs: list) -> list:
        """Group install records so one archive fetch serves every folder
        from the same source: git repos group by (git, branch, ref),
        direct downloads group by URL. A shared (git, None, None) key
        never mixes the two — direct groups carry the source marker."""
        groups: dict = {}
        order: list = []
        for rec in recs:
            if (rec.get("source_type") or "git") == "direct":
                key = ("direct", rec.get("download_url"), None, None)
            else:
                key = (
                    "git",
                    rec.get("git"),
                    rec.get("branch"),
                    rec.get("ref"),
                )
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(rec)
        return [(key, groups[key]) for key in order]

    def _apply_worker(self, client: str, recs: list):
        failed = []
        for key, group in self._group_recs(recs):
            kind = key[0]
            # The unit is the row's learned folder set when known,
            # else the single primary slot.
            folders = sorted(
                {
                    f
                    for rec in group
                    for f in (
                        rec.get("folders")
                        if isinstance(rec.get("folders"), list)
                        else [rec["folder"]]
                    )
                    if isinstance(f, str) and f
                }
            )
            if not folders:
                continue
            self._dispatcher.post(StatusChanged(f"Installing {folders[0]}…"))
            try:
                if kind == "direct":
                    url = key[1]
                    if not url:
                        raise RuntimeError("Addon has no direct download URL")
                    self._install_direct(client, url, folders, group)
                else:
                    git, branch, ref = key[1], key[2], key[3]
                    if not git or not addons.is_allowed_git_url(git):
                        raise RuntimeError(
                            "Addon URL is not from an allowed git host"
                        )
                    self._install_group(
                        client, git, branch, ref, folders, group
                    )
                self._dispatcher.post(AddonsLoaded(self.state))
            except Exception as e:
                err = describe_install_error(e)
                self._fail_group(folders, key[1], err)
                failed.extend(folders)

        self.state.busy = False
        self.state.installing = False
        self.state.verified_ts = 0.0  # make the re-verify run
        self._dispatcher.post(
            OperationFinished(
                "addons",
                not failed,
                "" if not failed else f"Failed addons: {', '.join(failed)}",
            )
        )
        if failed:
            # Only rescan when something failed — the overlay attaches the
            # install error to the matching AVAILABLE row. On full success
            # the worker has already marked every just-installed addon
            # upToDate and published the snapshot; running verify() here
            # would re-scan the disk and could flip those rows back to
            # outOfDate (e.g. when RECOMMENDED_ADDONS defines a curated
            # fork with a different git URL than the catalog entry the
            # user just installed).
            self.verify(remote_checks=False)

    def _unit_folders_for(self, folder: str) -> list:
        """The disk folders one pending tick removes: the row's learned
        set when known, else the single folder. Pending is keyed by
        primary folder; the tick expands to the unit. Never raises."""
        try:
            st = self.state.addons.get(folder)
            folders = getattr(st, "folders", None)
            if isinstance(folders, list):
                cleaned = sorted(
                    {f for f in folders if isinstance(f, str) and f}
                )
                if cleaned:
                    return cleaned
            for row in self.state.available or []:
                try:
                    name = (
                        row.get("folder")
                        if isinstance(row, dict)
                        else getattr(row, "folder", None)
                    )
                    if name != folder:
                        continue
                    rfolders = (
                        row.get("folders")
                        if isinstance(row, dict)
                        else getattr(row, "folders", None)
                    )
                    if isinstance(rfolders, list):
                        cleaned = sorted(
                            {f for f in rfolders if isinstance(f, str) and f}
                        )
                        if cleaned:
                            return cleaned
                except Exception:
                    continue
        except Exception:
            pass
        return [folder]

    def _apply_pending_worker(self, client: str):
        """Remove addons staged (ticked) for uninstall."""
        pending = self.state.pending
        failed = []

        # Only uninstall ticks are acted on — installs come from the
        # Browse dialog via apply(), never through pending. Pending is
        # keyed by primary folder; the tick expands to the unit's
        # learned set at apply time.
        for folder, pend in list(pending.items()):
            if getattr(pend, "enabled", pend) is not False:
                continue
            targets = self._unit_folders_for(folder)
            for target in targets:
                try:
                    dirp = os.path.join(addons.addons_path(client), target)
                    if os.path.isdir(dirp):
                        shutil.rmtree(dirp)
                    config_store.update_config(
                        lambda c, f=target: c.get("addons", {}).pop(f, None)
                    )
                    self.state.addons.pop(target, None)
                    self.state.errors.pop(target, None)
                    self._index_drop_folder(target)
                    self._dispatcher.post(
                        LogMessage(f"Removed addon {target}\n", "dim")
                    )
                except Exception as e:
                    err = describe_install_error(e)
                    self._dispatcher.post(
                        LogMessage(
                            f"Failed to remove addon {target}: {err}", "err"
                        )
                    )
                    failed.append(target)

        self.state.pending = {}
        self.state.busy = False
        self.state.installing = False
        self.state.verified_ts = 0.0
        self._dispatcher.post(AddonsLoaded(self.state))
        self._dispatcher.post(
            OperationFinished(
                "addons",
                not failed,
                "" if not failed else f"Failed addons: {', '.join(failed)}",
            )
        )
        if failed:
            self.verify(remote_checks=False)

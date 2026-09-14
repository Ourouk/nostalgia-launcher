"""Addons panel controller.

Owns the ADDONS-panel business logic: the catalog fetch with its offline
fallback, the Interface/AddOns scan with .toc parsing, the cached remote-sha
verification (TTL-gated, cache-only on demand), the sequential
install/update/remove worker, and the recommended-addons install triggered by
the panel's "Install Recommended" button. Publishes snapshots as AddonsLoaded
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

# Footer-label colours — mirror qt_theme's ok / text-dim so the
# toolkit-agnostic footer_state() can render without importing Qt.
C_OK = "#6abf69"
C_TEXT_DIM = "#7a7670"

# Status message when the remote could not be reached to compare SHAs — a
# transient state (rate limit, outage), distinct from a broken addon folder.
C_COULD_NOT_CHECK = "Couldn't check for updates"


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


def _suppress_shadowed(available: list, installed_gits: dict) -> list:
    """Drop AVAILABLE rows that denote an installed addon under a
    textually different spelling (case/whitespace/unicode variant).

    The git URL differentiates: a same-norm row pointing at the same repo
    (or with an unknown source on either side) is one addon shown twice —
    drop the available-side shadow. A same-norm row pointing at a
    *different* repo is a different addon (fork/custom) sharing a folder
    spelling — keep it visible. Producers spell names from the catalog
    side while ``installed`` keys come from the disk side. Exact installed
    identity is untouched. Logs what it drops (observability for variant
    spellings in the wild). Never raises."""
    try:
        norms = {}
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
        except Exception:
            kept.append(row)
            continue
        if key and key in norms and not _gits_differ(norms[key], git):
            try:
                log(
                    f"  Addon {folder!r} shadows an installed folder — hiding."
                )
            except Exception:
                pass
            continue
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
        self._recommended = set(addons.RECOMMENDED_ADDONS)
        if get_out_dir is None:

            def _default_get_out_dir() -> str:
                val = config_store.load_config().get("out_dir", "")
                return val if isinstance(val, str) else ""

            get_out_dir = _default_get_out_dir

        self._get_out_dir = get_out_dir

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def recommended(self) -> set:
        """Set of recommended addon folder names (★ badge / sort order).
        Grows as the catalog flags addons as recommended during a verify."""
        return self._recommended

    def is_allowed_git_url(self, url: str) -> bool:
        return addons.is_allowed_git_url(url)

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
            self._dispatcher.post(AddonsLoaded(snapshot))

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

    def _discover_siblings(
        self, catalog_list, disk_names, records, force, addons_dir=""
    ) -> dict:
        """Map sibling folder name → catalog entry for multi-addon repos.

        One Git repo often ships several addons (AtlasLoot's seven
        modules): only the catalog's primary folder would otherwise match
        and the rest would stay "Not tracked" forever. Discovery fetches a
        repo archive only when the repo is relevant — its catalog folder is
        on disk, a saved record points at the same repo, or a snapjaw
        clone of it sits in `.snapjaw_cache` — and only when its sha moved
        (cached otherwise), so unrelated catalog entries cost nothing.
        Entries sharing one repo (git#branch/ref) resolve once.
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
        # Group by repo key so multi-folder repos resolve once; force is
        # any-true across the group.
        groups: dict = {}
        order: list = []
        for entry in catalog_list:
            git = entry.get("git")
            if not git:
                continue
            relevant = (
                _norm_folder(entry.get("name")) in disk_norms
                or any(same_git_repo(saved, git) for saved in saved_gits)
                or any(same_git_repo(snap, git) for snap in snapjaw_urls)
            )
            if not relevant:
                continue
            key = (git, entry.get("branch"), entry.get("ref"))
            if key not in groups:
                groups[key] = entry
                order.append(key)
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
        available_by_folder = {a["folder"]: a for a in available}
        # Norm-keyed index (last wins, mirroring merge_addons override
        # order): exact match always wins at lookup time, this is only the
        # fallback for variant spellings (case/whitespace/unicode).
        available_by_norm: dict = {}
        for row in available:
            key = _norm_folder(row.get("folder"))
            if key:
                available_by_norm[key] = row

        installed = {}
        records = config_store.load_config().get("addons", {})
        if not isinstance(records, dict):
            records = {}
        ap = addons.addons_path(client) if client else ""
        disk_names: set[str] = set()
        dir_names: list[str] = []
        if ap:
            try:
                with os.scandir(ap) as it:
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
                disk_names = set(seen)
                dir_names = sorted(seen)
            except OSError:
                disk_names = set()
                dir_names = []
        # Serial per-verify memo: folders sharing one repo (git#branch/ref)
        # resolve the remote sha once instead of once per folder.
        sha_memo: dict = {}
        siblings: dict = {}
        siblings_by_norm: dict = {}
        if remote_checks and disk_names:
            siblings = self._discover_siblings(
                catalog_list, disk_names, records, force, ap
            )
            siblings_by_norm = {
                _norm_folder(s): s for s in siblings if _norm_folder(s)
            }
            # Discovered-but-not-installed siblings are installable too.
            # Norm-based: a sibling variant-spelling an installed folder is
            # adopted in the scan loop, never listed as available.
            known = {_norm_folder(a["folder"]) for a in available}
            known |= {_norm_folder(n) for n in disk_names}
            known.discard("")
            siblings_by_norm = {
                _norm_folder(s): s for s in siblings if _norm_folder(s)
            }
            for sibling, entry in sorted(siblings.items()):
                if _norm_folder(sibling) in known:
                    continue
                row = {
                    "folder": sibling,
                    "status": "available",
                    "git": entry.get("git"),
                    "branch": entry.get("branch"),
                    "ref": entry.get("ref"),
                    "toc": entry.get("toc") or {},
                    "description": entry.get("description"),
                    "error": None,
                }
                available.append(row)
                available_by_folder.setdefault(sibling, row)
                key = _norm_folder(sibling)
                if key:
                    available_by_norm.setdefault(key, row)
                known.add(_norm_folder(sibling))
        if dir_names:
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
                toc_path = os.path.join(ap, name, f"{name}.toc")
                if not os.path.exists(toc_path):
                    rec.update(status="invalid", error="Missing .toc file")
                    installed[name] = rec
                    continue
                rec["toc"] = addons.read_toc_file_cached(toc_path)
                saved = records.get(name)
                saved_git = (
                    saved.get("git") if isinstance(saved, dict) else None
                )
                avail = available_by_folder.get(name)
                if avail is None:
                    # Variant-spelling fallback: only associate when the
                    # sources agree — a norm match across *different* repos
                    # is a different addon sharing a folder spelling, not
                    # this install (exact matches keep the legacy
                    # catalog-authoritative migration below).
                    cand = available_by_norm.get(_norm_folder(name))
                    if cand is not None and not _gits_differ(
                        saved_git, cand.get("git")
                    ):
                        avail = cand
                if avail is None:
                    sib_entry = siblings.get(name)
                    if sib_entry is None:
                        sib_hit = siblings_by_norm.get(_norm_folder(name))
                        sib_entry = siblings[sib_hit] if sib_hit else None
                    if sib_entry is not None and _gits_differ(
                        saved_git, sib_entry.get("git")
                    ):
                        sib_entry = None
                    if sib_entry is not None:
                        # Multi-addon repo sibling (e.g. AtlasLoot's
                        # modules): adopt it under the repo's catalog entry
                        # like a primary folder instead of leaving it
                        # "Not tracked".
                        entry = sib_entry
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
                if avail:
                    rec["description"] = avail["description"]
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
                        branch=None,
                        ref=None,
                        status="outOfDate",
                    )
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
                            branch=avail["branch"],
                            ref=avail["ref"],
                            status="outOfDate",
                        )
                    else:
                        rec.update(
                            git=avail["git"],
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
                        self._apply_remote_status(
                            rec, remote, saved.get("sha")
                        )
                elif saved and saved.get("git"):
                    # Installed addon not in the catalog (a custom entry) —
                    # keep tracking the saved source.
                    rec.update(
                        git=saved.get("git"),
                        branch=saved.get("branch"),
                        ref=saved.get("ref"),
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
                elif avail:
                    # Installed addon the launcher has never recorded (a
                    # folder set up elsewhere, or addons pre-dating the
                    # launcher). Adopt it silently: record its catalog
                    # source and resolve its current remote sha as the
                    # baseline, so it's tracked like any launcher-installed
                    # addon from now on — no re-download and no "update"
                    # flag on every pre-existing addon. Offline/rate-limited
                    # resolution leaves it retryable, never "out of date".
                    remote = self._resolve_remote_sha(
                        avail["git"],
                        avail["branch"],
                        avail["ref"],
                        force=force,
                        remote_checks=remote_checks,
                        memo=sha_memo,
                    )
                    rec.update(
                        git=avail["git"],
                        branch=avail["branch"],
                        ref=avail["ref"],
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
                installed[name] = rec

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
        available = _suppress_shadowed(
            available,
            {
                name: (rec.get("git") if isinstance(rec, dict) else None)
                for name, rec in installed.items()
            },
        )

        self._finish_verify(installed, available)

    def _available_from_catalog(self, catalog_list: list) -> list:
        """Map catalog entries to AVAILABLE row dicts: curated blocked/
        recommended flags plus any flags the catalog carries, with curated
        git overrides applied (and synthesized rows for renamed entries)."""
        blocked = set(addons.BLOCKED_ADDONS)
        recommended = set(addons.RECOMMENDED_ADDONS)
        blocked_norms = {_norm_folder(n) for n in blocked}
        blocked_norms.discard("")
        available = []
        by_name = {}
        by_norm: dict = {}
        for a in catalog_list:
            name = a.get("name")
            if not name:
                continue
            if a.get("blocked"):
                blocked.add(name)
                blocked_norms.add(_norm_folder(name))
                blocked_norms.discard("")
            if a.get("recommended"):
                recommended.add(name)
            if _norm_folder(name) in blocked_norms:
                # A blocked folder stays hidden even under a variant
                # spelling; drop any earlier variant row too.
                key = _norm_folder(name)
                if key and key in by_norm:
                    old = by_norm.pop(key)
                    available = [r for r in available if r is not old]
                    by_name.pop(old["folder"], None)
                continue
            rec = {
                "folder": name,
                "status": "available",
                "git": a.get("git"),
                "branch": a.get("branch"),
                "ref": a.get("ref"),
                "toc": a.get("toc") or {},
                "description": a.get("description"),
                "error": None,
            }
            # Same repo (or unknown source): last wins by norm (mirrors
            # merge_addons override order). Different repos sharing one
            # spelling are different addons — keep both rows.
            key = _norm_folder(name)
            if key and key in by_norm:
                old = by_norm[key]
                if _gits_differ(old.get("git"), rec.get("git")):
                    try:
                        log(
                            f"  Catalog has {name!r} from two repos — "
                            "keeping both."
                        )
                    except Exception:
                        pass
                    available.append(rec)
                    by_name[name] = rec
                    continue
                available = [r for r in available if r is not old]
                by_name.pop(old["folder"], None)
            available.append(rec)
            by_name[name] = rec
            if key:
                by_norm[key] = rec
        for name, override in addons.RECOMMENDED_ADDONS.items():
            rec = by_name.get(name)
            if rec is None:
                rec = by_norm.get(_norm_folder(name))
            if rec is None:
                available.append(
                    {
                        "folder": name,
                        "status": "available",
                        "git": override,
                        "branch": None,
                        "ref": None,
                        "toc": {},
                        "description": None,
                        "error": None,
                    }
                )
            elif not same_git_repo(rec.get("git"), override):
                rec.update(git=override, branch=None, ref=None)
        self._recommended = recommended
        return available

    def _overlay_errors(self, available: list):
        """Re-attach session install errors to their AVAILABLE rows,
        synthesizing a row for failed custom addons."""
        by_name = {a["folder"]: a for a in available}
        for folder, info in self.state.errors.items():
            rec = by_name.get(folder)
            if rec is None:
                rec = {
                    "folder": folder,
                    "status": "available",
                    "git": info.git,
                    "branch": None,
                    "ref": None,
                    "toc": {},
                    "description": None,
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
        """Record a pending checkbox change for the addon."""
        self.state.pending[folder] = enabled

    def add_custom_entry(self, entry: dict) -> str | None:
        """Validate a user-built addon entry and persist it into the local
        addons repo's "custom" list so it survives restarts. Returns an
        error message, or None on success."""
        if addons.validate_custom_entry(entry) is None:
            return "This addon entry is not usable."
        return catalog.add_custom_entry("addons", entry)

    def apply_pending(self) -> bool:
        """Install/update checked addons and remove unchecked ones.

        Returns True when the worker actually started."""
        if self.state.busy or not self.state.pending:
            return False
        client = (self._get_out_dir() or "").strip()
        if not client:
            self._dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return False
        self.state.busy = True
        self.state.installing = True
        self._dispatcher.post(StatusChanged("Applying addon changes…"))
        threading.Thread(
            target=self._apply_pending_worker, args=(client,), daemon=True
        ).start()
        return True

    def apply(self, recs) -> bool:
        """Install/update the given addon records sequentially. Returns True
        when the worker actually started."""
        client = (self._get_out_dir() or "").strip()
        if not client or self.state.busy or not recs:
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

    def update_all(self) -> list:
        """The out-of-date installed addons as record dicts, for apply()."""
        return [
            rec.to_dict()
            for rec in self.state.addons.values()
            if rec.status == "outOfDate"
        ]

    def apply_recommended_addons(self) -> bool:
        """Install every recommended addon (from catalog + constant) not yet
        present. Returns True when an install actually started."""
        if self.state.busy:
            return False
        client = (self._get_out_dir() or "").strip()
        from ..core.filesystem import game_executable_exists

        if not client or not game_executable_exists(client):
            return False
        # If the recommended set only has the constant (empty by default) and
        # the available list is empty, force a catalog fetch so the recommended
        # set gets populated from the server's catalog.
        if not self.state.available and self._recommended == set(
            addons.RECOMMENDED_ADDONS
        ):
            self._ensure_catalog_loaded()
        ap = addons.addons_path(client)
        recs = []
        for name in sorted(self._recommended):
            if os.path.isdir(os.path.join(ap, name)):
                continue
            rec = next(
                (r for r in self.state.available if r.folder == name), None
            )
            if rec is not None:
                recs.append(rec.to_dict())
            elif name in addons.RECOMMENDED_ADDONS:
                recs.append(
                    {
                        "folder": name,
                        "status": "available",
                        "git": addons.RECOMMENDED_ADDONS[name],
                        "branch": None,
                        "ref": None,
                        "toc": {},
                        "description": None,
                        "error": None,
                    }
                )
        if not recs:
            return False
        self._dispatcher.post(
            LogMessage("\nInstalling recommended addons...\n", "acct")
        )
        # Propagate the worker's verdict: a refused start must not report
        # success (the panel keys its busy chrome off this boolean).
        return self.apply(recs)

    def reset(self):
        """Drop the session verify TTL/content (called when the game folder
        changes). The section open/closed state is intentionally preserved."""
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
        self._recommended = set(addons.RECOMMENDED_ADDONS)

    def invalidate(self):
        """Drop the verify TTL so the next verify() rescans and rebuilds the
        list (used after a catalog reload)."""
        self.state.verified_ts = 0.0
        self.state.state = "idle"

    def footer_state(self) -> tuple[str, str, str]:
        """The ADDONS footer label as (text, fg, cursor)."""
        if self.state.state == "verifying" or self.state.busy:
            return "Checking…", C_TEXT_DIM, "arrow"
        if any(
            rec.status == "outOfDate" for rec in self.state.addons.values()
        ):
            return "Update all", C_OK, "hand2"
        return "Everything up to date", C_TEXT_DIM, "arrow"

    def _ensure_catalog_loaded(self):
        """Force a catalog fetch and populate _recommended and state.available.
        Used by apply_recommended_addons on first run when the catalog hasn't
        been fetched yet. Never raises — this runs on the GUI thread, and an
        escaping exception would abort the caller's Qt slot mid-install."""
        try:
            catalog = addons.addons_catalog(force=True)
        except Exception:
            try:
                catalog = addons.catalog_from_cache()
            except Exception:
                catalog = []
        available = self._available_from_catalog(catalog)
        available = _suppress_shadowed(
            available,
            {
                f: (s.git if isinstance(s, AddonState) else None)
                for f, s in self.state.addons.items()
            },
        )
        self.state.available = [AddonState.from_dict(rec) for rec in available]

    # ── internals ───────────────────────────────────────────────────────────

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

    def _install_group(
        self, client: str, git: str, branch, ref, folders: list
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
        if "pfUI" in installed:
            addons.patch_pfui_default_profile(client)
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
        """Group install records by repo so one archive fetch serves every
        folder from the same repo (multi-addon repos install once)."""
        groups: dict = {}
        order: list = []
        for rec in recs:
            key = (rec.get("git"), rec.get("branch"), rec.get("ref"))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(rec)
        return [(key, groups[key]) for key in order]

    def _apply_worker(self, client: str, recs: list):
        failed = []
        for (git, branch, ref), group in self._group_recs(recs):
            folders = [rec["folder"] for rec in group]
            self._dispatcher.post(StatusChanged(f"Installing {folders[0]}…"))
            try:
                if not git or not addons.is_allowed_git_url(git):
                    raise RuntimeError(
                        "Addon URL is not from an allowed git host"
                    )
                self._install_group(client, git, branch, ref, folders)
                self._dispatcher.post(AddonsLoaded(self.state))
            except Exception as e:
                err = describe_install_error(e)
                self._fail_group(folders, git, err)
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

    def _apply_pending_worker(self, client: str):
        """Install checked addons and remove unchecked ones from pending."""
        pending = self.state.pending
        failed = []

        # Process removals first (unchecked addons).
        for folder, enabled in list(pending.items()):
            if enabled:
                continue
            try:
                dirp = os.path.join(addons.addons_path(client), folder)
                if os.path.isdir(dirp):
                    shutil.rmtree(dirp)
                config_store.update_config(
                    lambda c, f=folder: c.get("addons", {}).pop(f, None)
                )
                self.state.addons.pop(folder, None)
                self.state.errors.pop(folder, None)
                self._dispatcher.post(
                    LogMessage(f"Removed addon {folder}\n", "dim")
                )
            except Exception as e:
                err = describe_install_error(e)
                self._dispatcher.post(
                    LogMessage(
                        f"Failed to remove addon {folder}: {err}", "err"
                    )
                )
                failed.append(folder)

        # Process installs/updates (checked addons), grouped by repo so
        # one archive fetch serves every folder from the same repo.
        wanted = []
        for folder, enabled in list(pending.items()):
            if not enabled:
                continue
            rec = next(
                (a for a in self.state.available if a.folder == folder),
                None,
            )
            if rec is None:
                continue
            wanted.append(rec)
        for (git, branch, ref), group in self._group_recs(
            [
                {
                    "folder": r.folder,
                    "git": r.git,
                    "branch": r.branch,
                    "ref": r.ref,
                }
                for r in wanted
            ]
        ):
            folders = [rec["folder"] for rec in group]
            try:
                if not git or not addons.is_allowed_git_url(git):
                    raise RuntimeError(
                        "Addon URL is not from an allowed git host"
                    )
                self._install_group(client, git, branch, ref, folders)
                self._dispatcher.post(AddonsLoaded(self.state))
            except Exception as e:
                err = describe_install_error(e)
                self._fail_group(folders, git, err)
                failed.extend(folders)

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

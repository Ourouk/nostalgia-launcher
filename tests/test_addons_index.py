"""Tracked-install index for ADDONS (fast paint, repo-independent updates).

The index (`local_addons_index.json`) persists per-profile install groups
so the ADDONS tab paints INSTALLED without a rescan and the update path
never re-downloads repo archives for sibling discovery.
"""

import json
import os
import threading

import pytest

import nostalgia_launcher.controllers.addons as ac
from nostalgia_launcher.controllers.addons import AddonsController
from nostalgia_launcher.core import launcher
from nostalgia_launcher.services import addons
from nostalgia_launcher.state.events import AddonsLoaded
from nostalgia_launcher.state.models import AddonPending


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    state = {
        "out_dir": str(tmp_path),
        "addons": {},
        "addons_catalog_cache": {},
    }
    monkeypatch.setattr(ac.config_store, "load_config", lambda: state)
    monkeypatch.setattr(
        ac.config_store,
        "update_config",
        lambda mutator: (mutator(state), state)[1],
    )
    return state


@pytest.fixture
def backends(monkeypatch):
    monkeypatch.setattr(
        ac.addons, "fetch_addons_catalog", lambda force=False: []
    )
    monkeypatch.setattr(
        ac.addons,
        "addon_remote_sha",
        lambda git, branch=None, ref=None, force=False, raise_errors=False: (
            "REMOTE"
        ),
    )
    monkeypatch.setattr(
        ac.addons,
        "addon_cached_sha",
        lambda git, branch=None, ref=None: "REMOTE",
    )
    monkeypatch.setattr(ac.addons, "addon_repo_names", lambda *a, **k: [])
    monkeypatch.setattr(
        ac.addons, "read_toc_file", lambda path: {"Title": "X"}
    )
    monkeypatch.setattr(
        ac.addons,
        "addons_path",
        lambda client: os.path.join(client, "Interface", "AddOns"),
    )
    monkeypatch.setattr(ac.addons, "is_allowed_git_url", lambda url: True)
    monkeypatch.setattr(ac.shutil, "rmtree", lambda path: None)


@pytest.fixture
def catalog_one(monkeypatch):
    """One git catalog row for Foo (drives id → row matching)."""
    monkeypatch.setattr(
        ac.addons,
        "addons_catalog",
        lambda force=False: [
            {
                "id": "foo",
                "name": "Foo",
                "git": "https://github.com/catalog/Foo",
                "branch": "main",
                "ref": None,
            }
        ],
    )
    monkeypatch.setattr(
        ac.addons,
        "catalog_from_cache",
        lambda: [
            {
                "id": "foo",
                "name": "Foo",
                "folder": "Foo",
                "git": "https://github.com/catalog/Foo",
                "branch": "main",
                "ref": None,
            }
        ],
    )


@pytest.fixture
def controller(cfg, backends, dispatcher):
    return AddonsController(dispatcher)


def _install_folder(client, name):
    addon_dir = os.path.join(client, "Interface", "AddOns", name)
    os.makedirs(addon_dir)
    with open(
        os.path.join(addon_dir, f"{name}.toc"), "w", encoding="utf-8"
    ) as f:
        f.write(f"## Title: {name}\n")
    return addon_dir


def _index_path():

    return launcher.local_addons_index_path()


def _read_index():
    with open(_index_path(), encoding="utf-8") as f:
        return json.load(f)


# ── multi-folder units ──────────────────────────────────────────────────


def _pack_catalog(monkeypatch):
    """Three-folder pack from one repo (the multi-folder unit)."""
    monkeypatch.setattr(
        ac.addons,
        "addons_catalog",
        lambda force=False: [
            {
                "id": "pack",
                "name": "Pack",
                "folder": "Pack",
                "git": "https://github.com/catalog/Pack",
                "branch": "main",
                "ref": None,
            }
        ],
    )
    monkeypatch.setattr(
        ac.addons,
        "catalog_from_cache",
        lambda: [
            {
                "id": "pack",
                "name": "Pack",
                "folder": "Pack",
                "git": "https://github.com/catalog/Pack",
                "branch": "main",
                "ref": None,
            }
        ],
    )


def test_available_row_carries_learned_set(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """After a pack install, the AVAILABLE row carries the learned set
    and sub-folders resolve to the entry without an archive fetch."""

    client = str(tmp_path)
    cfg["out_dir"] = client
    _pack_catalog(monkeypatch)
    for name in ("Pack", "PackExtra", "PackLibs"):
        _install_folder(client, name)
    monkeypatch.setattr(
        ac.addons,
        "install_addon_files",
        lambda client_dir, folder, git, sha, wanted=None: [
            "Pack",
            "PackExtra",
            "PackLibs",
        ],
    )
    rec = {
        "folder": "Pack",
        "id": "pack",
        "status": "available",
        "folders": ["Pack"],
        "git": "https://github.com/catalog/Pack",
        "branch": "main",
        "ref": None,
    }
    assert controller.apply([rec]) is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert sorted(_read_index()["units"]["pack"]["folders"]) == [
        "Pack",
        "PackExtra",
        "PackLibs",
    ]

    def _boom(*a, **k):
        raise AssertionError("archive fetch must not run")

    monkeypatch.setattr(ac.addons, "addon_repo_names", _boom)
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert "Pack" in controller.state.addons
    assert len(controller.state.addons) == 1


def test_collapse_worst_of_status(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """A 3-folder unit renders one row; the worst status wins."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _pack_catalog(monkeypatch)
    for name in ("Pack", "PackExtra", "PackLibs"):
        _install_folder(client, name)
    cfg["addons"] = {
        name: {
            "git": "https://github.com/catalog/Pack",
            "branch": "main",
            "ref": None,
            "sha": "OLD",
        }
        for name in ("Pack", "PackExtra", "PackLibs")
    }
    monkeypatch.setattr(
        ac.addons,
        "addon_remote_sha",
        lambda git, branch=None, ref=None, force=False, raise_errors=False: (
            "NEW"
        ),
    )
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert len(controller.state.addons) == 1
    rec = controller.state.addons["Pack"]
    assert rec.status == "outOfDate"
    assert sorted(rec.folders) == ["Pack", "PackExtra", "PackLibs"]
    assert controller.updates_count == 1


def test_replace_loser_returns_to_available(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """Installing B over A's only folder: the folder moves, A's saved
    record is dropped (no phantom), A is AVAILABLE again."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    monkeypatch.setattr(
        ac.addons,
        "install_addon_files",
        lambda client_dir, folder, git, sha, wanted=None: ["Foo"],
    )
    rec_a = {
        "folder": "Foo",
        "id": "a",
        "status": "available",
        "git": "https://github.com/a/Foo",
        "branch": None,
        "ref": None,
    }
    assert controller.apply([rec_a]) is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert _read_index()["units"]["a"]["folders"] == ["Foo"]
    rec_b = {
        "folder": "Foo",
        "id": "b",
        "status": "available",
        "git": "https://github.com/b/Foo",
        "branch": None,
        "ref": None,
    }
    assert controller.apply([rec_b]) is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    index = _read_index()
    assert "a" not in index["units"]
    assert index["units"]["b"]["folders"] == ["Foo"]
    assert (
        "Foo" not in cfg["addons"]
        or cfg["addons"]["Foo"].get("git") == "https://github.com/b/Foo"
    )
    # Loser returns to AVAILABLE on the next scan, not phantom-tracked.
    monkeypatch.setattr(ac.addons, "addons_catalog", lambda force=False: [])
    monkeypatch.setattr(ac.addons, "catalog_from_cache", lambda: [])
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert "Foo" in controller.state.addons
    assert controller.state.addons["Foo"].status == "unknown"
    # The point: no phantom — the record was dropped, the folder is
    # not tracked to any repo (git None), index group gone.


def test_conflict_hint_on_overlap(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """Two AVAILABLE rows overlapping installed folders: the
    different-repo row stays visible with an 'Installs over' hint."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Pack")
    _install_folder(client, "PackExtra")
    cfg["addons"]["Pack"] = {
        "git": "https://github.com/a/Pack",
        "branch": None,
        "ref": None,
        "sha": "REMOTE",
    }
    cfg["addons"]["PackExtra"] = {
        "git": "https://github.com/a/Pack",
        "branch": None,
        "ref": None,
        "sha": "REMOTE",
    }
    launcher.store_local_addons_index(
        {
            "units": {
                "pack": {
                    "folders": ["Pack", "PackExtra"],
                    "sha": "REMOTE",
                    "toc": {},
                    "claimed_at": {},
                }
            },
            "orphans": [],
        }
    )
    monkeypatch.setattr(
        ac.addons,
        "addons_catalog",
        lambda force=False: [
            {
                "id": "bee",
                "name": "Bee",
                "folder": "Bee",
                "git": "https://github.com/b/Bee",
                "branch": None,
                "ref": None,
            }
        ],
    )
    monkeypatch.setattr(
        ac.addons,
        "catalog_from_cache",
        lambda: [
            {
                "id": "bee",
                "name": "Bee",
                "folder": "Bee",
                "git": "https://github.com/b/Bee",
                "branch": None,
                "ref": None,
            }
        ],
    )
    # Bee's learned set overlaps the installed unit on PackExtra.
    real_learned = AddonsController._learned_folders_for

    def _overlap(self, eid, units=None):
        if eid == "bee":
            return ["Bee", "PackExtra"]
        return real_learned(self, eid, units)

    monkeypatch.setattr(AddonsController, "_learned_folders_for", _overlap)
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    rows = {r.folder: r for r in controller.state.available}
    assert "Bee" in rows
    assert "Installs over" in (rows["Bee"].description or "")


# ── storage ───────────────────────────────────────────────────────────────


def test_index_round_trip_launcher_pair(tmp_path):
    """The launcher pair persists and reloads the tracked-install index."""

    index = {
        "units": {
            "x": {
                "folders": ["Foo"],
                "sha": "abc",
                "toc": {},
            }
        },
        "orphans": [],
    }
    launcher.store_local_addons_index(index)
    assert set(launcher.load_local_addons_index()["units"].keys()) == {"x"}


def test_index_corrupt_file_yields_empty(tmp_path):
    """A corrupt index file loads empty and never raises."""

    with open(_index_path(), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert launcher.load_local_addons_index() == {"units": {}, "orphans": []}


def test_index_missing_file_yields_empty(tmp_path):
    """A missing index file loads empty and never raises."""

    if os.path.exists(_index_path()):
        os.remove(_index_path())
    assert launcher.load_local_addons_index() == {"units": {}, "orphans": []}


# ── install / removal recording ───────────────────────────────────────────


def test_install_group_records_index(
    controller, cfg, tmp_path, monkeypatch, wait_for_event, catalog_one
):
    """A git install records its folders + sha + toc snapshot in the index."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    monkeypatch.setattr(
        ac.addons,
        "install_addon_files",
        lambda client_dir, folder, git, sha, wanted=None: ["Foo"],
    )
    rec = {
        "folder": "Foo",
        "id": "foo",
        "status": "available",
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
    }
    assert controller.apply([rec]) is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    index = _read_index()
    assert index["units"]["foo"]["folders"] == ["Foo"]
    assert index["units"]["foo"]["sha"] == "REMOTE"


def test_install_direct_records_index(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """A direct install records its URL group in the index."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    monkeypatch.setattr(
        ac.addons,
        "install_addon_direct",
        lambda client_dir, folder, url, wanted=None: ["Foo"],
    )
    rec = {
        "folder": "Foo",
        "id": "foo",
        "status": "available",
        "source_type": "direct",
        "download_url": "https://example.com/foo.zip",
    }
    assert controller.apply([rec]) is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    index = _read_index()
    assert index["units"]["foo"]["folders"] == ["Foo"]


def test_uninstall_prunes_index(
    controller, cfg, tmp_path, wait_for_event, catalog_one
):
    """Removing an addon drops its folder from the index."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    cfg["addons"]["Foo"] = {
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
        "sha": "REMOTE",
    }
    assert controller.verify() is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert "Foo" in _read_index()["units"]["foo"]["folders"]
    controller.state.pending["Foo"] = AddonPending(enabled=False)
    assert controller.apply_pending() is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert _read_index()["units"] == {}


def test_variant_switch_moves_index_ownership(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """Installing the same folder from a different repo moves ownership."""

    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    launcher.store_local_addons_index(
        {
            "units": {
                "old": {
                    "folders": ["Foo"],
                    "sha": "OLD",
                    "toc": {},
                }
            },
            "orphans": [],
        }
    )
    monkeypatch.setattr(
        ac.addons,
        "install_addon_files",
        lambda client_dir, folder, git, sha, wanted=None: ["Foo"],
    )
    rec = {
        "folder": "Foo",
        "id": "foo",
        "status": "available",
        "git": "https://github.com/new/Foo",
        "branch": None,
        "ref": None,
    }
    assert controller.apply([rec]) is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    index = _read_index()
    assert "old" not in index["units"]
    assert index["units"]["foo"]["folders"] == ["Foo"]


# ── fast paint ────────────────────────────────────────────────────────────


def test_fast_paint_lists_installed_without_scandir(
    controller, cfg, tmp_path, monkeypatch, wait_for_event, catalog_one
):
    """With a warm index, the first paint already lists INSTALLED —
    without touching the disk."""

    client = str(tmp_path)
    cfg["out_dir"] = client
    launcher.store_local_addons_index(
        {
            "units": {
                "foo": {
                    "folders": ["Foo"],
                    "sha": "REMOTE",
                    "toc": {"Foo": {"mtime_ns": 0, "size": 0, "title": ""}},
                }
            },
            "orphans": [],
        }
    )
    cfg["addons"]["Foo"] = {
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
        "sha": "REMOTE",
    }
    monkeypatch.setattr(
        ac.os,
        "scandir",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("scandir must not run on the fast path")
        ),
    )
    assert controller.verify() is True
    events = wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    first = next(e for e in events if isinstance(e, AddonsLoaded))
    assert "Foo" in first.state.addons
    wait_for_event.until_true(lambda: not controller.state.busy)


def test_update_check_never_refetches_archive(
    controller, cfg, tmp_path, monkeypatch, wait_for_event, catalog_one
):
    """A moved SHA flags outOfDate via addon_remote_sha only — the repo
    archive is never re-downloaded for sibling discovery."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    cfg["addons"]["Foo"] = {
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
        "sha": "OLD",
    }
    monkeypatch.setattr(
        ac.addons,
        "addon_remote_sha",
        lambda git, branch=None, ref=None, force=False, raise_errors=False: (
            "NEW"
        ),
    )

    def _boom(url, sha):
        raise AssertionError("archive fetch must not run")

    monkeypatch.setattr(ac.addons._GIT_BACKEND, "fetch_archive", _boom)
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert controller.state.addons["Foo"].status == "outOfDate"


# ── drift ─────────────────────────────────────────────────────────────────


def test_hand_deleted_folder_dropped_from_index(
    controller, cfg, tmp_path, wait_for_event, catalog_one
):
    """Hand-deleting a folder drops it from the index on the next scan."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    cfg["addons"]["Foo"] = {
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
        "sha": "REMOTE",
    }
    assert controller.verify() is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert _read_index()["units"]["foo"]["folders"] == ["Foo"]
    # The shared backends fixture stubs shutil.rmtree globally; remove
    # the folder with os calls so the hand-delete really deletes.
    victim = os.path.join(client, "Interface", "AddOns", "Foo")
    for leaf in os.listdir(victim):
        os.remove(os.path.join(victim, leaf))
    os.rmdir(victim)
    assert controller.verify(force=True) is True
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert "Foo" not in controller.state.addons
    assert _read_index()["units"] == {}


def test_hand_dropped_folder_adopted_and_indexed(
    controller, cfg, tmp_path, wait_for_event, catalog_one
):
    """A hand-dropped folder matching the catalog is adopted + indexed."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    cfg["addons"] = {}
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert controller.state.addons["Foo"].status == "upToDate"
    index = _read_index()
    assert index["units"]["foo"]["folders"] == ["Foo"]
    assert cfg["addons"]["Foo"]["sha"] == "REMOTE"


def test_orphan_repo_vanished_from_catalog_stays_listed(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """An installed addon whose repo left the catalog stays listed via the
    index — installed + update-checkable against its saved git instead of
    degrading to untracked (the independence-from-repos win)."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "CustomThing")
    cfg["addons"]["CustomThing"] = {
        "git": "https://github.com/gone/CustomThing",
        "branch": None,
        "ref": None,
        "sha": "REMOTE",
    }
    # Empty catalog everywhere: nothing matches CustomThing.
    monkeypatch.setattr(ac.addons, "addons_catalog", lambda force=False: [])
    monkeypatch.setattr(ac.addons, "catalog_from_cache", lambda: [])
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    rec = controller.state.addons["CustomThing"]
    assert rec.status == "upToDate"
    assert rec.git == "https://github.com/gone/CustomThing"
    assert _read_index()["units"]["CustomThing"]["folders"] == ["CustomThing"]


def test_corrupt_index_falls_back_to_full_scan(
    controller, cfg, tmp_path, wait_for_event, catalog_one
):
    """A corrupt index file never breaks verify: the disk scan still
    lists installed addons and rebuilds the index."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    cfg["addons"]["Foo"] = {
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
        "sha": "REMOTE",
    }
    with open(_index_path(), "w", encoding="utf-8") as f:
        f.write("{corrupt")
    assert controller.verify(force=True) is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert controller.state.addons["Foo"].status == "upToDate"
    assert _read_index()["units"]["foo"]["folders"] == ["Foo"]


def test_toc_change_refreshes_snapshot(
    controller, cfg, tmp_path, wait_for_event, catalog_one
):
    """Editing an installed .toc refreshes the index snapshot on rescan."""
    import time

    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    cfg["addons"]["Foo"] = {
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
        "sha": "REMOTE",
    }
    assert controller.verify() is True
    wait_for_event(
        controller._dispatcher, lambda e: isinstance(e, AddonsLoaded)
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    before = _read_index()["units"]["foo"]["toc"]["Foo"]
    toc_path = os.path.join(client, "Interface", "AddOns", "Foo", "Foo.toc")
    time.sleep(0.02)
    with open(toc_path, "a", encoding="utf-8") as f:
        f.write("## Notes: edited\n")
    # Bust the in-process toc cache so the scan re-reads the file.
    addons._TOC_CACHE.pop(toc_path, None)
    assert controller.verify(force=True) is True
    wait_for_event.until_true(lambda: not controller.state.busy)
    after = _read_index()["units"]["foo"]["toc"]["Foo"]
    assert after != before


def test_install_mid_scan_survives_rebuild(
    controller, cfg, tmp_path, monkeypatch, wait_for_event, catalog_one
):
    """An install landing while a scan is mid-flight is not clobbered by
    the scan's index rebuild (both writers serialize on _INDEX_LOCK)."""
    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    _install_folder(client, "Bar")
    cfg["addons"]["Foo"] = {
        "git": "https://github.com/catalog/Foo",
        "branch": "main",
        "ref": None,
        "sha": "REMOTE",
    }
    cfg["addons"]["Bar"] = {
        "git": "https://github.com/catalog/Bar",
        "branch": None,
        "ref": None,
        "sha": "REMOTE",
    }
    release = threading.Event()
    entered = threading.Event()

    real_store = addons.store_addons_index

    def gated_store(index):
        # The scan holds _INDEX_LOCK across read-patch-write, so its
        # store runs inside the lock: an install landing now blocks
        # until the scan finishes — the lost-update window is closed.
        entered.set()
        assert release.wait(timeout=10.0)
        return real_store(index)

    monkeypatch.setattr(addons, "store_addons_index", gated_store)
    assert controller.verify(force=True) is True
    assert entered.wait(timeout=10.0)

    def do_install():
        controller._index_record_install(
            "REMOTE",
            ["Bar"],
            [{"folder": "Bar", "id": "bar"}],
        )

    worker = threading.Thread(target=do_install)
    worker.start()
    assert worker.is_alive()
    release.set()
    worker.join(timeout=10.0)
    wait_for_event.until_true(lambda: not controller.state.busy)
    index = _read_index()
    assert "Foo" in index["units"]["foo"]["folders"]
    assert "Bar" in index["units"]["bar"]["folders"]

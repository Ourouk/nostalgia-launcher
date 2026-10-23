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
    from nostalgia_launcher.core import launcher

    return launcher.local_addons_index_path()


def _read_index():
    with open(_index_path(), encoding="utf-8") as f:
        return json.load(f)


# ── storage ───────────────────────────────────────────────────────────────


def test_index_round_trip_launcher_pair(tmp_path):
    """The launcher pair persists and reloads the tracked-install index."""
    from nostalgia_launcher.core import launcher

    index = {
        "repos": {
            "git:https://github.com/a/b#main#": {
                "entry_ids": ["x"],
                "folders": ["Foo"],
                "git": "https://github.com/a/b",
                "branch": "main",
                "ref": None,
                "sha": "abc",
                "installed_at": 1.0,
                "toc": {},
            }
        },
        "orphans": [],
    }
    launcher.store_local_addons_index(index)
    assert launcher.load_local_addons_index()["repos"].keys() == (
        index["repos"].keys()
    )


def test_index_corrupt_file_yields_empty(tmp_path):
    """A corrupt index file loads empty and never raises."""
    from nostalgia_launcher.core import launcher

    with open(_index_path(), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert launcher.load_local_addons_index() == {"repos": {}, "orphans": []}


def test_index_missing_file_yields_empty(tmp_path):
    """A missing index file loads empty and never raises."""
    from nostalgia_launcher.core import launcher

    if os.path.exists(_index_path()):
        os.remove(_index_path())
    assert launcher.load_local_addons_index() == {"repos": {}, "orphans": []}


def test_index_group_key_shapes():
    """Group keys mirror the controller's _group_recs split."""
    assert addons.index_group_key(git="g", branch="b", ref=None) == "git:g#b#"
    assert (
        addons.index_group_key(download_url="https://x/y.zip")
        == "direct:https://x/y.zip"
    )


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
    key = "git:https://github.com/catalog/foo#main#"
    assert index["repos"][key]["folders"] == ["Foo"]
    assert index["repos"][key]["sha"] == "REMOTE"
    assert index["repos"][key]["entry_ids"] == ["foo"]


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
    assert index["repos"]["direct:https://example.com/foo.zip"]["folders"] == [
        "Foo"
    ]


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
    assert "Foo" in _read_index()["repos"].get(
        "git:https://github.com/catalog/foo#main#", {}
    ).get("folders", [])
    controller.state.pending["Foo"] = AddonPending(enabled=False)
    assert controller.apply_pending() is True
    wait_for_event(
        controller._dispatcher,
        lambda e: e.__class__.__name__ == "OperationFinished",
    )
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert _read_index()["repos"] == {}


def test_variant_switch_moves_index_ownership(
    controller, cfg, tmp_path, monkeypatch, wait_for_event
):
    """Installing the same folder from a different repo moves ownership."""
    from nostalgia_launcher.core import launcher

    client = str(tmp_path)
    cfg["out_dir"] = client
    _install_folder(client, "Foo")
    launcher.store_local_addons_index(
        {
            "repos": {
                "git:https://github.com/old/foo##": {
                    "entry_ids": [],
                    "folders": ["Foo"],
                    "git": "https://github.com/old/Foo",
                    "branch": None,
                    "ref": None,
                    "sha": "OLD",
                    "installed_at": 1.0,
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
    assert "git:https://github.com/old/foo##" not in index["repos"]
    key = "git:https://github.com/new/foo##"
    assert index["repos"][key]["folders"] == ["Foo"]


# ── fast paint ────────────────────────────────────────────────────────────


def test_fast_paint_lists_installed_without_scandir(
    controller, cfg, tmp_path, monkeypatch, wait_for_event, catalog_one
):
    """With a warm index, the first paint already lists INSTALLED —
    without touching the disk."""
    from nostalgia_launcher.core import launcher

    client = str(tmp_path)
    cfg["out_dir"] = client
    launcher.store_local_addons_index(
        {
            "repos": {
                "git:https://github.com/catalog/foo#main#": {
                    "entry_ids": ["foo"],
                    "folders": ["Foo"],
                    "git": "https://github.com/catalog/Foo",
                    "branch": "main",
                    "ref": None,
                    "sha": "REMOTE",
                    "installed_at": 1.0,
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
    key = "git:https://github.com/catalog/foo#main#"
    assert _read_index()["repos"][key]["folders"] == ["Foo"]
    # The shared backends fixture stubs shutil.rmtree globally; remove
    # the folder with os calls so the hand-delete really deletes.
    victim = os.path.join(client, "Interface", "AddOns", "Foo")
    for leaf in os.listdir(victim):
        os.remove(os.path.join(victim, leaf))
    os.rmdir(victim)
    assert controller.verify(force=True) is True
    wait_for_event.until_true(lambda: not controller.state.busy)
    assert "Foo" not in controller.state.addons
    assert _read_index()["repos"] == {}


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
    key = "git:https://github.com/catalog/foo#main#"
    assert index["repos"][key]["folders"] == ["Foo"]
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
    key = "git:https://github.com/gone/customthing##"
    assert _read_index()["repos"][key]["folders"] == ["CustomThing"]


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
    key = "git:https://github.com/catalog/foo#main#"
    assert _read_index()["repos"][key]["folders"] == ["Foo"]


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
    key = "git:https://github.com/catalog/foo#main#"
    before = _read_index()["repos"][key]["toc"]["Foo"]
    toc_path = os.path.join(client, "Interface", "AddOns", "Foo", "Foo.toc")
    time.sleep(0.02)
    with open(toc_path, "a", encoding="utf-8") as f:
        f.write("## Notes: edited\n")
    # Bust the in-process toc cache so the scan re-reads the file.
    addons._TOC_CACHE.pop(toc_path, None)
    assert controller.verify(force=True) is True
    wait_for_event.until_true(lambda: not controller.state.busy)
    after = _read_index()["repos"][key]["toc"]["Foo"]
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

    real_reconcile = AddonsController._reconcile_index

    def gated(self, installed, records, addons_dir, sha_memo):
        entered.set()
        assert release.wait(timeout=5.0)
        return real_reconcile(self, installed, records, addons_dir, sha_memo)

    monkeypatch.setattr(AddonsController, "_reconcile_index", gated)
    assert controller.verify(force=True) is True
    assert entered.wait(timeout=5.0)

    def do_install():
        controller._index_record_install(
            addons.index_group_key(git="https://github.com/catalog/Bar"),
            {"git": "https://github.com/catalog/Bar"},
            "REMOTE",
            ["Bar"],
            [{"folder": "Bar", "id": "bar"}],
        )

    worker = threading.Thread(target=do_install)
    worker.start()
    worker.join(timeout=5.0)
    release.set()
    wait_for_event.until_true(lambda: not controller.state.busy)
    index = _read_index()
    assert (
        "Foo"
        in index["repos"]["git:https://github.com/catalog/foo#main#"][
            "folders"
        ]
    )
    assert (
        "Bar"
        in index["repos"]["git:https://github.com/catalog/bar##"]["folders"]
    )


def test_git_url_spellings_share_one_group():
    """`.../b` and `.../b.git` (any case) map to one index group."""
    assert addons.index_group_key(
        git="https://github.com/a/B.git"
    ) == addons.index_group_key(git="https://github.com/a/b")

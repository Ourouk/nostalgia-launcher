"""Unit tests for the mods controller (mods_controller).

No Tk involved: the controller is driven directly and its effects are read
from the shared EventDispatcher and its ModsState. Backends (mods registry,
version fetch, install helpers, config store) are swapped for fakes via
monkeypatch so nothing touches the network or the real filesystem.
"""

import threading
import time

import pytest

import vanilla_wow_launcher.services.client_update as client_update
import vanilla_wow_launcher.controllers.mods as mc
from vanilla_wow_launcher.controllers.mods import ModsController
from vanilla_wow_launcher.state.events import (
    EventDispatcher,
    LogMessage,
    ModsLoaded,
    OperationFinished,
    StatusChanged,
)
from vanilla_wow_launcher.state.models import ModPending

# Small synthetic registry so tests don't depend on the real one.
MOD_A = {
    "id": "AlphaMod",
    "essential": True,
    "name": "AlphaMod",
    "description": "alpha",
    "source": {"kind": "github_release", "owner": "o", "repo": "r",
               "asset_pattern": "*.zip", "prefer_no": None, "extract_map": None},
}
MOD_B = {
    "id": "BetaMod",
    "essential": False,
    "name": "BetaMod",
    "description": "beta",
    "source": {"kind": "github_release", "owner": "o", "repo": "r2",
               "asset_pattern": "*.zip", "prefer_no": None, "extract_map": None},
}


class FakeUpdateWorker:
    """Stand-in for client_update.UpdateWorker (never used by the mods flow,
    patched so no backend can ever hit the network by accident)."""

    def __init__(self, *args, **kwargs):
        pass

    def cancel(self):
        pass

    def run(self, *args, **kwargs):
        pass


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(mc.mods, "mods_registry",
                        lambda *a, **k: [MOD_A, MOD_B])
    monkeypatch.setattr(client_update, "UpdateWorker", FakeUpdateWorker)


@pytest.fixture
def cfg(monkeypatch):
    state = {"out_dir": "/tmp/octo-game", "mods": {}}
    monkeypatch.setattr(mc.config_store, "load_config", lambda: state)
    monkeypatch.setattr(mc.config_store, "update_config",
                        lambda mutator: (mutator(state), state)[1])
    return state


@pytest.fixture
def versions(monkeypatch):
    table = {}
    monkeypatch.setattr(mc.mods, "fetch_mod_latest_version_cached",
                        lambda mod: table.get(mod["id"]))
    return table


@pytest.fixture
def controller(registry, cfg):
    return ModsController(EventDispatcher())


def _drain_for(dispatcher, predicate, timeout=2.0):
    """Drain until an event matching `predicate` arrives; return everything
    drained along the way (assertion failure on timeout)."""
    deadline = time.monotonic() + timeout
    collected = []
    while True:
        collected.extend(dispatcher.drain())
        if any(predicate(e) for e in collected):
            return collected
        if time.monotonic() > deadline:
            raise AssertionError("expected event never arrived")
        time.sleep(0.005)


# ── load_latest_versions ───────────────────────────────────────────────

def test_load_latest_versions_fills_state_and_posts_event(controller, versions):
    versions["AlphaMod"] = "1.2"
    versions["BetaMod"] = "2.0"
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert controller.state.latest_versions == {
        "AlphaMod": "1.2", "BetaMod": "2.0"}


def test_load_latest_versions_skips_failed_fetch(controller, monkeypatch):
    def boom(mod):
        raise ConnectionError("offline")
    monkeypatch.setattr(mc.mods, "fetch_mod_latest_version_cached", boom)
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert controller.state.latest_versions == {}


def test_load_latest_versions_fetches_catalog_on_first_launch(
        controller, monkeypatch, versions):
    calls = []
    monkeypatch.setattr(mc.mods, "mods_registry",
                        lambda *a, **k: calls.append(k) or [MOD_A, MOD_B])
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    # No cached catalog → a force fetch runs (the refresh re-reads from cache).
    assert calls[0] == {"force": True}
    assert calls.count({"force": True}) == 1


def test_load_latest_versions_uses_cache_without_fetching(
        controller, monkeypatch, cfg, versions):
    cfg["mods_catalog_cache"] = {"timestamp": 0, "catalog": [{}]}
    calls = []
    monkeypatch.setattr(mc.mods, "mods_registry",
                        lambda *a, **k: calls.append(k) or [MOD_A, MOD_B])
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert calls == [{}, {}]


def test_load_latest_versions_offline_first_launch_stays_empty(
        controller, monkeypatch, versions):
    def boom(*a, **k):
        raise ConnectionError("offline")
    monkeypatch.setattr(mc.mods, "mods_registry", boom)
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert controller.state.latest_versions == {}


# ── updates_count ──────────────────────────────────────────────────────

def test_updates_count_matches_apply_semantics(controller, versions, cfg):
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "1.0",
                     "ignore_updates": False},
        "BetaMod": {"enabled": True, "installed_version": "1.0",
                    "ignore_updates": False, "error": "download blocked"},
    }
    controller.state.records = controller._load_records()
    versions["AlphaMod"] = "2.0"
    versions["BetaMod"] = "2.0"
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    # AlphaMod has an update; BetaMod is skipped because it's in error.
    assert controller.updates_count == 1


def test_updates_count_uses_mod_update_available(controller, versions, cfg,
                                                 monkeypatch):
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "1.0"},
        "BetaMod": {"enabled": True, "installed_version": "1.0"},
    }
    controller.state.records = controller._load_records()
    versions["AlphaMod"] = "2.0"
    versions["BetaMod"] = "2.0"
    calls = []
    monkeypatch.setattr(mc.mods, "mod_update_available",
                        lambda mod, state, live: calls.append(
                            (mod["id"], live)) or True)
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert controller.updates_count == 2
    assert {mod_id for mod_id, _ in calls} == {"AlphaMod", "BetaMod"}
    assert all(live.get("latest_version") == "2.0" for _, live in calls)


# ── toggle / set_ignore ────────────────────────────────────────────────

def test_toggle_and_set_ignore_update_pending(controller):
    assert not controller.state.has_pending_changes
    controller.toggle("AlphaMod", True)
    controller.set_ignore("AlphaMod", True)
    p = controller.state.pending["AlphaMod"]
    assert p.enabled is True
    assert p.ignore_updates is True
    assert controller.state.has_pending_changes
    controller.toggle("AlphaMod", False)
    assert controller.state.pending["AlphaMod"].enabled is False
    assert controller.state.pending["AlphaMod"].ignore_updates is True


# ── action_for ─────────────────────────────────────────────────────────

def test_action_for_returns_retry_update_none(controller, versions, cfg):
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "1.0"},
        "BetaMod": {"enabled": True, "installed_version": "1.0",
                    "error": "oops"},
    }
    controller.state.records = controller._load_records()
    versions["AlphaMod"] = "2.0"
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert controller.action_for("AlphaMod") == "update"
    assert controller.action_for("BetaMod") == "retry"


def test_action_for_none_when_up_to_date(controller, versions, cfg):
    cfg["mods"] = {"AlphaMod": {"enabled": True, "installed_version": "1.0"}}
    controller.state.records = controller._load_records()
    versions["AlphaMod"] = "1.0"
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert controller.action_for("AlphaMod") is None


def test_action_for_unknown_mod_is_none(controller):
    assert controller.action_for("NoSuchMod") is None


# ── apply ──────────────────────────────────────────────────────────────

@pytest.fixture
def apply_backends(monkeypatch):
    monkeypatch.setattr(mc.mods, "install_mod",
                        lambda mod, cd, release=None: ["new.dll"])
    monkeypatch.setattr(mc.mods, "uninstall_mod", lambda mod, cd: None)
    monkeypatch.setattr(mc.mods, "add_dll", lambda cd, name: None)
    monkeypatch.setattr(mc.mods, "remove_dll", lambda cd, name: None)
    monkeypatch.setattr(mc.mods, "mod_installed_files_present",
                        lambda mod, cd: True)
    monkeypatch.setattr(mc.mods, "_fetch_release_cached",
                        lambda mod: {"tag_name": "2.0"})
    monkeypatch.setattr(mc.mods, "_release_version",
                        lambda mod, rel: rel["tag_name"])
    monkeypatch.setattr(mc.mods, "fetch_mod_latest_version_cached",
                        lambda mod: "2.0")


def test_apply_installs_and_posts_finished(controller, cfg, apply_backends):
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "0.5",
                     "installed_files": ["old.dll"]},
    }
    controller.state.records = controller._load_records()
    controller.state.pending["AlphaMod"] = ModPending(enabled=True)

    controller.apply()

    collected = _drain_for(
        controller._dispatcher, lambda e: isinstance(e, OperationFinished))
    assert OperationFinished("mods", True, "") in collected
    assert any(isinstance(e, ModsLoaded) for e in collected)
    assert any(isinstance(e, StatusChanged) for e in collected)
    assert controller.state.pending == {}
    rec = cfg["mods"]["AlphaMod"]
    assert rec["enabled"] is True
    assert rec["installed_version"] == "2.0"
    assert rec["installed_files"] == ["new.dll"]
    assert rec["error"] is None


def test_apply_records_per_mod_error_on_failure(controller, cfg, monkeypatch,
                                                apply_backends):
    def failing_install(mod, cd, release=None):
        raise RuntimeError("download blocked")
    monkeypatch.setattr(mc.mods, "install_mod", failing_install)
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "0.5",
                     "installed_files": ["old.dll"]},
    }
    controller.state.records = controller._load_records()
    controller.state.pending["AlphaMod"] = ModPending(enabled=True)

    controller.apply()

    collected = _drain_for(
        controller._dispatcher, lambda e: isinstance(e, OperationFinished))
    assert OperationFinished("mods", True, "") in collected
    rec = cfg["mods"]["AlphaMod"]
    assert rec["enabled"] is False
    assert rec["installed_version"] is None
    assert rec["installed_files"] == []
    assert rec["error"] == "download blocked"
    assert controller.state.has_errors


def test_apply_skips_mods_without_changes(controller, cfg, apply_backends):
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "2.0",
                     "installed_files": ["new.dll"], "ignore_updates": True},
    }
    controller.state.records = controller._load_records()

    controller.apply()

    _drain_for(controller._dispatcher, lambda e: isinstance(e, OperationFinished))
    # No action taken, records untouched.
    assert cfg["mods"]["AlphaMod"]["installed_version"] == "2.0"


def test_apply_without_folder_is_noop(controller, cfg):
    cfg["out_dir"] = ""
    controller.apply()
    assert controller._dispatcher.drain() == []


def test_apply_targeted_mod_keeps_pending(controller, cfg, apply_backends):
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "0.5",
                     "installed_files": ["old.dll"]},
        "BetaMod": {"enabled": True, "installed_version": "0.5",
                    "installed_files": ["old.dll"]},
    }
    controller.state.records = controller._load_records()
    controller.state.pending["BetaMod"] = ModPending(enabled=True)

    controller.apply(only_mod_id="AlphaMod")

    _drain_for(controller._dispatcher, lambda e: isinstance(e, OperationFinished))
    # A targeted update only touches its own mod and leaves pending intact.
    assert cfg["mods"]["AlphaMod"]["installed_version"] == "2.0"
    assert cfg["mods"]["BetaMod"]["installed_version"] == "0.5"
    assert "BetaMod" in controller.state.pending


def test_apply_ignores_concurrent_apply(controller, cfg, apply_backends,
                                        monkeypatch):
    """A second apply() while the first worker is mid-flight is rejected."""
    started = threading.Event()
    release_event = threading.Event()
    install_calls = []

    def blocking_install(mod, cd, release=None):
        install_calls.append(mod["id"])
        started.set()
        assert release_event.wait(2.0)
        return ["new.dll"]

    monkeypatch.setattr(mc.mods, "install_mod", blocking_install)
    cfg["mods"] = {
        "AlphaMod": {"enabled": True, "installed_version": "0.5",
                     "installed_files": ["old.dll"]},
    }
    controller.state.records = controller._load_records()
    controller.state.pending["AlphaMod"] = ModPending(enabled=True)

    assert controller.apply() is True
    assert started.wait(2.0)
    assert controller.busy is True
    assert controller.apply() is False
    assert install_calls == ["AlphaMod"]

    release_event.set()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, OperationFinished))
    assert install_calls == ["AlphaMod"]
    assert controller.busy is False


# ── maybe_install_essential_mods ───────────────────────────────────────

@pytest.fixture
def fresh_folder(controller, cfg, tmp_path):
    out = str(tmp_path)
    (tmp_path / "WoW.exe").write_bytes(b"MZ")
    cfg["out_dir"] = out
    cfg.pop("mods", None)
    return out


def test_maybe_install_essential_mods_queues_essential_and_starts(
        controller, cfg, fresh_folder, monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "apply", lambda *a, **k: calls.append(a))

    assert controller.maybe_install_essential_mods() is True
    assert calls == [()]
    assert controller.state.pending["AlphaMod"].enabled is True
    assert "BetaMod" not in controller.state.pending
    assert any(isinstance(e, LogMessage) for e in controller._dispatcher.drain())


def test_maybe_install_essential_mods_is_one_shot(
        controller, cfg, fresh_folder, monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "apply", lambda *a, **k: calls.append(a))

    assert controller.maybe_install_essential_mods() is True
    assert controller.maybe_install_essential_mods() is False
    assert calls == [()]


def test_maybe_install_essential_mods_skips_when_initialized(
        controller, cfg, fresh_folder, monkeypatch):
    cfg["mods"] = {"AlphaMod": {"enabled": True}}
    assert controller.maybe_install_essential_mods() is False


def test_maybe_install_essential_mods_skips_without_client(
        controller, cfg, tmp_path):
    cfg["out_dir"] = str(tmp_path)
    cfg.pop("mods", None)
    assert controller.maybe_install_essential_mods() is False


# ── reset / invalidate ─────────────────────────────────────────────────

def test_reset_clears_state_and_rearms_one_shot(controller, cfg, fresh_folder,
                                                monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "apply", lambda *a, **k: calls.append(a))
    controller.toggle("AlphaMod", True)
    controller.state.latest_versions["AlphaMod"] = "2.0"
    controller.state.updates_count = 3
    assert controller.maybe_install_essential_mods() is True

    controller.reset()

    assert controller.state.pending == {}
    assert controller.state.latest_versions == {}
    assert controller.state.updates_count == 0
    # The one-shot auto-install is re-armed for the new folder.
    assert controller.maybe_install_essential_mods() is True
    assert len(calls) == 2


def test_invalidate_drops_latest_versions(controller, versions):
    versions["AlphaMod"] = "2.0"
    controller.load_latest_versions()
    _drain_for(controller._dispatcher, lambda e: isinstance(e, ModsLoaded))
    assert controller.state.latest_versions
    controller.invalidate()
    assert controller.state.latest_versions == {}

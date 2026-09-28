"""Cover the shared apply-worker leaves (pure, both controllers use)."""

from types import SimpleNamespace

from nostalgia_launcher.controllers.apply_shared import (
    action_label,
    resolve_enabled,
    sync_skipped,
)


def test_resolve_enabled_prefers_pending_then_state():
    assert resolve_enabled(SimpleNamespace(enabled=True), {}, False) is True
    assert (
        resolve_enabled(
            SimpleNamespace(enabled=None), {"enabled": True}, False
        )
        is True
    )
    assert resolve_enabled(None, {"enabled": False}, False) is False
    assert resolve_enabled(None, {}, False) is False


def test_resolve_enabled_forced_means_install():
    assert resolve_enabled(None, {"enabled": False}, True) is True
    assert resolve_enabled(SimpleNamespace(enabled=False), {}, True) is True


def test_action_label():
    assert action_label(True, False) == "Installing"
    assert action_label(False, True) == "Updating"
    assert action_label(False, False) == "Removing"


def test_sync_skipped_carries_pending_and_dismisses_error():
    cfg: dict = {}
    sync_skipped(cfg, "a", {"a": True}, False, {"error": "boom"})
    assert cfg == {"a": {"enabled": False, "error": None}}


def test_sync_skipped_leaves_clean_entries_alone():
    cfg: dict = {}
    sync_skipped(cfg, "a", {}, True, {})
    assert cfg == {}

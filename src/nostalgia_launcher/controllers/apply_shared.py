"""Shared apply-worker fragments for the mods/assets controllers.

Both workers run the same loop: resolve each entry's enabled flag, skip
entries needing no change, label the action, then install/uninstall/update
through kind-specific service calls. Only the identical leaves live here —
the version lookup, service calls, and config record shapes differ per
kind, so a full shared engine would cost more interface than it saves
(ponytail: two callers earn these three helpers, nothing more).
"""

from __future__ import annotations


def resolve_enabled(pending_entry, state: dict, forced: bool) -> bool:
    """An entry's effective enabled flag.

    Pending UI changes win, else the saved config. A targeted single-item
    update/retry (``forced``) always means "install this": without it,
    retrying a failed install is a no-op because the error handler
    recorded enabled=False.
    """
    if pending_entry is not None and pending_entry.enabled is not None:
        enabled = pending_entry.enabled
    else:
        enabled = state.get("enabled", False)
    if forced:
        enabled = True
    return enabled


def action_label(needs_install: bool, needs_update: bool) -> str:
    """Status verb for an entry needing change: Installing/Updating."""
    if needs_install:
        return "Installing"
    if needs_update:
        return "Updating"
    return "Removing"


def sync_skipped(
    cfg: dict, key: str, pending: dict, enabled: bool, state: dict
) -> None:
    """Record a no-change entry (mutates ``cfg``).

    Carries a pending toggle into the config, and clears a previous
    failure when the user leaves the entry disabled — a dismissed error
    must stop blocking PLAY.
    """
    if key in pending:
        cfg.setdefault(key, {})["enabled"] = enabled
    if not enabled and state.get("error"):
        cfg.setdefault(key, {})["error"] = None

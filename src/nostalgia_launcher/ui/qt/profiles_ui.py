"""Shared profile-switch plumbing for the Qt layer.

Used by BOTH switch surfaces: the main-window header combo (quick
switch) and the Settings PROFILES editor's delete-active restart offer.
Kept in its own module so neither widget needs to import the other
(main_window ↔ settings_dialog would be circular).

Flow is always: persist the pointer (`profiles.set_active`) FIRST, then
relaunch detached; a failed relaunch leaves the pointer persisted, so a
manual start still lands on the chosen profile.
"""

import os
import sys

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMessageBox

from ...core import profiles
from ...core.log_sink import log


def relaunch_with_profile(name: str) -> bool:
    """Restart the app into another profile (detached child).

    Strips BOTH ``--profile X`` and ``--profile=X`` from the old argv and
    appends the new one. Source runs spawn
    ``[sys.executable, sys.argv[0], *argv]``; frozen builds
    ``[sys.executable, *argv]``. The child gets ``NOSTALGIA_RELAUNCH=1``
    so its store-lock acquisition tolerates this quitting parent (bounded
    grace window). macOS frozen (.app) bundles are not supported in v1 —
    returns False so the UI can ask the user to relaunch manually.
    """
    argv = []
    skip_value = False
    for arg in sys.argv[1:]:
        if skip_value:
            skip_value = False
            continue
        if arg == "--profile":
            skip_value = True  # drop the value that follows
            continue
        if arg.startswith("--profile="):
            continue
        argv.append(arg)
    argv += ["--profile", name]

    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return False  # .app relaunch needs `open -na`; manual for now
        program, args = sys.executable, argv
    else:
        program, args = sys.executable, [sys.argv[0], *argv]

    # The child inherits its environment snapshot at spawn time, so set
    # the grace flag just around the (synchronous) detached fork.
    prev = os.environ.get("NOSTALGIA_RELAUNCH")
    os.environ["NOSTALGIA_RELAUNCH"] = "1"
    try:
        return QProcess.startDetached(program, args)
    finally:
        if prev is None:
            os.environ.pop("NOSTALGIA_RELAUNCH", None)
        else:
            os.environ["NOSTALGIA_RELAUNCH"] = prev


def confirm_switch(parent, name: str) -> bool:
    """The standard "the launcher will restart" confirmation."""
    answer = QMessageBox.question(
        parent,
        "Switch profile",
        f"The launcher will restart using profile '{name}'.",
        QMessageBox.Yes | QMessageBox.No,
    )
    return answer == QMessageBox.Yes


def switch_profile(name: str) -> bool:
    """Persist the active pointer, spawn the detached child and quit.

    Returns True when the relaunch was handed off successfully (the app
    quits). On failure the pointer stays persisted and this logs the
    manual-restart instruction — callers surface it in their own UI.
    """
    profiles.set_active(name)
    if relaunch_with_profile(name):
        QApplication.quit()
        return True
    log(
        "Could not relaunch automatically — restart manually to "
        "switch profiles.",
        "err",
    )
    return False

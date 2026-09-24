"""First-launch/import wizard model for QML (Phase 6b).

Mirrors `ui/qt/launcher_config_dialog.py`: input stage (file or URL) →
fetch/read + validate → install-folder stage (REQUIRED, pre-filled with
the Games/<ServerName> suggestion) → trust stage (hosts, capabilities,
folder, explicit Trust). Nothing is persisted; the selection dict matches
the widget shape so `cli._first_launch` consumes it unchanged.
"""

import os
import threading

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine

from ...core import launcher, platform_support
from ...core.log_sink import log
from ...services import config_import
from ..qt.launcher_config_dialog import (
    trust_capabilities,
    trust_hosts,
)
from ..qt.theme import palette_for_config
from .viewmodels import ThemeBridge

STAGE_INPUT = "input"
STAGE_FOLDER = "folder"
STAGE_TRUST = "trust"

_TITLES = {
    STAGE_INPUT: "Welcome to Nostalgia Launcher",
    STAGE_FOLDER: "Choose your install folder",
    STAGE_TRUST: "You're about to trust:",
}
_STEPS = {
    STAGE_INPUT: "Step 1 of 3 — Import configuration",
    STAGE_FOLDER: "Step 2 of 3 — Install folder",
    STAGE_TRUST: "Step 3 of 3 — Trust configuration",
}


class WizardModel(QObject):
    """Import wizard state (shared by first-launch and profile import)."""

    changed = Signal()
    fetchFinished = Signal(object)

    def __init__(self, initial_path="", parent=None):
        super().__init__(parent)
        self._stage = STAGE_INPUT
        self._source = "file"
        self._url = ""
        self._path = initial_path if os.path.isfile(initial_path) else ""
        self._folder = ""
        self._status = ""
        self._error = ""
        self._fetching = False
        self._validated = None  # (kind, source, raw, config)
        self._pending = None  # (kind, source, raw, config) at trust stage
        self._install_dir = ""
        self._trust_name = ""
        self._trust_source = ""
        self._trust_hosts: list = []
        self._trust_caps: list = []
        self.fetchFinished.connect(self._on_fetch_result)

    # ── properties ────────────────────────────────────────────────────

    def _get_stage(self) -> str:
        return self._stage

    stage = Property(str, _get_stage, notify=changed)

    def _get_title(self) -> str:
        return _TITLES[self._stage]

    title = Property(str, _get_title, notify=changed)

    def _get_step(self) -> str:
        return _STEPS[self._stage]

    step = Property(str, _get_step, notify=changed)

    def _get_source(self) -> str:
        return self._source

    source = Property(str, _get_source, notify=changed)

    def _get_url(self) -> str:
        return self._url

    url = Property(str, _get_url, notify=changed)

    def _get_path(self) -> str:
        return self._path

    configPath = Property(str, _get_path, notify=changed)

    def _get_folder(self) -> str:
        return self._folder

    folder = Property(str, _get_folder, notify=changed)

    def _get_status(self) -> str:
        return self._status

    statusText = Property(str, _get_status, notify=changed)

    def _get_error(self) -> str:
        return self._error

    errorText = Property(str, _get_error, notify=changed)

    def _get_fetching(self) -> bool:
        return self._fetching

    fetching = Property(bool, _get_fetching, notify=changed)

    def _get_ok_enabled(self) -> bool:
        if self._fetching:
            return False
        if self._stage == STAGE_TRUST:
            return True
        if self._stage == STAGE_FOLDER:
            return bool(self._folder.strip())
        if self._source == "url":
            return bool(self._url.strip())
        return bool(self._path.strip())

    okEnabled = Property(bool, _get_ok_enabled, notify=changed)

    def _get_ok_text(self) -> str:
        return (
            "Trust configuration" if self._stage == STAGE_TRUST else "Continue"
        )

    okText = Property(str, _get_ok_text, notify=changed)

    def _get_back_visible(self) -> bool:
        return self._stage != STAGE_INPUT

    backVisible = Property(bool, _get_back_visible, notify=changed)

    def _get_trust_name(self) -> str:
        return self._trust_name

    trustName = Property(str, _get_trust_name, notify=changed)

    def _get_trust_source(self) -> str:
        return self._trust_source

    trustSource = Property(str, _get_trust_source, notify=changed)

    def _get_trust_hosts(self) -> list:
        return list(self._trust_hosts)

    trustHosts = Property("QVariantList", _get_trust_hosts, notify=changed)

    def _get_trust_caps(self) -> list:
        return list(self._trust_caps)

    trustCaps = Property("QVariantList", _get_trust_caps, notify=changed)

    # ── input slots ───────────────────────────────────────────────────

    @Slot(str)
    def setSource(self, source: str):
        if self._stage != STAGE_INPUT:
            return
        if source in ("url", "file") and source != self._source:
            self._source = source
            self._error = ""
            self.changed.emit()

    @Slot(str)
    def setUrl(self, url: str):
        self._url = url
        if url.strip():
            self._source = "url"
        self._error = ""
        self.changed.emit()

    @Slot(str)
    def setPath(self, path: str):
        self._path = path
        if path.strip():
            self._source = "file"
        self._error = ""
        self.changed.emit()

    @Slot(str)
    def setFolder(self, folder: str):
        self._folder = folder
        self._error = ""
        self.changed.emit()

    # ── navigation ────────────────────────────────────────────────────

    @Slot()
    def goBack(self):
        self._error = ""
        if self._stage == STAGE_TRUST:
            self._pending = None
            self._set_stage(STAGE_FOLDER)
            self._status = "Confirm where the game client will be installed."
        elif self._stage == STAGE_FOLDER:
            self._validated = None
            self._set_stage(STAGE_INPUT)
            self._status = ""
        self.changed.emit()

    @Slot()
    def reset(self):
        """Clear all state for reuse (profile-import reopen)."""
        self._stage = STAGE_INPUT
        self._source = "file"
        self._url = ""
        self._path = ""
        self._folder = ""
        self._status = ""
        self._error = ""
        self._fetching = False
        self._validated = None
        self._pending = None
        self._install_dir = ""
        self._trust_name = ""
        self._trust_source = ""
        self._trust_hosts = []
        self._trust_caps = []
        for attr in ("_accepted", "_selection_result"):
            if hasattr(self, attr):
                delattr(self, attr)
        self.changed.emit()

    @Slot(result=bool)
    def submit(self):
        """Continue/Trust button (returns True when the dialog accepts)."""
        if self._stage == STAGE_TRUST:
            return self._accept_pending()
        if self._stage == STAGE_FOLDER:
            self._confirm_folder()
            return False
        if self._active_source() == "url":
            url = self._url.strip()
            if not url:
                self._show_error("Enter a configuration URL.")
                return False
            self._submit_url(url)
        else:
            path = self._path.strip()
            if not path:
                self._show_error("Choose a local configuration file.")
                return False
            self._submit_file(path)
        return False

    def takeSelection(self):
        """The accepted selection dict (widget `selection()` shape)."""
        return self._selection() if hasattr(self, "_accepted") else None

    # ── internals ─────────────────────────────────────────────────────

    def _active_source(self) -> str:
        if self._source == "url" and self._url.strip():
            return "url"
        if self._source == "file" and self._path.strip():
            return "file"
        if self._url.strip():
            return "url"
        return "file"

    def _set_stage(self, stage: str):
        self._stage = stage

    def _show_error(self, message: str):
        self._error = message
        self.changed.emit()

    def _submit_file(self, path: str):
        config, err = launcher.validate_path(path)
        if config is None:
            self._show_error(
                str(err) or "Please choose a valid nostalgia_launcher.json."
            )
            return
        try:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            self._show_error(f"Could not read the configuration: {exc}")
            return
        self._enter_folder_stage("file", path, raw, config)

    def _submit_url(self, url: str):
        try:
            url = config_import.check_config_url(url)
        except config_import.ConfigUrlError as exc:
            self._show_error(str(exc))
            return
        self._error = ""
        self._fetching = True
        self._status = "Fetching the configuration…"
        self.changed.emit()

        def work():
            try:
                data, raw, err = config_import.fetch_config_url(url)
            except Exception as exc:  # defensive: never stick the wizard
                data, raw, err = None, None, f"Fetch failed: {exc}"
            self.fetchFinished.emit(
                {"url": url, "data": data, "raw": raw, "err": err}
            )

        threading.Thread(target=work, daemon=True).start()

    @Slot(object)
    def _on_fetch_result(self, result):
        if not self._fetching:
            return  # cancelled meanwhile
        self._fetching = False
        self._status = ""
        if result.get("err"):
            self._show_error(result["err"])
            return
        config, verr = launcher.validate_dict(result.get("data"))
        if config is None:
            self._show_error(str(verr) or "The configuration is invalid.")
            return
        self._enter_folder_stage(
            "url", result["url"], result.get("raw") or "", config
        )

    def cancel_fetch(self):
        """A late fetch result must not advance a cancelled dialog."""
        self._fetching = False

    def _enter_folder_stage(self, kind, source, raw, config):
        self._validated = (kind, source, raw, config)
        if not self._folder.strip():
            suggestion = platform_support.default_game_folder(
                config.server_name, config.client_version
            )
            if suggestion:
                self._folder = suggestion
        self._set_stage(STAGE_FOLDER)
        self._status = (
            "Confirm the configuration source, then choose the install folder."
        )
        self.changed.emit()

    def _confirm_folder(self):
        if not self._validated:
            self._show_error("Pick a configuration first.")
            return
        chosen = os.path.normpath(os.path.expanduser(self._folder.strip()))
        if not chosen or chosen == ".":
            self._show_error(
                "Choose the folder where the game client is installed."
            )
            return
        self._install_dir = chosen
        kind, source, raw, config = self._validated
        self._render_trust(kind, source, config)
        self._pending = (kind, source, raw, config)
        self._set_stage(STAGE_TRUST)
        self._status = (
            "Review this configuration. Only trust it if you trust its source."
        )
        self.changed.emit()

    def _render_trust(self, kind, source, config):
        self._trust_name = f"Server:\n{config.server_name or '(unnamed)'}"
        if kind == "url":
            self._trust_source = f"Source: {source}"
        else:
            self._trust_source = f"File: {source}"
        hosts = trust_hosts(config)
        self._trust_hosts = hosts or ["(no hosts configured)"]
        self._trust_caps = [
            {"key": key, "enabled": enabled, "detail": detail}
            for key, enabled, detail in trust_capabilities(config)
        ]

    def _accept_pending(self):
        if not self._pending:
            return False
        kind, source, raw, config = self._pending
        server_name = getattr(config, "server_name", "") or ""
        if kind == "file":
            self._selection_result = {
                "kind": "file",
                "path": source,
                "raw": raw,
                "install_dir": self._install_dir,
                "server_name": server_name,
            }
        else:
            self._selection_result = {
                "kind": "url",
                "config_url": source,
                "raw": raw,
                "install_dir": self._install_dir,
                "server_name": server_name,
            }
        self._accepted = True
        return True

    def _selection(self):
        return self._selection_result


def run_import_wizard_qml(initial_path=""):
    """Modal first-launch import (QML backend's `_pick_launcher_config`).

    Runs a nested event loop over WizardWindow.qml; returns the selection
    dict (widget `selection()` shape) or None on cancel.
    """
    from PySide6.QtCore import QEventLoop

    from .app import create_qml_app, qml_dir

    create_qml_app()
    model = WizardModel(initial_path=initial_path or "")
    palette = palette_for_config(launcher.config())
    theme = ThemeBridge(palette)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("wizard", model)
    engine.rootContext().setContextProperty("appTheme", theme)
    engine.load(
        QUrl.fromLocalFile(os.path.join(qml_dir(), "WizardWindow.qml"))
    )
    if not engine.rootObjects():
        log("QML import wizard failed to load.", "err")
        return None

    loop = QEventLoop()
    window = engine.rootObjects()[0]
    window.wizardDone.connect(loop.quit)
    if loop.exec() != 0:
        return None
    model.cancel_fetch()
    if getattr(model, "_accepted", False):
        return model.takeSelection()
    return None

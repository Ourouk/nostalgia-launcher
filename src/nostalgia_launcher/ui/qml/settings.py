"""Settings dialog + session-log models for QML (Phase 6a).

Mirrors `ui/qt/settings_dialog.py` (Game / Sources / Profiles /
Troubleshooting tabs) and `ui/qt/log_window.py` (session-log viewer).
`SettingsModel` flattens `SettingsController` state into properties; the
snapshot provider and action callbacks in `ui.qml.app` own the controller
wiring. Transient statuses (registry/profiles) are model-owned.
"""

from PySide6.QtCore import Property, QObject, Signal, Slot

from ...core.log_sink import read_lines


class SettingsModel(QObject):
    """Settings dialog state (one instance, four tabs)."""

    changed = Signal()
    transientChanged = Signal()
    logsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._game_path = ""
        self._game_suggestion = ""
        self._sources: list = []
        self._clear_wdb = False
        self._close_on_launch = False
        self._client_updates = True
        self._can_launch = False
        self._can_antivirus = False
        self._is_linux = False
        self._addons_url = ""
        self._mods_url = ""
        self._addons_default = False
        self._mods_default = False
        self._addons_default_avail = False
        self._mods_default_avail = False
        self._profiles: list = []
        self._active_profile = ""
        self._logs_open = False
        self._registry_status = ""
        self._profiles_status = ""
        self._switch_prompt = ""
        self._snapshot_provider = None
        self._handlers: dict = {}

    # ── snapshot ──────────────────────────────────────────────────────

    def set_snapshot_provider(self, provider):
        self._snapshot_provider = provider

    @Slot()
    def refresh(self):
        if self._snapshot_provider is None:
            return
        snap = self._snapshot_provider()
        self._game_path = str(snap.get("game_path", ""))
        self._game_suggestion = str(snap.get("game_suggestion", ""))
        self._sources = list(snap.get("sources", []))
        self._clear_wdb = bool(snap.get("clear_wdb", False))
        self._close_on_launch = bool(snap.get("close_on_launch", False))
        self._client_updates = bool(snap.get("client_updates", True))
        self._can_launch = bool(snap.get("can_launch", False))
        self._can_antivirus = bool(snap.get("can_antivirus", False))
        self._is_linux = bool(snap.get("is_linux", False))
        self._addons_url = str(snap.get("addons_url", ""))
        self._mods_url = str(snap.get("mods_url", ""))
        self._addons_default = bool(snap.get("addons_default", False))
        self._mods_default = bool(snap.get("mods_default", False))
        self._addons_default_avail = bool(
            snap.get("addons_default_avail", False)
        )
        self._mods_default_avail = bool(snap.get("mods_default_avail", False))
        self._profiles = list(snap.get("profiles", []))
        self._active_profile = str(snap.get("active_profile", ""))
        self.changed.emit()

    # ── properties ────────────────────────────────────────────────────

    def _get_game_path(self) -> str:
        return self._game_path

    gamePath = Property(str, _get_game_path, notify=changed)

    def _get_game_suggestion(self) -> str:
        return self._game_suggestion

    gameSuggestion = Property(str, _get_game_suggestion, notify=changed)

    def _get_sources(self) -> list:
        return list(self._sources)

    sources = Property("QVariantList", _get_sources, notify=changed)

    def _get_clear_wdb(self) -> bool:
        return self._clear_wdb

    clearWdb = Property(bool, _get_clear_wdb, notify=changed)

    def _get_close_on_launch(self) -> bool:
        return self._close_on_launch

    closeOnLaunch = Property(bool, _get_close_on_launch, notify=changed)

    def _get_client_updates(self) -> bool:
        return self._client_updates

    clientUpdates = Property(bool, _get_client_updates, notify=changed)

    def _get_can_launch(self) -> bool:
        return self._can_launch

    canLaunch = Property(bool, _get_can_launch, notify=changed)

    def _get_can_antivirus(self) -> bool:
        return self._can_antivirus

    canAntivirus = Property(bool, _get_can_antivirus, notify=changed)

    def _get_addons_url(self) -> str:
        return self._addons_url

    addonsUrl = Property(str, _get_addons_url, notify=changed)

    def _get_mods_url(self) -> str:
        return self._mods_url

    modsUrl = Property(str, _get_mods_url, notify=changed)

    def _get_addons_default(self) -> bool:
        return self._addons_default

    addonsDefault = Property(bool, _get_addons_default, notify=changed)

    def _get_mods_default(self) -> bool:
        return self._mods_default

    modsDefault = Property(bool, _get_mods_default, notify=changed)

    def _get_addons_default_avail(self) -> bool:
        return self._addons_default_avail

    addonsDefaultAvail = Property(
        bool, _get_addons_default_avail, notify=changed
    )

    def _get_mods_default_avail(self) -> bool:
        return self._mods_default_avail

    modsDefaultAvail = Property(bool, _get_mods_default_avail, notify=changed)

    def _get_profiles(self) -> list:
        return list(self._profiles)

    profiles = Property("QVariantList", _get_profiles, notify=changed)

    def _get_active_profile(self) -> str:
        return self._active_profile

    activeProfile = Property(str, _get_active_profile, notify=changed)

    def _get_logs_open(self) -> bool:
        return self._logs_open

    logsOpen = Property(bool, _get_logs_open, notify=transientChanged)

    def _get_registry_status(self) -> str:
        return self._registry_status

    registryStatus = Property(
        str, _get_registry_status, notify=transientChanged
    )

    def _get_profiles_status(self) -> str:
        return self._profiles_status

    profilesStatus = Property(
        str, _get_profiles_status, notify=transientChanged
    )

    def _get_switch_prompt(self) -> str:
        return self._switch_prompt

    switchPrompt = Property(str, _get_switch_prompt, notify=transientChanged)

    @Slot(bool)
    def setLogsOpen(self, open_: bool):
        open_ = bool(open_)
        if open_ != self._logs_open:
            self._logs_open = open_
            self.transientChanged.emit()

    set_logs_open = setLogsOpen

    # ── actions ───────────────────────────────────────────────────────

    def set_handlers(self, **handlers):
        self._handlers = dict(handlers)

    def _call(self, name, *args):
        handler = self._handlers.get(name)
        if handler is None:
            return None
        return handler(*args)

    @Slot()
    def openClientFolder(self):
        self._call("open_folder")

    @Slot(str)
    def setGameFolder(self, path: str):
        if self._call("set_path", path):
            self.refresh()

    @Slot()
    def checkSources(self):
        self._call("check_sources")

    @Slot()
    def verifyFiles(self):
        self._call("verify")

    @Slot()
    def allowAntivirus(self):
        self._call("antivirus")

    @Slot(bool)
    def setClearWdb(self, enabled: bool):
        self._call("clear_wdb", bool(enabled))
        self.refresh()

    @Slot(bool)
    def setCloseOnLaunch(self, enabled: bool):
        self._call("close_on_launch", bool(enabled))
        self.refresh()

    @Slot(bool)
    def setClientUpdates(self, enabled: bool):
        self._call("client_updates", bool(enabled))
        self.refresh()

    @Slot(bool)
    def setAddonsDefault(self, enabled: bool):
        self._call("addons_default", bool(enabled))
        self.refresh()

    @Slot(bool)
    def setModsDefault(self, enabled: bool):
        self._call("mods_default", bool(enabled))
        self.refresh()

    @Slot(str)
    def applyAddonsUrl(self, url: str):
        err = self._call("set_addons_url", url)
        self._registry_status = f"✗ {err}" if err else ""
        self.transientChanged.emit()
        self.refresh()

    @Slot(str)
    def applyModsUrl(self, url: str):
        err = self._call("set_mods_url", url)
        self._registry_status = f"✗ {err}" if err else ""
        self.transientChanged.emit()
        self.refresh()

    @Slot()
    def resetAddonsUrl(self):
        self._call("reset_addons_url")
        self._registry_status = ""
        self.transientChanged.emit()
        self.refresh()

    @Slot()
    def resetModsUrl(self):
        self._call("reset_mods_url")
        self._registry_status = ""
        self.transientChanged.emit()
        self.refresh()

    @Slot()
    def reloadAddons(self):
        self._call("reload_addons")

    @Slot()
    def reloadMods(self):
        self._call("reload_mods")

    @Slot()
    def openAddonsCustom(self):
        self._call("open_addons_custom")

    @Slot()
    def openModsCustom(self):
        self._call("open_mods_custom")

    @Slot()
    def clearAddonsCustom(self):
        self._call("clear_addons_custom")

    @Slot()
    def clearModsCustom(self):
        self._call("clear_mods_custom")

    @Slot(str)
    def deleteProfile(self, name: str):
        err = self._call("delete_profile", name)
        self._profiles_status = f"✗ {err}" if err else ""
        self.transientChanged.emit()
        self.refresh()

    @Slot()
    def requestLogs(self):
        self.logsRequested.emit()

    @Slot()
    def finishImport(self):
        self._call("finish_import")

    @Slot(bool)
    def resolveSwitch(self, yes: bool):
        self._call("resolve_switch", bool(yes))

    def prompt_switch(self, target: str):
        """Ask whether to restart on a freshly imported profile."""
        self._switch_prompt = target
        self.transientChanged.emit()

    def clear_switch_prompt(self):
        self._switch_prompt = ""
        self.transientChanged.emit()


class LogModel(QObject):
    """Session-log viewer (mirrors `ui/qt/log_window.py`)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: list = []

    def _get_lines(self) -> list:
        return list(self._lines)

    lines = Property("QStringList", _get_lines, notify=changed)

    @Slot()
    def refresh(self):
        """Reload the retained session log (old rotation first)."""
        try:
            self._lines = [line.rstrip("\n") for line in read_lines(2000)]
        except Exception:
            self._lines = []
        self.changed.emit()

    @Slot(str, str)
    def appendLine(self, text: str, _tag: str):
        """Live tail while the viewer is open (`bridge.logMessage`)."""
        self._lines.append(text)
        if len(self._lines) > 2000:
            del self._lines[: len(self._lines) - 2000]
        self.changed.emit()

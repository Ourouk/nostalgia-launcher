"""Shared content-list model for the MODS/ASSETS QML tabs.

Mirrors `ui/qt/content_panel.py`: one catalog-entry row (checkbox, required
star, name/version, repo link, retry/update action, description, error),
a banner with search + All/Installed/Updates chips, an empty state, and a
★-install/Apply footer. `build_rows` flattens a controller registry +
state into plain role dicts so this module never imports controllers; the
per-kind wiring (registry/state/action callbacks) lives in `ui.qml.app`.
"""

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    Qt,
    Signal,
    Slot,
)


def build_rows(entries, state, *, required_of, version_of, action_for):
    """Flatten registry + state into role dicts, sorted by name.

    `required_of(entry) -> bool`, `version_of(entry, rec, state) -> str`,
    `action_for(eid) -> "retry" | "update" | None` come from the tab glue.
    """
    recs = getattr(state, "records", {}) or {}
    pending = getattr(state, "pending", {}) or {}
    rows = []
    for entry in sorted(entries or [], key=lambda e: e["name"].lower()):
        eid = entry["id"]
        rec = recs.get(eid)
        pend = pending.get(eid)
        if pend is not None and pend.enabled is not None:
            enabled = bool(pend.enabled)
        elif rec is not None:
            enabled = bool(rec.enabled)
        else:
            enabled = False
        rows.append(
            {
                "eid": eid,
                "name": entry.get("name", eid),
                "version": version_of(entry, rec, state),
                "description": entry.get("description", ""),
                "repoUrl": entry.get("repo_url", ""),
                "installed": bool(rec.present) if rec is not None else False,
                "enabled": enabled,
                "required": bool(required_of(entry)),
                "action": action_for(eid) or "",
                "error": (rec.error if rec is not None else None) or "",
            }
        )
    return rows


def essential_pending(entries, state, *, required_of) -> bool:
    """True while a required entry is not yet present on disk."""
    recs = getattr(state, "records", {}) or {}
    for entry in entries or []:
        if not required_of(entry):
            continue
        rec = recs.get(entry["id"])
        if rec is None or not rec.present:
            return True
    return False


class ContentListModel(QAbstractListModel):
    """Filterable catalog list (one instance per tab: mods / assets)."""

    EIDRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    VersionRole = Qt.ItemDataRole.UserRole + 3
    DescriptionRole = Qt.ItemDataRole.UserRole + 4
    InstalledRole = Qt.ItemDataRole.UserRole + 5
    EnabledRole = Qt.ItemDataRole.UserRole + 6
    RequiredRole = Qt.ItemDataRole.UserRole + 7
    ActionRole = Qt.ItemDataRole.UserRole + 8
    ErrorRole = Qt.ItemDataRole.UserRole + 9
    RepoUrlRole = Qt.ItemDataRole.UserRole + 10

    changed = Signal()
    chromeChanged = Signal()

    def __init__(self, empty_text="", parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._visible: list = []
        self._filter_text = ""
        self._filter_mode = "all"
        self._updates_count = 0
        self._apply_visible = False
        self._essential_pending = False
        self._busy = False
        self._empty_text = empty_text
        self._snapshot_provider = None
        self._on_toggle = None
        self._on_action = None
        self._on_apply = None
        self._on_essential = None
        self._on_reload = None

    # ── model ─────────────────────────────────────────────────────────

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._visible)

    def roleNames(self) -> dict:
        return {
            ContentListModel.EIDRole: b"eid",
            ContentListModel.NameRole: b"name",
            ContentListModel.VersionRole: b"version",
            ContentListModel.DescriptionRole: b"description",
            ContentListModel.InstalledRole: b"installed",
            ContentListModel.EnabledRole: b"enabled",
            ContentListModel.RequiredRole: b"required",
            ContentListModel.ActionRole: b"action",
            ContentListModel.ErrorRole: b"error",
            ContentListModel.RepoUrlRole: b"repoUrl",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._visible[index.row()]
        _roles = {
            ContentListModel.EIDRole: "eid",
            ContentListModel.NameRole: "name",
            ContentListModel.VersionRole: "version",
            ContentListModel.DescriptionRole: "description",
            ContentListModel.InstalledRole: "installed",
            ContentListModel.EnabledRole: "enabled",
            ContentListModel.RequiredRole: "required",
            ContentListModel.ActionRole: "action",
            ContentListModel.ErrorRole: "error",
            ContentListModel.RepoUrlRole: "repoUrl",
        }
        attr = _roles.get(int(role))
        return row.get(attr) if attr else None

    # ── filter ────────────────────────────────────────────────────────

    def _matches(self, row) -> bool:
        mode = self._filter_mode
        if mode == "installed" and not row["installed"]:
            return False
        if mode == "updates" and row["action"] != "update":
            return False
        needle = self._filter_text.strip().lower()
        if needle and needle not in (
            f"{row['name']}\n{row['description']}".lower()
        ):
            return False
        return True

    def _refilter(self):
        self.beginResetModel()
        try:
            self._visible = [r for r in self._rows if self._matches(r)]
        finally:
            self.endResetModel()
        self.changed.emit()

    @Slot(str)
    def setFilterText(self, text: str):
        if text != self._filter_text:
            self._filter_text = text
            self._refilter()

    @Slot(str)
    def setFilterMode(self, mode: str):
        if mode in ("all", "installed", "updates"):
            if mode != self._filter_mode:
                self._filter_mode = mode
                self._refilter()

    # ── snapshot + chrome ─────────────────────────────────────────────

    def set_snapshot_provider(self, provider):
        """Zero-arg callable → (rows, updates, pending, errors, essential)."""
        self._snapshot_provider = provider

    @Slot()
    def refresh(self):
        """Re-pull rows + chrome from the provider (loaded events)."""
        if self._snapshot_provider is None:
            return
        rows, updates, pending, errors, essential = self._snapshot_provider()
        self.set_snapshot(rows, updates, pending, errors, essential)

    def set_snapshot(
        self,
        rows,
        updates_count=0,
        has_pending=False,
        has_errors=False,
        essential_pending=False,
    ):
        self._rows = list(rows or [])
        self._updates_count = int(updates_count or 0)
        self._apply_visible = bool(has_pending or has_errors)
        self._essential_pending = bool(essential_pending)
        self._refilter()
        self.chromeChanged.emit()

    def set_running(self, running: bool):
        """Busy chrome while an apply/install operation is in flight."""
        running = bool(running)
        if running != self._busy:
            self._busy = running
            self.chromeChanged.emit()

    # ── properties ────────────────────────────────────────────────────

    def _get_updates_count(self) -> int:
        return self._updates_count

    updatesCount = Property(int, _get_updates_count, notify=chromeChanged)

    def _get_apply_visible(self) -> bool:
        return self._apply_visible

    applyVisible = Property(bool, _get_apply_visible, notify=chromeChanged)

    def _get_essential_enabled(self) -> bool:
        return self._essential_pending and not self._busy

    essentialEnabled = Property(
        bool, _get_essential_enabled, notify=chromeChanged
    )

    def _get_busy(self) -> bool:
        return self._busy

    busy = Property(bool, _get_busy, notify=chromeChanged)

    def _get_empty_text(self) -> str:
        return self._empty_text

    emptyText = Property(str, _get_empty_text, constant=True)

    def _get_empty_visible(self) -> bool:
        return not self._visible

    emptyVisible = Property(bool, _get_empty_visible, notify=changed)

    def _get_filter_mode(self) -> str:
        return self._filter_mode

    filterMode = Property(str, _get_filter_mode, notify=changed)

    # ── actions (controller callbacks, test seams) ────────────────────

    def set_handlers(
        self,
        *,
        toggle=None,
        action=None,
        apply=None,
        essential=None,
        reload=None,
    ):
        self._on_toggle = toggle
        self._on_action = action
        self._on_apply = apply
        self._on_essential = essential
        self._on_reload = reload

    def _row(self, eid):
        for row in self._rows:
            if row["eid"] == eid:
                return row
        return None

    @Slot(str, bool)
    def toggleEntry(self, eid: str, checked: bool):
        """Checkbox flip — dropped when it echoes current state (QML
        re-fires onCheckedChanged for programmatic refreshes)."""
        row = self._row(eid)
        if row is None or bool(checked) == bool(row["enabled"]):
            return
        if self._on_toggle is not None:
            self._on_toggle(eid, bool(checked))

    @Slot(str)
    def actOn(self, eid: str):
        if self._on_action is not None and self._on_action(eid):
            self.set_running(True)

    @Slot()
    def applyAll(self):
        if self._on_apply is not None and self._on_apply():
            self.set_running(True)

    @Slot()
    def installEssential(self):
        if self._on_essential is not None:
            if self._on_essential():
                self.set_running(True)
            else:
                self.chromeChanged.emit()

    @Slot()
    def reloadCatalog(self):
        if self._on_reload is not None:
            self._on_reload()

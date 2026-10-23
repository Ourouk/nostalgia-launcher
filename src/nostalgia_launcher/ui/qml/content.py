"""Shared manage-list model for the MODS/ASSETS/ADDONS QML tabs.

One catalog-entry row (checkbox, name, repo link, description), a banner
with search, an empty state, and an Uninstall footer. ``build_rows``
flattens a controller registry + state into plain role dicts so this
module never imports controllers; the per-kind wiring
(registry/state/action callbacks) lives in `ui.qml.app`.
"""

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    Qt,
    Signal,
    Slot,
)


def is_server_managed(entry: dict) -> bool:
    """Whether a registry entry is server-managed (standard update owns it).

    Mirrors ``services.manifest.is_server_managed`` without importing
    services (this module must stay importable without Qt *and* without
    pulling the manifest engine into the UI layer): manifest-routed
    bundled entries carry a ``manifest-`` id prefix.
    """
    eid = entry.get("id", "") if isinstance(entry, dict) else ""
    return isinstance(eid, str) and eid.startswith("manifest-")


def build_rows(entries, state) -> list:
    """Flatten registry + state into role dicts, sorted by name.

    The manage tab is a select-to-uninstall list: ``enabled`` is True
    only when the entry is staged for removal (the checkbox tick).
    Unticked rows render unchecked; ``staged`` mirrors the tick so the
    QML ● dot marks rows queued for uninstall. The stored
    ``pending.enabled`` keeps its worker meaning (False = uninstall),
    so a tick maps to ``pending.enabled is False``. Server-managed
    rows carry ``serverManaged: True`` — the QML delegate renders them
    locked (badge, disabled checkbox).
    """
    recs = getattr(state, "records", {}) or {}
    pending = getattr(state, "pending", {}) or {}
    rows = []
    for entry in sorted(entries or [], key=lambda e: e["name"].lower()):
        eid = entry["id"]
        rec = recs.get(eid)
        pend = pending.get(eid)
        pend_enabled = getattr(pend, "enabled", None)
        enabled = pend is not None and pend_enabled is False
        rows.append(
            {
                "eid": eid,
                "name": entry.get("name", eid),
                "description": (
                    entry.get("description", "")
                    + (
                        " [server — updated via UPDATE]"
                        if is_server_managed(entry)
                        else ""
                    )
                ).strip(),
                "repoUrl": entry.get("repo_url", ""),
                "installed": bool(rec.present) if rec is not None else False,
                "enabled": enabled,
                "serverManaged": is_server_managed(entry),
                "staged": enabled,
            }
        )
    return rows


class ContentListModel(QAbstractListModel):
    """Filterable manage list (one instance per tab: mods/assets/addons)."""

    EIDRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    DescriptionRole = Qt.ItemDataRole.UserRole + 3
    InstalledRole = Qt.ItemDataRole.UserRole + 4
    EnabledRole = Qt.ItemDataRole.UserRole + 5
    RepoUrlRole = Qt.ItemDataRole.UserRole + 6
    StagedRole = Qt.ItemDataRole.UserRole + 7
    ServerManagedRole = Qt.ItemDataRole.UserRole + 8

    changed = Signal()
    scanningChanged = Signal()
    chromeChanged = Signal()
    browseRequested = Signal()

    def __init__(self, empty_text="", parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._visible: list = []
        self._filter_text = ""
        self._updates_count = 0
        self._pending_count = 0
        self._busy = False
        self._loading = True
        self._scanning = True
        self._empty_text = empty_text
        self._snapshot_provider = None
        self._on_toggle = None
        self._on_apply = None
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
            ContentListModel.DescriptionRole: b"description",
            ContentListModel.InstalledRole: b"installed",
            ContentListModel.EnabledRole: b"enabled",
            ContentListModel.RepoUrlRole: b"repoUrl",
            ContentListModel.StagedRole: b"staged",
            ContentListModel.ServerManagedRole: b"serverManaged",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._visible[index.row()]
        _roles = {
            ContentListModel.EIDRole: "eid",
            ContentListModel.NameRole: "name",
            ContentListModel.DescriptionRole: "description",
            ContentListModel.InstalledRole: "installed",
            ContentListModel.EnabledRole: "enabled",
            ContentListModel.RepoUrlRole: "repoUrl",
            ContentListModel.StagedRole: "staged",
            ContentListModel.ServerManagedRole: "serverManaged",
        }
        attr = _roles.get(int(role))
        return row.get(attr) if attr else None

    # ── filter ────────────────────────────────────────────────────────

    def _matches(self, row) -> bool:
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

    # ── snapshot + chrome ─────────────────────────────────────────────

    def set_snapshot_provider(self, provider):
        """Zero-arg callable → (rows, updates, pending_count)."""
        self._snapshot_provider = provider

    @Slot()
    def refresh(self):
        """Re-pull rows + chrome from the provider (loaded events)."""
        if self._snapshot_provider is None:
            return
        (rows, updates, pending_count) = self._snapshot_provider()
        self.set_snapshot(rows, updates, pending_count)

    def set_snapshot(
        self,
        rows,
        updates_count=0,
        pending_count=0,
    ):
        self._rows = list(rows or [])
        self._updates_count = int(updates_count or 0)
        self._pending_count = int(pending_count or 0)
        if self._rows:
            # Data on screen: nothing left to wait for, even before the
            # first loaded event arrives.
            self._loading = False
        self._refilter()
        self.set_scanning(bool(self._loading))
        self.chromeChanged.emit()

    def set_running(self, running: bool):
        """Busy chrome while an apply/install operation is in flight."""
        running = bool(running)
        if running != self._busy:
            self._busy = running
            self.chromeChanged.emit()

    def set_scanning(self, scanning: bool):
        """Waiting-screen flag for reload/verify scans (distinct from
        apply `busy`: the list itself is stale, not just the footer)."""
        scanning = bool(scanning)
        if scanning != self._scanning:
            self._scanning = scanning
            self.scanningChanged.emit()

    # ── properties ────────────────────────────────────────────────────

    def _get_updates_count(self) -> int:
        return self._updates_count

    updatesCount = Property(int, _get_updates_count, notify=chromeChanged)

    def _get_pending_count(self) -> int:
        return self._pending_count

    pendingCount = Property(int, _get_pending_count, notify=chromeChanged)

    def _get_busy(self) -> bool:
        return self._busy

    busy = Property(bool, _get_busy, notify=chromeChanged)

    def _get_scanning(self) -> bool:
        return self._scanning

    scanning = Property(bool, _get_scanning, notify=scanningChanged)

    def _get_loading(self) -> bool:
        return self._loading

    loading = Property(bool, _get_loading, notify=chromeChanged)

    @Slot()
    def markLoaded(self):
        """First loaded event arrived: leave the waiting screens."""
        if self._loading:
            self._loading = False
            self.chromeChanged.emit()
        self.set_scanning(False)

    def _get_empty_text(self) -> str:
        return self._empty_text

    emptyText = Property(str, _get_empty_text, constant=True)

    def _get_empty_visible(self) -> bool:
        return not self._visible

    emptyVisible = Property(bool, _get_empty_visible, notify=changed)

    # ── actions (controller callbacks, test seams) ────────────────────

    def set_handlers(
        self,
        *,
        toggle=None,
        apply=None,
        reload=None,
    ):
        self._on_toggle = toggle
        self._on_apply = apply
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

    @Slot()
    def applyAll(self):
        if self._on_apply is not None and self._on_apply():
            self.set_running(True)

    @Slot()
    def reloadCatalog(self):
        if self._on_reload is not None and self._on_reload():
            self.set_scanning(True)

    @Slot()
    def requestBrowse(self):
        """Empty-state Browse button (app.py opens the dialog)."""
        self.browseRequested.emit()

"""Shared content-list model for the MODS/ASSETS QML tabs.

Implements the shared MODS/ASSETS list: one catalog-entry row (checkbox,
name/version, repo link, retry/update action, description, error),
a banner with search + All/Installed/Updates chips, an empty state, and an
Apply footer. `build_rows` flattens a controller registry +
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


def is_server_managed(entry: dict) -> bool:
    """Whether a registry entry is server-managed (standard update owns it).

    Mirrors ``services.manifest.is_server_managed`` without importing
    services (this module must stay importable without Qt *and* without
    pulling the manifest engine into the UI layer): manifest-routed
    bundled entries carry a ``manifest-`` id prefix.
    """
    eid = entry.get("id", "") if isinstance(entry, dict) else ""
    return isinstance(eid, str) and eid.startswith("manifest-")


def build_rows(entries, state, *, version_of, action_for):
    """Flatten registry + state into role dicts, sorted by name.

    The manage tab is a select-to-uninstall list: ``enabled`` is True
    only when the entry is staged for removal (the checkbox tick).
    Unticked rows render unchecked; ``staged`` mirrors the tick so the
    QML ● dot marks rows queued for uninstall. The stored
    ``pending.enabled`` keeps its worker meaning (False = uninstall),
    so a tick maps to ``pending.enabled is False``.
    ``version_of``, ``action_for(eid) -> "retry" | "update" | None``
    come from the tab glue. Server-managed rows carry
    ``serverManaged: True`` — the QML delegate renders them locked
    (badge, disabled checkbox, no action button).
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
                "version": version_of(entry, rec, state),
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
                "action": action_for(eid) or "",
                "error": (rec.error if rec is not None else None) or "",
                "staged": enabled,
            }
        )
    return rows


def unknown_sections(unknown) -> list:
    """MODS extra: dlls.txt entries no catalog mod claims."""
    if not unknown:
        return []
    return [
        {
            "title": "Detected (not in catalog)",
            "color": "dim",
            "rows": [
                {"name": name, "meta": "", "action": "Remove", "confirm": ""}
                for name in unknown
            ],
        }
    ]


def managed_names(registry) -> dict:
    """dest basename (lowercased) → display name for launcher assets."""
    index = {}
    for entry in registry or []:
        if not isinstance(entry, dict):
            continue
        dest = entry.get("dest")
        if not dest:
            continue
        base = str(dest).replace("\\", "/").rsplit("/", 1)[-1]
        index[base.lower()] = (
            f"{entry.get('name', entry.get('id', '?'))} (launcher asset)"
        )
    return index


def scan_extras(scan, names: dict, client_dir: str) -> tuple:
    """ASSETS extras: (headline, sections) for the Data/ scan block.

    Every MPQ present is treated as bundled with the game — stock,
    launcher-managed and anything else alike — so rows carry no
    action. There is deliberately no foreign/third-party distinction."""
    from ...services.mpq import human_size

    if not client_dir:
        return (
            "Set the game folder in Settings to scan its Data/ folder.",
            [],
        )
    rows = [
        {"name": rel, "meta": "stock", "action": "", "confirm": ""}
        for rel in scan.get("stock") or []
    ]
    for info in scan.get("custom_managed") or []:
        base = info["path"].rsplit("/", 1)[-1]
        note = names.get(base.lower(), "")
        meta = "  ·  ".join(
            x for x in (human_size(info.get("size")), note) if x
        )
        rows.append(
            {"name": info["path"], "meta": meta, "action": "", "confirm": ""}
        )
    for info in scan.get("custom_foreign") or []:
        rows.append(
            {
                "name": info["path"],
                "meta": human_size(info.get("size")),
                "action": "",
                "confirm": "",
            }
        )
    rows.sort(key=lambda r: r["name"].lower())
    headline = (
        f"Data/ scan ({scan['version']}): {len(rows)} bundled archive(s)."
    )
    sections = (
        [{"title": "Bundled", "color": "dim", "rows": rows}] if rows else []
    )
    return headline, sections


def assets_extras(ctrl) -> tuple:
    """ASSETS extras block for the snapshot provider.

    Returns ``(sections, headline)`` — the ``extras_of`` convention the
    snapshot unpacks (``scan_extras`` itself returns the reverse order).
    The scan always targets the server-pinned client version: offering
    a choice would just mislabel stock archives.
    """
    from ...core import launcher as _launcher
    from ...services import mpq

    version = _launcher.client_version() or mpq.SUPPORTED_VERSIONS[0]
    scan = ctrl.data_scan(version)
    headline, sections = scan_extras(
        scan, managed_names(ctrl.registry), ctrl.client_dir()
    )
    return sections, headline


class ContentListModel(QAbstractListModel):
    """Filterable catalog list (one instance per tab: mods / assets)."""

    EIDRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    VersionRole = Qt.ItemDataRole.UserRole + 3
    DescriptionRole = Qt.ItemDataRole.UserRole + 4
    InstalledRole = Qt.ItemDataRole.UserRole + 5
    EnabledRole = Qt.ItemDataRole.UserRole + 6
    ActionRole = Qt.ItemDataRole.UserRole + 7
    ErrorRole = Qt.ItemDataRole.UserRole + 8
    RepoUrlRole = Qt.ItemDataRole.UserRole + 9
    StagedRole = Qt.ItemDataRole.UserRole + 10
    ServerManagedRole = Qt.ItemDataRole.UserRole + 11

    changed = Signal()
    scanningChanged = Signal()
    chromeChanged = Signal()
    browseRequested = Signal()

    def __init__(self, empty_text="", extras_on_top=False, parent=None):
        super().__init__(parent)
        self._rows: list = []
        self._visible: list = []
        self._filter_text = ""
        self._filter_mode = "all"
        self._updates_count = 0
        self._apply_visible = False
        self._pending_count = 0
        self._busy = False
        self._loading = True
        self._scanning = True
        self._empty_text = empty_text
        self._extras_on_top = bool(extras_on_top)
        self._extra_headline = ""
        self._extra_sections: list = []
        self._extra_confirm = ""
        self._pending_extra = None
        self._snapshot_provider = None
        self._on_toggle = None
        self._on_action = None
        self._on_apply = None
        self._on_reload = None
        self._on_extra = None

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
            ContentListModel.ActionRole: b"action",
            ContentListModel.ErrorRole: b"error",
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
            ContentListModel.VersionRole: "version",
            ContentListModel.DescriptionRole: "description",
            ContentListModel.InstalledRole: "installed",
            ContentListModel.EnabledRole: "enabled",
            ContentListModel.ActionRole: "action",
            ContentListModel.ErrorRole: "error",
            ContentListModel.RepoUrlRole: "repoUrl",
            ContentListModel.StagedRole: "staged",
            ContentListModel.ServerManagedRole: "serverManaged",
        }
        attr = _roles.get(int(role))
        return row.get(attr) if attr else None

    # ── filter ────────────────────────────────────────────────────────

    def _matches(self, row) -> bool:
        mode = self._filter_mode
        if mode == "installed" and not row["installed"]:
            return False
        if mode == "updates" and row["action"] not in ("update", "retry"):
            return False
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
        """Zero-arg callable → (rows, updates, pending, errors)."""
        self._snapshot_provider = provider

    @Slot()
    def refresh(self):
        """Re-pull rows + chrome from the provider (loaded events)."""
        if self._snapshot_provider is None:
            return
        (
            rows,
            updates,
            pending,
            errors,
            extras,
            headline,
            pending_count,
        ) = self._snapshot_provider()
        self.set_snapshot(
            rows,
            updates,
            pending,
            errors,
            extras=extras,
            headline=headline,
            pending_count=pending_count,
        )

    def set_snapshot(
        self,
        rows,
        updates_count=0,
        has_pending=False,
        has_errors=False,
        extras=None,
        headline="",
        pending_count=0,
    ):
        self._rows = list(rows or [])
        self._updates_count = int(updates_count or 0)
        self._apply_visible = bool(has_pending or has_errors)
        self._pending_count = int(pending_count or 0)
        if self._rows:
            # Data on screen: nothing left to wait for, even before the
            # first loaded event arrives.
            self._loading = False
        self._extra_sections = list(extras or [])
        self._extra_headline = str(headline or "")
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

    def _get_apply_visible(self) -> bool:
        return self._apply_visible

    applyVisible = Property(bool, _get_apply_visible, notify=chromeChanged)

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

    def _get_filter_mode(self) -> str:
        return self._filter_mode

    filterMode = Property(str, _get_filter_mode, notify=changed)

    def _get_extras_on_top(self) -> bool:
        return self._extras_on_top

    extrasOnTop = Property(bool, _get_extras_on_top, constant=True)

    def _get_extra_headline(self) -> str:
        return self._extra_headline

    extraHeadline = Property(str, _get_extra_headline, notify=chromeChanged)

    def _get_extra_sections(self) -> list:
        return list(self._extra_sections)

    extraSections = Property(
        "QVariantList", _get_extra_sections, notify=chromeChanged
    )

    def _get_extra_confirm(self) -> str:
        return self._extra_confirm

    extraConfirmText = Property(str, _get_extra_confirm, notify=chromeChanged)

    # ── actions (controller callbacks, test seams) ────────────────────

    def set_handlers(
        self,
        *,
        toggle=None,
        action=None,
        apply=None,
        reload=None,
        extra=None,
    ):
        self._on_toggle = toggle
        self._on_action = action
        self._on_apply = apply
        self._on_reload = reload
        self._on_extra = extra

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
    def reloadCatalog(self):
        if self._on_reload is not None and self._on_reload():
            self.set_scanning(True)

    @Slot()
    def requestBrowse(self):
        """Empty-state Browse button (app.py opens the dialog)."""
        self.browseRequested.emit()

    @Slot(str, str)
    def requestExtra(self, section: str, name: str):
        """Extra-row action: confirm first when the row demands it."""
        row = self._extra_row(section, name)
        if row is None:
            return
        if row.get("confirm"):
            self._pending_extra = (section, name)
            self._extra_confirm = str(row["confirm"])
            self.chromeChanged.emit()
            return
        if self._on_extra is not None:
            self._on_extra(section, name)

    @Slot()
    def confirmExtra(self):
        pending, self._pending_extra = self._pending_extra, None
        self._extra_confirm = ""
        self.chromeChanged.emit()
        if pending is not None and self._on_extra is not None:
            self._on_extra(*pending)

    @Slot()
    def cancelExtra(self):
        self._pending_extra = None
        self._extra_confirm = ""
        self.chromeChanged.emit()

    def _extra_row(self, section: str, name: str):
        for sec in self._extra_sections:
            if sec.get("title") != section:
                continue
            for row in sec.get("rows", []):
                if row.get("name") == name:
                    return row
        return None

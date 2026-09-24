"""ADDONS tab model for QML (Phase 5b).

Implements the ADDONS tab: NEED UPDATE / INSTALLED / AVAILABLE
sections (collapsible), rows (checkbox, ★, stripped title, status action,
repo link, description, error), search, catalog age, ★-recommended/Apply
footer. Title color escapes are stripped (per-segment colors deferred).
The module never imports controllers — the tab glue in `ui.qml.app`
supplies state snapshots and action callbacks.
"""

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    Qt,
    Signal,
    Slot,
)

from ...core.helpers import strip_wow_colors

INTERFACE_BY_VERSION = {
    "1.12.1": "11200",
    "2.4.3": "20400",
    "3.3.5a": "30300",
}
_INTERFACE_FALLBACK = INTERFACE_BY_VERSION["1.12.1"]


def expected_interface(client_version: str) -> str:
    """Declared Interface number for a profile client version."""
    return INTERFACE_BY_VERSION.get(client_version or "", _INTERFACE_FALLBACK)


def matches(folder, title, needle: str) -> bool:
    """Space-insensitive filter both ways (widget `_matches` parity)."""
    flt = (needle or "").strip().lower()
    if not flt:
        return True
    hay = f"{folder} {title}".lower()
    return flt in hay or flt.replace(" ", "") in hay.replace(" ", "")


def row_warning(rec, installed: bool, installed_names, expected: str) -> str:
    """First row warning, if any (widget AddonRow parity)."""
    toc = rec.toc or {}
    if toc.get("Interface") and toc["Interface"] != expected:
        return f"Made for client {toc['Interface']} (profile: {expected})"
    if installed and rec.folder != "pfUI":
        deps = [
            d.strip()
            for d in (toc.get("Dependencies") or "")
            .replace(";", ",")
            .split(",")
            if d.strip()
        ]
        missing = [d for d in deps if d not in installed_names]
        if missing:
            return "Missing deps: " + ", ".join(missing)
    return ""


def row_status(rec, installed: bool, warning: str):
    """(kind, text) for the row status slot (widget parity)."""
    if rec.status == "downloading":
        return ("downloading", "downloading…")
    if rec.status == "unknown":
        if rec.git:
            return ("retry", "⟳ Couldn't check")
        return ("note", "Not tracked")
    if rec.status == "invalid" or rec.error:
        return ("error", "⛔ Addon error")
    if rec.status == "outOfDate" and installed:
        return ("update", "Update")
    if warning:
        return ("warning", f"⚠ {warning}")
    if rec.status == "upToDate":
        return ("none", "")
    return ("note", "Not versioned")


def build_items(state, *, recommended, expected, needle=""):
    """Flat section/row items for the ListView (widget `_render` parity)."""
    addons = getattr(state, "addons", {}) or {}
    available = getattr(state, "available", []) or []
    installed_names = set(addons)
    installed_gits = {}
    for folder, rec in addons.items():
        if isinstance(folder, str) and folder.strip().casefold():
            installed_gits[folder.strip().casefold()] = rec.git

    def visible_available(rec):
        key = (rec.folder or "").strip().casefold()
        if not key or key not in installed_gits:
            return True
        if rec.git and installed_gits[key]:
            return installed_gits[key] != rec.git
        return False

    installed = [
        r
        for r in addons.values()
        if matches(
            r.folder,
            strip_wow_colors((r.toc or {}).get("Title") or ""),
            needle,
        )
    ]
    installed.sort(key=lambda r: r.folder.lower())
    avail = [
        a
        for a in available
        if visible_available(a)
        and matches(
            a.folder,
            strip_wow_colors((a.toc or {}).get("Title") or ""),
            needle,
        )
    ]
    avail.sort(key=lambda a: (a.folder not in recommended, a.folder.lower()))

    need_update = [r for r in installed if r.status == "outOfDate"]
    up_to_date = [r for r in installed if r.status != "outOfDate"]
    sections = [("INSTALLED", up_to_date)]
    if need_update:
        sections.insert(0, ("NEED UPDATE", need_update))
    sections.append(("AVAILABLE", avail))

    sections_open = getattr(state, "sections_open", {}) or {}
    items = []
    for title, rows in sections:
        is_open = sections_open.get(title, True)
        count = len(rows)
        if is_open and not rows:
            empty = (
                "Verifying…"
                if getattr(state, "state", "") == "verifying"
                else "Nothing here."
            )
        else:
            empty = ""
        items.append(
            {
                "kind": "section",
                "title": title,
                "count": count,
                "open": is_open,
                "empty": empty,
            }
        )
        if not is_open:
            continue
        for rec in rows:
            installed = rec.folder in installed_names
            warning = row_warning(rec, installed, installed_names, expected)
            kind, text = row_status(rec, installed, warning)
            toc = rec.toc or {}
            git = rec.git or ""
            items.append(
                {
                    "kind": "row",
                    "folder": rec.folder,
                    "title": strip_wow_colors(toc.get("Title") or rec.folder),
                    "checked": installed,
                    "recommended": rec.folder in recommended,
                    "statusKind": kind,
                    "statusText": text,
                    "repoUrl": (git[:-4] if git.endswith(".git") else git),
                    "description": strip_wow_colors(
                        toc.get("Notes") or rec.description or ""
                    ),
                    "error": (
                        "" if rec.status == "unknown" else (rec.error or "")
                    ),
                }
            )
    return items


class AddonsModel(QAbstractListModel):
    """ADDONS tab: section/row items + footer chrome."""

    KindRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    CountRole = Qt.ItemDataRole.UserRole + 3
    OpenRole = Qt.ItemDataRole.UserRole + 4
    EmptyRole = Qt.ItemDataRole.UserRole + 5
    FolderRole = Qt.ItemDataRole.UserRole + 6
    RowTitleRole = Qt.ItemDataRole.UserRole + 7
    CheckedRole = Qt.ItemDataRole.UserRole + 8
    RecommendedRole = Qt.ItemDataRole.UserRole + 9
    StatusKindRole = Qt.ItemDataRole.UserRole + 10
    StatusTextRole = Qt.ItemDataRole.UserRole + 11
    RepoUrlRole = Qt.ItemDataRole.UserRole + 12
    DescriptionRole = Qt.ItemDataRole.UserRole + 13
    ErrorRole = Qt.ItemDataRole.UserRole + 14

    changed = Signal()
    chromeChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._filter = ""
        self._loading = True
        self._updates_count = 0
        self._apply_visible = False
        self._recommended_pending = False
        self._busy = False
        self._footer_text = ""
        self._footer_color = ""
        self._footer_clickable = False
        self._age_text = ""
        self._snapshot_provider = None
        self._handlers: dict = {}

    # ── model ─────────────────────────────────────────────────────────

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._items)

    def roleNames(self) -> dict:
        return {
            AddonsModel.KindRole: b"kind",
            AddonsModel.TitleRole: b"sectionTitle",
            AddonsModel.CountRole: b"sectionCount",
            AddonsModel.OpenRole: b"sectionOpen",
            AddonsModel.EmptyRole: b"sectionEmpty",
            AddonsModel.FolderRole: b"folder",
            AddonsModel.RowTitleRole: b"rowTitle",
            AddonsModel.CheckedRole: b"checked",
            AddonsModel.RecommendedRole: b"recommended",
            AddonsModel.StatusKindRole: b"statusKind",
            AddonsModel.StatusTextRole: b"statusText",
            AddonsModel.RepoUrlRole: b"repoUrl",
            AddonsModel.DescriptionRole: b"description",
            AddonsModel.ErrorRole: b"error",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        _roles = {
            AddonsModel.KindRole: "kind",
            AddonsModel.TitleRole: "title",
            AddonsModel.CountRole: "count",
            AddonsModel.OpenRole: "open",
            AddonsModel.EmptyRole: "empty",
            AddonsModel.FolderRole: "folder",
            AddonsModel.RowTitleRole: "title",
            AddonsModel.CheckedRole: "checked",
            AddonsModel.RecommendedRole: "recommended",
            AddonsModel.StatusKindRole: "statusKind",
            AddonsModel.StatusTextRole: "statusText",
            AddonsModel.RepoUrlRole: "repoUrl",
            AddonsModel.DescriptionRole: "description",
            AddonsModel.ErrorRole: "error",
        }
        attr = _roles.get(int(role))
        return item.get(attr) if attr else None

    # ── snapshot + chrome ─────────────────────────────────────────────

    def set_snapshot_provider(self, provider):
        """Zero-arg callable → snapshot dict (see `refresh`)."""
        self._snapshot_provider = provider

    @Slot()
    def refresh(self):
        if self._snapshot_provider is None:
            return
        self.set_snapshot(self._snapshot_provider())

    def set_snapshot(self, snap: dict):
        self.beginResetModel()
        try:
            self._items = list(snap.get("items", []))
            if any(i.get("kind") == "row" for i in self._items):
                # Rows on screen: nothing left to wait for.
                self._loading = False
            self._updates_count = int(snap.get("updates_count", 0))
            self._apply_visible = bool(snap.get("apply_visible", False))
            self._recommended_pending = bool(
                snap.get("recommended_enabled", False)
            )
            self._footer_text = str(snap.get("footer_text", ""))
            self._footer_color = str(snap.get("footer_color", ""))
            self._footer_clickable = bool(snap.get("footer_clickable", False))
            self._age_text = str(snap.get("age_text", ""))
        finally:
            self.endResetModel()
        self.changed.emit()
        self.chromeChanged.emit()

    def set_running(self, running: bool):
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

    def _get_recommended_enabled(self) -> bool:
        return self._recommended_pending and not self._busy

    recommendedEnabled = Property(
        bool, _get_recommended_enabled, notify=chromeChanged
    )

    def _get_busy(self) -> bool:
        return self._busy

    busy = Property(bool, _get_busy, notify=chromeChanged)

    def _get_loading(self) -> bool:
        return self._loading

    loading = Property(bool, _get_loading, notify=chromeChanged)

    @Slot()
    def markLoaded(self):
        """First loaded event arrived: leave the waiting screen."""
        if self._loading:
            self._loading = False
            self.chromeChanged.emit()

    def _get_footer_text(self) -> str:
        return self._footer_text

    footerText = Property(str, _get_footer_text, notify=chromeChanged)

    def _get_footer_color(self) -> str:
        return self._footer_color

    footerColor = Property(str, _get_footer_color, notify=chromeChanged)

    def _get_footer_clickable(self) -> bool:
        return self._footer_clickable

    footerClickable = Property(
        bool, _get_footer_clickable, notify=chromeChanged
    )

    def _get_age_text(self) -> str:
        return self._age_text

    ageText = Property(str, _get_age_text, notify=chromeChanged)

    # ── actions ───────────────────────────────────────────────────────

    def set_handlers(self, **handlers):
        self._handlers = dict(handlers)

    @Slot(str)
    def toggleSection(self, title: str):
        handler = self._handlers.get("section")
        if handler is not None:
            handler(title)

    @Slot(str)
    def setFilterText(self, text: str):
        if text != self._filter:
            self._filter = text
            self.refresh()

    def current_filter(self) -> str:
        """Active search text (read by the snapshot provider)."""
        return self._filter

    @Slot(str, bool)
    def toggleEntry(self, folder: str, checked: bool):
        handler = self._handlers.get("toggle")
        if handler is not None:
            handler(folder, bool(checked))

    @Slot(str)
    def updateOne(self, folder: str):
        if self._call("update_one", folder):
            self.set_running(True)

    @Slot()
    def updateAll(self):
        if self._call("update_all"):
            self.set_running(True)

    @Slot()
    def applyAll(self):
        if self._call("apply"):
            self.set_running(True)

    @Slot()
    def installRecommended(self):
        if self._call("recommended"):
            self.set_running(True)

    @Slot()
    def checkForUpdates(self):
        self._call("check")

    def _call(self, name, *args):
        handler = self._handlers.get(name)
        if handler is None:
            return False
        return bool(handler(*args))

"""Browse dialog model: catalog search + checkbox bulk install.

Rows are checkbox-selected; Install fires the tab's existing apply-one
path for each selected entry in turn (the backend refuses concurrent
applies, so installs chain as each operation settles). Pure view-model —
controllers are injected callbacks.
"""

from PySide6.QtCore import Property, QAbstractListModel, Qt, Signal, Slot


class BrowseModel(QAbstractListModel):
    """Not-installed catalog entries for one tab (mods / assets / addons)."""

    IdRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    VersionRole = Qt.ItemDataRole.UserRole + 3
    DescriptionRole = Qt.ItemDataRole.UserRole + 4
    RepoUrlRole = Qt.ItemDataRole.UserRole + 5
    SelectedRole = Qt.ItemDataRole.UserRole + 6

    changed = Signal()
    chromeChanged = Signal()
    selectionChanged = Signal()

    def __init__(self, empty_text="Nothing available.", parent=None):
        super().__init__(parent)
        self._entries: list = []
        self._visible: list = []
        self._selected: set = set()
        self._queue: list = []
        self._filter_text = ""
        self._busy = False
        self._empty_text = empty_text
        self._on_install = None

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._visible)

    def roleNames(self) -> dict:
        return {
            BrowseModel.IdRole: b"entryId",
            BrowseModel.NameRole: b"entryName",
            BrowseModel.VersionRole: b"entryVersion",
            BrowseModel.DescriptionRole: b"entryDescription",
            BrowseModel.RepoUrlRole: b"entryRepoUrl",
            BrowseModel.SelectedRole: b"entrySelected",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._visible[index.row()]
        _roles = {
            BrowseModel.IdRole: "id",
            BrowseModel.NameRole: "name",
            BrowseModel.VersionRole: "version",
            BrowseModel.DescriptionRole: "description",
            BrowseModel.RepoUrlRole: "repo_url",
        }
        attr = _roles.get(int(role))
        if attr is not None:
            return row.get(attr)
        if int(role) == BrowseModel.SelectedRole:
            return row.get("id") in self._selected
        return None

    def set_entries(self, entries) -> None:
        self.beginResetModel()
        try:
            self._entries = list(entries or [])
            self._refilter_locked()
        finally:
            self.endResetModel()
        self.changed.emit()
        self.chromeChanged.emit()

    def _refilter_locked(self) -> None:
        needle = self._filter_text.strip().lower()
        self._visible = [
            e
            for e in self._entries
            if not needle
            or needle
            in f"{e.get('name', '')}\n{e.get('description', '')}".lower()
        ]

    @Slot(str)
    def setFilterText(self, text: str):
        if text != self._filter_text:
            self._filter_text = text
            self.beginResetModel()
            try:
                self._refilter_locked()
            finally:
                self.endResetModel()
            self.changed.emit()

    def set_install_handler(self, callback) -> None:
        """Install click (test seam): eid -> bool (True = started)."""
        self._on_install = callback

    @Slot(str)
    def install(self, eid: str):
        if self._on_install is not None and self._on_install(eid):
            self.set_running(True)

    def set_running(self, running: bool):
        running = bool(running)
        if not running and self._queue:
            # An install just settled with more queued: chain the next
            # one instead of dropping the busy chrome.
            self._pump_next()
            return
        if running != self._busy:
            self._busy = running
            self.chromeChanged.emit()

    @Slot(str, bool)
    def setSelected(self, eid: str, checked: bool):
        """Model-side echo guard: a programmatic refresh re-firing the
        checkbox is dropped when nothing changed."""
        checked = bool(checked)
        if (eid in self._selected) == checked:
            return
        if checked:
            self._selected.add(eid)
        else:
            self._selected.discard(eid)
        for row, entry in enumerate(self._visible):
            if entry.get("id") == eid:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [BrowseModel.SelectedRole])
        self.selectionChanged.emit()

    def _get_selected_count(self) -> int:
        return len(self._selected)

    selectedCount = Property(int, _get_selected_count, notify=selectionChanged)

    @Slot()
    def installSelected(self):
        """Install every selected entry in turn, chained on settle."""
        wanted = set(self._selected)
        ids = [e.get("id") for e in self._visible if e.get("id") in wanted]
        if self._selected:
            self._selected.clear()
            self.selectionChanged.emit()
            if self._visible:
                top = self.index(0, 0)
                bottom = self.index(len(self._visible) - 1, 0)
                self.dataChanged.emit(top, bottom, [BrowseModel.SelectedRole])
        self._queue = ids
        self._pump_next()

    def _pump_next(self) -> None:
        """Start the next queued install; drop busy when drained."""
        while self._queue:
            eid = self._queue.pop(0)
            started = False
            if self._on_install is not None:
                try:
                    started = bool(self._on_install(eid))
                except Exception:
                    started = False
            if started:
                if not self._busy:
                    self._busy = True
                    self.chromeChanged.emit()
                return
        if self._busy:
            self._busy = False
            self.chromeChanged.emit()

    def _get_busy(self) -> bool:
        return self._busy

    busy = Property(bool, _get_busy, notify=chromeChanged)

    def _get_empty_visible(self) -> bool:
        return not self._visible

    emptyVisible = Property(bool, _get_empty_visible, notify=changed)

    def _get_empty_text(self) -> str:
        return self._empty_text

    emptyText = Property(str, _get_empty_text, constant=True)


def content_browse_entries(entries, state) -> list:
    """Registry entries not yet installed (mods/assets Browse page).

    Installed = present on disk. Records that exist but are not present
    (failed installs) stay visible so a failed entry can never strand
    itself out of sight.
    """
    recs = getattr(state, "records", {}) or {}
    present = {
        eid for eid, rec in recs.items() if getattr(rec, "present", False)
    }
    return [e for e in entries or [] if e["id"] not in present]


def addon_browse_entries(state) -> list:
    """Not-installed addon catalog (ADDONS Browse page).

    `state.available` is already shadow-suppressed against installed
    gits by the controller, so it passes through untouched.
    """
    return list(getattr(state, "available", []) or [])


def addon_tab_entries(state) -> list:
    """Installed addons as registry-like dicts for the shared manage tab.

    Maps ``state.addons`` (folder → AddonState) to ``{id, name,
    description, repo_url}`` dicts the shared ``build_rows`` consumes.
    Titles/descriptions strip WoW color escapes (Browse mapping parity);
    repo URL drops a trailing ``.git`` for display. Installed-only by
    construction: only ``state.addons`` keys appear."""
    from ...core.helpers import strip_wow_colors

    entries = []
    for folder, rec in (getattr(state, "addons", {}) or {}).items():
        toc = getattr(rec, "toc", None) or {}
        title = strip_wow_colors(toc.get("Title") or "") or folder
        notes = strip_wow_colors(
            toc.get("Notes") or getattr(rec, "description", None) or ""
        )
        git = getattr(rec, "git", None) or ""
        if git.endswith(".git"):
            git = git[: -len(".git")]
        entries.append(
            {
                "id": folder,
                "name": title,
                "description": notes,
                "repo_url": git,
            }
        )
    return entries


def addon_tab_rows(state) -> list:
    """Manage-tab entries for ADDONS: installed folders plus rows
    staged for uninstall (pending.enabled is False)."""
    present = set(getattr(state, "addons", {}) or {})
    staged = {
        folder
        for folder, pend in (getattr(state, "pending", {}) or {}).items()
        if getattr(pend, "enabled", pend) is False
    }
    return [e for e in addon_tab_entries(state) if e["id"] in present | staged]


def tab_rows(entries, state) -> list:
    """Registry entries the manage-tab may show: installed entries
    (present on disk, or staged for uninstall) — never pure catalog
    entries. Installs happen in the Browse dialog; this tab only
    lists what can be uninstalled."""
    recs = getattr(state, "records", {}) or {}
    pending = getattr(state, "pending", {}) or {}
    staged = set()
    for eid, pend in pending.items():
        enabled = getattr(pend, "enabled", pend)
        if enabled is False:
            staged.add(eid)
    known = {
        eid for eid, rec in recs.items() if getattr(rec, "present", False)
    } | staged
    return [e for e in entries or [] if e["id"] in known]

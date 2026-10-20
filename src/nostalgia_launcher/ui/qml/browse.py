"""Browse dialog model: catalog search + one-click install.

Unlike ContentListModel there is no staging/pending/Apply here:
clicking Install installs immediately through the tab's existing
apply-one path. Pure view-model — controllers are injected callbacks.
"""

from PySide6.QtCore import Property, QAbstractListModel, Qt, Signal, Slot


class BrowseModel(QAbstractListModel):
    """Not-installed catalog entries for one tab (mods / assets / addons)."""

    IdRole = Qt.ItemDataRole.UserRole + 1
    NameRole = Qt.ItemDataRole.UserRole + 2
    VersionRole = Qt.ItemDataRole.UserRole + 3
    DescriptionRole = Qt.ItemDataRole.UserRole + 4
    RepoUrlRole = Qt.ItemDataRole.UserRole + 5

    changed = Signal()
    chromeChanged = Signal()

    def __init__(self, empty_text="Nothing available.", parent=None):
        super().__init__(parent)
        self._entries: list = []
        self._visible: list = []
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
        return row.get(attr) if attr else None

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
        if running != self._busy:
            self._busy = running
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

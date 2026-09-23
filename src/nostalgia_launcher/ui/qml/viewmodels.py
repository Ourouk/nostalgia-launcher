"""QML view-models — toolkit-agnostic state as QObjects.

`LauncherState` mirrors the scalar signals of `qt.bridge.ControllerBridge`
(`statusChanged`, `progressChanged`) as Qt properties so QML binds to them
declaratively. `ThemeBridge` exposes the `qt.theme.Palette` color slots as
a string map (`appTheme.colors["C_GOLD"]`) plus the `themed` flag, keeping
the themed/native rule in one place: unmigrated Python decides the palette,
QML only reads it. (The context property is named `appTheme` because
`theme` collides with a QtQuick.Controls internal.)
themed/native rule in one place: unmigrated Python decides the palette,
QML only reads it.

Both classes stay importable without a running QApplication (QObjects
construct headlessly); the engine wiring lives in `ui.qml.app`.
"""

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QObject,
    Qt,
    Signal,
    Slot,
)

from ...state.events import NewsLoaded, ProgressChanged, StatusChanged


class LauncherState(QObject):
    """Scalar launcher state for QML bindings (status + progress)."""

    statusTextChanged = Signal()
    progressValueChanged = Signal()
    progressLabelChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._status_text = "Ready to update"
        self._progress_value = 0.0
        self._progress_label = ""

    # ── properties ────────────────────────────────────────────────────

    def _get_status_text(self) -> str:
        return self._status_text

    def _set_status_text(self, value: str):
        if value != self._status_text:
            self._status_text = value
            self.statusTextChanged.emit()

    statusText = Property(
        str, _get_status_text, _set_status_text, notify=statusTextChanged
    )

    def _get_progress_value(self) -> float:
        return self._progress_value

    def _set_progress_value(self, value: float):
        if value != self._progress_value:
            self._progress_value = float(value)
            self.progressValueChanged.emit()

    progressValue = Property(
        float,
        _get_progress_value,
        _set_progress_value,
        notify=progressValueChanged,
    )

    def _get_progress_label(self) -> str:
        return self._progress_label

    def _set_progress_label(self, value: str):
        if value != self._progress_label:
            self._progress_label = value
            self.progressLabelChanged.emit()

    progressLabel = Property(
        str,
        _get_progress_label,
        _set_progress_label,
        notify=progressLabelChanged,
    )

    # ── event intake ──────────────────────────────────────────────────

    @Slot(str)
    def setStatusText(self, text: str):
        """Slot for `ControllerBridge.statusChanged`."""
        self._set_status_text(text)

    @Slot(float, str)
    def setProgress(self, value: float, label: str):
        """Slot for `ControllerBridge.progressChanged`."""
        self._set_progress_value(value)
        self._set_progress_label(label)

    def attach(self, bridge):
        """Connect a `ControllerBridge` (or compatible signal source)."""
        bridge.statusChanged.connect(self.setStatusText)
        bridge.progressChanged.connect(self.setProgress)

    def on_event(self, event):
        """Direct `EventDispatcher` intake (no Qt bridge required)."""
        if isinstance(event, StatusChanged):
            self._set_status_text(event.text)
        elif isinstance(event, ProgressChanged):
            self._set_progress_value(event.value)
            self._set_progress_label(event.label)


class ThemeBridge(QObject):
    """Palette slots for QML (`appTheme.colors["C_GOLD"]`, `appTheme.themed`)."""

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self._colors = {
            name: color.name() for name, color in palette.colors.items()
        }
        self._themed = bool(palette.themed)

    def _get_colors(self) -> dict:
        return dict(self._colors)

    colors = Property("QVariantMap", _get_colors, constant=True)

    def _get_themed(self) -> bool:
        return self._themed

    themed = Property(bool, _get_themed, constant=True)

    @Slot(str, result=str)
    def color(self, name: str) -> str:
        """Hex string for a palette slot ("" when unknown)."""
        return self._colors.get(name, "")


class NewsFeedModel(QAbstractListModel):
    """Announcements list for QML (`newsModel`), fed by `NewsLoaded`."""

    TitleRole = Qt.ItemDataRole.UserRole + 1
    DateRole = Qt.ItemDataRole.UserRole + 2
    BodyRole = Qt.ItemDataRole.UserRole + 3
    AuthorRole = Qt.ItemDataRole.UserRole + 4
    UrlRole = Qt.ItemDataRole.UserRole + 5

    statusTextChanged = Signal()
    featuredChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._loading = True
        self._error = ""
        self._configured = True
        self._status = "Loading\u2026"
        self._featured_title = ""
        self._featured_url = ""
        self._on_refresh = None

    # ── QAbstractListModel ──────────────────────────────────────────

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._items)

    def roleNames(self) -> dict:
        return {
            NewsFeedModel.TitleRole: b"title",
            NewsFeedModel.DateRole: b"date",
            NewsFeedModel.BodyRole: b"body",
            NewsFeedModel.AuthorRole: b"author",
            NewsFeedModel.UrlRole: b"url",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        return {
            NewsFeedModel.TitleRole: str(item.get("title", "")),
            NewsFeedModel.DateRole: str(item.get("date", "")),
            NewsFeedModel.BodyRole: str(item.get("body", "")),
            NewsFeedModel.AuthorRole: str(item.get("author", "")),
            NewsFeedModel.UrlRole: str(item.get("url", "")),
        }.get(role)

    # ── status + featured properties ───────────────────────────────

    def _get_status_text(self) -> str:
        return self._status

    statusText = Property(str, _get_status_text, notify=statusTextChanged)

    def _get_featured_title(self) -> str:
        return self._featured_title

    featuredTitle = Property(str, _get_featured_title, notify=featuredChanged)

    def _get_featured_url(self) -> str:
        return self._featured_url

    featuredUrl = Property(str, _get_featured_url, notify=featuredChanged)

    # ── intake ──────────────────────────────────────────────────────

    def set_refresh_handler(self, callback):
        """Controller callback for the QML refresh button (test seam)."""
        self._on_refresh = callback

    @Slot()
    def refresh(self):
        """QML refresh button → controller (force refetch)."""
        if self._on_refresh is not None:
            self._on_refresh()

    def set_snapshot(self, items, loading=False, error="", configured=True):
        """Replace the announcements snapshot (mirrors panel `render`)."""
        self.beginResetModel()
        try:
            self._items = list(items or [])
            self._loading = bool(loading)
            self._error = str(error or "")
            self._configured = bool(configured)
        finally:
            self.endResetModel()
        self._refresh_status()

    def on_event(self, event):
        """`ControllerBridge.newsLoaded` intake (featured + items)."""
        if not isinstance(event, NewsLoaded) or event.data is None:
            return
        snap = event.data
        if event.kind == "featured":
            feat = snap.data or {}
            title = str(feat.get("title", ""))
            url = str(feat.get("url", ""))
            if (title, url) != (self._featured_title, self._featured_url):
                self._featured_title = title
                self._featured_url = url
                self.featuredChanged.emit()
        elif event.kind == "items":
            self.set_snapshot(
                snap.data,
                loading=snap.loading,
                error=snap.error,
                configured=snap.configured,
            )

    def _refresh_status(self):
        if self._loading:
            status = "Loading\u2026"
        elif self._error:
            status = self._error
        elif not self._configured:
            status = "News feed not configured."
        elif not self._items:
            status = "No announcements."
        else:
            status = ""
        if status != self._status:
            self._status = status
            self.statusTextChanged.emit()

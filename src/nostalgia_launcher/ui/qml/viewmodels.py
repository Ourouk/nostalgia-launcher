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
    QModelIndex,
    QObject,
    Qt,
    Signal,
    Slot,
)

from ...core.helpers import fmt_size, fmt_speed
from ...state.events import (
    NewsLoaded,
    ProgressChanged,
    StatusChanged,
    UpdateFilesList,
)


class LauncherState(QObject):
    """Scalar launcher state for QML bindings (status + progress).

    Also owns the header wordmark: `serverName` (server-name text shown
    until a logo arrives) and `logoSource` (local `file://` URL of the
    fetched server logo, "" until/unless the fetch succeeds — the text
    stays on failure, matching the old widget shell).
    """

    statusTextChanged = Signal()
    progressValueChanged = Signal()
    progressLabelChanged = Signal()
    serverNameChanged = Signal()
    logoSourceChanged = Signal()
    logoFetched = Signal(str)

    def __init__(self, parent=None, server_name="", logo_source=""):
        super().__init__(parent)
        self._status_text = "Ready to update"
        self._progress_value = 0.0
        self._progress_label = ""
        self._server_name = str(server_name or "")
        self._logo_source = str(logo_source or "")
        self.logoFetched.connect(
            self.applyLogoSource, type=Qt.ConnectionType.QueuedConnection
        )

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

    def _get_server_name(self) -> str:
        return self._server_name

    def _set_server_name(self, value: str):
        value = str(value or "")
        if value != self._server_name:
            self._server_name = value
            self.serverNameChanged.emit()

    serverName = Property(
        str, _get_server_name, _set_server_name, notify=serverNameChanged
    )

    def _get_logo_source(self) -> str:
        return self._logo_source

    def _set_logo_source(self, value: str):
        value = str(value or "")
        if value != self._logo_source:
            self._logo_source = value
            self.logoSourceChanged.emit()

    logoSource = Property(
        str, _get_logo_source, _set_logo_source, notify=logoSourceChanged
    )

    # ── event intake ──────────────────────────────────────────────────

    @Slot(str)
    def setServerName(self, text: str):
        """Header wordmark text (main-thread slot for async updates)."""
        self._set_server_name(text)

    @Slot(str)
    def setLogoSource(self, source: str):
        """Header logo `file://` URL (main-thread direct setter)."""
        self._set_logo_source(source)

    @Slot(str)
    def applyLogoSource(self, source: str):
        """Queued logo setter — the worker thread emits `logoFetched`."""
        self._set_logo_source(source)

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


class UpdateFilesModel(QAbstractListModel):
    """Updated-files list for QML (mirrors panel file tracking)."""

    NameRole = Qt.ItemDataRole.UserRole + 1
    DoneRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: list = []
        self._done: set = set()

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self._files)

    def roleNames(self) -> dict:
        return {
            UpdateFilesModel.NameRole: b"name",
            UpdateFilesModel.DoneRole: b"done",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        name = self._files[index.row()]
        if role == UpdateFilesModel.NameRole:
            return name
        if role == UpdateFilesModel.DoneRole:
            return name in self._done
        return None

    def set_files(self, files):
        """Replace the list (separators normalized to "/"), all pending."""
        self.beginResetModel()
        try:
            self._files = [str(f).replace("\\", "/") for f in (files or [])]
            self._done = set()
        finally:
            self.endResetModel()

    def mark_done(self, path: str) -> bool:
        """Mark a listed file done; True when it was listed."""
        name = str(path).replace("\\", "/")
        if name not in self._files:
            return False
        if name not in self._done:
            self._done.add(name)
            row = self._files.index(name)
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [UpdateFilesModel.DoneRole])
        return True

    def append_done(self, path: str):
        """Append an already-updated file (dedups against listed ones)."""
        name = str(path).replace("\\", "/")
        if name in self._files:
            self.mark_done(name)
            return
        row = len(self._files)
        self.beginInsertRows(QModelIndex(), row, row)
        self._files.append(name)
        self._done.add(name)
        self.endInsertRows()

    def mark_all_done(self):
        """Mark every listed file done (successful verify/update)."""
        if len(self._done) == len(self._files):
            return
        self._done = set(self._files)
        if self._files:
            top = self.index(0)
            bottom = self.index(len(self._files) - 1)
            self.dataChanged.emit(top, bottom, [UpdateFilesModel.DoneRole])


class UpdateState(QObject):
    """UPDATE tab + footer primary button (mirrors UpdatePanel + footer).

    Formatting (sizes, speeds) happens here via `core.helpers` so QML
    stays declarative. Controller calls arrive as injected callbacks
    (`set_primary_handler` / `set_recheck_handler`) — never imports.
    """

    phaseTextChanged = Signal()
    progressChanged = Signal()
    fileTextChanged = Signal()
    statsChanged = Signal()
    primaryChanged = Signal()
    filesChanged = Signal()
    realmChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = "Idle"
        self._progress_value = 0.0
        self._progress_visible = True
        self._file = "No update is running."
        self._transport = "-"
        self._amount = "-"
        self._speed = "-"
        self._peers = "-"
        self._primary_label = "UPDATE"
        self._primary_enabled = True
        self._files = UpdateFilesModel(self)
        self._on_primary = None
        self._on_recheck = None
        self._on_realm = None
        self._realm_prompt = ""

    # ── properties ──────────────────────────────────────────────────

    def _get_phase(self) -> str:
        return self._phase

    phaseText = Property(str, _get_phase, notify=phaseTextChanged)

    def _get_progress_value(self) -> float:
        return self._progress_value

    progressValue = Property(
        float, _get_progress_value, notify=progressChanged
    )

    def _get_progress_visible(self) -> bool:
        return self._progress_visible

    progressVisible = Property(
        bool, _get_progress_visible, notify=progressChanged
    )

    def _get_file(self) -> str:
        return self._file

    fileText = Property(str, _get_file, notify=fileTextChanged)

    def _get_transport(self) -> str:
        return self._transport

    transportText = Property(str, _get_transport, notify=statsChanged)

    def _get_amount(self) -> str:
        return self._amount

    amountText = Property(str, _get_amount, notify=statsChanged)

    def _get_speed(self) -> str:
        return self._speed

    speedText = Property(str, _get_speed, notify=statsChanged)

    def _get_peers(self) -> str:
        return self._peers

    peersText = Property(str, _get_peers, notify=statsChanged)

    def _get_primary_label(self) -> str:
        return self._primary_label

    primaryLabel = Property(str, _get_primary_label, notify=primaryChanged)

    def _get_primary_enabled(self) -> bool:
        return self._primary_enabled

    primaryEnabled = Property(
        bool, _get_primary_enabled, notify=primaryChanged
    )

    def _get_files(self) -> QObject:
        return self._files

    files = Property(QObject, _get_files, notify=filesChanged)

    def _get_realm_prompt(self) -> str:
        return self._realm_prompt

    realmPrompt = Property(str, _get_realm_prompt, notify=realmChanged)

    # ── controller callbacks ────────────────────────────────────────

    def set_primary_handler(self, callback):
        """Footer primary-button click (test seam)."""
        self._on_primary = callback

    def set_recheck_handler(self, callback):
        """Force-recheck click (test seam)."""
        self._on_recheck = callback

    def set_realm_handler(self, callback):
        """Realm-prompt answer (test seam)."""
        self._on_realm = callback

    @Slot()
    def primary(self):
        if self._on_primary is not None:
            self._on_primary()

    @Slot()
    def recheck(self):
        if self._on_recheck is not None:
            self._on_recheck()

    @Slot(bool)
    def resolveRealm(self, inject: bool):
        self._realm_prompt = ""
        self.realmChanged.emit()
        if self._on_realm is not None:
            self._on_realm(bool(inject))

    def prompt_realm(self, actual: str, expected: str):
        """Show the realm-mismatch prompt before launching."""
        self._realm_prompt = (
            f"The game folder realm is '{actual}' but this server wants "
            f"'{expected}'. Injecting overwrites the realm files with a "
            "third-party address. Only proceed if you trust this server."
        )
        self.realmChanged.emit()

    def update_primary(self, label: str, enabled: bool):
        """Footer button state from the readiness decision."""
        if (label, enabled) != (self._primary_label, self._primary_enabled):
            self._primary_label = label
            self._primary_enabled = bool(enabled)
            self.primaryChanged.emit()

    # ── event intake (mirrors UpdatePanel) ──────────────────────────

    def on_progress(self, event):
        """Full `ProgressChanged` (via `bridge.updateProgressChanged`)."""
        if not isinstance(event, ProgressChanged):
            return
        if event.phase:
            self._set_phase(event.phase)
        elif event.label:
            self._set_phase("Working")
        value = max(0.0, min(1.0, event.value))
        if (value, 0.0 < value < 1.0) != (
            self._progress_value,
            self._progress_visible,
        ):
            # (progressVisible follows the footer mini-bar rule: only
            # visible while something is actually in flight.)
            self._progress_value = value
            self._progress_visible = 0.0 < value < 1.0
            self.progressChanged.emit()
        if event.current_file or event.label:
            self._set_file(event.current_file or event.label)
        self._set_stats(
            event.transport or "-",
            (
                f"{fmt_size(event.downloaded)} / {fmt_size(event.total)}"
                if event.total
                else f"{event.value * 100:.0f}%"
            ),
            fmt_speed(event.speed) if event.speed else "-",
            str(event.peers) if event.peers else "-",
        )
        if event.current_file and "/" in event.current_file:
            path = event.current_file.replace("\\", "/")
            if not self._files.mark_done(path):
                self._files.append_done(path)

    def on_files(self, event):
        if isinstance(event, UpdateFilesList):
            self._files.set_files(event.files or [])

    def on_status(self, text: str):
        if text in ("Verifying…", "Updating…"):
            self._set_phase(text)
            self._set_file("Preparing client update…")

    def on_finished(self, kind: str, ok: bool, message: str):
        if kind in ("update", "verify"):
            self._set_visible(False)
            if ok:
                self._set_phase("Verified" if kind == "verify" else "Complete")
                self._files.mark_all_done()
            else:
                self._set_phase(
                    "Update required" if kind == "verify" else "Failed"
                )
                self._set_file(message or "Update failed.")
        elif kind == "mods" and ok:
            self._set_phase("Updating addons and mods")
            self._set_file("Mods complete; checking addons…")
        elif kind == "addons" and ok:
            self._set_phase("Complete")

    def on_failed(self, kind: str, message: str):
        if kind in ("update", "verify"):
            self._set_visible(False)
            self._set_phase("Failed")
            self._set_file(message or "Update failed.")

    # ── internals ───────────────────────────────────────────────────

    def _set_phase(self, text: str):
        if text != self._phase:
            self._phase = text
            self.phaseTextChanged.emit()

    def _set_file(self, text: str):
        if text != self._file:
            self._file = text
            self.fileTextChanged.emit()

    def _set_visible(self, visible: bool):
        if visible != self._progress_visible:
            self._progress_visible = visible
            self.progressChanged.emit()

    def _set_stats(self, transport, amount, speed, peers):
        if (transport, amount, speed, peers) != (
            self._transport,
            self._amount,
            self._speed,
            self._peers,
        ):
            self._transport = transport
            self._amount = amount
            self._speed = speed
            self._peers = peers
            self.statsChanged.emit()

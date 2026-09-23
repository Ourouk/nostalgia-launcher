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

from PySide6.QtCore import Property, QObject, Signal, Slot

from ...state.events import ProgressChanged, StatusChanged


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

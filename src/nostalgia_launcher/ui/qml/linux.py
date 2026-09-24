"""Linux (UMU) settings for QML (Phase 6b).

Mirrors `ui/qt/linux_settings_dialog.py`: umu-run hint, Proton combo,
renderer combo, DXVK/GameMode/Wayland checks, GAMEID field, umu-run path
field. Snapshot + callbacks come from `SettingsController` via the tab
glue in `ui.qml.app`.
"""

from PySide6.QtCore import Property, QObject, Signal, Slot

from ...services.umu import RENDERER_CHOICES

_RENDERER_LABELS = {value: label for value, label in RENDERER_CHOICES}
_RENDERER_BY_LABEL = {label: value for value, label in RENDERER_CHOICES}


def renderer_labels() -> list:
    """Renderer choice labels for the combo."""
    return [label for _, label in RENDERER_CHOICES]


def renderer_value(label: str) -> str:
    """Combo label back to the stored renderer value."""
    return _RENDERER_BY_LABEL.get(label, "auto")


def renderer_label(value: str) -> str:
    """Stored renderer value to its combo label."""
    return _RENDERER_LABELS.get(value, "Auto (Proton default)")


class LinuxModel(QObject):
    """Linux (UMU) settings state."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._umu_hint = ""
        self._proton_options: list = []
        self._proton = ""
        self._renderer_options: list = []
        self._renderer = ""
        self._dxvk = False
        self._gamemode = False
        self._gamemode_avail = False
        self._wayland = False
        self._wayland_avail = False
        self._game_id = ""
        self._umu_path = ""
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
        self._umu_hint = str(snap.get("umu_hint", ""))
        self._proton_options = list(snap.get("proton_options", []))
        self._proton = str(snap.get("proton", ""))
        self._renderer_options = list(snap.get("renderer_options", []))
        self._renderer = str(snap.get("renderer", ""))
        self._dxvk = bool(snap.get("dxvk", False))
        self._gamemode = bool(snap.get("gamemode", False))
        self._gamemode_avail = bool(snap.get("gamemode_avail", False))
        self._wayland = bool(snap.get("wayland", False))
        self._wayland_avail = bool(snap.get("wayland_avail", False))
        self._game_id = str(snap.get("game_id", ""))
        self._umu_path = str(snap.get("umu_path", ""))
        self.changed.emit()

    # ── properties ────────────────────────────────────────────────────

    def _get_umu_hint(self) -> str:
        return self._umu_hint

    umuHint = Property(str, _get_umu_hint, notify=changed)

    def _get_proton_options(self) -> list:
        return list(self._proton_options)

    protonOptions = Property(
        "QVariantList", _get_proton_options, notify=changed
    )

    def _get_proton(self) -> str:
        return self._proton

    proton = Property(str, _get_proton, notify=changed)

    def _get_renderer_options(self) -> list:
        return list(self._renderer_options)

    rendererOptions = Property(
        "QVariantList", _get_renderer_options, notify=changed
    )

    def _get_renderer(self) -> str:
        return self._renderer

    renderer = Property(str, _get_renderer, notify=changed)

    def _get_dxvk(self) -> bool:
        return self._dxvk

    dxvk = Property(bool, _get_dxvk, notify=changed)

    def _get_gamemode(self) -> bool:
        return self._gamemode

    gamemode = Property(bool, _get_gamemode, notify=changed)

    def _get_gamemode_avail(self) -> bool:
        return self._gamemode_avail

    gamemodeAvail = Property(bool, _get_gamemode_avail, notify=changed)

    def _get_wayland(self) -> bool:
        return self._wayland

    wayland = Property(bool, _get_wayland, notify=changed)

    def _get_wayland_avail(self) -> bool:
        return self._wayland_avail

    waylandAvail = Property(bool, _get_wayland_avail, notify=changed)

    def _get_game_id(self) -> str:
        return self._game_id

    gameId = Property(str, _get_game_id, notify=changed)

    def _get_umu_path(self) -> str:
        return self._umu_path

    umuPath = Property(str, _get_umu_path, notify=changed)

    # ── actions ───────────────────────────────────────────────────────

    def set_handlers(self, **handlers):
        self._handlers = dict(handlers)

    def _call(self, name, *args):
        handler = self._handlers.get(name)
        if handler is None:
            return
        handler(*args)
        self.refresh()

    @Slot(str)
    def applyProton(self, value: str):
        self._call("proton", value)

    @Slot(str)
    def applyRenderer(self, label: str):
        self._call("renderer", renderer_value(label))

    @Slot(bool)
    def setDxvk(self, enabled: bool):
        self._call("dxvk", bool(enabled))

    @Slot(bool)
    def setGamemode(self, enabled: bool):
        self._call("gamemode", bool(enabled))

    @Slot(bool)
    def setWayland(self, enabled: bool):
        self._call("wayland", bool(enabled))

    @Slot(str)
    def applyGameId(self, value: str):
        self._call("game_id", value)

    @Slot(str)
    def applyUmuPath(self, value: str):
        self._call("umu_path", value)

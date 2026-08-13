"""Octo Updater Qt (PySide6) main window — chrome, tab switching, footer.

Phase 3 of the Qt migration: `MainWindow` is the PySide6 counterpart of the
Tk OctoUpdaterApp chrome — the header (wordmark, nav tabs, settings gear),
the stacked content area and the footer (status, UPDATE/PLAY button, version,
progress). It owns no business logic: the controllers' events arrive through
the `ControllerHub` bridge and are rendered here, exactly like the Tk
adapter renders the same events. Qt layouts (not absolute positioning) do
all sizing; the look comes from `qt_theme.theme_qss`.

The C16-C19 panel ports build their content into the placeholder pages of
`self._stack`, keyed by tab name in `self._pages`; the nav gear and footer
widgets are exposed as attributes for the settings/update workflows (C20+).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from constants import UPDATER_VERSION
from qt_bridge import ControllerHub
from qt_mods_panel import ModsPanel
from qt_news_panel import NewsPanel
from qt_theme import Palette, theme_qss
from qt_tweaks_panel import TweaksPanel
from ui_metrics import BASE_H, BASE_W, clamp


class MainWindow(QMainWindow):
    """The Qt main window shell: header, content stack and footer.

    Receives a fully-assembled `ControllerHub` (controllers + bridge) and
    renders the events the bridge forwards. `close()` tears the bridge down
    so posting after close is a safe no-op.
    """

    TABS = ["NEWS", "TWEAKS", "ADDONS", "MODS"]

    def __init__(self, hub: ControllerHub, parent=None):
        super().__init__(parent)
        self._hub = hub
        self._palette = Palette()
        self.setStyleSheet(theme_qss(self._palette))
        self.setWindowTitle("Octo Updater")
        self.setMinimumSize(clamp(BASE_W // 2, 560, 800),
                            clamp(BASE_H // 2, 420, 600))

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_central(), 1)
        root.addWidget(self._build_footer())
        self.setCentralWidget(central)

        self._wire_signals()
        self._navButtons["NEWS"].setChecked(True)

    # ── build ────────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        p = self._palette
        header = QWidget(self)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(16)

        self._wordmark = QLabel("Octo Updater", header)
        font = self._wordmark.font()
        font.setPointSize(17)
        font.setBold(True)
        self._wordmark.setFont(font)
        self._wordmark.setStyleSheet(f"color: {p.purple.name()};")
        layout.addWidget(self._wordmark)

        navRow = QWidget(header)
        navLayout = QHBoxLayout(navRow)
        navLayout.setContentsMargins(0, 0, 0, 0)
        navLayout.setSpacing(2)
        self._navButtons = {}
        self._tabBadges = {}
        self._navGroup = QButtonGroup(navRow)
        self._navGroup.setExclusive(True)
        for name in self.TABS:
            button = QPushButton(name, navRow)
            button.setCheckable(True)
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            self._navButtons[name] = button
            self._navGroup.addButton(button)
            # Each tab is wrapped in a grid cell so a small count badge can
            # overlay the button's top-right corner without shifting layout.
            holder = QWidget(navRow)
            grid = QGridLayout(holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(0)
            grid.addWidget(button, 0, 0)
            badge = QLabel("", holder)
            badge.setObjectName(f"tabBadge_{name}")
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedHeight(16)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            badge.setStyleSheet(
                f"background-color: {p.gold.name()}; color: {p.hdr.name()};"
                f" border-radius: 8px; font-size: 8pt; font-weight: bold;"
                f" padding: 0 4px;")
            badge.hide()
            grid.addWidget(badge, 0, 0, Qt.AlignTop | Qt.AlignRight)
            self._tabBadges[name] = badge
            navLayout.addWidget(holder)
            button.clicked.connect(
                lambda checked=False, tab=name: self.switch_tab(tab))
        navRow.setStyleSheet(
            "QPushButton { color: %s; background: transparent; border: none;"
            " padding: 6px 12px; font-size: 10pt; font-weight: bold; }"
            "QPushButton:hover { color: %s; }"
            "QPushButton:checked { color: %s; }"
            % (p.text.name(), p.gold.name(), p.gold_lt.name()))
        layout.addWidget(navRow)

        layout.addStretch(1)

        self._gearButton = QToolButton(header)
        self._gearButton.setText("⚙")
        self._gearButton.setToolTip("Settings")
        self._gearButton.setCursor(Qt.PointingHandCursor)
        self._gearButton.setStyleSheet(
            f"QToolButton {{ color: {p.text_dim.name()}; font-size: 14pt; }}"
            f"QToolButton:hover {{ color: {p.gold.name()}; }}")
        layout.addWidget(self._gearButton)

        return header

    def _build_central(self) -> QStackedWidget:
        self._stack = QStackedWidget(self)
        self._pages: dict[str, int] = {}
        for i, name in enumerate(self.TABS):
            if name == "NEWS":
                page = NewsPanel(self._hub.news, self._hub.bridge,
                                 self._palette, self._stack)
            elif name == "TWEAKS":
                page = TweaksPanel(self._hub.tweaks, self._hub.bridge,
                                   self._palette, self._stack)
            elif name == "MODS":
                page = ModsPanel(
                    self._hub.mods, self._hub.bridge,
                    self._palette, self._stack,
                    on_badge=lambda n: self.set_tab_badge("MODS", n))
            else:
                page = QLabel(f"{name} panel (C{i + 16})", self._stack)
                page.setAlignment(Qt.AlignCenter)
            self._pages[name] = i
            self._stack.addWidget(page)
        return self._stack

    def _build_footer(self) -> QWidget:
        p = self._palette
        footer = QWidget(self)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(40, 10, 40, 12)
        layout.setSpacing(24)

        left = QWidget(footer)
        leftLayout = QVBoxLayout(left)
        leftLayout.setContentsMargins(0, 0, 0, 0)
        leftLayout.setSpacing(4)

        self._statusLabel = QLabel("Ready to update", left)
        font = self._statusLabel.font()
        font.setBold(True)
        self._statusLabel.setFont(font)
        leftLayout.addWidget(self._statusLabel)

        self._buttonStyles = {
            "update": (
                "QPushButton { background-color: %s; color: #ffffff;"
                " border: 1px solid %s; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                "QPushButton:hover { background-color: %s; }"
                % (p.gold.name(), p.gold_lt.name(), p.gold_lt.name())),
            "play": (
                "QPushButton { background-color: %s; color: #ffffff;"
                " border: 1px solid %s; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                "QPushButton:hover { background-color: %s; }"
                % (p.green_btn.name(), p.green_hov.name(), p.green_hov.name())),
        }
        self._updateButton = QPushButton("UPDATE", left)
        self._updateButton.setMinimumWidth(150)
        self._updateButton.setStyleSheet(self._buttonStyles["update"])
        leftLayout.addWidget(self._updateButton)

        self._versionLabel = QLabel(f"v{UPDATER_VERSION}", left)
        self._versionLabel.setStyleSheet(f"color: {p.text_dim.name()};")
        leftLayout.addWidget(self._versionLabel)

        layout.addWidget(left)
        layout.addStretch(1)

        right = QWidget(footer)
        rightLayout = QVBoxLayout(right)
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.setSpacing(4)

        self._progressBar = QProgressBar(right)
        self._progressBar.setTextVisible(False)
        self._progressBar.setRange(0, 100)
        self._progressBar.setValue(0)
        self._progressBar.hide()
        self._progressBar.setStyleSheet(
            "QProgressBar { background-color: %s; border: 1px solid %s;"
            " border-radius: 3px; height: 8px; }"
            "QProgressBar::chunk { background-color: %s;"
            " border-radius: 3px; }"
            % (p.hdr.name(), p.panel_bdr.name(), p.gold.name()))
        rightLayout.addWidget(self._progressBar)

        self._progressLabel = QLabel("", right)
        rightLayout.addWidget(self._progressLabel)

        layout.addWidget(right)
        return footer

    def _wire_signals(self):
        bridge = self._hub.bridge
        bridge.statusChanged.connect(self._onStatusChanged)
        bridge.progressChanged.connect(self._onProgressChanged)
        bridge.operationFinished.connect(self._onOperationFinished)
        bridge.operationFailed.connect(self._onOperationFailed)

    # ── tabs ─────────────────────────────────────────────────────────────────

    def switch_tab(self, name: str):
        """Show the page for `name`; unknown names are a no-op."""
        if name not in self._pages:
            return
        self._stack.setCurrentIndex(self._pages[name])
        button = self._navButtons.get(name)
        if button is not None:
            button.setChecked(True)

    def set_tab_badge(self, tab: str, count: int):
        """Show a small gold count badge on a nav tab (hidden at 0)."""
        badge = self._tabBadges.get(tab)
        if badge is None:
            return
        count = max(0, int(count))
        if count:
            badge.setText(str(count))
            badge.show()
        else:
            badge.hide()

    # ── slots ────────────────────────────────────────────────────────────────

    def _onStatusChanged(self, text: str):
        self._statusLabel.setText(text)

    def _onProgressChanged(self, value: float, label: str):
        value = max(0.0, min(1.0, float(value)))
        self._progressBar.setValue(int(round(value * 100)))
        self._progressLabel.setText(label)
        # Hide the bar when idle (0) or finished/full (1), like the Tk
        # version — it only shows while something is downloading.
        if value <= 0.0 or value >= 1.0:
            self._progressBar.hide()
        else:
            self._progressBar.show()

    def _onOperationFinished(self, kind: str, ok: bool, message: str):
        self._set_button_ready(bool(ok))

    def _onOperationFailed(self, kind: str, message: str):
        self._set_button_ready(False)

    def _set_button_ready(self, ready: bool):
        """Stub ready-state flip (gold UPDATE ↔ green PLAY); the full
        workflow with UpdateController.compute_readiness lands in C22."""
        self._updateButton.setText("PLAY" if ready else "UPDATE")
        self._updateButton.setStyleSheet(
            self._buttonStyles["play" if ready else "update"])

    # ── lifecycle ────────────────────────────────────────────────────────────

    def close(self):
        self._hub.close()
        return super().close()

    def closeEvent(self, event):
        self._hub.close()
        super().closeEvent(event)

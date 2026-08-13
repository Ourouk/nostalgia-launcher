"""Octo Updater Qt (PySide6) settings dialog.

A dark QDialog rendering the GAME FOLDER row (open-folder link, readonly path
entry, Change), the DOWNLOAD MIRROR row (status dot + Iceland + status label
+ refresh), the TROUBLESHOOTING and SUPPORT THE DEVELOPER clickable rows and
the GENERAL checkboxes. It renders the SettingsController's state and forwards
user actions straight into the toolkit-agnostic controller; mirror results
arrive as MirrorStatusChanged events through the ControllerBridge and are
rendered here.
"""

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import platform_support
from qt_bridge import ControllerBridge
from qt_theme import Palette, theme_qss
from settings_controller import SettingsController

KO_FI_URL = "https://ko-fi.com/rebased"
BMC_URL = "https://buymeacoffee.com/rebased"


class _ClickableLabel(QLabel):
    """A QLabel that emits clicked on a left mouse release."""

    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _ClickableRow(QWidget):
    """A clickable icon+text row. Children are mouse-transparent so a click
    anywhere on the row fires clicked."""

    clicked = Signal()

    def __init__(self, icon: str, text: str, palette: Palette,
                 icon_color, parent=None):
        super().__init__(parent)
        p = palette
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        icon_label = QLabel(icon, self)
        icon_label.setStyleSheet(
            f"color: {icon_color.name()}; font-size: 11pt;")
        icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        text_label = QLabel(text, self)
        text_label.setWordWrap(True)
        text_label.setStyleSheet(f"color: {p.text.name()}; font-size: 10pt;")
        text_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        layout.addWidget(icon_label, 0, Qt.AlignLeft)
        layout.addWidget(text_label, 0, Qt.AlignLeft)
        layout.addStretch(1)

        self._palette = palette
        self._text_label = text_label

    def click(self):
        self.clicked.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self._text_label.setStyleSheet(
            f"color: {self._palette.gold.name()}; font-size: 10pt;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._text_label.setStyleSheet(
            f"color: {self._palette.text.name()}; font-size: 10pt;")
        super().leaveEvent(event)


class SettingsDialog(QDialog):
    """The SETTINGS dialog.

    Constructible and closable headlessly: it reads the controller's state,
    renders the mirror status it already holds, and only starts work when the
    user clicks a row/button. `showLogsRequested` fires for the Show logs row.
    """

    showLogsRequested = Signal()

    def __init__(self, settings: SettingsController,
                 bridge: ControllerBridge, palette: Palette, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._palette = palette
        p = palette
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setMinimumSize(520, 440)
        self.setStyleSheet(
            theme_qss(p) + f"\nQDialog {{ background-color: {p.bg.name()}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_divider())
        root.addWidget(self._build_body(), 1)

        bridge.mirrorStatusChanged.connect(self._on_mirror_status)

    # ── build ───────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        p = self._palette
        hdr = QWidget(self)
        layout = QHBoxLayout(hdr)
        layout.setContentsMargins(18, 12, 12, 12)

        title = QLabel("SETTINGS", hdr)
        title.setStyleSheet(
            f"color: {p.purple.name()}; font-weight: bold; font-size: 13pt;")
        layout.addWidget(title)
        layout.addStretch(1)

        close_btn = QToolButton(hdr)
        close_btn.setObjectName("settingsClose")
        close_btn.setText("✕")
        close_btn.setToolTip("Close settings")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QToolButton {{ color: {p.text_dim.name()}; font-size: 12pt; }}"
            f"QToolButton:hover {{ color: {p.gold.name()}; }}")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)
        return hdr

    def _build_divider(self) -> QFrame:
        p = self._palette
        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(
            f"background-color: {p.panel_bdr.name()};"
            f" border: none; max-height: 1px;")
        return sep

    def _build_body(self) -> QWidget:
        p = self._palette
        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 16, 22, 12)
        body_layout.setSpacing(8)

        folder_row = QHBoxLayout()
        folder_label = QLabel("GAME FOLDER", body)
        folder_label.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;")
        folder_row.addWidget(folder_label)
        folder_row.addStretch(1)
        open_link = _ClickableLabel("Open folder", body)
        open_link.setObjectName("settingsOpenFolder")
        open_link.setCursor(Qt.PointingHandCursor)
        open_link.setStyleSheet(
            f"color: {p.text_dim.name()}; font-size: 9pt;")
        open_link.clicked.connect(self._settings.open_client_folder)
        folder_row.addWidget(open_link)
        body_layout.addLayout(folder_row)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(self._settings.state.path, body)
        self._path_edit.setObjectName("settingsPath")
        self._path_edit.setReadOnly(True)
        self._path_edit.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        path_row.addWidget(self._path_edit, 1)
        change_btn = QPushButton("Change", body)
        change_btn.setObjectName("settingsChange")
        change_btn.clicked.connect(self._on_change_dir)
        path_row.addWidget(change_btn)
        body_layout.addLayout(path_row)

        mirror_title = QLabel("DOWNLOAD MIRROR", body)
        mirror_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;")
        body_layout.addWidget(mirror_title)
        body_layout.addSpacing(2)

        mirror_row = QHBoxLayout()
        self._mirror_dot = QLabel("●", body)
        self._mirror_dot.setStyleSheet(f"color: {p.text_dim.name()};")
        mirror_row.addWidget(self._mirror_dot)
        mirror_label = QLabel("Iceland", body)
        mirror_label.setStyleSheet(
            f"color: {p.text.name()}; font-weight: bold; font-size: 10pt;")
        mirror_row.addWidget(mirror_label)
        self._mirror_status = QLabel("", body)
        self._mirror_status.setObjectName("settingsMirrorStatus")
        self._mirror_status.setStyleSheet(
            f"color: {p.text_dim.name()}; font-size: 9pt;")
        mirror_row.addWidget(self._mirror_status)
        mirror_row.addStretch(1)
        refresh = QToolButton(body)
        refresh.setObjectName("settingsMirrorRefresh")
        refresh.setText("⟳")
        refresh.setToolTip("Check mirror")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setStyleSheet(
            f"QToolButton {{ color: {p.text_dim.name()}; font-size: 11pt; }}"
            f"QToolButton:hover {{ color: {p.gold.name()}; }}")
        refresh.clicked.connect(self._on_refresh_mirror)
        mirror_row.addWidget(refresh)
        body_layout.addLayout(mirror_row)

        self._render_mirror_status()

        body_layout.addSpacing(6)

        cols = QHBoxLayout()
        cols.setSpacing(24)
        lcol = QWidget(body)
        lcol_layout = QVBoxLayout(lcol)
        lcol_layout.setContentsMargins(0, 0, 0, 0)
        lcol_layout.setSpacing(0)
        lcol_layout.setAlignment(Qt.AlignTop)

        ts_title = QLabel("TROUBLESHOOTING", lcol)
        ts_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;")
        lcol_layout.addWidget(ts_title)

        self._add_row(lcol_layout, "✓", "Verify game files",
                      self._settings.verify_files, "settingsVerify", p.gold)
        self._add_row(lcol_layout, "☰", "Show logs",
                      self.showLogsRequested.emit, "settingsLogs", p.gold)
        if platform_support.can_manage_antivirus():
            self._add_row(
                lcol_layout, "⛊",
                "Add game folder to Defender exclusions",
                self._settings.allow_through_antivirus, "settingsAv", p.gold)

        support_title = QLabel("SUPPORT THE DEVELOPER", lcol)
        support_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;")
        lcol_layout.addWidget(support_title)
        self._add_row(
            lcol_layout, "♥", "Ko-fi",
            lambda: self._settings.open_url(KO_FI_URL), "settingsKoFi",
            p.pink)
        self._add_row(
            lcol_layout, "☕", "Buy Me a Coffee",
            lambda: self._settings.open_url(BMC_URL), "settingsBmc", p.warn)

        rcol = QWidget(body)
        rcol_layout = QVBoxLayout(rcol)
        rcol_layout.setContentsMargins(0, 0, 0, 0)
        rcol_layout.setSpacing(4)
        rcol_layout.setAlignment(Qt.AlignTop)

        general_title = QLabel("GENERAL", rcol)
        general_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;")
        rcol_layout.addWidget(general_title)

        cfg = self._settings.state.config
        self._clear_wdb_check = None
        self._close_on_launch_check = None
        if platform_support.can_launch_client():
            self._clear_wdb_check = self._add_check(
                rcol_layout, "Clear WDB on game launch", "settingsClearWdb",
                bool(cfg.get("clear_wdb_on_launch", False)),
                self._settings.set_clear_wdb)
            self._close_on_launch_check = self._add_check(
                rcol_layout, "Close Octo Updater on game launch",
                "settingsCloseOnLaunch",
                bool(cfg.get("close_on_launch", False)),
                self._settings.set_close_on_launch)
        self._auto_mods_check = self._add_check(
            rcol_layout, "Install essential mods", "settingsAutoMods",
            bool(cfg.get("auto_install_mods", True)),
            self._settings.set_auto_mods)
        self._auto_addons_check = self._add_check(
            rcol_layout, "Install recommended addons", "settingsAutoAddons",
            bool(cfg.get("auto_install_addons", True)),
            self._settings.set_auto_addons)

        cols.addWidget(lcol, 3)
        cols.addWidget(rcol, 2)
        body_layout.addLayout(cols, 1)
        return body

    def _add_row(self, layout, icon, text, command, object_name, color):
        row = _ClickableRow(icon, text, self._palette, color, self)
        row.setObjectName(object_name)
        row.clicked.connect(command)
        layout.addWidget(row)
        layout.addSpacing(8)
        return row

    def _add_check(self, layout, text, object_name, checked, on_toggled):
        check = QCheckBox(text, self)
        check.setObjectName(object_name)
        check.setCursor(Qt.PointingHandCursor)
        check.blockSignals(True)
        check.setChecked(bool(checked))
        check.blockSignals(False)
        check.toggled.connect(on_toggled)
        layout.addWidget(check)
        return check

    # ── actions ─────────────────────────────────────────────────────────────

    def _on_change_dir(self):
        cur = self._settings.state.path
        initial = cur if os.path.isdir(cur) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Select game client folder", initial)
        if chosen:
            chosen = os.path.normpath(chosen)
            self._settings.set_path(chosen)
            self._path_edit.setText(chosen)

    def _on_refresh_mirror(self):
        self._settings.check_mirror()
        self._mirror_status.setText("checking…")
        self._mirror_status.setStyleSheet(
            f"color: {self._palette.text_dim.name()}; font-size: 9pt;")
        self._mirror_dot.setStyleSheet(
            f"color: {self._palette.text_dim.name()};")

    # ── mirror status rendering ─────────────────────────────────────────────

    def _render_mirror_status(self):
        p = self._palette
        status = self._settings.mirror_status
        if status == "online":
            color = p.ok
            text = "online"
        elif status == "offline":
            color = p.err
            text = "offline"
        else:
            color = p.text_dim
            text = status or "checking…"
        self._mirror_status.setText(text)
        self._mirror_status.setStyleSheet(
            f"color: {color.name()}; font-size: 9pt;")
        self._mirror_dot.setStyleSheet(f"color: {color.name()};")

    def _on_mirror_status(self, ok: bool, text: str):
        p = self._palette
        color = p.ok if ok else p.err
        self._mirror_status.setText(text)
        self._mirror_status.setStyleSheet(
            f"color: {color.name()}; font-size: 9pt;")
        self._mirror_dot.setStyleSheet(f"color: {color.name()};")

    # ── lifecycle ───────────────────────────────────────────────────────────

    def done(self, result):
        """Consume the close-time auto-install flags armed by the toggles so
        toggling 'Install essential mods' / 'Install recommended addons' on
        starts the missing installs when the dialog closes (idempotent)."""
        if self._settings.take_pending_auto_mods():
            self._settings.install_missing_essential_mods()
        if self._settings.take_pending_auto_addons():
            self._settings.install_missing_recommended_addons()
        super().done(result)

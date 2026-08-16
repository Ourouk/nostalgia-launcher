"""Vanilla WoW Launcher Qt (PySide6) first-launch launcher config dialog.

A QDialog shown on first launch to let the user pick a
``vanilla_wow_launcher.json`` before any MainWindow exists. The chosen file is
validated with ``launcher.validate_path`` (which never stores it as the active
config) and exposed via ``selected_path()``.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core import launcher
from .theme import Palette, theme_qss


class LauncherConfigDialog(QDialog):
    """First-launch wizard: pick and validate a launcher config file."""

    def __init__(
        self,
        palette: Palette | None = None,
        parent=None,
        initial_path: str = "",
    ):
        super().__init__(parent)
        p = palette or Palette()
        self._palette = p
        self._selected = ""
        self.setObjectName("launcherConfigDialog")
        self.setWindowTitle("FIRST LAUNCH — CHOOSE A SERVER")
        self.setMinimumWidth(560)
        self.setStyleSheet(
            theme_qss(p)
            + f"\nQDialog {{ background-color: {p.panel.name()}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        title = QLabel("FIRST LAUNCH — CHOOSE A SERVER", self)
        title.setObjectName("launcherConfigTitle")
        title.setStyleSheet(
            f"color: {p.purple.name()}; font-weight: bold; font-size: 12pt;"
        )
        root.addWidget(title)

        intro = QLabel(
            "The launcher needs a vanilla_wow_launcher.json config file to "
            "know which server to connect to. Choose the file that ships with "
            "your server's client, or pass --launcher-config on the command "
            "line when starting the launcher.",
            self,
        )
        intro.setObjectName("launcherConfigIntro")
        intro.setStyleSheet(f"color: {p.text_dim.name()};")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._path = QLineEdit(self)
        self._path.setObjectName("launcherConfigPath")
        self._path.setReadOnly(True)
        self._path.setPlaceholderText("No configuration selected yet")
        self._path.textChanged.connect(self._validate_light)
        root.addWidget(self._path)

        browse = QPushButton("Browse…", self)
        browse.setObjectName("launcherConfigBrowse")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self.browse)
        root.addWidget(browse)

        self._error = QLabel("", self)
        self._error.setObjectName("launcherConfigError")
        self._error.setStyleSheet(f"color: {p.err.name()};")
        self._error.setWordWrap(True)
        self._error.hide()
        root.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("launcherConfigCancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("Continue", self)
        ok.setObjectName("launcherConfigOk")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self._submit)
        buttons.addWidget(ok)
        root.addLayout(buttons)

        if initial_path and os.path.isfile(initial_path):
            self._path.setText(initial_path)

    def browse(self):
        current = self._path.text()
        start_dir = os.path.dirname(current) if current else os.getcwd()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select launcher configuration",
            start_dir,
            "Launcher configuration (*.json)",
        )
        if path:
            self._path.setText(path)
            self._submit()

    def _validate_light(self, _text):
        self._error.clear()
        self._error.hide()

    def _submit(self):
        path = self._path.text().strip()
        if not path:
            self._show_error("Please choose a vanilla_wow_launcher.json file.")
            return
        config, err = launcher.validate_path(path)
        if config is None:
            self._show_error(
                str(err) or "Please choose a vanilla_wow_launcher.json file."
            )
            return
        self._selected = path
        self.accept()

    def _show_error(self, message: str):
        self._error.setText(message)
        self._error.show()

    def selected_path(self) -> str:
        return self._selected

"""Nostalgia Launcher Qt (PySide6) custom git addon dialog.

A QDialog with a mono URL entry, an allowed-hosts hint, an error label and
Install/Cancel buttons; on a valid URL it emits `addonRequested` with the
record AddonsController.apply expects.
"""

from urllib.parse import urlsplit

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core.launcher import ADDON_GIT_HOSTS
from ...services import addons
from .theme import Palette, apply_theme


class CustomAddonDialog(QDialog):
    """Asks for a git URL and emits the resulting addon record."""

    addonRequested = Signal(dict)

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        p = palette
        self.setObjectName("customAddonDialog")
        self.setWindowTitle("ADD CUSTOM GIT ADDON")
        self.setMinimumWidth(520)
        apply_theme(
            self,
            p,
            f"\nQDialog {{ background-color: {p.panel.name()}; }}",
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        title = QLabel("ADD CUSTOM GIT ADDON", self)
        title.setStyleSheet(
            f"color: {p.gold_lt.name()}; font-weight: bold; font-size: 12pt;"
        )
        root.addWidget(title)

        url_label = QLabel("REPOSITORY URL", self)
        url_label.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 9pt;"
        )
        root.addWidget(url_label)

        self._url = QLineEdit(self)
        self._url.setObjectName("customAddonUrl")
        self._url.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        root.addWidget(self._url)

        self._hint = QLabel(
            "Allowed hosts: "
            + ", ".join(ADDON_GIT_HOSTS)
            + "\nInstalls into Interface/AddOns/<folder>.",
            self,
        )
        self._hint.setObjectName("customAddonHint")
        self._hint.setStyleSheet(f"color: {p.text_dim.name()};")
        root.addWidget(self._hint)

        self._error = QLabel("", self)
        self._error.setObjectName("customAddonError")
        self._error.setStyleSheet(f"color: {p.err.name()};")
        root.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("customAddonCancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        install = QPushButton("Install", self)
        install.setObjectName("customAddonInstall")
        install.setCursor(Qt.PointingHandCursor)
        install.clicked.connect(self._submit)
        buttons.addWidget(install)
        root.addLayout(buttons)

    def _submit(self):
        url = self._url.text().strip().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        if not addons.is_allowed_git_url(url):
            self._error.setText("URL must be https from an allowed host.")
            return
        folder = url.rsplit("/", 1)[-1]
        if (
            not folder
            or folder in (".", "..")
            or "\\" in folder
            or not urlsplit(url).path
        ):
            self._error.setText("Could not derive addon folder name.")
            return
        self.addonRequested.emit(
            {
                "folder": folder,
                "status": "available",
                "git": url,
                "branch": None,
                "ref": None,
                "toc": {},
                "description": None,
                "error": None,
            }
        )
        self.accept()

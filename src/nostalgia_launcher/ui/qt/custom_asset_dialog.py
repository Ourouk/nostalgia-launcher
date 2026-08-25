"""Nostalgia Launcher Qt (PySide6) custom asset dialog.

A QDialog assembling a single-file server-content entry (an MPQ-style
patch), validated with `catalog.validate_asset` before accepting; emits
`assetRequested` with the raw entry so the caller can persist it into the
local assets repo's "custom" list.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ...services import catalog
from .theme import Palette, apply_theme


class CustomAssetDialog(QDialog):
    """Assembles and validates a custom asset entry, then emits it."""

    assetRequested = Signal(dict)

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        p = palette
        self.setObjectName("customAssetDialog")
        self.setWindowTitle("ADD CUSTOM ASSET")
        self.setMinimumWidth(560)
        apply_theme(
            self,
            p,
            f"\nQDialog {{ background-color: {p.panel.name()}; }}",
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        title = QLabel("ADD CUSTOM ASSET", self)
        title.setStyleSheet(
            f"color: {p.gold_lt.name()}; font-weight: bold; font-size: 12pt;"
        )
        root.addWidget(title)

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        def field(label: str) -> QLineEdit:
            cap = QLabel(label, self)
            cap.setStyleSheet(
                f"color: {p.gold.name()}; font-weight: bold;font-size: 9pt;"
            )
            root.addWidget(cap)
            edit = QLineEdit(self)
            edit.setFont(mono)
            root.addWidget(edit)
            return edit

        self._id = field("ASSET ID")
        self._name = field("NAME (defaults to the id)")
        self._url = field("DOWNLOAD URL (https)")
        self._dest = field("DESTINATION PATH (relative to the game folder)")
        self._dest.setPlaceholderText("Data/patch.mpq")
        self._sha1 = field("SHA-1 (optional, 40 hex digits)")
        self._size = field("SIZE IN BYTES (optional)")
        self._version = field("VERSION TAG (optional)")
        self._repo_url = field("REPO URL (optional https link)")

        self._essential = QCheckBox("Essential (auto-install)", self)
        self._essential.setObjectName("customAssetEssential")
        root.addWidget(self._essential)

        self._probe = QCheckBox(
            "Probe the remote file for updates (drift detection)", self
        )
        self._probe.setObjectName("customAssetProbe")
        root.addWidget(self._probe)

        dest_hint = QLabel(
            "Assets are server content patches and install under the "
            "client's Data/ folder.",
            self,
        )
        dest_hint.setObjectName("customAssetDestHint")
        dest_hint.setStyleSheet(f"color: {p.text_dim.name()};")
        dest_hint.setWordWrap(True)
        root.addWidget(dest_hint)

        self._error = QLabel("", self)
        self._error.setObjectName("customAssetError")
        self._error.setStyleSheet(f"color: {p.err.name()};")
        self._error.setWordWrap(True)
        root.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("customAssetCancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        submit = QPushButton("Add asset", self)
        submit.setObjectName("customAssetSubmit")
        submit.setCursor(Qt.PointingHandCursor)
        submit.clicked.connect(self._submit)
        buttons.addWidget(submit)
        root.addLayout(buttons)

    def _entry(self) -> dict:
        aid = self._id.text().strip()
        entry = {
            "id": aid,
            "name": self._name.text().strip() or aid,
            "url": self._url.text().strip(),
            "dest": self._dest.text().strip(),
            "description": "",
            "essential": self._essential.isChecked(),
            "probe": self._probe.isChecked(),
        }
        for key, edit in (
            ("sha1", self._sha1),
            ("version", self._version),
            ("repo_url", self._repo_url),
        ):
            value = edit.text().strip()
            if value:
                entry[key] = value
        size = self._size.text().strip()
        if size:
            try:
                entry["size"] = int(size)
            except ValueError:
                raise ValueError(
                    "Size must be a whole number of bytes."
                ) from None
        return entry

    def _submit(self):
        try:
            entry = self._entry()
        except ValueError as e:
            self._error.setText(str(e))
            return
        if not entry["id"]:
            self._error.setText("An asset id is required.")
            return
        cleaned = catalog.validate_asset(entry)
        if cleaned is None:
            self._error.setText(
                "This entry is not usable — the URL must be https and the "
                "destination a safe path relative to the game folder."
            )
            return
        self.assetRequested.emit(entry)
        self.accept()

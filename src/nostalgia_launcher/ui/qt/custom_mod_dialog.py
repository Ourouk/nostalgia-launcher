"""Nostalgia Launcher Qt (PySide6) custom mod dialog.

A QDialog that assembles a mod catalog entry for every registered source
kind (`services/sources`) — github/codeberg release, direct_file,
direct_tar — validates it with `catalog.validate_mod` before accepting, and
emits `modRequested` with the raw entry so the caller can persist it into
the local mods repo's "custom" list.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ...services import catalog
from .theme import Palette, apply_theme

_KINDS = ("github_release", "codeberg_release", "direct_file", "direct_tar")

_MAP_HINT = 'Extract map — one "zip-pattern=dest/path" line per entry:'


class CustomModDialog(QDialog):
    """Assembles and validates a custom mod entry, then emits it."""

    modRequested = Signal(dict)

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._release_caps: list = []
        self._release_edits: list = []
        self._direct_caps: list = []
        self._direct_edits: list = []
        self._external_caps: list = []
        self._external_edits: list = []
        p = palette
        self.setObjectName("customModDialog")
        self.setWindowTitle("ADD CUSTOM MOD")
        self.setMinimumWidth(560)
        apply_theme(
            self,
            p,
            f"\nQDialog {{ background-color: {p.panel.name()}; }}",
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        title = QLabel("ADD CUSTOM MOD", self)
        title.setStyleSheet(
            f"color: {p.gold_lt.name()}; font-weight: bold; font-size: 12pt;"
        )
        root.addWidget(title)

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._id = self._field(root, "MOD ID", mono)
        self._name = self._field(root, "NAME (defaults to the id)", mono)
        self._repo_url = self._field(
            root, "REPO URL (optional https link)", mono
        )
        self._description = self._field(root, "DESCRIPTION (optional)", mono)

        type_row = QHBoxLayout()
        type_label = QLabel("TYPE", self)
        type_label.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 9pt;"
        )
        type_row.addWidget(type_label)
        self._type = QComboBox(self)
        self._type.setObjectName("customModType")
        self._type.addItems(catalog.MOD_TYPES)
        self._type.currentTextChanged.connect(self._sync_type_fields)
        type_row.addWidget(self._type, 1)
        root.addLayout(type_row)

        install_row = QHBoxLayout()
        install_label = QLabel("INSTALLATION", self)
        install_label.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 9pt;"
        )
        install_row.addWidget(install_label)
        self._installation = QComboBox(self)
        self._installation.setObjectName("customModInstallation")
        self._installation.addItems(catalog.MOD_INSTALLATIONS)
        install_row.addWidget(self._installation, 1)
        root.addLayout(install_row)

        self._executable_cap, self._executable = self._external_field(
            root,
            "GAME EXECUTABLE (relative to the game folder)",
            mono,
        )
        self._executable.setPlaceholderText("ExampleLoader.exe")
        self._client_versions_cap, self._client_versions = (
            self._external_field(
                root,
                "CLIENT VERSIONS (comma-separated, optional metadata)",
                mono,
            )
        )
        self._client_versions.setPlaceholderText("1.12.1, 1.12.2")

        kind_row = QHBoxLayout()
        kind_label = QLabel("SOURCE KIND", self)
        kind_label.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 9pt;"
        )
        kind_row.addWidget(kind_label)
        self._kind = QComboBox(self)
        self._kind.setObjectName("customModKind")
        self._kind.addItems(_KINDS)
        self._kind.currentTextChanged.connect(self._sync_kind_fields)
        kind_row.addWidget(self._kind, 1)
        root.addLayout(kind_row)

        # github_release / codeberg_release fields
        self._owner = self._field(root, "OWNER", mono, kind="release")
        self._repo = self._field(root, "REPOSITORY", mono, kind="release")
        self._pattern = self._field(
            root, "RELEASE ASSET PATTERN (fnmatch)", mono, kind="release"
        )
        self._prefer_no = self._field(
            root,
            "PREFER ASSETS WITHOUT SUBSTRING (optional)",
            mono,
            kind="release",
        )
        self._version_from_asset = QCheckBox(
            "Derive the version from the matched asset name", self
        )
        self._version_from_asset.setObjectName("customModVersionFrom")
        root.addWidget(self._version_from_asset)

        # direct_file / direct_tar fields
        self._url = self._field(root, "FILE URL (https)", mono, kind="direct")
        self._dest = self._field(
            root,
            "DESTINATION PATH (relative to the game folder)",
            mono,
            kind="direct",
        )
        self._dest.setPlaceholderText("d3d9.dll")
        self._pinned_version = self._field(
            root, "PINNED VERSION (optional)", mono, kind="direct"
        )

        map_label = QLabel(_MAP_HINT, self)
        map_label.setStyleSheet(f"color: {p.gold.name()}; font-weight: bold;")
        self._map_label = map_label
        root.addWidget(map_label)
        self._extract_map = QPlainTextEdit(self)
        self._extract_map.setObjectName("customModExtractMap")
        self._extract_map.setFont(mono)
        self._extract_map.setPlaceholderText("ExampleMod.dll = ExampleMod.dll")
        self._extract_map.setFixedHeight(64)
        root.addWidget(self._extract_map)

        dest_hint = QLabel(
            "Mods install into the game-folder root — DLLs land next to "
            "WoW.exe and get registered in dlls.txt.",
            self,
        )
        dest_hint.setObjectName("customModDestHint")
        dest_hint.setStyleSheet(f"color: {p.text_dim.name()};")
        dest_hint.setWordWrap(True)
        root.addWidget(dest_hint)

        self._error = QLabel("", self)
        self._error.setObjectName("customModError")
        self._error.setStyleSheet(f"color: {p.err.name()};")
        self._error.setWordWrap(True)
        root.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("customModCancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        submit = QPushButton("Add mod", self)
        submit.setObjectName("customModSubmit")
        submit.setCursor(Qt.PointingHandCursor)
        submit.clicked.connect(self._submit)
        buttons.addWidget(submit)
        root.addLayout(buttons)

        self._sync_kind_fields()
        self._sync_type_fields()

    # ── construction helpers ─────────────────────────────────────────────

    def _external_field(
        self, root: QVBoxLayout, label: str, font
    ) -> tuple[QLabel, QLineEdit]:
        """A labelled line edit tagged for show/hide with the
        external-launcher mod type."""
        cap = QLabel(label, self)
        cap.setStyleSheet(
            f"color: {self._palette.gold.name()};"
            "font-weight: bold; font-size: 9pt;"
        )
        root.addWidget(cap)
        edit = QLineEdit(self)
        edit.setFont(font)
        root.addWidget(edit)
        self._external_caps.append(cap)
        self._external_edits.append(edit)
        return cap, edit

    def _field(
        self,
        root: QVBoxLayout,
        label: str,
        font,
        kind: str | None = None,
    ) -> QLineEdit:
        """A labelled line edit; `kind` tags it for per-source-kind show/
        hide (None = always visible)."""
        cap = QLabel(label, self)
        cap.setStyleSheet(
            f"color: {self._palette.gold.name()};"
            "font-weight: bold; font-size: 9pt;"
        )
        root.addWidget(cap)
        edit = QLineEdit(self)
        edit.setFont(font)
        root.addWidget(edit)
        if kind == "release":
            self._release_caps.append(cap)
            self._release_edits.append(edit)
        elif kind == "direct":
            self._direct_caps.append(cap)
            self._direct_edits.append(edit)
        return edit

    # ── interaction ──────────────────────────────────────────────────────

    def _sync_type_fields(self):
        external = self._type.currentText() == "external-launcher"
        for widget in (*self._external_caps, *self._external_edits):
            widget.setVisible(external)
        if not external:
            # Hidden fields must not leak stale text into the saved entry.
            for edit in self._external_edits:
                edit.clear()

    def _sync_kind_fields(self):
        kind = self._kind.currentText()
        release = kind in ("github_release", "codeberg_release")
        direct = not release
        for widget in (*self._release_caps, *self._release_edits):
            widget.setVisible(release)
        for widget in (*self._direct_caps, *self._direct_edits):
            widget.setVisible(direct)
        self._map_label.setVisible(direct)

    def _parse_extract_map(self) -> dict | None:
        """The extract-map textarea as {pattern: dest}, or None when a line
        is malformed."""
        emap = {}
        for line in self._extract_map.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            pattern, sep, dest = line.partition("=")
            if not sep or not pattern.strip() or not dest.strip():
                return None
            emap[pattern.strip()] = dest.strip()
        return emap

    def _entry(self) -> dict:
        mid = self._id.text().strip()
        name = self._name.text().strip()
        kind = self._kind.currentText()
        mod_type = self._type.currentText()
        entry = {
            "id": mid,
            "name": name or mid,
            "type": mod_type,
            "installation": self._installation.currentText(),
            "description": self._description.text().strip(),
            "source": {"kind": kind},
        }
        if mod_type == "external-launcher":
            executable = self._executable.text().strip()
            if executable:
                entry["executable"] = executable
        client_versions = [
            v.strip()
            for v in self._client_versions.text().split(",")
            if v.strip()
        ]
        if client_versions:
            entry["client_versions"] = client_versions
        repo_url = self._repo_url.text().strip()
        if repo_url:
            entry["repo_url"] = repo_url
        emap = self._parse_extract_map()
        if emap is None:
            raise ValueError(
                "Extract map lines must look like 'pattern = dest/file'."
            )
        if kind in ("github_release", "codeberg_release"):
            entry["source"].update(
                {
                    "owner": self._owner.text().strip(),
                    "repo": self._repo.text().strip(),
                    "asset_pattern": self._pattern.text().strip(),
                }
            )
            prefer_no = self._prefer_no.text().strip()
            if prefer_no:
                entry["source"]["prefer_no"] = prefer_no
            if self._version_from_asset.isChecked():
                entry["source"]["version_from"] = "asset"
            if emap:
                entry["source"]["extract_map"] = emap
        else:
            entry["source"].update({"url": self._url.text().strip()})
            dest = self._dest.text().strip()
            if dest:
                entry["source"]["dest"] = dest
            pinned = self._pinned_version.text().strip()
            if pinned:
                entry["source"]["pinned_version"] = pinned
            if emap:
                entry["source"]["extract_map"] = emap
        return entry

    def _submit(self):
        try:
            entry = self._entry()
        except ValueError as e:
            self._error.setText(str(e))
            return
        if not entry["id"]:
            self._error.setText("A mod id is required.")
            return
        if catalog.validate_mod(entry) is None:
            self._error.setText(
                "This entry is not usable — check the highlighted fields "
                "(https URLs, safe relative paths, allowed source kinds)."
            )
            return
        self.modRequested.emit(entry)
        self.accept()

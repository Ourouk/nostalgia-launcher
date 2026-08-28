"""Detailed client update progress panel."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.helpers import fmt_size, fmt_speed
from . import metrics


class UpdatePanel(QWidget):
    """Render structured verify/download progress for the UPDATE tab."""

    forceRecheckClicked = Signal()

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        self.setObjectName("updatePanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("CLIENT UPDATE", self)
        title.setObjectName("updateTitle")
        font = title.font()
        font.setBold(True)
        font.setPointSize(metrics.PT_PAGE)
        title.setFont(font)
        title.setStyleSheet(f"color: {palette.gold_lt.name()};")
        title_row.addWidget(title)
        title_row.addStretch(1)
        # Force recheck: drop the hash/torrent-verdict cache and re-verify
        # every file — SHA-1 checksums when the manifest is reachable,
        # BitTorrent piece hashes otherwise (the worker picks per backend).
        self._recheck = QToolButton(self)
        self._recheck.setObjectName("updateRecheck")
        self._recheck.setText("⟳  Force recheck")
        self._recheck.setToolTip(
            "Re-verify every game file from scratch — checksums when the "
            "manifest is reachable, BitTorrent piece hashes otherwise"
        )
        self._recheck.setCursor(Qt.PointingHandCursor)
        self._recheck.setStyleSheet(
            f"QToolButton {{ color: {palette.text_dim.name()};"
            " font-size: 9pt; }"
            f"QToolButton:hover {{ color: {palette.gold.name()}; }}"
            f"QToolButton:disabled {{ color:"
            f" {palette.panel_bdr.name()}; }}"
        )
        self._recheck.clicked.connect(self.forceRecheckClicked)
        title_row.addWidget(self._recheck)
        root.addLayout(title_row)

        self._phase = QLabel("Idle", self)
        self._phase.setObjectName("updatePhase")
        root.addWidget(self._phase)

        self._progress = QProgressBar(self)
        self._progress.setObjectName("updateProgressBar")
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(True)
        root.addWidget(self._progress)

        self._file = QLabel("No update is running.", self)
        self._file.setObjectName("updateFile")
        self._file.setWordWrap(True)
        root.addWidget(self._file)

        self._file_list = QListWidget(self)
        self._file_list.setObjectName("updateFileList")
        self._file_list.setMinimumHeight(60)
        root.addWidget(self._file_list)

        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(8)
        self._transport = self._add_value(grid, 0, "Method")
        self._amount = self._add_value(grid, 1, "Progress")
        self._speed = self._add_value(grid, 2, "Speed")
        self._peers = self._add_value(grid, 3, "Peers")
        root.addLayout(grid)
        root.addStretch(1)

    def set_recheck_enabled(self, enabled: bool):
        """Enable/disable the Force recheck control (busy-guards live in the
        main window; the panel only renders the state it is given)."""
        self._recheck.setEnabled(enabled)

    def _add_value(self, grid, row: int, name: str) -> QLabel:
        label = QLabel(f"{name}:", self)
        label.setStyleSheet("font-weight: bold;")
        value = QLabel("-", self)
        value.setObjectName(f"update{name.replace(' ', '')}")
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(label, row, 0)
        grid.addWidget(value, row, 1)
        return value

    def set_updated_files(self, files):
        """Replace the updated-files list with `files`, each marked pending.
        Separators are normalized to "/" to match the streamed current-file
        paths (which are normalized before matching)."""
        self._file_list.clear()
        for rel in files:
            item = QListWidgetItem(str(rel).replace("\\", "/"))
            item.setData(Qt.UserRole, False)
            self._file_list.addItem(item)

    def _has_file(self, rel: str) -> bool:
        for i in range(self._file_list.count()):
            if self._file_list.item(i).text() == rel:
                return True
        return False

    def _set_file_done(self, rel: str) -> bool:
        """Mark a listed file as updated (match by text); True when found."""
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item.text() == rel:
                item.setData(Qt.UserRole, True)
                item.setForeground(self._palette.ok)
                return True
        return False

    def progress_changed(self, event):
        if event.phase:
            self._phase.setText(event.phase)
        elif event.label:
            self._phase.setText("Working")
        value = int(round(max(0.0, min(1.0, event.value)) * 100))
        self._progress.setValue(value)
        # Same visibility rule as the footer mini-bar: only visible while
        # something is actually in flight (0 < progress < 100).
        self._progress.setVisible(0 < value < 100)
        if event.current_file or event.label:
            self._file.setText(event.current_file or event.label)
        self._transport.setText(event.transport or "-")
        if event.total:
            self._amount.setText(
                f"{fmt_size(event.downloaded)} / {fmt_size(event.total)}"
            )
        else:
            self._amount.setText(f"{event.value * 100:.0f}%")
        self._speed.setText(fmt_speed(event.speed) if event.speed else "-")
        self._peers.setText(str(event.peers) if event.peers else "-")
        # Track the file currently being updated. When it matches an item
        # pre-seeded by set_updated_files() it's marked done; otherwise it's
        # appended (HTTP streams per-file paths, torrent only reports its
        # own name which has no "/").
        if event.current_file and "/" in event.current_file:
            txt = event.current_file.replace("\\", "/")
            if not self._set_file_done(txt) and not self._has_file(txt):
                item = QListWidgetItem(txt)
                item.setData(Qt.UserRole, True)
                item.setForeground(self._palette.ok)
                self._file_list.addItem(item)

    def status_changed(self, text: str):
        if text in ("Verifying…", "Updating…"):
            self._phase.setText(text)
            self._file.setText("Preparing client update…")
            # Keep the needed-files list populated across the verify→update
            # sequence: a torrent download reports no per-file paths, so
            # clearing here would leave the list empty for the whole update.

    def operation_finished(self, kind: str, ok: bool, message: str):
        if kind in ("update", "verify"):
            self._progress.setVisible(False)
            if ok:
                self._phase.setText(
                    "Verified" if kind == "verify" else "Complete"
                )
                for i in range(self._file_list.count()):
                    item = self._file_list.item(i)
                    item.setData(Qt.UserRole, True)
                    item.setForeground(self._palette.ok)
            else:
                self._phase.setText(
                    "Update required" if kind == "verify" else "Failed"
                )
                self._file.setText(message or "Update failed.")
        elif kind == "mods" and ok:
            self._phase.setText("Updating addons and mods")
            self._file.setText("Mods complete; checking addons…")
        elif kind == "addons" and ok:
            self._phase.setText("Complete")

    def operation_failed(self, kind: str, message: str):
        if kind in ("update", "verify"):
            self._progress.setVisible(False)
            self._phase.setText("Failed")
            self._file.setText(message or "Update failed.")

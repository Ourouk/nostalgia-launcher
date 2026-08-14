"""Vanilla WoW Launcher Qt (PySide6) session-log window.

A non-modal QWidget titled "Session log" whose read-only text area renders
(text, tag) log lines in the colour scheme — ok→green, err→red, acct→gold,
dim→dim gray, default→text. The MainWindow owns the session-log buffer and
feeds this window through append(); seed() renders the accumulated buffer
when the window is (re)opened.
"""

from PySide6.QtGui import QFontDatabase, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import Palette, theme_qss


class LogWindow(QWidget):
    """Non-modal, read-only session log, coloured per line tag."""

    def __init__(self, palette: Palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        self.setObjectName("logWindow")
        self.setWindowTitle("Session log")
        self.setStyleSheet(theme_qss(palette))
        self.resize(760, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        title = QLabel("SESSION LOG", self)
        title.setStyleSheet(
            f"color: {palette.gold.name()}; font-weight: bold;"
            " font-size: 10pt;")
        layout.addWidget(title)

        self._text = QPlainTextEdit(self)
        self._text.setObjectName("logText")
        self._text.setReadOnly(True)
        self._text.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        layout.addWidget(self._text, 1)

    def _color_for_tag(self, tag: str):
        p = self._palette
        if tag == "ok":
            return p.ok
        if tag == "err":
            return p.err
        if tag == "acct":
            return p.gold
        if tag == "dim":
            return p.text_dim
        return p.text

    def append(self, text: str, tag: str = ""):
        """Render one (text, tag) line and scroll to the bottom. Main thread
        only."""
        fmt = QTextCharFormat()
        fmt.setForeground(self._color_for_tag(tag))
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, fmt)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

    def seed(self, buffer):
        """Render the accumulated (text, tag) tuples in `buffer`."""
        for text, tag in buffer:
            self.append(text, tag)

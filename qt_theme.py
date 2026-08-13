"""Octo Updater Qt (PySide6) design system — palette, stylesheet, metrics.

Phase 2 of the Qt migration: the PySide6 counterpart of the color constants
in app.py and the layout math in ui_metrics.py. It must stay Tk-free so the
Qt backend can import it without pulling in tkinter.

- `Palette` mirrors the app.py color constants as QColor values.
- `theme_qss(palette)` renders the dark-purple/gold stylesheet.
- `default_window_size(screen)` derives the initial window size from the
  toolkit-agnostic ui_metrics helpers. Qt works in logical pixels and applies
  the display scale factor internally, so the ui_metrics scale stays at 1.0.
"""

from PySide6.QtGui import QColor

from ui_metrics import UIScale, initial_window_size

# Hex values copied verbatim from app.py; importing app.py itself would pull
# in tkinter, which this module must never do.
HEX = {
    "C_BG": "#120e1a",
    "C_PANEL": "#161120",
    "C_HDR": "#0d0a14",
    "C_PANEL_BDR": "#261d3a",
    "C_DIVIDER": "#2a2142",
    "C_GOLD": "#c8922a",
    "C_GOLD_LT": "#e8b84b",
    "C_PURPLE": "#8a4fa5",
    "C_GREEN_BTN": "#4a7c2f",
    "C_GREEN_HOV": "#5a9438",
    "C_TEXT": "#d8d4cc",
    "C_TEXT_DIM": "#7a7670",
    "C_LOG_BG": "#0f0b16",
    "C_OK": "#6abf69",
    "C_ERR": "#bf6969",
    "C_MOD_HL": "#a8b83c",
    "C_PARCH": "#e9dcb8",
    "C_PARCH_BAND": "#ddcda0",
    "C_PARCH_LINE": "#c3b083",
    "C_PARCH_TITLE": "#7c5a12",
    "C_PARCH_TEXT": "#3a352a",
    "C_PARCH_DIM": "#8b8064",
    "C_PARCH_LINK": "#a3561c",
    "C_PARCH_EDGE": "#b7a678",
}

_ATTRS = [
    ("bg", "C_BG"),
    ("panel", "C_PANEL"),
    ("hdr", "C_HDR"),
    ("panel_bdr", "C_PANEL_BDR"),
    ("divider", "C_DIVIDER"),
    ("gold", "C_GOLD"),
    ("gold_lt", "C_GOLD_LT"),
    ("purple", "C_PURPLE"),
    ("green_btn", "C_GREEN_BTN"),
    ("green_hov", "C_GREEN_HOV"),
    ("text", "C_TEXT"),
    ("text_dim", "C_TEXT_DIM"),
    ("log_bg", "C_LOG_BG"),
    ("ok", "C_OK"),
    ("err", "C_ERR"),
    ("mod_hl", "C_MOD_HL"),
    ("parch", "C_PARCH"),
    ("parch_band", "C_PARCH_BAND"),
    ("parch_line", "C_PARCH_LINE"),
    ("parch_title", "C_PARCH_TITLE"),
    ("parch_text", "C_PARCH_TEXT"),
    ("parch_dim", "C_PARCH_DIM"),
    ("parch_link", "C_PARCH_LINK"),
    ("parch_edge", "C_PARCH_EDGE"),
]


class Palette:
    """Qt color set mirroring the Tk palette in app.py.

    Convenience attributes (``palette.bg``, ``palette.gold``, ...) plus a
    ``colors`` dict keyed by the app.py constant name for dynamic lookup.
    """

    def __init__(self):
        self.colors = {name: QColor(value) for name, value in HEX.items()}
        for attr, key in _ATTRS:
            setattr(self, attr, self.colors[key])


def theme_qss(palette):
    """Stylesheet for the Octo Updater dark purple/gold look."""
    p = palette
    return f"""
QMainWindow {{
    background-color: {p.bg.name()};
}}
QLabel {{
    color: {p.text.name()};
    background-color: transparent;
}}
QLabel[dimm="true"] {{
    color: {p.text_dim.name()};
}}
QPushButton {{
    background-color: {p.panel.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    border-color: {p.gold.name()};
    color: {p.gold_lt.name()};
}}
QPushButton:pressed {{
    background-color: {p.hdr.name()};
}}
QPushButton:disabled {{
    color: {p.text_dim.name()};
}}
QCheckBox {{
    color: {p.text.name()};
    background-color: transparent;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 3px;
    background-color: {p.hdr.name()};
}}
QCheckBox::indicator:checked {{
    background-color: {p.gold.name()};
}}
QLineEdit {{
    background-color: {p.hdr.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: {p.gold.name()};
    selection-color: {p.hdr.name()};
}}
QTextEdit {{
    background-color: {p.log_bg.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
}}
QListWidget {{
    background-color: {p.panel.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
    outline: none;
}}
QListWidget::item:selected {{
    background-color: {p.panel_bdr.name()};
    color: {p.gold_lt.name()};
}}
QScrollArea {{
    background-color: {p.bg.name()};
    border: none;
}}
QScrollBar:vertical {{
    background-color: {p.bg.name()};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background-color: {p.panel_bdr.name()};
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {p.gold.name()};
}}
QScrollBar:horizontal {{
    background-color: {p.bg.name()};
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background-color: {p.panel_bdr.name()};
    min-width: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {p.gold.name()};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px;
    height: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background-color: transparent;
}}
QSplitter::handle {{
    background-color: {p.divider.name()};
}}
QTabBar::tab {{
    background-color: {p.panel.name()};
    color: {p.text_dim.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: 6px 14px;
}}
QTabBar::tab:selected {{
    background-color: {p.hdr.name()};
    color: {p.gold_lt.name()};
}}
QTabBar::tab:hover {{
    color: {p.gold.name()};
}}
QToolButton {{
    background-color: transparent;
    color: {p.text.name()};
    border: none;
    padding: 4px;
}}
QToolButton:hover {{
    color: {p.gold_lt.name()};
}}
"""


def default_window_size(screen):
    """Initial logical window size for a QScreen (or fallback 1920x1080).

    The ui_metrics scale is fixed at 1.0: Qt renders in logical pixels and
    applies the display scale factor internally, so there is nothing to scale
    here — only the 90%-of-screen cap from `ui_metrics.initial_window_size`.
    """
    if screen is None:
        return initial_window_size(UIScale(None), 1920, 1080)
    geo = screen.availableGeometry()
    return initial_window_size(UIScale(None), geo.width(), geo.height())

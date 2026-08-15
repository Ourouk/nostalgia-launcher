"""Vanilla WoW Launcher Qt (PySide6) design system — palette, stylesheet, metrics.

The source of the dark purple/gold palette (the octowow theme) and the QSS
stylesheet. Pure Qt (PySide6) — no other GUI toolkit involved.

The palette can be themed by the launcher configuration: `palette_for_config`
builds a `Palette` from the server's ``"theme"`` color overrides (see
`core/themes`), falling back to the default octowow palette on any problem.

- `Palette` exposes the palette colors as QColor values.
- `theme_qss(palette)` renders the dark-purple/gold stylesheet.
"""

from PySide6.QtGui import QColor

from ...core.themes import DEFAULT_COLORS, resolve_colors

# Hex values of the Vanilla WoW Launcher palette (dark backgrounds, gold accents,
# the parchment addon cards, and the semantic ok/err colors).
HEX = dict(DEFAULT_COLORS)

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
    """Qt color set for the dark purple/gold design.

    Convenience attributes (``palette.bg``, ``palette.gold``, ...) plus a
    ``colors`` dict keyed by the HEX constant name for dynamic lookup. A
    ``colors`` dict may be passed to theme the palette (the launcher config's
    ``"theme"`` overrides); the default is the octowow palette.
    """

    def __init__(self, colors: dict | None = None):
        colors = colors or HEX
        self.colors = {name: QColor(value) for name, value in colors.items()}
        for attr, key in _ATTRS:
            setattr(self, attr, self.colors[key])
        # Extra accent colors beyond the core palette (pink/warn), added as
        # convenience attributes for the addons panel.
        self.pink = QColor("#d76f9e")
        self.pink_lt = QColor("#eb96ba")
        self.warn = QColor("#d4b43c")


def palette_for_config(cfg) -> Palette:
    """The app palette for a launcher configuration.

    Uses the config's ``theme`` color overrides (falling back to the default
    octowow palette on any problem), or the default palette when there is no
    config.
    """
    spec = getattr(cfg, "theme", None)
    return Palette(resolve_colors(spec))


def theme_qss(palette):
    """Stylesheet for the Vanilla WoW Launcher dark purple/gold look."""
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

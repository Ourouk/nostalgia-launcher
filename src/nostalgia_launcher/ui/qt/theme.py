"""Nostalgia Launcher Qt (PySide6) design system — palette, stylesheet, metrics.

Two theming modes:

- **Themed**: the launcher configuration carries a valid ``theme`` object.
  The source of the dark purple/gold palette (the default theme) and the
  QSS stylesheet. `palette_for_config` builds a `Palette` from the server's
  ``"theme"`` color overrides (see `core/themes`), and `apply_theme` applies
  `theme_qss` to the widget tree.
- **Native** (no config theme, or an invalid one): standard widgets render
  with the platform style — no stylesheet is applied. A `Palette` derived
  from the system `QPalette` (`system_palette`) still drives the few
  in-content accents (section titles, error/ok states), while semantic and
  parchment slots keep their fixed brand values.

Pure Qt (PySide6) — no other GUI toolkit involved.
"""

from PySide6.QtGui import QColor, QGuiApplication, QPalette

from ...core.themes import (
    DEFAULT_COLORS,
    has_valid_theme,
    resolve_colors,
    resolve_logo,
)

# Hex values of the Nostalgia Launcher palette (dark backgrounds, gold accents,
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
    ("pink", "C_PINK"),
    ("pink_lt", "C_PINK_LT"),
    ("warn", "C_WARN"),
    ("btn_text", "C_BTN_TEXT"),
]


class Palette:
    """Qt color set for the launcher design.

    Convenience attributes (``palette.bg``, ``palette.gold``, ...) plus a
    ``colors`` dict keyed by the HEX constant name for dynamic lookup. A
    ``colors`` dict may be passed to theme the palette (the launcher config's
    ``"theme"`` overrides); the default is the default palette.

    ``themed`` marks a config-driven palette: only then is the global QSS
    applied (see `apply_theme`). A system-derived palette keeps the
    attributes usable for in-content accents while widgets stay native.
    """

    def __init__(self, colors: dict | None = None, themed: bool = True):
        colors = colors or HEX
        self.themed = themed
        self.colors = {name: QColor(value) for name, value in colors.items()}
        for attr, key in _ATTRS:
            setattr(self, attr, self.colors[key])


# QPalette role → color slot for the native (unthemed) mode. Slots not
# listed (semantic ok/err/pink/warn, the parchment card, the greens) keep
# their fixed brand values — they are content colors, readable on both the
# system's light and dark scheme.
_NATIVE_ROLE_MAP = {
    "C_BG": QPalette.ColorRole.Window,
    "C_PANEL": QPalette.ColorRole.Base,
    "C_HDR": QPalette.ColorRole.Button,
    "C_PANEL_BDR": QPalette.ColorRole.Mid,
    "C_DIVIDER": QPalette.ColorRole.Midlight,
    "C_GOLD": QPalette.ColorRole.Highlight,
    "C_GOLD_LT": QPalette.ColorRole.Highlight,
    "C_PURPLE": QPalette.ColorRole.Link,
    "C_MOD_HL": QPalette.ColorRole.Highlight,
    "C_TEXT": QPalette.ColorRole.WindowText,
    "C_TEXT_DIM": QPalette.ColorRole.PlaceholderText,
    "C_LOG_BG": QPalette.ColorRole.Base,
    "C_BTN_TEXT": QPalette.ColorRole.ButtonText,
}


def system_palette() -> Palette:
    """A Palette derived from the system QPalette (native mode)."""
    qp = QGuiApplication.palette()
    colors = dict(HEX)
    for key, role in _NATIVE_ROLE_MAP.items():
        colors[key] = qp.color(role).name()
    highlight = qp.color(QPalette.ColorRole.Highlight)
    if highlight.isValid():
        colors["C_GOLD_LT"] = highlight.lighter(125).name()
    return Palette(colors, themed=False)


def palette_for_config(cfg) -> Palette:
    """The app palette for a launcher configuration.

    A valid config ``theme`` yields the themed palette (defaults overlaid
    with the config's color overrides). Without a theme — or with an
    invalid one — the app runs native: a system-derived palette.
    """
    spec = getattr(cfg, "theme", None)
    if not has_valid_theme(spec):
        return system_palette()
    return Palette(resolve_colors(spec))


def logo_for_config(cfg) -> str | None:
    """The header logo URL from a launcher configuration's theme, or None.

    Only an https URL is accepted; anything else (missing, malformed, not a
    dict) resolves to no logo rather than an error.
    """
    spec = getattr(cfg, "theme", None)
    return resolve_logo(spec)


def theme_qss(palette):
    """Stylesheet for the Nostalgia Launcher dark purple/gold look."""
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
QCheckBox::indicator:hover {{
    border-color: {p.gold.name()};
}}
QCheckBox::indicator:checked {{
    background-color: {p.gold.name()};
    border-color: {p.gold_lt.name()};
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
QPushButton[variant="primary"] {{
    color: {p.gold.name()};
    border: 1px solid {p.gold.name()};
    border-radius: 4px;
    background-color: {p.panel_bdr.name()};
    padding: 5px 18px;
    font-weight: bold;
}}
QPushButton[variant="primary"]:hover {{
    background-color: {p.gold.name()};
    color: {p.hdr.name()};
}}
QPushButton[variant="positive"] {{
    color: {p.ok.name()};
    border: 1px solid {p.ok.name()};
    border-radius: 4px;
    background-color: {p.panel_bdr.name()};
    padding: 5px 18px;
    font-weight: bold;
}}
QPushButton[variant="positive"]:hover {{
    background-color: {p.ok.name()};
    color: {p.hdr.name()};
}}
QPushButton[variant="outline"] {{
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
    background-color: transparent;
    padding: 3px 10px;
}}
QPushButton[variant="outline"]:hover {{
    border-color: {p.gold.name()};
    color: {p.gold_lt.name()};
}}
QPushButton[variant="compact"] {{
    color: {p.gold.name()};
    border: 1px solid {p.gold.name()};
    border-radius: 4px;
    background-color: transparent;
    padding: 1px 10px;
}}
QPushButton[variant="compact"]:hover {{
    background-color: {p.gold.name()};
    color: {p.hdr.name()};
}}
QPushButton[variant="primary"]:disabled,
QPushButton[variant="positive"]:disabled {{
    color: {p.text_dim.name()};
    border-color: {p.panel_bdr.name()};
    background-color: {p.panel.name()};
}}
QLabel[role="sectionTitle"] {{
    color: {p.gold_lt.name()};
    font-size: 12pt;
    font-weight: bold;
}}
QFrame[role="hairline"] {{
    background-color: {p.divider.name()};
    border: none;
    max-height: 1px;
}}
QDialog {{
    background-color: {p.bg.name()};
}}
QProgressBar {{
    background-color: {p.hdr.name()};
    color: transparent;
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {p.gold.name()};
    border-radius: 3px;
}}
QSpinBox {{
    background-color: {p.hdr.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: {p.gold.name()};
    selection-color: {p.hdr.name()};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background-color: transparent;
    border: none;
    width: 16px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    color: {p.gold_lt.name()};
}}
QComboBox {{
    background-color: {p.hdr.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    border-radius: 4px;
    padding: 3px 8px;
}}
QComboBox:hover {{
    border-color: {p.gold.name()};
}}
QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
QComboBox QAbstractItemView {{
    background-color: {p.panel.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
    selection-background-color: {p.panel_bdr.name()};
    selection-color: {p.gold_lt.name()};
    outline: none;
}}
QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus {{
    border: 1px solid {p.gold.name()};
}}
QToolTip {{
    background-color: {p.hdr.name()};
    color: {p.text.name()};
    border: 1px solid {p.gold_lt.name()};
    padding: 3px 6px;
}}
QMenu {{
    background-color: {p.panel.name()};
    color: {p.text.name()};
    border: 1px solid {p.panel_bdr.name()};
}}
QMenu::item {{
    padding: 4px 18px;
}}
QMenu::item:selected {{
    background-color: {p.panel_bdr.name()};
    color: {p.gold_lt.name()};
}}
"""


def apply_theme(widget, palette, extra_qss: str = ""):
    """Apply the global stylesheet to `widget` when the app is themed.

    Native mode (no valid launcher-config theme) applies nothing — widgets
    render with the platform style; in-content accents still come from the
    palette attributes. `extra_qss` is appended verbatim in themed mode
    (e.g. a dialog's own background rule). Idempotent.
    """
    if palette.themed:
        widget.setStyleSheet(theme_qss(palette) + extra_qss)
    else:
        widget.setStyleSheet("")

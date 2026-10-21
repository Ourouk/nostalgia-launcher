"""Nostalgia Launcher design system — palette for the QML shell.

Two theming modes:

- **Themed**: the launcher configuration carries a valid ``theme`` object.
  The source of the dark purple/gold palette (the default theme).
  `palette_for_config` builds a `Palette` from the server's
  ``"theme"`` color overrides (see `core/themes`).
- **Native** (no config theme, or an invalid one): standard widgets render
  with the platform style — no stylesheet is applied. A `Palette` derived
  from the system `QPalette` (`system_palette`) still drives the few
  in-content accents (section titles, error/ok states), while semantic and
  parchment slots keep their fixed brand values.

Pure Qt (PySide6) — no other GUI toolkit involved.
"""

from PySide6.QtGui import QColor, QGuiApplication, QPalette

from ..core.themes import (
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

    ``themed`` marks a config-driven palette. A system-derived palette keeps
    the attributes usable for in-content accents while widgets stay native.
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

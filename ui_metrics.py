"""UI metrics: DPI-aware scaling and responsive layout math.

The interface was designed around fixed logical dimensions (1000x700). To
make it work well across display scalings (100%–200%) and window sizes,
all geometry flows through these helpers:

- `UIScale` maps a logical point size to the detected display scale factor
  and produces *pixel-sized* Tk font specs (negative size), so fonts scale
  reliably even on platforms where Tk's `tk scaling` is ignored.
- Pure layout helpers (`panel_rect`, `news_columns`, `settings_rect`,
  `layout_mode`) compute placements from a *current* window size instead of
  hardcoded constants, so the UI can resize and reflow.

The pure helpers take no Tk dependency, which keeps them unit-testable.
"""

import math

from platform_support import ui_font_family

# Logical design size and chrome heights (in "100%" pixels).
BASE_W = 1000
BASE_H = 700
HDR_H  = 108
FOOT_H = 130
PANEL_PAD = 40

# Reference DPI used by Tk for point sizes (1.333 px/point == 96 DPI).
TK_BASE_SCALING = 96.0 / 72.0


class UIScale:
    """Detects the display scale factor and provides scaled sizes/fonts.

    factor == 1.0 at 96 DPI; 1.25 at 125%, 1.5 at 150%, 2.0 at 200%.
    """

    def __init__(self, root=None):
        self.factor = 1.0
        if root is not None:
            self.update(root)

    def update(self, root):
        """Re-derive the scale factor from the (now-mapped) root window.

        Prefers the DPI inferred from the physical screen size, falling back
        to Tk's own `tk scaling` value.
        """
        factor = _detect_scale_from_screen(root)
        if factor is None:
            factor = _detect_scale_from_tk(root)
        self.factor = clamp(factor, 0.75, 3.0)

    def s(self, value):
        """Scale a logical (100% DPI) pixel size to the display size."""
        return max(1, int(round(value * self.factor)))

    def px(self, size):
        """Scaled pixel size for a logical (100% DPI) point size."""
        return max(7, int(round(size * self.factor * TK_BASE_SCALING)))

    def font(self, size, weight=None, family=None):
        """A Tk font spec at the given logical point size, DPI-scaled.

        The size is negative (Tk pixel mode) so the rendered size is exactly
        `px(size)` pixels regardless of the platform's `tk scaling` handling.
        """
        fam = family or ui_font_family()
        spec = (fam, -self.px(size))
        if weight:
            spec += (weight,)
        return spec

    def mono(self, size, weight=None):
        """A monospace Tk font spec at the given logical point size."""
        return self.font(size, weight, family="Consolas")

    def tk_scaling(self) -> float:
        """The `tk scaling` value that makes points render at factor×96 DPI."""
        return TK_BASE_SCALING * self.factor


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _detect_scale_from_screen(root):
    """Scale factor from the physical screen diagonal (Windows/macOS/Linux).

    Returns None when the reported screen geometry is unusable.
    """
    try:
        px = float(root.winfo_screenwidth())
        mm = float(root.winfo_screenmmwidth())
    except Exception:
        return None
    if mm <= 0 or px <= 0:
        return None
    dpi = px / (mm / 25.4)
    if not (72.0 <= dpi <= 260.0):   # ignore broken/headless reports
        return None
    return dpi / 96.0


def _detect_scale_from_tk(root):
    """Fallback: Tk's own scaling (px per point), normalized to 96 DPI."""
    try:
        scaling = float(root.tk.call("tk", "scaling"))
    except Exception:
        return 1.0
    if scaling <= 0:
        return 1.0
    return scaling / TK_BASE_SCALING


def initial_window_size(scale: UIScale, sw: int, sh: int):
    """Window size for a fresh start: the design size scaled, capped at
    ~90% of the screen so it never overflows on small displays."""
    w = BASE_W * scale.factor
    h = BASE_H * scale.factor
    max_w, max_h = int(sw * 0.92), int(sh * 0.92)
    return min(int(w), max_w), min(int(h), max_h)


def panel_rect(w: int, h: int, top: int = HDR_H + 11):
    """Main content panel placement for a window of w×h."""
    x, y = PANEL_PAD, top
    width = w - PANEL_PAD * 2
    height = h - top - FOOT_H - 10
    return (x, y, max(width, 320), max(height, 120))


def news_columns(inner_w: int):
    """Split the news panel inner width into (featured, announcements)."""
    left = int(inner_w * 0.60)
    right = inner_w - left - 12
    return left, max(right, 200)


def progress_width(w: int) -> int:
    """Width of the footer progress bar for a window of width w."""
    return max(0, w - 250 - 40)


def settings_rect(w: int, h: int):
    """Settings dialog size for a window of w×h (min 560×380, max 800×500)."""
    mw = int(clamp(int(w * 0.80), 560, 800))
    mh = int(clamp(int(h * 0.78), 380, 500))
    return mw, mh


def layout_mode(w: int) -> str:
    """Responsive layout tier for a window of width w."""
    if w >= 1100:
        return "large"
    if w <= 850:
        return "compact"
    return "standard"

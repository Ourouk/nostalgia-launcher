"""UI metrics: responsive layout math for the Qt interface.

The interface is designed around fixed logical dimensions (1000x700). All
geometry flows through these pure helpers, which compute placements from a
*current* window size instead of hardcoded constants, so the UI can resize
and reflow:

- `initial_window_size` derives a fresh window size from the screen,
  optionally multiplied by a scale factor, capped so it never overflows.
- Layout helpers (`panel_rect`, `news_columns`, `settings_rect`,
  `layout_mode`) place panels and dialogs for a given window size.

Display scaling needs no detection here: Qt renders in logical pixels and
applies the display scale factor internally.
"""

# Logical design size and chrome heights (in "100%" pixels).
BASE_W = 1000
BASE_H = 700
HDR_H  = 108
FOOT_H = 130
PANEL_PAD = 40


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def initial_window_size(sw: int, sh: int, factor: float = 1.0) -> tuple[int, int]:
    """Window size for a fresh start: the design size × `factor`, capped at
    ~90% of the screen so it never overflows on small displays."""
    w = BASE_W * factor
    h = BASE_H * factor
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

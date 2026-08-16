"""UI metrics: responsive layout math for the Qt interface.

The interface is designed around fixed logical dimensions (1000x700). All
geometry flows through these pure helpers, which compute placements from a
*current* window size instead of hardcoded constants, so the UI can resize
and reflow:

- `initial_window_size` derives a fresh window size from the screen,
  optionally multiplied by a scale factor, capped so it never overflows.

Display scaling needs no detection here: Qt renders in logical pixels and
applies the display scale factor internally.
"""

# Logical design size and chrome heights (in "100%" pixels).
BASE_W = 1000
BASE_H = 700
HDR_H = 108
FOOT_H = 130
PANEL_PAD = 40


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def initial_window_size(
    sw: int, sh: int, factor: float = 1.0
) -> tuple[int, int]:
    """Window size for a fresh start: the design size × `factor`, capped at
    ~90% of the screen so it never overflows on small displays."""
    w = BASE_W * factor
    h = BASE_H * factor
    max_w, max_h = int(sw * 0.92), int(sh * 0.92)
    return min(int(w), max_w), min(int(h), max_h)

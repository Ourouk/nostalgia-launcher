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

Scale detection honors, in order of precedence:

1. The explicit `OCTO_UI_SCALE` environment variable (e.g. `OCTO_UI_SCALE=1.25`).
2. The active desktop's toolkit scaling — GNOME/GTK (`GDK_SCALE`,
   `GDK_DPI_SCALE`) and KDE/Qt (`QT_SCALE_FACTOR`, `QT_SCREEN_SCALE_FACTORS`)
   — which is what reliably reports *fractional* scaling like 125%.
3. Tk's own `tk scaling` on Linux, where the X11/XWayland physical geometry
   report is often bogus under Wayland.
4. The physical screen DPI as a last resort (primary on Windows/macOS).

The pure helpers take no Tk dependency, which keeps them unit-testable.
"""

import os

from platform_support import is_linux, is_macos, ui_font_family

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

    def __init__(self, root=None, env=None, platform=None):
        self.factor = 1.0
        self._env = env if env is not None else os.environ
        self._platform = platform
        if root is not None:
            self.update(root)

    def update(self, root):
        """Re-derive the scale factor from the environment and root window."""
        factor = _detect_scale(root, env=self._env, platform=self._platform)
        self.factor = clamp(factor if factor is not None else 1.0, 0.75, 3.0)

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


def _auto_platform() -> str:
    """'linux', 'macos' or 'windows' for the current host."""
    if is_linux():
        return "linux"
    if is_macos():
        return "macos"
    return "windows"


def _detect_scale(root, env=None, platform=None):
    """Ordered scale-factor detection (see the module docstring).

    Returns a positive factor, or None when nothing usable was found.
    """
    env = env if env is not None else os.environ
    if platform is None:
        platform = _auto_platform()
    factor = _detect_scale_from_override(env)
    if factor is None:
        factor = _detect_scale_from_desktop(env)
    if factor is None:
        # On Linux the X11/XWayland physical report is often wrong under
        # Wayland, so trust Tk's own scaling first. Windows/macOS report
        # physical geometry reliably, so prefer that there.
        if platform == "linux":
            factor = _detect_scale_from_tk(root)
            if factor is None:
                factor = _detect_scale_from_screen(root)
        else:
            factor = _detect_scale_from_screen(root)
            if factor is None:
                factor = _detect_scale_from_tk(root)
    return factor


def _detect_scale_from_override(env):
    """OCTO_UI_SCALE explicit override (invalid values are ignored)."""
    raw = env.get("OCTO_UI_SCALE")
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _desktop_kind(env):
    """'gnome', 'kde' or None, from the desktop session environment."""
    cur = (env.get("XDG_CURRENT_DESKTOP") or "").lower()
    sess = (env.get("XDG_SESSION_DESKTOP") or "").lower()
    gdm = (env.get("GDMSESSION") or "").lower()
    kde_full = (env.get("KDE_FULL_SESSION") or "").lower() == "true"
    if kde_full or "kde" in cur or "kde" in sess \
            or "plasma" in sess or "plasma" in gdm:
        return "kde"
    if "gnome" in cur or "gnome" in sess or "gnome" in gdm \
            or "ubuntu" in cur:
        return "gnome"
    return None


def _detect_scale_from_gnome(env):
    """GDK integer scale × fractional DPI scale (GNOME/GTK)."""
    gdk = env.get("GDK_SCALE")
    dpi = env.get("GDK_DPI_SCALE")
    if gdk is None and dpi is None:
        return None
    try:
        s = float(gdk) if gdk else 1.0
        d = float(dpi) if dpi else 1.0
    except (TypeError, ValueError):
        return None
    if s <= 0 or d <= 0:
        return None
    return s * d


def _detect_scale_from_kde(env):
    """Qt global scale factor, else the largest per-screen factor."""
    qf = env.get("QT_SCALE_FACTOR")
    if qf:
        try:
            v = float(qf)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    qs = env.get("QT_SCREEN_SCALE_FACTORS")
    if qs:
        values = []
        for entry in qs.split(";"):
            if "=" in entry:
                try:
                    values.append(float(entry.split("=", 1)[1]))
                except (TypeError, ValueError):
                    pass
        if values:
            return max(values)
    return None


def _detect_scale_from_desktop(env):
    """Scale from the active GNOME/KDE desktop's toolkit variables."""
    kind = _desktop_kind(env)
    if kind == "gnome":
        factor = _detect_scale_from_gnome(env)
        if factor is not None:
            return factor
    elif kind == "kde":
        factor = _detect_scale_from_kde(env)
        if factor is not None:
            return factor
    # Honor explicit toolkit overrides even on unrecognized desktops.
    factor = _detect_scale_from_kde(env)
    if factor is not None:
        return factor
    return _detect_scale_from_gnome(env)


def _detect_scale_from_screen(root):
    """Scale factor from the physical screen diagonal (Windows/macOS).

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
        return None
    if scaling <= 0:
        return None
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

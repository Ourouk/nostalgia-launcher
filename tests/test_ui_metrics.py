"""Unit tests for the UI metrics / scaling helpers."""

import pytest

import ui_metrics
from ui_metrics import (
    UIScale,
    initial_window_size,
    layout_mode,
    news_columns,
    panel_rect,
    progress_width,
    settings_rect,
)


class _FakeTcl:
    def __init__(self, scaling):
        self._scaling = scaling

    def call(self, *_):
        return self._scaling


class _FakeRoot:
    """Minimal stand-in exposing the bits UIScale queries."""

    def __init__(self, sw=1920, smm=508, scaling=1.333):
        self._sw, self._smm = sw, smm
        self.tk = _FakeTcl(scaling)

    def winfo_screenwidth(self):
        return self._sw

    def winfo_screenmmwidth(self):
        return self._smm


# ── scale detection ─────────────────────────────────────────────────────────

def test_scale_from_screen_96dpi():
    s = UIScale(_FakeRoot(sw=1920, smm=508))   # ~96 DPI
    assert s.factor == pytest.approx(1.0, abs=0.02)


def test_scale_from_screen_150_percent():
    s = UIScale(_FakeRoot(sw=1920, smm=338))   # ~144 DPI → 1.5x
    assert s.factor == pytest.approx(1.5, abs=0.05)


def test_scale_falls_back_to_tk_scaling():
    s = UIScale(_FakeRoot(sw=0, smm=0, scaling=2.0))
    assert s.factor == pytest.approx(1.5, abs=0.02)


def test_scale_clamped():
    s = UIScale(_FakeRoot(sw=4000, smm=254))   # ~400 DPI — bogus
    assert s.factor == ui_metrics.clamp(s.factor, 0.75, 3.0)
    assert 0.75 <= s.factor <= 3.0


def test_no_root_keeps_factor_one():
    assert UIScale(None).factor == 1.0


# ── scaled sizes / fonts ────────────────────────────────────────────────────

def test_scale_s_rounds_up_minimum():
    s = UIScale(None)
    s.factor = 2.0
    assert s.s(10) == 20
    assert s.s(0) == 1


def test_font_scales_and_keeps_weight():
    s = UIScale(None)
    s.factor = 1.5
    fam, px = s.font(10)[0], s.font(10)[1]
    assert px == -int(round(10 * 1.5 * (96 / 72)))   # pixel-sized (negative)
    assert s.font(9, "bold")[2] == "bold"


def test_font_uses_negative_pixel_size():
    s = UIScale(None)
    s.factor = 1.0
    assert s.font(10)[1] < 0


def test_font_accepts_family():
    s = UIScale(None)
    s.factor = 1.0
    assert s.font(11, family="Segoe UI Symbol")[0] == "Segoe UI Symbol"


def test_mono_font_family():
    s = UIScale(None)
    s.factor = 1.0
    spec = s.mono(9)
    assert spec[0] == "Consolas"
    assert spec[1] < 0


def test_tk_scaling_matches_factor():
    s = UIScale(None)
    s.factor = 1.5
    assert s.tk_scaling() == pytest.approx(2.0, abs=0.01)


# ── layout math ─────────────────────────────────────────────────────────────

def test_initial_window_size_scales():
    s = UIScale(None)
    s.factor = 1.5
    w, h = initial_window_size(s, 2560, 1440)
    assert w == 1500 and h == 1050


def test_initial_window_size_caps_to_screen():
    s = UIScale(None)
    s.factor = 2.0
    w, h = initial_window_size(s, 1280, 800)
    assert w <= int(1280 * 0.92)
    assert h <= int(800 * 0.92)


def test_panel_rect_full_width():
    x, y, w, h = panel_rect(1200, 800)
    assert (x, y) == (40, 119)
    assert w == 1200 - 80
    assert h == 800 - 119 - 130 - 10


def test_panel_rect_minimums():
    x, y, w, h = panel_rect(300, 200)
    assert w >= 320 and h >= 120


def test_news_columns_split():
    left, right = news_columns(920)
    assert left == 552
    assert right == 920 - 552 - 12
    assert right >= 200


def test_news_columns_minimum_right():
    _l, right = news_columns(260)
    assert right == 200


@pytest.mark.parametrize("width,expected", [
    (1200, "large"),
    (1000, "standard"),
    (800, "compact"),
])
def test_layout_mode(width, expected):
    assert layout_mode(width) == expected


def test_progress_width():
    assert progress_width(1200) == 910
    assert progress_width(200) == 0   # window narrower than the left column


def test_settings_rect_scales_and_clamps():
    assert settings_rect(1500, 900) == (800, 500)
    assert settings_rect(1200, 800) == (800, 500)
    assert settings_rect(700, 500) == (560, 390)

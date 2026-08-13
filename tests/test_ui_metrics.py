"""Unit tests for the UI metrics / responsive layout helpers."""

import pytest

from ui_metrics import (
    initial_window_size,
    layout_mode,
    news_columns,
    panel_rect,
    progress_width,
    settings_rect,
)


# ── layout math ─────────────────────────────────────────────────────────────

def test_initial_window_size_default_factor():
    w, h = initial_window_size(2560, 1440)
    assert w == 1000 and h == 700


def test_initial_window_size_scales():
    w, h = initial_window_size(2560, 1440, factor=1.5)
    assert w == 1500 and h == 1050


def test_initial_window_size_caps_to_screen():
    w, h = initial_window_size(1280, 800, factor=2.0)
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

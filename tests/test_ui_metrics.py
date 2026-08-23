"""Unit tests for the UI metrics / responsive layout helpers."""

from nostalgia_launcher.ui.qt.metrics import initial_window_size

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

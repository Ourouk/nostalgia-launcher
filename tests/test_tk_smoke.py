"""Optional Tk smoke tests — run the app shell under a real display.

These verify the DPI scaling path, initial window sizing and relayout
actually work against Tk. They skip when tkinter (or a display) isn't
available, e.g. on headless CI.

Run with a virtual display where Xvfb is installed:
    xvfb-run -a uv run pytest tests/test_tk_smoke.py
"""

import importlib.util
import tkinter

import pytest


def _tk_usable() -> bool:
    """True when tkinter can create a real root window (needs libtk + a
    display). Skips cleanly on headless hosts."""
    try:
        root = tkinter.Tk()
    except Exception:
        return False
    try:
        root.destroy()
    except Exception:
        pass
    return True


pytestmark = pytest.mark.skipif(
    not _tk_usable(),
    reason="tkinter not usable on this host (no libtk / no display)")


@pytest.fixture
def app():
    import app as app_module

    inst = app_module.OctoUpdaterApp()
    try:
        inst.update_idletasks()
        inst.update()
        yield inst
    finally:
        inst.destroy()


def test_app_creates_root(app):
    assert app.winfo_ismapped() or app.winfo_viewable()


def test_app_is_resizable(app):
    assert app.resizable() == (1, 1)


def test_app_ui_scale_matches_tk_scaling(app):
    import ui_metrics

    expected = app._ui.tk_scaling()
    actual = float(app.tk.call("tk", "scaling"))
    assert abs(actual - expected) < 0.01


def test_app_geometry_matches_initial_size(app):
    w, h = app.winfo_width(), app.winfo_height()
    assert w == app._win_w and h == app._win_h


def test_relayout_repositions_panels(app):
    app._relayout()
    # Panels must stay inside the window after a relayout.
    x, y, pw, ph = app._news_panel.winfo_x(), app._news_panel.winfo_y(), \
        app._news_panel.winfo_width(), app._news_panel.winfo_height()
    assert x >= 0 and y >= 0
    assert pw > 100 and ph > 100


def test_widget_font_is_dpi_scaled(app):
    import tkinter.font as tkfont

    fam, px = app._font(10)
    label = tkinter.Label(app, text="sample", font=app._font(10))
    try:
        f = tkfont.Font(family=fam, size=px)
        # Tk keeps the requested negative (pixel) size as-is.
        assert f.cget("size") == px
        assert px == -app._ui.px(10)
    finally:
        label.destroy()


def test_mouse_wheel_and_button_handlers_exist(app):
    assert hasattr(app, "_on_mousewheel")
    assert hasattr(app, "_on_wheel_button")
    assert hasattr(app, "_scroll_wheel")

"""Headless Qt tests — palette, stylesheet and the app shell.

QT_QPA_PLATFORM=offscreen is set before PySide6 is imported so the module
runs without a display. The QApplication is created once and shared through
the create_qt_app() singleton — a second QApplication in one process would
abort Qt.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from nostalgia_launcher.ui.qt.app import (
    QtNostalgiaLauncherApp,
    create_qt_app,
)
from nostalgia_launcher.ui.qt.theme import (
    HEX,
    Palette,
    palette_for_config,
    theme_qss,
)


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


def test_palette_exposes_documented_key_colors():
    palette = Palette()
    expected = {
        "C_BG": "#120e1a",
        "C_PANEL": "#161120",
        "C_HDR": "#0d0a14",
        "C_GOLD": "#c8922a",
        "C_GOLD_LT": "#e8b84b",
        "C_TEXT": "#d8d4cc",
        "C_TEXT_DIM": "#7a7670",
        "C_OK": "#6abf69",
        "C_ERR": "#bf6969",
        "C_PARCH": "#e9dcb8",
        "C_PARCH_TITLE": "#7c5a12",
    }
    for name, value in expected.items():
        assert palette.colors[name].name() == value


def test_palette_convenience_attributes():
    palette = Palette()
    assert palette.bg.name() == palette.colors["C_BG"].name()
    assert palette.gold.name() == palette.colors["C_GOLD"].name()
    assert palette.parch.name() == palette.colors["C_PARCH"].name()


def test_theme_qss_is_non_empty_string_with_selectors():
    qss = theme_qss(Palette())
    assert isinstance(qss, str) and qss.strip()
    for selector in (
        "QMainWindow",
        "QPushButton",
        "QLineEdit",
        "QListWidget",
        "QScrollBar",
        "QTabBar",
        "QDialog",
        "QProgressBar",
        "QSpinBox",
        "QComboBox",
        "QToolTip",
        "QMenu",
        ":focus",
    ):
        assert selector in qss


def test_theme_qss_uses_palette_colors():
    qss = theme_qss(Palette())
    assert HEX["C_BG"] in qss
    assert HEX["C_GOLD"] in qss


def test_qapp_is_singleton():
    assert create_qt_app() is create_qt_app()


def test_palette_for_config_applies_theme_overrides(qapp):
    # No launcher configuration: native mode — a system-derived palette,
    # no global stylesheet.
    native = palette_for_config(None)
    assert native.themed is False

    class Cfg:
        theme = {"C_GOLD": "#d4a02f"}

    themed = palette_for_config(Cfg())
    assert themed.themed is True
    assert themed.gold.name() == "#d4a02f"
    # Unlisted slots keep the default palette.
    assert themed.bg.name() == HEX["C_BG"]


def test_palette_for_config_falls_back_on_invalid_theme(qapp):
    class Cfg:
        theme = {"C_GOLD": "not-a-color"}

    palette = palette_for_config(Cfg())
    # An invalid theme means unthemed: native system-derived palette.
    assert palette.themed is False


def test_system_palette_maps_system_roles_keeps_semantic_brand(qapp):
    from PySide6.QtGui import QGuiApplication, QPalette

    from nostalgia_launcher.ui.qt.theme import system_palette

    qp = QGuiApplication.palette()
    pal = system_palette()

    assert pal.themed is False
    # Mapped slots follow the system QPalette…
    assert pal.bg.name() == qp.color(QPalette.ColorRole.Window).name()
    assert pal.panel.name() == qp.color(QPalette.ColorRole.Base).name()
    assert pal.text.name() == qp.color(QPalette.ColorRole.WindowText).name()
    assert pal.gold.name() == qp.color(QPalette.ColorRole.Highlight).name()
    assert (
        pal.gold_lt.name()
        == qp.color(QPalette.ColorRole.Highlight).lighter(125).name()
    )
    # …while semantic/content slots keep their fixed brand values.
    assert pal.ok.name() == HEX["C_OK"]
    assert pal.err.name() == HEX["C_ERR"]
    assert pal.parch.name() == HEX["C_PARCH"]
    assert pal.green_btn.name() == HEX["C_GREEN_BTN"]


def test_app_shell_constructs_shows_and_closes_offscreen(qapp):
    shell = QtNostalgiaLauncherApp()
    assert shell._app is qapp
    assert shell._window.windowTitle() == "Nostalgia Launcher"
    shell.show()
    assert shell._window.isVisible()
    shell.close()
    assert not shell._window.isVisible()


def test_app_shell_run_shows_window_if_hidden(qapp, monkeypatch):
    """run() must never leave the window invisible: even a caller that skips
    show() gets a visible window once the event loop starts (regression for
    the entry point calling mainloop() without show())."""
    shell = QtNostalgiaLauncherApp()
    assert not shell._window.isVisible()
    monkeypatch.setattr(shell._app, "exec", lambda: 0)
    shell.run()
    assert shell._window.isVisible()
    shell.close()

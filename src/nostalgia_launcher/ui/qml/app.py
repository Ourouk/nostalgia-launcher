"""Nostalgia Launcher QML application shell (`NOSTALGIA_UI_BACKEND=qml`).

Mirrors `ui.qt.app.QtNostalgiaLauncherApp` so `cli.main()` works unchanged:
construction wires the toolkit-agnostic `ControllerHub` (shared dispatcher
+ controllers) into QML view-models, `show()`/`run()` drive the engine.
The QtWidgets shell stays the default backend — this one is opt-in until
the per-panel migration (News → Update → content lists → dialogs) lands.

QML sources resolve from `ui/qml/qml/` in dev and from `sys._MEIPASS/qml`
in frozen builds (bundled via the PyInstaller specs' `datas`).
"""

import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ...core import launcher
from ..qt.bridge import ControllerHub
from ..qt.theme import palette_for_config
from .viewmodels import LauncherState, NewsFeedModel, ThemeBridge


def _repo_file(*parts: str) -> str:
    """Path under the repo root (dev) for bundled resources."""
    here = os.path.abspath(__file__)
    root = here
    for _ in range(6):
        root = os.path.dirname(root)
    return os.path.join(root, *parts)


def qml_dir() -> str:
    """Directory holding the bundled `.qml` sources (dev or frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "qml")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")


def _load_bundled_font():
    """Load the bundled STIX Two Math font so ⌕ and ⧉ glyphs render."""
    if getattr(sys, "frozen", False):
        font_path = os.path.join(
            sys._MEIPASS, "fonts", "STIXTwoMath-Regular.otf"
        )
    else:
        font_path = _repo_file("packaging", "fonts", "STIXTwoMath-Regular.otf")
    if os.path.isfile(font_path):
        QFontDatabase.addApplicationFont(font_path)


def _icon_path() -> str:
    """Resolve bundled launcher icon for frozen and dev builds."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "icons", "NostalgiaLauncher.png")
    return _repo_file("packaging", "icons", "NostalgiaLauncher.png")


def create_qml_app():
    """Return the process-wide QApplication, creating it exactly once.

    QApplication (not QGuiApplication) so the widget shell can later host
    migrated views via QQuickWidget during the strangler period. Pins the
    QtQuick.Controls style to Material (the modern one); the brand
    dark/gold itself comes from `appTheme` (QML sets Material Dark +
    gold accent on top of it).
    """
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is not None:
        return app
    app = QApplication([])
    _load_bundled_font()
    ip = _icon_path()
    if os.path.isfile(ip):
        app.setWindowIcon(QIcon(ip))
    return app


class QmlNostalgiaLauncherApp:
    """QML application shell — hub + view-models + QQmlApplicationEngine."""

    def __init__(self):
        self._app = create_qml_app()
        self._hub = ControllerHub()
        self._state = LauncherState()
        self._state.attach(self._hub.bridge)
        palette = palette_for_config(launcher.config())
        self._theme = ThemeBridge(palette)
        self._news = NewsFeedModel()
        self._news.set_refresh_handler(
            lambda: self._hub.news.refresh_announcements(force=True)
        )
        self._hub.bridge.newsLoaded.connect(self._news.on_event)
        self._engine = QQmlApplicationEngine()
        self._engine.rootContext().setContextProperty(
            "launcherState", self._state
        )
        self._engine.rootContext().setContextProperty("appTheme", self._theme)
        self._engine.rootContext().setContextProperty("newsModel", self._news)
        self._engine.load(
            QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml"))
        )
        # Same background fetch the widget shell schedules: cached news
        # stays visible, TTL decides the refetch (threads, never blocks).
        self._hub.news.load()

    @property
    def engine(self) -> QQmlApplicationEngine:
        return self._engine

    def show(self):
        roots = self._engine.rootObjects()
        if roots:
            roots[0].show()

    def raise_to_front(self):
        """Bring the QML window to the front (single-instance "raise")."""
        roots = self._engine.rootObjects()
        if not roots:
            return
        window = roots[0]
        if window.visibility() == QGuiApplication.Visibility.Minimized:
            window.showNormal()
        window.raise_()
        window.requestActivate()

    def run(self):
        """Show the window if needed, then start the event loop."""
        roots = self._engine.rootObjects()
        if roots and not roots[0].isVisible():
            roots[0].show()
        return self._app.exec()

    def close(self):
        self._hub.close()

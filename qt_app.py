"""Octo Updater Qt (PySide6) application context and shell.

Phase 2 of the Qt migration: this module owns the process-wide QApplication
singleton (`create_qt_app`) and a minimal `QtOctoUpdaterApp` shell, the class
the entry point imports for `OCTO_UI_BACKEND=qt`. It is the PySide6
counterpart of app.py and deliberately never imports the Tk UI, so the two
backends coexist in one process tree.

Phase 3 owns the QMainWindow chrome (header, tabs, footer) via
`qt_main_window.MainWindow`; the panels are still placeholders and land in
C16-C19.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

import ui_metrics
from ui_metrics import UIScale, initial_window_size

from qt_bridge import ControllerHub
from qt_main_window import MainWindow


def create_qt_app():
    """Return the process-wide QApplication, creating it exactly once.

    High-DPI settings are configured before the instance exists: the scale
    factor rounding policy is PassThrough so fractional display scales are
    preserved, and the per-monitor DPI attributes are enabled. Both are only
    touched when no instance exists yet — later calls reuse it unchanged.
    """
    app = QApplication.instance()
    if app is not None:
        return app
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    return QApplication([])


def _initial_size(app):
    """Logical window size: the design size, capped at ~90% of the screen."""
    screen = app.primaryScreen()
    if screen is None:
        return ui_metrics.BASE_W, ui_metrics.BASE_H
    geo = screen.availableGeometry()
    return initial_window_size(UIScale(None), geo.width(), geo.height())


def _center(window, app):
    """Center `window` on the primary screen's available geometry."""
    screen = app.primaryScreen()
    if screen is None:
        return
    geo = screen.availableGeometry()
    x = geo.x() + (geo.width() - window.width()) // 2
    y = geo.y() + (geo.height() - window.height()) // 2
    window.move(max(x, geo.x()), max(y, geo.y()))


class QtOctoUpdaterApp:
    """Qt application shell — wires the controller hub into the main window.

    Construction never opens a window or starts the event loop, so it works
    headlessly (e.g. `QT_QPA_PLATFORM=offscreen`) and under tests.
    """

    def __init__(self):
        self._app = create_qt_app()
        self._hub = ControllerHub()
        self._window = MainWindow(self._hub)
        w, h = _initial_size(self._app)
        self._window.resize(w, h)
        _center(self._window, self._app)

    def show(self):
        self._window.show()

    def run(self):
        return self._app.exec()

    def mainloop(self):
        """Alias for run(); the octo_updater entry point calls mainloop()."""
        return self.run()

    def close(self):
        self._window.close()

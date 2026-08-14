"""Vanilla WoW Launcher Qt (PySide6) main window — chrome, tab switching, footer.

`MainWindow` owns no business logic: the controllers' events arrive through
the `ControllerHub` bridge and are rendered here. Qt layouts (not absolute
positioning) do all sizing; the look comes from `qt_theme.theme_qss`.

The panels build their content into the placeholder pages of `self._stack`,
keyed by tab name in `self._pages`; the nav gear and footer widgets are
exposed as attributes for the settings and update workflows.
"""

import queue

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.constants import UPDATER_VERSION
from ...core.log_sink import _LOG_Q, log
from .addons_panel import AddonsPanel
from .bridge import ControllerHub
from .custom_addon_dialog import CustomAddonDialog
from .log_window import LogWindow
from .mods_panel import ModsPanel
from .news_panel import NewsPanel
from .settings_dialog import SettingsDialog
from .theme import Palette, theme_qss
from .tweaks_panel import TweaksPanel
from ...state.events import LogMessage
from .metrics import BASE_H, BASE_W, clamp


class MainWindow(QMainWindow):
    """The Qt main window shell: header, content stack and footer.

    Receives a fully-assembled `ControllerHub` (controllers + bridge) and
    renders the events the bridge forwards. `close()` tears the bridge down
    so posting after close is a safe no-op.
    """

    TABS = ["NEWS", "TWEAKS", "ADDONS", "MODS"]

    def __init__(self, hub: ControllerHub, parent=None):
        super().__init__(parent)
        self._hub = hub
        self._palette = Palette()
        self._settingsDialog = None
        self._log_buffer: list = []
        self._logWindow = None
        self._customAddonDialog = None
        self._firstRunTimer = None
        self._oneShotTimers: list = []
        self.setStyleSheet(theme_qss(self._palette))
        self.setWindowTitle("Vanilla WoW Launcher")
        self.setMinimumSize(clamp(BASE_W // 2, 560, 800),
                            clamp(BASE_H // 2, 420, 600))

        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_central(), 1)
        root.addWidget(self._build_footer())
        self.setCentralWidget(central)

        self._wire_signals()
        self._navButtons["NEWS"].setChecked(True)

        # Seed the client-version footer label from disk and sync the
        # button/status with the controller's current readiness.
        if self._hub.updater.read_client_version():
            self._versionLabel.setText(self._hub.updater.state.client_version)
        self._refresh_ready_state()

        # Session-log drain: the global log_sink queue receives lines written
        # from worker threads; a periodic QTimer drains it here and feeds the
        # shared buffer.
        self._logTimer = QTimer(self)
        self._logTimer.setInterval(50)
        self._logTimer.timeout.connect(self._drain_log_q)
        self._logTimer.start()

        # Update workers publish into UpdateController's queues; poll them on
        # the Qt event loop so verify/update progress, completion markers and
        # the self-update-available flag are actually processed.
        self._pollTimer = QTimer(self)
        self._pollTimer.setInterval(50)
        self._pollTimer.timeout.connect(self._poll_updater)
        self._pollTimer.start()

        # First run: auto-open the settings dialog once.
        if hub.settings.state.first_run:
            self._firstRunTimer = QTimer(self)
            self._firstRunTimer.setSingleShot(True)
            self._firstRunTimer.setInterval(500)
            self._firstRunTimer.timeout.connect(self._open_settings_dialog)
            self._firstRunTimer.start()

    # ── build ────────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        p = self._palette
        header = QWidget(self)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(16)

        self._wordmark = QLabel("Vanilla WoW Launcher", header)
        font = self._wordmark.font()
        font.setPointSize(17)
        font.setBold(True)
        self._wordmark.setFont(font)
        self._wordmark.setStyleSheet(f"color: {p.purple.name()};")
        # "Update available!" sits under the wordmark (hidden until the
        # daily self-update check finds a newer release).
        wordmarkBox = QWidget(header)
        wmLayout = QVBoxLayout(wordmarkBox)
        wmLayout.setContentsMargins(0, 0, 0, 0)
        wmLayout.setSpacing(0)
        wmLayout.addWidget(self._wordmark)
        self._updateAvailableLabel = QLabel("Update available!", wordmarkBox)
        self._updateAvailableLabel.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 8pt;")
        self._updateAvailableLabel.hide()
        wmLayout.addWidget(self._updateAvailableLabel)
        self._updateAvailableShown = False
        layout.addWidget(wordmarkBox)

        navRow = QWidget(header)
        navLayout = QHBoxLayout(navRow)
        navLayout.setContentsMargins(0, 0, 0, 0)
        navLayout.setSpacing(2)
        self._navButtons = {}
        self._tabBadges = {}
        self._navGroup = QButtonGroup(navRow)
        self._navGroup.setExclusive(True)
        for name in self.TABS:
            button = QPushButton(name, navRow)
            button.setCheckable(True)
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            self._navButtons[name] = button
            self._navGroup.addButton(button)
            # Each tab is wrapped in a grid cell so a small count badge can
            # overlay the button's top-right corner without shifting layout.
            holder = QWidget(navRow)
            grid = QGridLayout(holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(0)
            grid.addWidget(button, 0, 0)
            badge = QLabel("", holder)
            badge.setObjectName(f"tabBadge_{name}")
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedHeight(16)
            badge.setAttribute(Qt.WA_TransparentForMouseEvents)
            badge.setStyleSheet(
                f"background-color: {p.gold.name()}; color: {p.hdr.name()};"
                f" border-radius: 8px; font-size: 8pt; font-weight: bold;"
                f" padding: 0 4px;")
            badge.hide()
            grid.addWidget(badge, 0, 0, Qt.AlignTop | Qt.AlignRight)
            self._tabBadges[name] = badge
            navLayout.addWidget(holder)
            button.clicked.connect(
                lambda checked=False, tab=name: self.switch_tab(tab))
        navRow.setStyleSheet(
            "QPushButton { color: %s; background: transparent; border: none;"
            " padding: 6px 12px; font-size: 10pt; font-weight: bold; }"
            "QPushButton:hover { color: %s; }"
            "QPushButton:checked { color: %s; }"
            % (p.text.name(), p.gold.name(), p.gold_lt.name()))
        layout.addWidget(navRow)

        layout.addStretch(1)

        self._gearButton = QToolButton(header)
        self._gearButton.setText("⚙")
        self._gearButton.setToolTip("Settings")
        self._gearButton.setCursor(Qt.PointingHandCursor)
        self._gearButton.setStyleSheet(
            f"QToolButton {{ color: {p.text_dim.name()}; font-size: 14pt; }}"
            f"QToolButton:hover {{ color: {p.gold.name()}; }}")
        self._gearButton.clicked.connect(self._open_settings_dialog)
        layout.addWidget(self._gearButton)

        return header

    def _build_central(self) -> QStackedWidget:
        self._stack = QStackedWidget(self)
        self._pages: dict[str, int] = {}
        for i, name in enumerate(self.TABS):
            if name == "NEWS":
                page = NewsPanel(self._hub.news, self._hub.bridge,
                                 self._palette, self._stack)
            elif name == "TWEAKS":
                page = TweaksPanel(self._hub.tweaks, self._hub.bridge,
                                   self._palette, self._stack)
            elif name == "ADDONS":
                page = AddonsPanel(
                    self._hub.addons, self._hub.bridge,
                    self._palette, self._stack,
                    on_badge=lambda n: self.set_tab_badge("ADDONS", n))
                page.customAddonRequested.connect(
                    self._on_custom_addon_requested)
            elif name == "MODS":
                page = ModsPanel(
                    self._hub.mods, self._hub.bridge,
                    self._palette, self._stack,
                    on_badge=lambda n: self.set_tab_badge("MODS", n))
            else:
                page = QLabel(f"{name} panel (C{i + 16})", self._stack)
                page.setAlignment(Qt.AlignCenter)
            self._pages[name] = i
            self._stack.addWidget(page)
        return self._stack

    def _build_footer(self) -> QWidget:
        p = self._palette
        footer = QWidget(self)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(40, 10, 40, 12)
        layout.setSpacing(24)

        left = QWidget(footer)
        leftLayout = QVBoxLayout(left)
        leftLayout.setContentsMargins(0, 0, 0, 0)
        leftLayout.setSpacing(4)

        self._statusLabel = QLabel("Ready to update", left)
        font = self._statusLabel.font()
        font.setBold(True)
        self._statusLabel.setFont(font)
        leftLayout.addWidget(self._statusLabel)

        self._buttonStyles = {
            "update": (
                "QPushButton { background-color: %s; color: #ffffff;"
                " border: 1px solid %s; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                "QPushButton:hover { background-color: %s; }"
                % (p.gold.name(), p.gold_lt.name(), p.gold_lt.name())),
            "play": (
                "QPushButton { background-color: %s; color: #ffffff;"
                " border: 1px solid %s; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                "QPushButton:hover { background-color: %s; }"
                % (p.green_btn.name(), p.green_hov.name(), p.green_hov.name())),
            "busy": (
                "QPushButton { background-color: %s; color: %s;"
                " border: 1px solid %s; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                % (p.panel.name(), p.text_dim.name(), p.panel_bdr.name())),
        }
        self._updateButton = QPushButton("UPDATE", left)
        self._updateButton.setObjectName("updateButton")
        self._updateButton.setMinimumWidth(150)
        self._updateButton.setStyleSheet(self._buttonStyles["update"])
        self._updateButton.clicked.connect(self._on_update_button_clicked)
        leftLayout.addWidget(self._updateButton)

        self._versionLabel = QLabel(f"v{UPDATER_VERSION}", left)
        self._versionLabel.setStyleSheet(f"color: {p.text_dim.name()};")
        leftLayout.addWidget(self._versionLabel)

        layout.addWidget(left)
        layout.addStretch(1)

        right = QWidget(footer)
        rightLayout = QVBoxLayout(right)
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.setSpacing(4)

        self._progressBar = QProgressBar(right)
        self._progressBar.setTextVisible(False)
        self._progressBar.setRange(0, 100)
        self._progressBar.setValue(0)
        self._progressBar.hide()
        self._progressBar.setStyleSheet(
            "QProgressBar { background-color: %s; border: 1px solid %s;"
            " border-radius: 3px; height: 8px; }"
            "QProgressBar::chunk { background-color: %s;"
            " border-radius: 3px; }"
            % (p.hdr.name(), p.panel_bdr.name(), p.gold.name()))
        rightLayout.addWidget(self._progressBar)

        self._progressLabel = QLabel("", right)
        rightLayout.addWidget(self._progressLabel)

        layout.addWidget(right)
        return footer

    def _wire_signals(self):
        bridge = self._hub.bridge
        bridge.statusChanged.connect(self._onStatusChanged)
        bridge.progressChanged.connect(self._onProgressChanged)
        bridge.operationFinished.connect(self._onOperationFinished)
        bridge.operationFailed.connect(self._onOperationFailed)
        bridge.logMessage.connect(self._on_log_message)
        # The panels re-render their own content on these; the footer just
        # re-evaluates readiness (addons installing / mod errors gate PLAY).
        bridge.addonsLoaded.connect(self._on_addons_or_mods_loaded)
        bridge.modsLoaded.connect(self._on_addons_or_mods_loaded)

    # ── tabs ─────────────────────────────────────────────────────────────────

    def switch_tab(self, name: str):
        """Show the page for `name`; unknown names are a no-op."""
        if name not in self._pages:
            return
        self._stack.setCurrentIndex(self._pages[name])
        button = self._navButtons.get(name)
        if button is not None:
            button.setChecked(True)

    def set_tab_badge(self, tab: str, count: int):
        """Show a small gold count badge on a nav tab (hidden at 0)."""
        badge = self._tabBadges.get(tab)
        if badge is None:
            return
        count = max(0, int(count))
        if count:
            badge.setText(str(count))
            badge.show()
        else:
            badge.hide()

    def _on_custom_addon_requested(self):
        """Open the custom-addon dialog; its addonRequested record is handed
        to the AddonsController."""
        if self._customAddonDialog is None:
            dialog = CustomAddonDialog(self._palette, self)
            dialog.addonRequested.connect(self._on_custom_addon_apply)
            dialog.finished.connect(self._on_custom_addon_finished)
            self._customAddonDialog = dialog
        self._customAddonDialog.show()
        self._customAddonDialog.raise_()
        self._customAddonDialog.activateWindow()

    def _on_custom_addon_apply(self, rec: dict):
        log(f"\nInstalling custom addon {rec['folder']}…\n", "acct")
        self._hub.addons.apply([rec])

    def _on_custom_addon_finished(self):
        self._customAddonDialog = None

    # ── settings dialog ─────────────────────────────────────────────────────

    def _open_settings_dialog(self):
        """Build the settings dialog on demand and show it non-modally.

        `show()` (not `exec()`) so opening never blocks the caller or an
        offscreen test; `raise_`/`activateWindow` still bring it to the
        foreground. A closed dialog is reused on the next gear click.
        """
        if self._settingsDialog is None:
            dialog = SettingsDialog(
                self._hub.settings, self._hub.bridge, self._palette, self)
            dialog.showLogsRequested.connect(self._on_show_logs_requested)
            dialog.finished.connect(self._on_settings_finished)
            self._settingsDialog = dialog
        self._settingsDialog.show()
        self._settingsDialog.raise_()
        self._settingsDialog.activateWindow()

    def _on_settings_finished(self):
        """First-run close: run the deferred verification against the chosen
        folder, recommend the Defender exclusion once, then mark the prompt
        done so closing Settings again never re-asks."""
        if self._hub.settings.state.first_run_verify_pending:
            self._hub.settings.state.first_run_verify_pending = False
            self._after(100, lambda: self._start_verify(overwrite_config=True))
        if not self._hub.settings.state.first_run_av_pending:
            return
        if self._hub.settings.should_prompt_av():
            ret = QMessageBox.question(
                self, "Game folder changed",
                "It is highly recommended to add the game folder to your "
                "antivirus exclusions. Antivirus software may incorrectly "
                "detect some mods as threats and prevent them from being "
                "downloaded or installed properly.\n\n"
                "Do you want to add the game folder to Defender exclusions?",
                QMessageBox.Yes | QMessageBox.No)
            if ret == QMessageBox.Yes:
                self._hub.settings.allow_through_antivirus()
        self._hub.settings.av_prompt_dismissed()

    def _on_show_logs_requested(self):
        """Open (or re-raise) the session-log window, seeded from the
        buffer so a freshly-opened window shows the whole session."""
        if self._logWindow is None:
            win = LogWindow(self._palette, self)
            win.seed(self._log_buffer)
            win.setAttribute(Qt.WA_DeleteOnClose, True)
            win.destroyed.connect(self._on_log_window_closed)
            self._logWindow = win
        self._logWindow.show()
        self._logWindow.raise_()
        self._logWindow.activateWindow()

    def _on_log_window_closed(self):
        self._logWindow = None

    # ── session log ─────────────────────────────────────────────────────────

    def _on_log_message(self, text: str, tag: str):
        self._render_log(text, tag)

    def _drain_log_q(self):
        """Drain the global worker log queue into the session buffer."""
        try:
            while True:
                msg, tag = _LOG_Q.get_nowait()
                self._render_log(msg, tag)
        except queue.Empty:
            pass

    def _poll_updater(self):
        """Process the update controller's worker queues and render the
        self-update availability flag (driven by _pollTimer)."""
        self._hub.updater.poll()
        available = self._hub.updater.updater_update_available
        if available != self._updateAvailableShown:
            self._updateAvailableShown = available
            if available:
                self._updateAvailableLabel.show()
            else:
                self._updateAvailableLabel.hide()

    def _render_log(self, msg: str, tag: str = ""):
        """Normalize a raw log message (trailing newline, auto-tag when
        untagged) into the session buffer and any open log window."""
        line = msg if msg.endswith("\n") else msg + "\n"
        if not tag:
            ml = line.lower()
            if "✓" in line or "success" in ml or "complete" in ml \
                    or "up to date" in ml:
                tag = "ok"
            elif "✗" in line or "error" in ml or "fail" in ml \
                    or "mismatch" in ml:
                tag = "err"
            elif line.strip().startswith("["):
                tag = "acct"
        self._log_buffer.append((line, tag))
        if self._logWindow is not None:
            self._logWindow.append(line, tag)

    # ── slots ────────────────────────────────────────────────────────────────

    def _onStatusChanged(self, text: str):
        self._statusLabel.setText(text)
        # Keep the button in sync (e.g. busy while a verify starts) without
        # letting the computed readiness overwrite the posted status line.
        self._apply_readiness(self._readiness(), update_status=False)

    def _onProgressChanged(self, value: float, label: str):
        value = max(0.0, min(1.0, float(value)))
        self._progressBar.setValue(int(round(value * 100)))
        self._progressLabel.setText(label)
        # Hide the bar when idle (0) or finished/full (1) — it only shows
        # while something is downloading.
        if value <= 0.0 or value >= 1.0:
            self._progressBar.hide()
        else:
            self._progressBar.show()

    def _onOperationFinished(self, kind: str, ok: bool, message: str):
        updater = self._hub.updater
        if kind in ("update", "verify"):
            # The update worker reports the (post-patch) client version just
            # before finishing; surface it when a fresh one arrived. The
            # panels re-render their own kinds themselves.
            if ok and updater.state.client_version:
                self._versionLabel.setText(updater.state.client_version)
            elif not ok:
                self._statusLabel.setText("Update available!")
        self._refresh_ready_state()

    def _onOperationFailed(self, kind: str, message: str):
        self._refresh_ready_state()

    def _on_addons_or_mods_loaded(self, _event=None):
        self._refresh_ready_state()

    # ── footer button / update workflow ──────────────────────────────────────

    def _on_update_button_clicked(self):
        """Footer PLAY/UPDATE click — launch when ready, update otherwise.
        Busy states are ignored."""
        updater = self._hub.updater
        if updater.running:
            return
        ready = updater.compute_readiness(
            addons_installing=self._hub.addons.installing)
        if ready.mode == "play":
            self._launch_game()
        elif ready.mode == "update":
            self._start_update()

    def _start_update(self):
        updater = self._hub.updater
        if updater.running:
            return
        if not (self._hub.settings.state.path or "").strip():
            self._hub.dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err"))
            return
        updater.start_update()
        self._refresh_ready_state()

    def _start_verify(self, overwrite_config: bool = False):
        if not (self._hub.settings.state.path or "").strip():
            self._set_button_ready(False)
            return
        self._hub.updater.start_verify(overwrite_config)
        self._refresh_ready_state()

    def _launch_game(self):
        """Launch the game detached; the launch logic (VanillaFixes/WoW.exe
        choice, DXVK notice, clear-wdb, subprocess) lives in the
        UpdateController — this only drives the footer chrome and dialogs."""
        ok, dxvk_notice = self._hub.updater.launch_game()
        if not ok:
            return
        if dxvk_notice:
            self._show_dxvk_notice()
        # Briefly disable PLAY so a double-click can't spawn two clients.
        self._set_button_busy("PLAY")
        self._statusLabel.setText("Launching...")
        if self._hub.settings.state.config.get("close_on_launch", False):
            self._after(1000, self.close)
            return
        self._after(5000, self._refresh_ready_state)

    def _show_dxvk_notice(self):
        QMessageBox.information(
            self, "DXVK mod first launch",
            "Initial shader compilation may cause temporary in-game "
            "stuttering during the first launch. This is a normal process "
            "while the game builds its shader cache.\n\n"
            "Users with AMD GPUs experiencing stability issues can switch "
            "to DXVK 2.5.3")

    def _refresh_ready_state(self):
        """Recompute the footer status/button from the controller's
        readiness. PLAY is only offered when the client files are up to date
        AND no mod is in an error state — the decision itself lives in
        UpdateController.compute_readiness."""
        self._apply_readiness(self._readiness())

    def _readiness(self):
        return self._hub.updater.compute_readiness(
            addons_installing=self._hub.addons.installing)

    def _apply_readiness(self, r, update_status: bool = True):
        if r.mode == "play":
            self._set_button_ready(True)
        elif r.mode == "update":
            self._set_button_ready(False)
        else:
            self._set_button_busy(r.label)
        if update_status:
            self._statusLabel.setText(r.status)

    def _set_button_ready(self, ready: bool):
        """Gold UPDATE ↔ green PLAY flip; the button stays clickable."""
        self._updateButton.setText("PLAY" if ready else "UPDATE")
        self._updateButton.setStyleSheet(
            self._buttonStyles["play" if ready else "update"])
        self._updateButton.setEnabled(True)

    def _set_button_busy(self, label: str):
        self._updateButton.setText(label)
        self._updateButton.setStyleSheet(self._buttonStyles["busy"])
        self._updateButton.setEnabled(False)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def schedule_startup_tasks(self):
        """Schedule the background verify, news load, mod/addon checks and
        the self-update check, all cancelled on close. On first run the
        settings dialog defers verification to its close."""
        hub = self._hub
        if not hub.settings.state.first_run_verify_pending:
            self._after(300, self._start_verify)
        self._after(600, hub.news.load)
        if hub.settings.state.config.get("mod_release_cache"):
            self._after(900, hub.mods.load_latest_versions)
        if hub.settings.state.config.get("addons") is not None:
            self._after(1500, hub.addons.verify)
        self._after(2000, hub.updater.check_updater_update)

    def _after(self, ms: int, callback):
        """A cancellable single-shot timer (stored for _stop_timers)."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ms)
        timer.timeout.connect(callback)
        timer.start()
        self._oneShotTimers.append(timer)
        return timer

    def close(self):
        self._teardown()
        return super().close()

    def closeEvent(self, event):
        self._teardown()
        super().closeEvent(event)

    def _teardown(self):
        """One-shot shutdown: stop timers, cancel live update workers and
        tear the hub down. Idempotent so the explicit close() and the Qt
        closeEvent can both fire without double-tearing."""
        if getattr(self, "_torn_down", False):
            return
        self._torn_down = True
        self._stop_timers()
        # Ask live update workers to stop before the UI goes away, so a
        # background download/verify can't keep mutating files or config
        # after the window is closed.
        self._hub.updater.cancel()
        self._hub.close()

    def _stop_timers(self):
        self._logTimer.stop()
        self._pollTimer.stop()
        for timer in self._oneShotTimers:
            timer.stop()
        self._oneShotTimers.clear()
        if self._firstRunTimer is not None:
            self._firstRunTimer.stop()

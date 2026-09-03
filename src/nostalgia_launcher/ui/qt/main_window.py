"""Nostalgia Launcher Qt (PySide6) main window — chrome, tab switching, footer.

`MainWindow` owns no business logic: the controllers' events arrive through
the `ControllerHub` bridge and are rendered here. Qt layouts (not absolute
positioning) do all sizing; the look comes from `qt_theme.apply_theme`
(themed launcher configs only — unthemed installs stay native).

The panels build their content into the placeholder pages of `self._stack`,
keyed by tab name in `self._pages`; the nav gear and footer widgets are
exposed as attributes for the settings and update workflows.
"""

import os
import sys
import threading
import webbrowser
from collections import deque

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import launcher, profiles
from ...core.constants import UPDATER_VERSION
from ...core.log_sink import log
from ...services import logo
from ...state.events import LogMessage
from . import metrics
from .addons_panel import AddonsPanel
from .assets_panel import AssetsPanel
from .bridge import ControllerHub
from .custom_addon_dialog import CustomAddonDialog
from .custom_asset_dialog import CustomAssetDialog
from .custom_mod_dialog import CustomModDialog
from .log_window import LogWindow
from .metrics import BASE_H, BASE_W, clamp
from .mods_panel import ModsPanel
from .news_panel import NewsPanel
from .settings_dialog import SettingsDialog
from .theme import apply_theme, logo_for_config, palette_for_config
from .tweaks_panel import TweaksPanel
from .update_panel import UpdatePanel


class _LogoFetcher(QObject):
    """Fetches the configured logo on a worker thread.

    Reports the cached local path (or '' on failure) via a Qt signal, which
    is auto-queued to the main thread so the pixmap is always built there.
    """

    finished = Signal(str)

    def start(self, url: str):
        threading.Thread(target=self._run, args=(url,), daemon=True).start()

    def _run(self, url: str):
        self.finished.emit(logo.fetch_logo(url) or "")


def _icon_path() -> str:
    """Resolve bundled launcher icon for frozen and dev builds."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "icons", "NostalgiaLauncher.png")
    # src/nostalgia_launcher/ui/qt/main_window.py -> repo root
    return os.path.join(
        os.path.dirname(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
        ),
        "packaging",
        "icons",
        "NostalgiaLauncher.png",
    )


# The header wordmark logo is scaled to fit within this box.
_LOGO_HEIGHT = 28
_LOGO_MAX_WIDTH = 320


class MainWindow(QMainWindow):
    """The Qt main window shell: header, content stack and footer.

    Receives a fully-assembled `ControllerHub` (controllers + bridge) and
    renders the events the bridge forwards. `close()` tears the bridge down
    so posting after close is a safe no-op.
    """

    TABS = ["NEWS", "UPDATE", "TWEAKS", "ADDONS", "MODS", "ASSETS"]

    def __init__(self, hub: ControllerHub, parent=None):
        super().__init__(parent)
        self._hub = hub
        self._palette = palette_for_config(launcher.config())
        self._settingsDialog = None
        self._log_buffer: deque = deque(maxlen=2000)
        self._logWindow = None
        self._customAddonDialog = None
        self._customModDialog = None
        self._customAssetDialog = None
        self._discordButton = None
        self._updatePanel = None
        self._firstRunTimer = None
        self._oneShotTimers: list = []
        apply_theme(self, self._palette)
        self.setWindowTitle("Nostalgia Launcher")
        _ip = _icon_path()
        if os.path.isfile(_ip):
            self.setWindowIcon(QIcon(_ip))
        self.setMinimumSize(
            clamp(BASE_W // 2, 560, 800), clamp(BASE_H // 2, 420, 600)
        )

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
        self._sync_folder_label()
        self._refresh_ready_state()

        # Workers post typed events to the shared EventDispatcher; the
        # ControllerBridge drains them every 50 ms. Only the self-update
        # availability label still needs a lightweight poll.
        self._pollTimer = QTimer(self)
        self._pollTimer.setInterval(500)
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

        self._wordmark = QLabel(
            launcher.server_name() or "Nostalgia Launcher", header
        )
        font = self._wordmark.font()
        font.setPointSize(metrics.PT_TITLE)
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
            f"color: {p.gold.name()}; font-weight: bold;"
            f" font-size: {metrics.PT_BADGE}pt;"
        )
        self._updateAvailableLabel.hide()
        wmLayout.addWidget(self._updateAvailableLabel)
        self._updateAvailableShown = False
        layout.addWidget(wordmarkBox)

        # Active-profile selector: picking another profile confirms and
        # RESTARTS the app into it (profiles_ui.switch_profile); a
        # declined confirmation or failed relaunch reverts the selection.
        # Management (create/duplicate/rename/delete) lives in Settings →
        # PROFILES.
        self._profileCombo = QComboBox(header)
        self._profileCombo.setObjectName("profileCombo")
        self._profileCombo.setToolTip(
            "Active profile — selecting another one restarts the launcher"
        )
        self._profileCombo.setAccessibleName("Active profile")
        self._fill_profile_combo()
        self._profileCombo.activated.connect(self._on_profile_combo_activated)
        self._profileCombo.setStyleSheet(
            f"QComboBox {{ font-size: {metrics.PT_BADGE}pt;"
            f" font-weight: bold; color: {p.text_dim.name()}; }}"
            f"QComboBox:hover {{ color: {p.gold.name()}; }}"
            f"QComboBox::drop-down {{ border: none; }}"
        )
        layout.addWidget(self._profileCombo)

        # A themed logo replaces the wordmark text once it has been fetched
        # (the server-name text shows until then, and stays on failure).
        logo_url = logo_for_config(launcher.config())
        if logo_url:
            self._logo_fetcher = _LogoFetcher(self)
            self._logo_fetcher.finished.connect(self._apply_logo)
            self._logo_fetcher.start(logo_url)

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
                f" border-radius: 8px; font-size: {metrics.PT_BADGE}pt;"
                f" font-weight: bold;"
                f" padding: 0 4px;"
            )
            badge.hide()
            grid.addWidget(badge, 0, 0, Qt.AlignTop | Qt.AlignRight)
            self._tabBadges[name] = badge
            navLayout.addWidget(holder)
            button.clicked.connect(
                lambda checked=False, tab=name: self.switch_tab(tab)
            )
        navRow.setStyleSheet(
            f"QPushButton {{ color: {p.text.name()}; background: transparent;"
            " border: none;"
            " padding: 6px 12px; font-size: 10pt; font-weight: bold; }"
            f"QPushButton:hover {{ color: {p.gold.name()}; }}"
            f"QPushButton:checked {{ color: {p.gold_lt.name()}; }}"
        )
        layout.addWidget(navRow)

        layout.addStretch(1)

        discord_url = launcher.discord_url()
        if discord_url:
            self._discordButton = QToolButton(header)
            self._discordButton.setObjectName("discordButton")
            self._discordButton.setText("DISCORD")
            self._discordButton.setToolTip("Open Discord")
            self._discordButton.setCursor(Qt.PointingHandCursor)
            self._discordButton.setStyleSheet(
                f"QToolButton {{ color: {p.text_dim.name()}; font-weight: bold; }}"
                f"QToolButton:hover {{ color: {p.gold.name()}; }}"
            )
            self._discordButton.clicked.connect(
                lambda: webbrowser.open(discord_url)
            )
            layout.addWidget(self._discordButton)

        self._gearButton = QToolButton(header)
        self._gearButton.setText("⚙")
        self._gearButton.setToolTip("Settings")
        self._gearButton.setAccessibleName("Settings")
        self._gearButton.setCursor(Qt.PointingHandCursor)
        self._gearButton.setStyleSheet(
            f"QToolButton {{ color: {p.text_dim.name()};"
            f" font-size: {metrics.PT_ICON}pt; }}"
            f"QToolButton:hover {{ color: {p.gold.name()}; }}"
        )
        self._gearButton.clicked.connect(self._open_settings_dialog)
        layout.addWidget(self._gearButton)

        # The wordmark text (server name or the app name) varies in length —
        # keep the header chrome at the design minimum so a short server name
        # can't collapse the header below it.
        header.setMinimumWidth(clamp(BASE_W // 2, 560, 800))
        return header

    def _apply_logo(self, path: str):
        """Swap the wordmark text for the fetched logo image.

        Called on the main thread when the logo fetch finished. A missing or
        unreadable logo leaves the server-name text in place.
        """
        if not path:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        scaled = pixmap.scaledToHeight(_LOGO_HEIGHT, Qt.SmoothTransformation)
        if scaled.width() > _LOGO_MAX_WIDTH:
            scaled = scaled.scaled(
                _LOGO_MAX_WIDTH,
                _LOGO_HEIGHT,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        self._wordmark.setPixmap(scaled)

    def _build_central(self) -> QStackedWidget:
        self._stack = QStackedWidget(self)
        self._pages: dict[str, int] = {}
        for i, name in enumerate(self.TABS):
            if name == "NEWS":
                page = NewsPanel(
                    self._hub.news,
                    self._hub.bridge,
                    self._palette,
                    self._stack,
                )
            elif name == "TWEAKS":
                page = TweaksPanel(
                    self._hub.tweaks,
                    self._hub.bridge,
                    self._palette,
                    self._stack,
                )
            elif name == "ADDONS":
                page = AddonsPanel(
                    self._hub.addons,
                    self._hub.bridge,
                    self._palette,
                    self._stack,
                    on_badge=lambda n: self.set_tab_badge("ADDONS", n),
                )
                page.customAddonRequested.connect(
                    self._on_custom_addon_requested
                )
            elif name == "MODS":
                page = ModsPanel(
                    self._hub.mods,
                    self._hub.bridge,
                    self._palette,
                    self._stack,
                    on_badge=lambda n: self.set_tab_badge("MODS", n),
                )
                page.customModRequested.connect(self._on_custom_mod_requested)
            elif name == "ASSETS":
                page = AssetsPanel(
                    self._hub.assets,
                    self._hub.bridge,
                    self._palette,
                    self._stack,
                    on_badge=lambda n: self.set_tab_badge("ASSETS", n),
                )
                page.customAssetRequested.connect(
                    self._on_custom_asset_requested
                )
            elif name == "UPDATE":
                page = UpdatePanel(self._palette, self._stack)
                page.forceRecheckClicked.connect(self._on_force_recheck)
                self._updatePanel = page
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
                f"QPushButton {{ background-color: {p.gold.name()};"
                f" color: {p.btn_text.name()}; border: 1px solid"
                f" {p.gold_lt.name()}; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                f"QPushButton:hover {{ background-color:"
                f" {p.gold_lt.name()}; }}"
            ),
            "play": (
                f"QPushButton {{ background-color: {p.green_btn.name()};"
                f" color: {p.btn_text.name()}; border: 1px solid"
                f" {p.green_hov.name()}; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                f"QPushButton:hover {{ background-color:"
                f" {p.green_hov.name()}; }}"
            ),
            "terminate": (
                f"QPushButton {{ background-color: {p.err.name()};"
                f" color: {p.btn_text.name()}; border: 1px solid"
                f" {p.err.name()}; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
                f"QPushButton:hover {{ background-color:"
                f" {p.err.name()}; }}"
            ),
            "busy": (
                f"QPushButton {{ background-color: {p.panel.name()};"
                f" color: {p.text_dim.name()}; border: 1px solid"
                f" {p.panel_bdr.name()}; border-radius: 6px;"
                " padding: 8px 26px; font-weight: bold; }"
            ),
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

        # The ACTIVE game folder, always visible — the launcher never
        # downloads without one, so this label makes the target unmistakable.
        self._folderLabel = QLabel("", left)
        self._folderLabel.setStyleSheet(f"color: {p.text_dim.name()};")
        leftLayout.addWidget(self._folderLabel)

        layout.addWidget(left)
        layout.addStretch(1)

        right = QWidget(footer)
        rightLayout = QVBoxLayout(right)
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.setSpacing(4)
        # Align the status column with the morphing button's width so the
        # footer reads as two stable columns.
        right.setMinimumWidth(150)

        # Footer keeps status text only — download progress lives in the
        # UPDATE panel's own progress bar.
        self._progressLabel = QLabel("", right)
        self._progressLabel.setStyleSheet(f"color: {p.text_dim.name()};")
        rightLayout.addWidget(self._progressLabel)

        layout.addWidget(right)
        return footer

    def _wire_signals(self):
        bridge = self._hub.bridge
        bridge.statusChanged.connect(self._onStatusChanged)
        bridge.progressChanged.connect(self._onProgressChanged)
        bridge.updateProgressChanged.connect(self._on_update_progress_changed)
        bridge.updateFilesList.connect(self._on_update_files_list)
        bridge.operationFinished.connect(self._onOperationFinished)
        bridge.operationFailed.connect(self._onOperationFailed)
        bridge.logMessage.connect(self._on_log_message)
        # The panels re-render their own content on these; the footer just
        # re-evaluates readiness (addons installing / mod errors gate PLAY).
        bridge.addonsLoaded.connect(self._on_addons_or_mods_loaded)
        bridge.modsLoaded.connect(self._on_addons_or_mods_loaded)
        # A game launch/exit flips the footer between PLAY and TERMINATE.
        bridge.gameLaunched.connect(self._on_game_launched)
        bridge.gameExited.connect(self._on_game_exited)

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
        err = self._hub.addons.add_custom_entry(
            {"name": rec["folder"], "git": rec.get("git")}
        )
        if err:
            log(f"✗ Custom addon {rec['folder']}: {err}\n", "err")
            return
        log(f"\nInstalling custom addon {rec['folder']}…\n", "acct")
        self._hub.addons.apply([rec])

    def _on_custom_addon_finished(self):
        self._customAddonDialog = None

    def _show_custom_dialog(self, dialog) -> None:
        """Show a non-modal custom-entry dialog, single-instance."""
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_custom_mod_requested(self):
        if self._customModDialog is None:
            dialog = CustomModDialog(self._palette, self)
            dialog.modRequested.connect(self._on_custom_mod_apply)
            dialog.finished.connect(
                lambda: setattr(self, "_customModDialog", None)
            )
            self._customModDialog = dialog
        self._show_custom_dialog(self._customModDialog)

    def _on_custom_mod_apply(self, entry: dict):
        err = self._hub.mods.add_custom_entry(entry)
        if err:
            log(f"✗ Custom mod {entry.get('id')}: {err}\n", "err")
            return
        log(f"\nCustom mod {entry['id']} saved to the local repo.\n", "acct")

    def _on_custom_asset_requested(self):
        if self._customAssetDialog is None:
            dialog = CustomAssetDialog(self._palette, self)
            dialog.assetRequested.connect(self._on_custom_asset_apply)
            dialog.finished.connect(
                lambda: setattr(self, "_customAssetDialog", None)
            )
            self._customAssetDialog = dialog
        self._show_custom_dialog(self._customAssetDialog)

    def _on_custom_asset_apply(self, entry: dict):
        err = self._hub.assets.add_custom_entry(entry)
        if err:
            log(f"✗ Custom asset {entry.get('id')}: {err}\n", "err")
            return
        log(f"\nCustom asset {entry['id']} saved to the local repo.\n", "acct")

    # ── settings dialog ─────────────────────────────────────────────────────

    def _sync_folder_label(self):
        """Mirror the active game folder into the footer — "" renders as an
        explicit not-set hint so the download target is never ambiguous."""
        path = self._hub.settings.state.path.strip()
        if path:
            self._folderLabel.setText(f"Game folder: {path}")
        else:
            self._folderLabel.setText("Game folder not set")

    # ── profile switching (header combo) ─────────────────────────────────

    def _fill_profile_combo(self):
        """(Re)populate from the registry, preselecting the active profile
        with signals blocked (programmatic changes must not trigger the
        switch flow)."""
        combo = self._profileCombo
        active = profiles.active().name
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(profiles.list_profiles())
            idx = combo.findText(active)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(False)

    def _on_profile_combo_activated(self, index: int):
        """User picked a profile in the header: confirm, then restart into
        it. Qt6's activated signal only delivers the item index, so the
        name is resolved here. A declined confirmation or failed relaunch
        reverts the combo to the still-active profile."""
        name = self._profileCombo.itemText(index)
        if not name or name == profiles.active().name:
            return
        from .profiles_ui import confirm_switch, switch_profile

        if confirm_switch(self, name) and switch_profile(name):
            return  # quitting; nothing left to do
        self._fill_profile_combo()

    def _open_settings_dialog(self):
        """Build the settings dialog on demand and show it non-modally.

        `show()` (not `exec()`) so opening never blocks the caller or an
        offscreen test; `raise_`/`activateWindow` still bring it to the
        foreground. A closed dialog is reused on the next gear click.
        """
        if self._settingsDialog is None:
            dialog = SettingsDialog(
                self._hub.settings, self._hub.bridge, self._palette, self
            )
            dialog.logsToggleRequested.connect(self._on_logs_toggle_requested)
            # Profile mutations inside Settings (new/duplicate/rename/
            # delete) must reach the header switcher without a restart.
            dialog.profilesChanged.connect(self._fill_profile_combo)
            dialog.set_logs_open(self._logWindow is not None)
            dialog.finished.connect(self._on_settings_finished)
            self._settingsDialog = dialog
        self._settingsDialog.show()
        self._settingsDialog.raise_()
        self._settingsDialog.activateWindow()

    def _on_settings_finished(self):
        """First-run close: run the deferred verification against the chosen
        folder, recommend the Defender exclusion once, then mark the prompt
        done so closing Settings again never re-asks."""
        self._sync_folder_label()
        if (
            self._hub.settings.client_update_enabled
            and self._hub.settings.state.first_run_verify_pending
        ):
            self._hub.settings.state.first_run_verify_pending = False
            self._after(100, lambda: self._start_verify(overwrite_config=True))
        if not self._hub.settings.state.first_run_av_pending:
            return
        if self._hub.settings.should_prompt_av():
            ret = QMessageBox.question(
                self,
                "Game folder changed",
                "It is highly recommended to add the game folder to your "
                "antivirus exclusions. Antivirus software may incorrectly "
                "detect some mods as threats and prevent them from being "
                "downloaded or installed properly.\n\n"
                "Do you want to add the game folder to Defender exclusions?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret == QMessageBox.Yes:
                self._hub.settings.allow_through_antivirus()
        self._hub.settings.av_prompt_dismissed()

    def open_session_log(self):
        """Public entry point (CLI --show-log, the settings row): open or
        raise the session-log window."""
        self._on_logs_toggle_requested()

    def _on_logs_toggle_requested(self):
        """The settings row toggles the session-log window: closed → open
        (seeded from the buffer so a fresh window shows the whole session);
        open → close (WA_DeleteOnClose destroys it and `destroyed` resets
        the state)."""
        if self._logWindow is None:
            self._open_log_window()
        else:
            self._logWindow.close()

    def _open_log_window(self):
        win = LogWindow(self._palette)
        win.seed(self._log_buffer)
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        win.destroyed.connect(self._on_log_window_closed)
        self._logWindow = win
        win.show()
        win.raise_()
        win.activateWindow()
        if self._settingsDialog is not None:
            self._settingsDialog.set_logs_open(True)

    def _on_log_window_closed(self):
        self._logWindow = None
        if self._settingsDialog is not None:
            self._settingsDialog.set_logs_open(False)

    # ── session log ─────────────────────────────────────────────────────────

    def _on_log_message(self, text: str, tag: str):
        self._render_log(text, tag)

    def _poll_updater(self):
        """Render the self-update availability label (driven by _pollTimer)."""
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
            if (
                "✓" in line
                or "success" in ml
                or "complete" in ml
                or "up to date" in ml
            ):
                tag = "ok"
            elif (
                "✗" in line
                or "error" in ml
                or "fail" in ml
                or "mismatch" in ml
            ):
                tag = "err"
            elif line.strip().startswith("["):
                tag = "acct"
        self._log_buffer.append((line, tag))
        if self._logWindow is not None:
            self._logWindow.append(line, tag)

    # ── slots ────────────────────────────────────────────────────────────────

    def _onStatusChanged(self, text: str):
        self._statusLabel.setText(text)
        self._stack.widget(self._pages["UPDATE"]).status_changed(text)
        # Keep the button in sync (e.g. busy while a verify starts) without
        # letting the computed readiness overwrite the posted status line.
        self._apply_readiness(self._readiness(), update_status=False)

    def _onProgressChanged(self, value: float, label: str):
        # The footer shows the phase text only; the numeric progress lives
        # in the UPDATE panel.
        self._progressLabel.setText(label)

    def _on_update_progress_changed(self, event):
        panel = self._stack.widget(self._pages["UPDATE"])
        panel.progress_changed(event)

    def _on_update_files_list(self, event):
        panel = self._stack.widget(self._pages["UPDATE"])
        panel.set_updated_files(event.files)

    def _onOperationFinished(self, kind: str, ok: bool, message: str):
        panel = self._stack.widget(self._pages["UPDATE"])
        panel.operation_finished(kind, ok, message)
        updater = self._hub.updater
        if kind in ("update", "verify") and ok:
            # The update worker reports the (post-patch) client version just
            # before finishing; surface it when a fresh one arrived.
            if updater.state.client_version:
                self._versionLabel.setText(updater.state.client_version)
        # Readiness owns the status line — it renders the accurate verdict
        # for both success and failure right below.
        self._refresh_ready_state()

    def _onOperationFailed(self, kind: str, message: str):
        self._stack.widget(self._pages["UPDATE"]).operation_failed(
            kind, message
        )
        self._refresh_ready_state()

    def _on_addons_or_mods_loaded(self, _event=None):
        self._refresh_ready_state()

    def _on_game_launched(self, pid: int, pgid: int):
        """The game started — the footer flips to TERMINATE via readiness."""
        self._refresh_ready_state()

    def _on_game_exited(self, pid: int, exit_code):
        """The game ended — the footer flips back to PLAY via readiness."""
        self._refresh_ready_state()

    # ── footer button / update workflow ──────────────────────────────────────

    def _on_update_button_clicked(self):
        """Footer PLAY/UPDATE/TERMINATE click — launch when ready, update
        otherwise, terminate a running game. Busy states are ignored."""
        updater = self._hub.updater
        if updater.running:
            return
        ready = updater.compute_readiness(
            addons_installing=self._hub.addons.installing
        )
        if ready.mode == "play":
            self._launch_game()
        elif ready.mode == "update":
            self._start_update()
        elif ready.mode == "download":
            self._start_client_download()
        elif ready.mode == "terminate":
            self._terminate_game()

    def _start_update(self):
        updater = self._hub.updater
        if updater.running:
            return
        if not (self._hub.settings.state.path or "").strip():
            self._hub.dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return
        self.switch_tab("UPDATE")
        updater.start_update()
        self._refresh_ready_state()

    def _start_client_download(self):
        """First-time client acquisition via BitTorrent (offered when client
        updates are disabled but no client is installed yet)."""
        updater = self._hub.updater
        if updater.running:
            return
        if not (self._hub.settings.state.path or "").strip():
            self._hub.dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return
        self.switch_tab("UPDATE")
        updater.start_client_download()
        self._refresh_ready_state()

    def _start_verify(self, overwrite_config: bool = False):
        if not (self._hub.settings.state.path or "").strip():
            self._set_button_ready(False)
            return
        self._hub.updater.start_verify(overwrite_config)
        self._refresh_ready_state()

    def _on_force_recheck(self):
        """UPDATE-tab "Force recheck" click: drop the hash/torrent-verdict
        cache and re-verify every file. The transport is the worker's choice
        — SHA-1 checksums against the manifest, or BitTorrent piece hashes
        when no manifest is available."""
        if not (self._hub.settings.state.path or "").strip():
            self._hub.dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return
        self.switch_tab("UPDATE")
        self._hub.settings.verify_files()
        self._refresh_ready_state()

    def _launch_game(self):
        """Launch the game detached; the launch logic (ExampleLoader/WoW.exe
        choice, DXVK notice, clear-wdb, subprocess) lives in the
        UpdateController — this only drives the footer chrome and dialogs."""
        # Realm mismatch check before launch: if the on-disk Config.wtf /
        # realmlist.wtf points elsewhere, ask before injecting the third-party
        # URL from server.json. Two choices, both then launch.
        try:
            client_dir = (self._hub.settings.state.path or "").strip()
            if client_dir:
                status = self._hub.updater.realm_status(client_dir)
                if status.mismatch:
                    actual = (
                        status.actual_config
                        or status.actual_realmlist
                        or "<unknown>"
                    )
                    expected = status.expected
                    server_name = (
                        launcher.server_name()
                        or launcher.server_url()
                        or "this server"
                    )
                    is_third_party = expected.strip().lower() != "localhost"
                    addr_label = (
                        f"third-party address ({expected})"
                        if is_third_party
                        else f"address ({expected})"
                    )
                    answer = QMessageBox.question(
                        self,
                        "Realm mismatch",
                        (
                            f"The game folder realm is '{actual}' but the "
                            f"server '{server_name}' wants '{expected}'.\n\n"
                            "Injecting will overwrite WTF/Config.wtf "
                            "(realmList/patchList) and realmlist.wtf with a "
                            f"{addr_label}. Only proceed "
                            "if you trust this server.\n\n"
                            "Do you want to update the realm before launching?"
                        ),
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if answer == QMessageBox.Yes:
                        self._hub.updater.inject_realm(client_dir)
                elif not status.config_exists or not status.realmlist_exists:
                    # Fresh install or deleted WTF — seed without a prompt.
                    # Only when Play is actually offered (playable client present)
                    # to avoid I/O in an empty folder.
                    if os.path.isdir(client_dir):
                        self._hub.updater.inject_realm(client_dir)
        except Exception:
            pass
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

    def _terminate_game(self):
        """End the running game; the button stays disabled until the watcher
        reports GameExited, then flips back to PLAY."""
        if not self._hub.updater.terminate_game():
            return
        self._set_button_busy("TERMINATE")
        self._statusLabel.setText("Terminating…")
        self._after(3000, self._refresh_ready_state)

    def _show_dxvk_notice(self):
        QMessageBox.information(
            self,
            "DXVK mod first launch",
            "Initial shader compilation may cause temporary in-game "
            "stuttering during the first launch. This is a normal process "
            "while the game builds its shader cache.\n\n"
            "Users with AMD GPUs experiencing stability issues can switch "
            "to DXVK 2.5.3",
        )

    def _refresh_ready_state(self):
        """Recompute the footer status/button from the controller's
        readiness. PLAY is only offered when the client files are up to date
        AND no mod is in an error state — the decision itself lives in
        UpdateController.compute_readiness."""
        self._apply_readiness(self._readiness())
        self._sync_recheck_button()

    def _sync_recheck_button(self):
        """Force recheck is only offered while nothing else is in flight and
        client updates are on — same busy-guards verify_files enforces."""
        if self._updatePanel is None:
            return
        hub = self._hub
        enabled = (
            not hub.updater.running
            and not hub.updater.state.game_running
            and not hub.addons.installing
            and hub.settings.client_update_enabled
        )
        self._updatePanel.set_recheck_enabled(enabled)

    def _readiness(self):
        return self._hub.updater.compute_readiness(
            addons_installing=self._hub.addons.installing
        )

    def _apply_readiness(self, r, update_status: bool = True):
        if r.mode == "play":
            self._set_button_ready(True)
        elif r.mode == "update":
            self._set_button_ready(False)
        elif r.mode == "download":
            # Clickable gold button (same action styling as UPDATE) but with
            # the DOWNLOAD label — triggers the BitTorrent client download.
            self._updateButton.setText("DOWNLOAD")
            self._updateButton.setStyleSheet(self._buttonStyles["update"])
            self._updateButton.setEnabled(True)
        elif r.mode == "terminate":
            self._set_button_terminate()
        elif r.mode == "disabled":
            # No manifest available: keep the UPDATE label but gray the
            # button out so it can't start a blind update.
            self._set_button_busy("UPDATE")
        else:
            self._set_button_busy(r.label)
        if update_status:
            self._statusLabel.setText(r.status)
            torrent_error = self._hub.updater.state.torrent_error
            self._statusLabel.setToolTip(torrent_error or "")

    def _set_button_ready(self, ready: bool):
        """Gold UPDATE ↔ green PLAY flip; the button stays clickable."""
        self._updateButton.setText("PLAY" if ready else "UPDATE")
        self._updateButton.setStyleSheet(
            self._buttonStyles["play" if ready else "update"]
        )
        self._updateButton.setEnabled(True)

    def _set_button_terminate(self):
        """Red TERMINATE button — clickable, ends the running game."""
        self._updateButton.setText("TERMINATE")
        self._updateButton.setStyleSheet(self._buttonStyles["terminate"])
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
        if (
            hub.settings.client_update_enabled
            and not hub.settings.state.first_run_verify_pending
        ):
            self._after(300, self._start_verify)
        self._after(600, hub.news.load)
        self._after(900, hub.mods.load_latest_versions)
        # Asset verdicts refresh off-thread too (a probe may HEAD the server).
        self._after(1100, hub.assets.refresh_verdicts)
        # Verify unconditionally so a first-launch user with an
        # uninitialized config still sees the catalog list (the verify TTL
        # skips redundant rescans on later launches). The catalog fetch
        # inside is served from the weekly cache unless it went stale.
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
        self._pollTimer.stop()
        for timer in self._oneShotTimers:
            timer.stop()
        self._oneShotTimers.clear()
        if self._firstRunTimer is not None:
            self._firstRunTimer.stop()

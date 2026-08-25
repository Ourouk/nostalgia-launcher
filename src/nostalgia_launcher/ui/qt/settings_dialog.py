"""Nostalgia Launcher Qt (PySide6) settings dialog.

A dark QDialog rendering the GAME FOLDER row (open-folder link, readonly path
entry, Change), the DOWNLOAD MIRRORS rows (one per configured server/mirror:
status dot + name + status label + a check button), the TROUBLESHOOTING
clickable rows and the GENERAL checkboxes. It renders the SettingsController's
state and forwards user actions straight into the toolkit-agnostic
controller; mirror results arrive as MirrorStatusChanged events through the
ControllerBridge and are rendered here.
"""

import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...controllers.settings import SettingsController
from ...core import config_store, launcher, platform_support, profiles
from . import metrics
from .bridge import ControllerBridge
from .linux_settings_dialog import LinuxSettingsDialog
from .list_panel import ClickableLabel, make_hairline
from .profiles_ui import switch_profile
from .theme import Palette, apply_theme


class _ClickableRow(QWidget):
    """A clickable icon+text row. Children are mouse-transparent so a click
    anywhere on the row fires clicked."""

    clicked = Signal()

    def __init__(
        self, icon: str, text: str, palette: Palette, icon_color, parent=None
    ):
        super().__init__(parent)
        p = palette
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        icon_label = QLabel(icon, self)
        icon_label.setStyleSheet(
            f"color: {icon_color.name()}; font-size: 11pt;"
        )
        icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        text_label = QLabel(text, self)
        text_label.setWordWrap(True)
        text_label.setStyleSheet(f"color: {p.text.name()}; font-size: 10pt;")
        text_label.setAttribute(Qt.WA_TransparentForMouseEvents)

        layout.addWidget(icon_label, 0, Qt.AlignLeft)
        layout.addWidget(text_label, 0, Qt.AlignLeft)
        layout.addStretch(1)

        self._palette = palette
        self._text_label = text_label

    def set_text(self, text: str):
        """Swap the row label (hover styling follows the same label)."""
        self._text_label.setText(text)

    def click(self):
        self.clicked.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self._text_label.setStyleSheet(
            f"color: {self._palette.gold.name()}; font-size: 10pt;"
        )
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._text_label.setStyleSheet(
            f"color: {self._palette.text.name()}; font-size: 10pt;"
        )
        super().leaveEvent(event)


class SettingsDialog(QDialog):
    """The SETTINGS dialog.

    Constructible and closable headlessly: it reads the controller's state,
    renders the mirror status it already holds, and only starts work when the
    user clicks a row/button. `logsToggleRequested` fires for the Show logs
    row; MainWindow pushes the log window's visibility back via
    `set_logs_open` so the row label always mirrors it.
    """

    logsToggleRequested = Signal()

    def __init__(
        self,
        settings: SettingsController,
        bridge: ControllerBridge,
        palette: Palette,
        parent=None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._palette = palette
        self._linuxDialog = None
        p = palette
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setMinimumSize(560, 660)
        apply_theme(
            self, p, f"\nQDialog {{ background-color: {p.bg.name()}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_divider())
        root.addWidget(self._build_body(), 1)

        bridge.mirrorStatusChanged.connect(self._on_mirror_status)

    # ── build ───────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        p = self._palette
        hdr = QWidget(self)
        layout = QHBoxLayout(hdr)
        layout.setContentsMargins(18, 12, 12, 12)

        title = QLabel("SETTINGS", hdr)
        title.setStyleSheet(
            f"color: {p.gold_lt.name()}; font-weight: bold;"
            f" font-size: {metrics.PT_DIALOG}pt;"
        )
        layout.addWidget(title)
        layout.addStretch(1)
        return hdr

    def _build_divider(self) -> QFrame:
        return make_hairline(self)

    def _build_body(self) -> QWidget:
        p = self._palette
        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(22, 16, 22, 12)
        body_layout.setSpacing(8)

        folder_row = QHBoxLayout()
        folder_label = QLabel("GAME FOLDER", body)
        folder_label.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;"
        )
        folder_row.addWidget(folder_label)
        folder_row.addStretch(1)
        open_link = ClickableLabel("Open folder", body)
        open_link.setObjectName("settingsOpenFolder")
        open_link.setCursor(Qt.PointingHandCursor)
        open_link.setStyleSheet(f"color: {p.text_dim.name()}; font-size: 9pt;")
        open_link.clicked.connect(self._settings.open_client_folder)
        folder_row.addWidget(open_link)
        body_layout.addLayout(folder_row)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit(self._settings.state.path, body)
        self._path_edit.setObjectName("settingsPath")
        self._path_edit.setReadOnly(True)
        # No confirmed folder yet: suggest Games/<ServerName> without
        # pretending it is already selected.
        self._path_edit.setPlaceholderText(
            self._settings.state.suggestion
            or "Select the game folder containing WoW.exe"
        )
        self._path_edit.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        path_row.addWidget(self._path_edit, 1)
        change_btn = QPushButton("Change", body)
        change_btn.setObjectName("settingsChange")
        change_btn.clicked.connect(self._on_change_dir)
        path_row.addWidget(change_btn)
        body_layout.addLayout(path_row)

        mirror_title = QLabel("DOWNLOAD MIRRORS", body)
        mirror_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;"
        )
        body_layout.addWidget(mirror_title)
        body_layout.addSpacing(2)

        self._mirror_rows: dict[str, QLabel] = {}
        self._mirror_dots: dict[str, QLabel] = {}
        names = self._settings._http_mirror_names()
        if not names:
            cfg = launcher.config()
            text = (
                "No server configured (launcher configuration missing)."
                if cfg is None or not cfg.server_url
                else "No HTTP mirrors configured — update uses the server directly."
            )
            hint = QLabel(text, body)
            hint.setObjectName("settingsMirrorEmpty")
            hint.setStyleSheet(f"color: {p.text_dim.name()}; font-size: 9pt;")
            body_layout.addWidget(hint)
        else:
            for name in names:
                row = QHBoxLayout()
                dot = QLabel("●", body)
                dot.setStyleSheet(f"color: {p.text_dim.name()};")
                row.addWidget(dot)
                label = QLabel(name, body)
                label.setStyleSheet(
                    f"color: {p.text.name()}; font-weight: bold; font-size: 10pt;"
                )
                row.addWidget(label)
                status = QLabel("", body)
                status.setObjectName(f"settingsMirrorStatus_{name}")
                status.setStyleSheet(
                    f"color: {p.text_dim.name()}; font-size: 9pt;"
                )
                row.addWidget(status)
                row.addStretch(1)
                body_layout.addLayout(row)
                self._mirror_rows[name] = status
                self._mirror_dots[name] = dot
        refresh = QToolButton(body)
        refresh.setObjectName("settingsMirrorRefresh")
        refresh.setText("⟳  Check mirrors")
        refresh.setToolTip("Check server and mirror reachability")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setVisible(bool(names))
        refresh.setStyleSheet(
            f"QToolButton {{ color: {p.text_dim.name()}; font-size: 9pt; }}"
            f"QToolButton:hover {{ color: {p.gold.name()}; }}"
        )
        refresh.clicked.connect(self._on_refresh_mirror)
        body_layout.addWidget(refresh)

        self._render_mirror_statuses()

        body_layout.addSpacing(6)

        cols = QHBoxLayout()
        cols.setSpacing(24)
        lcol = QWidget(body)
        lcol_layout = QVBoxLayout(lcol)
        lcol_layout.setContentsMargins(0, 0, 0, 0)
        lcol_layout.setSpacing(0)
        lcol_layout.setAlignment(Qt.AlignTop)

        ts_title = QLabel("TROUBLESHOOTING", lcol)
        ts_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;"
        )
        lcol_layout.addWidget(ts_title)

        self._add_row(
            lcol_layout,
            "✓",
            "Verify game files",
            self._settings.verify_files,
            "settingsVerify",
            p.gold,
        )
        self._logsRow = self._add_row(
            lcol_layout,
            "☰",
            "Show logs",
            self.logsToggleRequested.emit,
            "settingsLogs",
            p.gold,
        )
        if platform_support.can_manage_antivirus():
            self._add_row(
                lcol_layout,
                "⛊",
                "Add game folder to Defender exclusions",
                self._settings.allow_through_antivirus,
                "settingsAv",
                p.gold,
            )

        rcol = QWidget(body)
        rcol_layout = QVBoxLayout(rcol)
        rcol_layout.setContentsMargins(0, 0, 0, 0)
        rcol_layout.setSpacing(4)
        rcol_layout.setAlignment(Qt.AlignTop)

        general_title = QLabel("GENERAL", rcol)
        general_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;"
        )
        rcol_layout.addWidget(general_title)

        cfg = self._settings.state.config
        self._clear_wdb_check = None
        self._close_on_launch_check = None
        if platform_support.can_launch_client():
            self._clear_wdb_check = self._add_check(
                rcol_layout,
                "Clear WDB on game launch",
                "settingsClearWdb",
                bool(cfg.get("clear_wdb_on_launch", False)),
                self._settings.set_clear_wdb,
            )
            self._close_on_launch_check = self._add_check(
                rcol_layout,
                "Close Nostalgia Launcher on game launch",
                "settingsCloseOnLaunch",
                bool(cfg.get("close_on_launch", False)),
                self._settings.set_close_on_launch,
            )
        self._client_update_check = self._add_check(
            rcol_layout,
            "Enable client updates",
            "settingsClientUpdate",
            self._settings.client_update_enabled,
            self._settings.set_client_update_enabled,
        )

        if platform_support.is_linux():
            self._build_linux_button(rcol_layout)

        cols.addWidget(lcol, 3)
        cols.addWidget(rcol, 2)
        body_layout.addLayout(cols, 1)

        self._build_registry_section(body_layout)
        self._build_profiles_section(body_layout)
        return body

    def _add_row(self, layout, icon, text, command, object_name, color):
        row = _ClickableRow(icon, text, self._palette, color, self)
        row.setObjectName(object_name)
        row.clicked.connect(command)
        layout.addWidget(row)
        layout.addSpacing(8)
        return row

    def set_logs_open(self, open_: bool):
        """Reflect the session-log window's visibility in the row label
        (the row toggles: "Show logs" when closed, "Hide logs" when open)."""
        self._logsRow.set_text("Hide logs" if open_ else "Show logs")

    # ── catalog registries ───────────────────────────────────────────────

    def _build_registry_section(self, layout):
        p = self._palette
        layout.addSpacing(10)

        title = QLabel("CATALOG REGISTRIES", self)
        title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;"
        )
        layout.addWidget(title)

        hint = QLabel(
            "For advanced users: point the mod/addon catalogs at another "
            "HTTPS JSON registry, or add your own entries via the per-user "
            "custom JSON files.",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {p.text_dim.name()}; font-size: 9pt;")
        layout.addWidget(hint)
        layout.addSpacing(4)

        self._registry_status = QLabel("", self)
        self._registry_status.setObjectName("settingsRegistryStatus")
        self._registry_status.setWordWrap(True)
        self._registry_status.setStyleSheet(
            f"color: {p.err.name()}; font-size: 9pt;"
        )
        layout.addWidget(self._registry_status)
        layout.addSpacing(2)

        self._build_registry_row(
            layout,
            "ADDONS",
            "settingsAddon",
            self._settings.addons_registry_url,
            self._settings.set_addons_registry_url,
            self._settings.reset_addons_registry_url,
            self._settings.reload_addons_registry,
            self._settings.open_addons_custom_file,
            self._settings.clear_addons_custom,
        )
        self._build_registry_row(
            layout,
            "MODS",
            "settingsMod",
            self._settings.mods_registry_url,
            self._settings.set_mods_registry_url,
            self._settings.reset_mods_registry_url,
            self._settings.reload_mods_registry,
            self._settings.open_mods_custom_file,
            self._settings.clear_mods_custom,
        )

    def _build_registry_row(
        self,
        layout,
        label,
        prefix,
        get_url,
        on_apply,
        on_reset,
        on_reload,
        on_open_custom,
        on_clear_custom,
    ):
        p = self._palette
        row = QHBoxLayout()
        name = QLabel(label, self)
        name.setStyleSheet(
            f"color: {p.text.name()}; font-weight: bold; font-size: 9pt;"
        )
        name.setFixedWidth(64)
        row.addWidget(name)

        edit = QLineEdit(get_url(), self)
        edit.setObjectName(f"{prefix}RegistryUrl")
        edit.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        row.addWidget(edit, 1)

        apply_btn = QPushButton("Apply", self)
        apply_btn.setObjectName(f"{prefix}RegistryApply")
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.clicked.connect(
            lambda: self._on_apply_registry(edit, get_url, on_apply)
        )
        row.addWidget(apply_btn)

        reset_btn = QToolButton(self)
        reset_btn.setObjectName(f"{prefix}RegistryReset")
        reset_btn.setText("Reset")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setToolTip("Use the default server catalog")
        reset_btn.clicked.connect(
            lambda: self._on_reset_registry(edit, get_url, on_reset)
        )
        row.addWidget(reset_btn)

        reload_btn = QToolButton(self)
        reload_btn.setObjectName(f"{prefix}RegistryReload")
        reload_btn.setText("Reload")
        reload_btn.setCursor(Qt.PointingHandCursor)
        reload_btn.setToolTip("Fetch the catalog now and refresh the tab")
        reload_btn.clicked.connect(on_reload)
        row.addWidget(reload_btn)
        layout.addLayout(row)

        links = QHBoxLayout()
        links.addSpacing(64)
        open_link = ClickableLabel("Open custom file", self)
        open_link.setObjectName(f"{prefix}RegistryOpenCustom")
        open_link.setCursor(Qt.PointingHandCursor)
        open_link.setStyleSheet(f"color: {p.gold.name()}; font-size: 9pt;")
        open_link.clicked.connect(on_open_custom)
        links.addWidget(open_link)
        clear_link = ClickableLabel("Clear custom entries", self)
        clear_link.setObjectName(f"{prefix}RegistryClearCustom")
        clear_link.setCursor(Qt.PointingHandCursor)
        clear_link.setStyleSheet(f"color: {p.err.name()}; font-size: 9pt;")
        clear_link.clicked.connect(on_clear_custom)
        links.addWidget(clear_link)
        links.addStretch(1)
        layout.addLayout(links)

    def _on_apply_registry(self, edit, get_url, on_apply):
        err = on_apply(edit.text())
        if err:
            self._registry_status.setText(f"✗ {err}")
        else:
            self._registry_status.setText("")
            edit.setText(get_url())

    # ── launcher profiles ────────────────────────────────────────────────

    def _build_profiles_section(self, layout):
        p = self._palette
        layout.addSpacing(10)

        title = QLabel("PROFILES", self)
        title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 10pt;"
        )
        layout.addWidget(title)

        hint = QLabel(
            "Profile editor — these buttons manage the profile selected "
            "above (fully isolated server config, game state, mods/addons "
            "records and caches). Switch profiles from the selector in the "
            "main-window header; switching restarts the launcher.",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {p.text_dim.name()}; font-size: 9pt;")
        layout.addWidget(hint)
        layout.addSpacing(4)

        self._profiles_status = QLabel("", self)
        self._profiles_status.setObjectName("settingsProfilesStatus")
        self._profiles_status.setWordWrap(True)
        self._profiles_status.setStyleSheet(
            f"color: {p.err.name()}; font-size: 9pt;"
        )
        layout.addWidget(self._profiles_status)
        layout.addSpacing(2)

        row = QHBoxLayout()
        self._profiles_combo = QComboBox(self)
        self._profiles_combo.setObjectName("profilesCombo")
        self._refresh_profiles_combo()
        row.addWidget(self._profiles_combo, 1)

        new_btn = QPushButton("New…", self)
        new_btn.setObjectName("profilesNew")
        new_btn.setCursor(Qt.PointingHandCursor)
        new_btn.clicked.connect(self._on_profile_new)
        row.addWidget(new_btn)

        dup_btn = QPushButton("Duplicate", self)
        dup_btn.setObjectName("profilesDuplicate")
        dup_btn.setCursor(Qt.PointingHandCursor)
        dup_btn.clicked.connect(self._on_profile_duplicate)
        row.addWidget(dup_btn)

        rename_btn = QPushButton("Rename…", self)
        rename_btn.setObjectName("profilesRename")
        rename_btn.setCursor(Qt.PointingHandCursor)
        rename_btn.clicked.connect(self._on_profile_rename)
        row.addWidget(rename_btn)

        delete_btn = QPushButton("Delete", self)
        delete_btn.setObjectName("profilesDelete")
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.clicked.connect(self._on_profile_delete)
        row.addWidget(delete_btn)

        layout.addLayout(row)

    def _refresh_profiles_combo(self, select=None):
        """Rebuild the combo from the registry; preselect `select` (or the
        active profile)."""
        combo = self._profiles_combo
        current = select or profiles.active().name
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(profiles.list_profiles())
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(False)

    def _selected_profile(self) -> str:
        return self._profiles_combo.currentText()

    def _profile_error(self, msg: str):
        self._profiles_status.setText(f"✗ {msg}" if msg else "")

    def _prompt_profile_name(self, title, initial=""):
        name, ok = QInputDialog.getText(
            self, title, "Profile name:", text=initial
        )
        return (name or "").strip(), ok

    def _on_profile_new(self):
        name, ok = self._prompt_profile_name("New profile")
        if not ok:
            return
        err = profiles.validate_name(name)
        if err:
            self._profile_error(err)
            return
        prof, err = profiles.create(name)
        if err:
            self._profile_error(err)
            return
        self._refresh_profiles_combo(select=name)
        self._profile_error("")
        self._configure_new_profile(prof)

    def _configure_new_profile(self, prof):
        """Open the first-launch wizard scoped to the fresh profile: BOTH
        the persist override and the process-active profile point at it
        while the dialog runs, so an accepted selection lands its
        launcher.json AND content repos into the new profile without ever
        touching the running profile's stores or the global launcher
        config. The wizard's required install folder is recorded into the
        new profile's OWN state store (the process store still points at
        the running profile), so the profile restarts fully configured.
        Skipping is acceptable — the profile simply stays unconfigured."""
        from .launcher_config_dialog import LauncherConfigDialog

        prev_active = profiles.active()
        try:
            launcher.set_profile_launcher_path(prof.launcher_path())
            profiles.activate(prof)
            dlg = LauncherConfigDialog(initial_path=launcher.discover_path())
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            sel = dlg.selection()
            err = self._persist_profile_selection(sel)
            if err:
                self._profile_error(err)
                return
            install_dir = (sel.get("install_dir") or "").strip()
            if install_dir:
                config_store.apply_confirmed_out_dir(
                    prof.state_path(), install_dir
                )
        finally:
            profiles.activate(prev_active)
            launcher.set_profile_launcher_path(
                prev_active.launcher_path() if prev_active.root else ""
            )

    def _persist_profile_selection(self, sel) -> str:
        """Persist a wizard selection (file or URL) into the CURRENT
        persist override / active-profile scope. Validation-only: never
        mutates the process-global launcher config (`persist`/
        `persist_text` re-validate internally; `validate_dict` is
        side-effect-free). Returns "" on success."""
        from ...services import config_import

        if sel["kind"] == "file":
            _dest, err = launcher.persist(sel["path"])
            return err
        raw = sel.get("raw")
        if not raw:
            _data, raw, err = config_import.fetch_config_url(sel["config_url"])
            if err:
                return err
        cfg, verr = launcher.validate_dict(json.loads(raw))
        if cfg is None:
            return f"Invalid launcher configuration: {verr}"
        _dest, err = launcher.persist_text(raw)
        return err

    def _on_profile_duplicate(self):
        src = self._selected_profile()
        if not src:
            return
        suggestion = f"{src}-copy"
        name, ok = self._prompt_profile_name(
            "Duplicate profile",
            initial=suggestion[:31].rstrip(". "),
        )
        if not ok:
            return
        err = profiles.duplicate(src, name)
        if err:
            self._profile_error(err)
            return
        self._profile_error("")
        self._refresh_profiles_combo(select=name)

    def _on_profile_rename(self):
        src = self._selected_profile()
        if not src:
            return
        name, ok = self._prompt_profile_name("Rename profile", initial=src)
        if not ok:
            return
        err = profiles.rename(src, name)
        if err:
            self._profile_error(err)
            return
        self._profile_error("")
        self._refresh_profiles_combo(select=name)

    def _on_profile_delete(self):
        name = self._selected_profile()
        if not name:
            return
        if name == profiles.DEFAULT_PROFILE:
            self._profile_error("The default profile cannot be deleted.")
            return
        answer = QMessageBox.question(
            self,
            "Delete profile",
            f"Delete profile '{name}'?\n\nIts server config, state and "
            "caches will be removed.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        was_active = name == profiles.active().name
        err = profiles.delete(name)
        if err:
            self._profile_error(err)
            return
        self._profile_error("")
        self._refresh_profiles_combo()
        if was_active:
            # Pointer was reset to default — offer the immediate restart
            # (no extra confirm; the deletion itself was just confirmed).
            answer = QMessageBox.question(
                self,
                "Profile deleted",
                f"'{name}' was the active profile. Restart now on 'default'?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer == QMessageBox.Yes and not switch_profile(
                profiles.DEFAULT_PROFILE
            ):
                self._profile_error("Restart the launcher manually to switch.")

    def _on_reset_registry(self, edit, get_url, on_reset):
        on_reset()
        edit.setText(get_url())
        self._registry_status.setText("")

    def _add_check(self, layout, text, object_name, checked, on_toggled):
        check = QCheckBox(text, self)
        check.setObjectName(object_name)
        check.setCursor(Qt.PointingHandCursor)
        check.blockSignals(True)
        check.setChecked(bool(checked))
        check.blockSignals(False)
        check.toggled.connect(on_toggled)
        layout.addWidget(check)
        return check

    # ── Linux umu-launcher ────────────────────────────────────────────────

    def _build_linux_button(self, layout):
        """On Linux, a single button that opens the separate Linux (UMU)
        settings window holding every Linux play setting (Proton, renderer,
        GameMode, Wayland, GAMEID, umu-run path)."""
        p = self._palette
        btn = QPushButton("Linux (UMU) Settings…", self)
        btn.setObjectName("settingsLinuxButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ color: {p.gold.name()}; font-size: 10pt; "
            f"text-align: left; padding: 6px 10px; }}"
            f"QPushButton:hover {{ color: {p.text.name()}; }}"
        )
        btn.clicked.connect(self._on_open_linux_settings)
        layout.addWidget(btn)

    def _on_open_linux_settings(self):
        if self._linuxDialog is None:
            self._linuxDialog = LinuxSettingsDialog(
                self._settings, self._palette, self
            )
            self._linuxDialog.finished.connect(self._on_linux_dialog_finished)
        self._linuxDialog.show()
        self._linuxDialog.raise_()
        self._linuxDialog.activateWindow()

    def _on_linux_dialog_finished(self):
        self._linuxDialog = None

    # ── actions ─────────────────────────────────────────────────────────────

    def _on_change_dir(self):
        cur = self._settings.state.path
        initial = cur if os.path.isdir(cur) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(
            self, "Select game client folder", initial
        )
        if chosen:
            chosen = os.path.normpath(chosen)
            self._settings.set_path(chosen)
            self._path_edit.setText(chosen)

    def _on_refresh_mirror(self):
        p = self._palette
        for name in self._mirror_rows:
            self._mirror_rows[name].setText("checking…")
            self._mirror_rows[name].setStyleSheet(
                f"color: {p.text_dim.name()}; font-size: 9pt;"
            )
            self._mirror_dots[name].setStyleSheet(
                f"color: {p.text_dim.name()};"
            )
        self._settings.check_mirror()

    # ── mirror status rendering ─────────────────────────────────────────────

    def _render_mirror_statuses(self):
        p = self._palette
        statuses = self._settings.mirror_statuses
        for name, status in self._mirror_rows.items():
            text = statuses.get(name, "")
            color = (
                p.ok
                if text == "online"
                else (p.err if text == "offline" else p.text_dim)
            )
            status.setText(text)
            status.setStyleSheet(f"color: {color.name()}; font-size: 9pt;")
            self._mirror_dots[name].setStyleSheet(f"color: {color.name()};")

    def _on_mirror_status(self, ok: bool, text: str):
        self._render_mirror_statuses()

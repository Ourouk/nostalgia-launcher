"""Nostalgia Launcher Qt (PySide6) first-launch configuration import dialog.

A QDialog shown on first launch to let the user import a launcher
configuration before any MainWindow exists. The launcher has no built-in
server directory: the configuration comes from a local file the user
selects or an https URL the user types — typically obtained independently
from a community or server operator.

Flow: import stage (file or URL radio) → fetch/read + validate →
install-folder stage confirming where the game client lives (pre-filled
with the Games/<ServerName> suggestion, editable + Browse; REQUIRED —
each profile keeps its own install folder) → trust stage showing exactly
what the configuration points at (server name, every host that will be
contacted, which capabilities are configured, the chosen install folder,
and an explicit note that the configuration controls game-file
downloads) → explicit Trust. Nothing is persisted by the dialog; the
chosen selection is exposed via ``selection()`` as ``{"kind": "file",
"path": ..., "install_dir": ..., "server_name": ...}`` or ``{"kind":
"url", "config_url": ..., "raw": ..., "install_dir": ...,
"server_name": ...}``.
"""

import os
import threading
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core import launcher, platform_support
from ...services import config_import
from .list_panel import make_hairline
from .metrics import PT_DIALOG, PT_SECTION
from .theme import Palette, apply_theme


class LauncherConfigDialog(QDialog):
    """First-launch wizard: import a launcher configuration (file or URL)."""

    def __init__(
        self,
        palette: Palette | None = None,
        parent=None,
        initial_path: str = "",
    ):
        super().__init__(parent)
        p = palette or Palette()
        self._palette = p
        self._selection = None
        self._fetching = False
        self._stage = "input"
        # Config validated but not yet confirmed (kind, source, raw, cfg):
        # held while the install-folder stage runs.
        self._validated = None
        self._install_dir = ""
        self._pending = None  # (kind, raw, config) awaiting confirmation
        self.setObjectName("launcherConfigDialog")
        self.setWindowTitle("Welcome to Nostalgia Launcher")
        self.setMinimumWidth(560)
        self.setMinimumHeight(400)
        apply_theme(
            self,
            p,
            "\nQDialog { background-color: "
            + p.panel.name()
            + "; }"
            + "\nQRadioButton { color: "
            + p.text.name()
            + "; background-color: transparent; }"
            + "\nQRadioButton::indicator { width: 14px; height: 14px; "
            + "border: 1px solid "
            + p.panel_bdr.name()
            + "; border-radius: 7px; background-color: "
            + p.hdr.name()
            + "; }"
            + "\nQRadioButton::indicator:checked { background-color: "
            + p.gold.name()
            + "; border-color: "
            + p.gold_lt.name()
            + "; }",
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        self._title = QLabel("Welcome to Nostalgia Launcher", self)
        self._title.setObjectName("launcherConfigTitle")
        self._title.setStyleSheet(
            f"color: {p.gold_lt.name()}; font-weight: bold; font-size: 12pt;"
        )
        root.addWidget(self._title)

        self._step = QLabel("Step 1 of 3 — Import configuration", self)
        self._step.setObjectName("launcherConfigStep")
        self._step.setStyleSheet(f"color: {p.text_dim.name()};")
        root.addWidget(self._step)

        self._intro = QLabel(
            "This launcher doesn't include any server list.\n\n"
            "To continue, import a configuration supplied independently "
            "by your community or server operator. The configuration "
            "controls where the launcher retrieves game files, news, "
            "mods, and addons from.",
            self,
        )
        self._intro.setObjectName("launcherConfigIntro")
        self._intro.setStyleSheet(f"color: {p.text_dim.name()};")
        self._intro.setWordWrap(True)
        root.addWidget(self._intro)

        self._status = QLabel("", self)
        self._status.setObjectName("launcherConfigStatus")
        self._status.setStyleSheet(f"color: {p.text_dim.name()};")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        # ── source picker ──────────────────────────────────────────────
        self._sourceUrl = QRadioButton("A configuration URL", self)
        self._sourceUrl.setObjectName("launcherConfigSourceUrlRadio")
        self._sourceUrl.toggled.connect(self._on_source_changed)
        root.addWidget(self._sourceUrl)
        self._sourceFile = QRadioButton("A configuration file", self)
        self._sourceFile.setObjectName("launcherConfigSourceFileRadio")
        self._sourceFile.toggled.connect(self._on_source_changed)
        root.addWidget(self._sourceFile)

        self._urlRow = QWidget(self)
        self._urlRow.setObjectName("launcherConfigUrlRow")
        url_row = QHBoxLayout(self._urlRow)
        url_row.setContentsMargins(0, 0, 0, 0)
        self._url = QLineEdit(self._urlRow)
        self._url.setObjectName("launcherConfigUrl")
        self._url.setPlaceholderText(
            "Configuration URL (https://example.com/community-config.json)"
        )
        self._url.textChanged.connect(self._on_input_changed)
        url_row.addWidget(self._url)
        root.addWidget(self._urlRow)

        self._pathRow = QWidget(self)
        self._pathRow.setObjectName("launcherConfigPathRow")
        path_row = QHBoxLayout(self._pathRow)
        path_row.setContentsMargins(0, 0, 0, 0)
        self._path = QLineEdit(self._pathRow)
        self._path.setObjectName("launcherConfigPath")
        self._path.setReadOnly(True)
        self._path.setPlaceholderText("Select a local configuration file")
        self._path.textChanged.connect(self._on_input_changed)
        path_row.addWidget(self._path)
        self._browse = QPushButton("Browse…", self._pathRow)
        self._browse.setObjectName("launcherConfigBrowse")
        self._browse.setCursor(Qt.PointingHandCursor)
        self._browse.clicked.connect(self.browse)
        path_row.addWidget(self._browse)
        root.addWidget(self._pathRow)

        # Install-folder stage (shown after the configuration validated):
        # each profile keeps its own game-client folder; the field is
        # pre-filled with the Games/<ServerName> suggestion and REQUIRED.
        self._folderGroup = QWidget(self)
        self._folderGroup.setObjectName("launcherConfigFolderGroup")
        folder_col = QVBoxLayout(self._folderGroup)
        folder_col.setContentsMargins(0, 4, 0, 0)
        folder_col.setSpacing(4)
        folder_title = QLabel("INSTALL FOLDER", self._folderGroup)
        folder_title.setObjectName("launcherConfigFolderTitle")
        folder_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold;"
            f" font-size: {PT_SECTION}pt;"
        )
        folder_col.addWidget(folder_title)
        folder_hint = QLabel(
            "Where the game client lives. Each launcher profile keeps its "
            "own install folder.",
            self._folderGroup,
        )
        folder_hint.setObjectName("launcherConfigFolderHint")
        folder_hint.setWordWrap(True)
        folder_hint.setStyleSheet(
            f"color: {p.text_dim.name()}; font-size: 9pt;"
        )
        folder_col.addWidget(folder_hint)
        folder_row = QHBoxLayout()
        self._folder = QLineEdit(self._folderGroup)
        self._folder.setObjectName("launcherConfigFolder")
        self._folder.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self._folder.textChanged.connect(self._on_folder_changed)
        folder_row.addWidget(self._folder, 1)
        folder_browse = QPushButton("Browse…", self._folderGroup)
        folder_browse.setObjectName("launcherConfigFolderBrowse")
        folder_browse.setCursor(Qt.PointingHandCursor)
        folder_browse.clicked.connect(self.browse_install_dir)
        folder_row.addWidget(folder_browse)
        folder_col.addLayout(folder_row)
        self._folderGroup.hide()
        root.addWidget(self._folderGroup)

        # ── trust stage ────────────────────────────────────────────────
        self._trustScroll = QScrollArea(self)
        self._trustScroll.setObjectName("launcherConfigSummaryScroll")
        self._trustScroll.setWidgetResizable(True)
        self._trustScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._trustScroll.setStyleSheet(
            f"QScrollArea {{ background-color: {p.log_bg.name()}; "
            f"border: 1px solid {p.divider.name()}; }}"
            f"QScrollBar:vertical {{ width: 8px; "
            f"background: {p.panel.name()}; }}"
            f"QScrollBar::handle:vertical {{ background: "
            f"{p.divider.name()}; border-radius: 4px; }}"
        )
        trust_content = QWidget(self._trustScroll)
        trust_content.setObjectName("launcherConfigTrustContent")
        trust_col = QVBoxLayout(trust_content)
        trust_col.setContentsMargins(12, 10, 12, 10)
        trust_col.setSpacing(6)
        trust_col.setAlignment(Qt.AlignTop)

        self._trustName = QLabel("", trust_content)
        self._trustName.setObjectName("launcherConfigTrustName")
        self._trustName.setStyleSheet(
            f"color: {p.text.name()}; font-weight: bold;"
            f" font-size: {PT_DIALOG}pt;"
        )
        self._trustName.setWordWrap(True)
        self._trustName.setTextInteractionFlags(Qt.TextSelectableByMouse)
        trust_col.addWidget(self._trustName)

        self._trustSource = QLabel("", trust_content)
        self._trustSource.setObjectName("launcherConfigTrustSource")
        self._trustSource.setStyleSheet(f"color: {p.text_dim.name()};")
        self._trustSource.setWordWrap(True)
        self._trustSource.setTextInteractionFlags(Qt.TextSelectableByMouse)
        trust_col.addWidget(self._trustSource)
        trust_col.addWidget(make_hairline(trust_content))

        hosts_title = QLabel("Will contact:", trust_content)
        hosts_title.setObjectName("launcherConfigTrustHostsTitle")
        hosts_title.setProperty("role", "sectionTitle")
        hosts_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold;"
            f" font-size: {PT_SECTION}pt;"
        )
        trust_col.addWidget(hosts_title)
        self._trustHosts = QLabel("", trust_content)
        self._trustHosts.setObjectName("launcherConfigTrustHosts")
        self._trustHosts.setStyleSheet(f"color: {p.text.name()};")
        self._trustHosts.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self._trustHosts.setWordWrap(True)
        self._trustHosts.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._trustHosts.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        trust_col.addWidget(self._trustHosts)
        trust_col.addWidget(make_hairline(trust_content))

        downloads_title = QLabel("Will download:", trust_content)
        downloads_title.setObjectName("launcherConfigTrustDownloadsTitle")
        downloads_title.setProperty("role", "sectionTitle")
        downloads_title.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold;"
            f" font-size: {PT_SECTION}pt;"
        )
        trust_col.addWidget(downloads_title)
        self._trustRows: dict[str, QLabel] = {}
        for key, name in (
            ("client", "launcherConfigTrustClient"),
            ("mods", "launcherConfigTrustMods"),
            ("addons", "launcherConfigTrustAddons"),
            ("news", "launcherConfigTrustNews"),
        ):
            row = QLabel("", trust_content)
            row.setObjectName(name)
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextSelectableByMouse)
            trust_col.addWidget(row)
            self._trustRows[key] = row
        trust_col.addWidget(make_hairline(trust_content))

        self._trustFolder = QLabel("", trust_content)
        self._trustFolder.setObjectName("launcherConfigTrustFolder")
        self._trustFolder.setStyleSheet(f"color: {p.text.name()};")
        self._trustFolder.setWordWrap(True)
        self._trustFolder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        trust_col.addWidget(self._trustFolder)

        trust_note = QLabel(
            "Only trust this configuration if you trust its source. It "
            "controls where game files, mods, and addons are downloaded "
            "from and can modify the selected game folder.",
            trust_content,
        )
        trust_note.setObjectName("launcherConfigTrustNote")
        trust_note.setStyleSheet(f"color: {p.text_dim.name()};")
        trust_note.setWordWrap(True)
        trust_col.addWidget(trust_note)

        self._trustScroll.setWidget(trust_content)
        self._trustScroll.hide()
        root.addWidget(self._trustScroll, 1)

        self._error = QLabel("", self)
        self._error.setObjectName("launcherConfigError")
        self._error.setStyleSheet(f"color: {p.err.name()};")
        self._error.setWordWrap(True)
        self._error.hide()
        root.addWidget(self._error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._back = QPushButton("Back", self)
        self._back.setObjectName("launcherConfigBack")
        self._back.clicked.connect(self._go_back)
        self._back.hide()
        buttons.addWidget(self._back)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("launcherConfigCancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self._ok = QPushButton("Continue", self)
        self._ok.setObjectName("launcherConfigOk")
        self._ok.setProperty("variant", "primary")
        self._ok.setCursor(Qt.PointingHandCursor)
        self._ok.clicked.connect(self._submit)
        self._ok.setEnabled(False)
        buttons.addWidget(self._ok)
        root.addLayout(buttons)

        if initial_path and os.path.isfile(initial_path):
            self._path.setText(initial_path)
            self._sourceFile.setChecked(True)
        else:
            self._sourceFile.setChecked(True)
        self._set_stage("input")
        self._refresh_ok()

    # ── population ───────────────────────────────────────────────────────

    def _set_stage(self, stage: str):
        self._stage = stage
        input_stage = stage == "input"
        folder_stage = stage == "folder"
        summary_visible = stage == "summary"
        titles = {
            "input": "Welcome to Nostalgia Launcher",
            "folder": "Choose your install folder",
            "summary": "You're about to trust:",
        }
        steps = {
            "input": "Step 1 of 3 — Import configuration",
            "folder": "Step 2 of 3 — Install folder",
            "summary": "Step 3 of 3 — Trust configuration",
        }
        self._title.setText(titles[stage])
        self._step.setText(steps[stage])
        self._intro.setVisible(input_stage)
        self._sourceFile.setVisible(input_stage)
        self._sourceUrl.setVisible(input_stage)
        self._urlRow.setVisible(input_stage and self._sourceUrl.isChecked())
        self._pathRow.setVisible(input_stage and self._sourceFile.isChecked())
        self._trustScroll.setVisible(summary_visible)
        # Back walks summary → folder → input.
        self._back.setVisible(not input_stage)
        self._url.setEnabled(input_stage)
        self._path.setEnabled(input_stage)
        self._sourceFile.setEnabled(input_stage)
        self._sourceUrl.setEnabled(input_stage)
        self._browse.setEnabled(input_stage)
        self._folderGroup.setVisible(not input_stage)
        self._folder.setEnabled(folder_stage)
        self.findChild(QPushButton, "launcherConfigFolderBrowse").setEnabled(
            folder_stage
        )
        self._ok.setText(
            "Trust configuration" if summary_visible else "Continue"
        )

    def _set_status(self, text: str):
        self._status.setText(text)
        self._status.setVisible(bool(text))

    # ── interaction ──────────────────────────────────────────────────────

    def reject(self):
        # A late fetch result must not re-accept a cancelled dialog.
        self._fetching = False
        super().reject()

    def browse(self):
        current = self._path.text()
        start_dir = os.path.dirname(current) if current else os.getcwd()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select launcher configuration",
            start_dir,
            "Launcher configuration (*.json)",
        )
        if path:
            self._path.setText(path)
            self._sourceFile.setChecked(True)

    def browse_install_dir(self):
        """Pick the game-client folder (existing directory)."""
        current = os.path.expanduser(self._folder.text().strip())
        start_dir = (
            current if os.path.isdir(current) else os.path.expanduser("~")
        )
        path = QFileDialog.getExistingDirectory(
            self, "Select game client folder", start_dir
        )
        if path:
            self._folder.setText(os.path.normpath(path))

    def _on_source_changed(self):
        if self._stage != "input":
            return
        self._clear_error()
        self._urlRow.setVisible(self._sourceUrl.isChecked())
        self._pathRow.setVisible(self._sourceFile.isChecked())
        self._refresh_ok()

    def _on_folder_changed(self, _text):
        self._clear_error()
        self._refresh_ok()

    def _on_input_changed(self, _text):
        self._error.clear()
        self._error.hide()
        sender = self.sender()
        # Typing in a field selects its source.
        if sender is self._url and self._url.text().strip():
            self._sourceUrl.setChecked(True)
        elif sender is self._path and self._path.text().strip():
            self._sourceFile.setChecked(True)
        self._refresh_ok()

    def _active_source(self) -> str:
        """The selected import source: "url" or "file"."""
        if self._sourceUrl.isChecked() and self._url.text().strip():
            return "url"
        if self._sourceFile.isChecked() and self._path.text().strip():
            return "file"
        # Fall back to whichever field holds a value (programmatic use).
        if self._url.text().strip():
            return "url"
        return "file"

    def _refresh_ok(self):
        if self._fetching:
            self._ok.setEnabled(False)
            return
        if self._stage == "summary":
            self._ok.setEnabled(True)
            return
        if self._stage == "folder":
            # REQUIRED: no accept without an install folder.
            self._ok.setEnabled(bool(self._folder.text().strip()))
            return
        if self._active_source() == "url":
            self._ok.setEnabled(bool(self._url.text().strip()))
        else:
            self._ok.setEnabled(bool(self._path.text().strip()))

    def _clear_error(self):
        self._error.clear()
        self._error.hide()

    def _show_error(self, message: str):
        self._error.setText(message)
        self._error.show()
        self._refresh_ok()

    def _go_back(self):
        if self._stage == "summary":
            self._pending = None
            self._set_stage("folder")
            self._set_status(
                "Confirm where the game client will be installed."
            )
            self._refresh_ok()
            return
        # folder → input: drop the validated config, re-pick the source.
        self._validated = None
        self._set_stage("input")
        self._set_status("")
        self._refresh_ok()

    # ── submission ───────────────────────────────────────────────────────

    def _submit(self):
        if self._stage == "summary":
            self._accept_pending()
            return
        if self._stage == "folder":
            self._confirm_folder()
            return
        if self._active_source() == "url":
            url = self._url.text().strip()
            if not url:
                self._show_error("Enter a configuration URL.")
                return
            self._submit_url(url)
        else:
            path = self._path.text().strip()
            if not path:
                self._show_error("Choose a local configuration file.")
                return
            self._submit_file(path)

    def _confirm_folder(self):
        """Validate the install folder and move to the summary stage."""
        if not self._validated:
            self._show_error("Pick a configuration first.")
            return
        chosen = os.path.normpath(
            os.path.expanduser(self._folder.text().strip())
        )
        if not chosen or chosen == ".":
            self._show_error(
                "Choose the folder where the game client is installed."
            )
            return
        self._install_dir = chosen
        kind, source, raw, config = self._validated
        self._show_summary(kind, source, raw, config)

    def _enter_folder_stage(self, kind: str, source: str, raw: str, config):
        """A validated configuration in hand: ask where the game client
        lives (pre-filled with the Games/<ServerName> suggestion)."""
        self._validated = (kind, source, raw, config)
        if not self._folder.text().strip():
            suggestion = platform_support.default_game_folder(
                config.server_name, config.client_version
            )
            if suggestion:
                self._folder.setText(suggestion)
        self._set_stage("folder")
        self._set_status(
            "Confirm the configuration source, then choose the install folder."
        )
        self._refresh_ok()

    def _submit_file(self, path: str):
        config, err = launcher.validate_path(path)
        if config is None:
            self._show_error(
                str(err) or "Please choose a valid nostalgia_launcher.json."
            )
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            self._show_error(f"Could not read the configuration: {e}")
            return
        self._enter_folder_stage("file", path, raw, config)

    def _submit_url(self, url: str):
        """Fetch and validate the configuration on a worker thread — the
        GUI thread must never block on network I/O. A poll timer applies
        the result on the main thread once the fetch finishes."""
        try:
            url = config_import.check_config_url(url)
        except config_import.ConfigUrlError as e:
            self._show_error(str(e))
            return
        self._clear_error()
        self._fetching = True
        self._ok.setEnabled(False)
        self._url.setEnabled(False)
        self._path.setEnabled(False)
        self._sourceFile.setEnabled(False)
        self._sourceUrl.setEnabled(False)
        self._browse.setEnabled(False)
        self._set_status("Fetching the configuration…")

        result: dict = {}

        def work():
            try:
                data, raw, err = config_import.fetch_config_url(url)
            except Exception as e:  # defensive: never leave the dialog stuck
                data, raw, err = None, None, f"Fetch failed: {e}"
            result.update(data=data, raw=raw, err=err)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        poll = QTimer(self)
        poll.setInterval(25)

        def check():
            if thread.is_alive():
                return
            poll.stop()
            if not self._fetching:  # the dialog was cancelled meanwhile
                return
            self._finish_url(url, result)

        poll.timeout.connect(check)
        poll.start()

    def _finish_url(self, url: str, result: dict):
        self._fetching = False
        self._url.setEnabled(True)
        self._path.setEnabled(True)
        self._sourceFile.setEnabled(True)
        self._sourceUrl.setEnabled(True)
        self._browse.setEnabled(True)
        self._set_status("")
        if result.get("err"):
            self._show_error(result["err"])
            return
        config, verr = launcher.validate_dict(result.get("data"))
        if config is None:
            self._show_error(str(verr) or "The configuration is invalid.")
            return
        self._enter_folder_stage("url", url, result.get("raw") or "", config)

    # ── trust stage ────────────────────────────────────────────────────

    def _show_summary(self, kind: str, source: str, raw: str, config):
        self._pending = (kind, source, raw, config)
        self._render_trust(kind, source, config, self._install_dir)
        self._set_stage("summary")
        self._set_status(
            "Review this configuration. Only trust it if you trust its source."
        )
        self._refresh_ok()

    def _render_trust(self, kind: str, source: str, config, install_dir: str):
        """Fill the trust screen from a validated configuration."""
        p = self._palette
        self._trustName.setText(
            f"Server:\n{config.server_name or '(unnamed)'}"
        )
        if kind == "url":
            self._trustSource.setText(f"Source: {source}")
        else:
            self._trustSource.setText(f"File: {source}")
        hosts = trust_hosts(config)
        if hosts:
            self._trustHosts.setText("\n".join(hosts))
        else:
            self._trustHosts.setText("(no hosts configured)")
        for key, enabled, detail in trust_capabilities(config):
            mark = "✓" if enabled else "—"
            label = self._trustRows[key]
            label.setText(f"{mark} {detail}")
            color = p.text.name() if enabled else p.text_dim.name()
            label.setStyleSheet(f"color: {color};")
        self._trustFolder.setText(f"Install folder:\n{install_dir}")

    def _accept_pending(self):
        if not self._pending:
            return
        kind, source, raw, _config = self._pending
        server_name = getattr(_config, "server_name", "") or ""
        if kind == "file":
            self._selection = {
                "kind": "file",
                "path": source,
                "raw": raw,
                "install_dir": self._install_dir,
                "server_name": server_name,
            }
        else:
            self._selection = {
                "kind": "url",
                "config_url": source,
                "raw": raw,
                "install_dir": self._install_dir,
                "server_name": server_name,
            }
        self.accept()

    # ── results ──────────────────────────────────────────────────────────

    def selection(self) -> dict | None:
        """The chosen selection: {"kind": "file", "path", "raw",
        "install_dir", "server_name"} or {"kind": "url", "config_url",
        "raw", "install_dir", "server_name"}, or None if cancelled.
        ``install_dir`` is the confirmed game-client folder (never empty
        on accept); ``server_name`` is the validated config's display
        name (may be "" when the config leaves it blank)."""
        return self._selection


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _theme_logo_host(config) -> str:
    """The host of the config's theme logo URL, or "" when none."""
    theme = getattr(config, "theme", None)
    if not isinstance(theme, dict):
        return ""
    logo = theme.get("logo")
    if not isinstance(logo, str) or not logo.strip():
        return ""
    try:
        parts = urlsplit(logo.strip())
    except ValueError:
        return ""
    if parts.scheme != "https":
        return ""
    return parts.hostname or ""


def _embedded_hosts(config) -> list[str]:
    """Hosts named by entries embedded directly in the configuration."""
    hosts: list[str] = []

    def _add(url: object):
        if not isinstance(url, str):
            return
        h = _host(url.strip())
        if h and h not in hosts:
            hosts.append(h)

    for mod in getattr(config, "embedded_mods", []) or []:
        if not isinstance(mod, dict):
            continue
        _add(mod.get("repo_url"))
        source = mod.get("source")
        if isinstance(source, dict):
            _add(source.get("url"))
    for addon in getattr(config, "embedded_addons", []) or []:
        if not isinstance(addon, dict):
            continue
        _add(addon.get("git"))
    for asset in getattr(config, "embedded_assets", []) or []:
        if not isinstance(asset, dict):
            continue
        _add(asset.get("url"))
    return hosts


def trust_hosts(config) -> list[str]:
    """Every host the launcher would contact under this configuration.

    Covers the registry/feed/download endpoints, the Discord button, the
    theme logo, and URLs embedded directly in the configuration. Sorted
    and de-duplicated; network-free.
    """
    hosts: list[str] = []
    urls: list[str] = [
        config.server_url,
        config.news_url,
        config.featured_news_url,
        config.mods_registry_url,
        *config.addons_registry_urls,
        config.assets_registry_url,
        config.download_fallback_url or "",
        config.download_torrent_url or "",
        config.discord_url or "",
    ]
    for url in urls:
        h = _host(url)
        if h and h not in hosts:
            hosts.append(h)
    logo_host = _theme_logo_host(config)
    if logo_host and logo_host not in hosts:
        hosts.append(logo_host)
    for h in _embedded_hosts(config):
        if h not in hosts:
            hosts.append(h)
    hosts.sort()
    return hosts


def _catalog_summary(explicit_url: bool, embedded: int) -> str:
    """One-line description of where a content category comes from:
    remote catalog URL(s), embedded entries, or both."""
    if explicit_url and embedded:
        return f"catalog + {embedded} embedded"
    if explicit_url:
        return "catalog"
    if embedded:
        return f"{embedded} embedded"
    return "not configured"


def trust_capabilities(config) -> list[tuple[str, bool, str]]:
    """The four trust rows: (key, enabled, label) for client, mods,
    addons and news. Network-free."""
    has_client = bool(config.download_fallback_url or config.has_torrent())
    if config.download_fallback_url and config.has_torrent():
        client_detail = "BitTorrent + HTTPS archive"
    elif config.has_torrent():
        client_detail = "BitTorrent"
    elif config.download_fallback_url:
        client_detail = "HTTPS archive"
    else:
        client_detail = "not configured"
    addon_urls = [u for u in (config.addons_registry_urls or []) if u]
    return [
        ("client", has_client, f"Client — {client_detail}"),
        (
            "mods",
            bool(config.mods_registry_url or config.embedded_mods),
            "Mods — "
            + _catalog_summary(
                bool(config.mods_registry_url),
                len(config.embedded_mods),
            ),
        ),
        (
            "addons",
            bool(addon_urls or config.embedded_addons),
            "Addons — "
            + _catalog_summary(bool(addon_urls), len(config.embedded_addons)),
        ),
        (
            "news",
            bool(config.news_url),
            f"News — {'feed configured' if config.news_url else 'no feed'}",
        ),
    ]

"""Nostalgia Launcher Qt (PySide6) first-launch configuration import dialog.

A QDialog shown on first launch to let the user import a launcher
configuration before any MainWindow exists. The launcher has no built-in
server directory: the configuration comes from a local file the user
selects or an https URL the user types — typically obtained independently
from a community or server operator.

Flow: input stage (file path or URL) → fetch/read + validate → summary
stage showing exactly what the configuration points at (server name, base
URL, every host that will be contacted, which features are configured, and
an explicit note that the configuration controls game-file downloads) →
explicit Accept. Nothing is persisted by the dialog; the chosen selection
is exposed via ``selection()`` as ``{"kind": "file", "path": ...}`` or
``{"kind": "url", "config_url": ..., "raw": ...}``.
"""

import os
import threading
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core import launcher
from ...services import config_import
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
        self._pending = None  # (kind, raw, config) awaiting confirmation
        self.setObjectName("launcherConfigDialog")
        self.setWindowTitle("FIRST LAUNCH — IMPORT A CONFIGURATION")
        self.setMinimumWidth(560)
        apply_theme(
            self,
            p,
            f"\nQDialog {{ background-color: {p.panel.name()}; }}",
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(8)

        title = QLabel("FIRST LAUNCH — IMPORT A CONFIGURATION", self)
        title.setObjectName("launcherConfigTitle")
        title.setStyleSheet(
            f"color: {p.gold_lt.name()}; font-weight: bold; font-size: 12pt;"
        )
        root.addWidget(title)

        intro = QLabel(
            "The launcher ships without any server list. Import a launcher "
            "configuration supplied independently by your community or "
            "server operator: choose its file below or enter its https "
            "URL. The configuration controls where the launcher retrieves "
            "game files, news, mods, and addons from.",
            self,
        )
        intro.setObjectName("launcherConfigIntro")
        intro.setStyleSheet(f"color: {p.text_dim.name()};")
        intro.setWordWrap(True)
        root.addWidget(intro)

        self._status = QLabel("", self)
        self._status.setObjectName("launcherConfigStatus")
        self._status.setStyleSheet(f"color: {p.text_dim.name()};")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        url_row = QHBoxLayout()
        self._url = QLineEdit(self)
        self._url.setObjectName("launcherConfigUrl")
        self._url.setPlaceholderText(
            "Configuration URL (https://example.com/community-config.json)"
        )
        self._url.textChanged.connect(self._on_input_changed)
        url_row.addWidget(self._url)
        root.addLayout(url_row)

        path_row = QHBoxLayout()
        self._path = QLineEdit(self)
        self._path.setObjectName("launcherConfigPath")
        self._path.setReadOnly(True)
        self._path.setPlaceholderText("…or select a local configuration file")
        self._path.textChanged.connect(self._on_input_changed)
        path_row.addWidget(self._path)
        browse = QPushButton("Browse…", self)
        browse.setObjectName("launcherConfigBrowse")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self.browse)
        path_row.addWidget(browse)
        root.addLayout(path_row)

        self._summary = QLabel("", self)
        self._summary.setObjectName("launcherConfigSummary")
        self._summary.setStyleSheet(
            f"color: {p.text.name()};"
            f"background-color: {p.log_bg.name()};"
            "padding: 8px; border: 1px solid "
            f"{p.divider.name()};"
        )
        self._summary.setWordWrap(True)
        self._summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._summary.hide()
        root.addWidget(self._summary)

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
        self._ok.setCursor(Qt.PointingHandCursor)
        self._ok.clicked.connect(self._submit)
        self._ok.setEnabled(False)
        buttons.addWidget(self._ok)
        root.addLayout(buttons)

        if initial_path and os.path.isfile(initial_path):
            self._path.setText(initial_path)
        self._refresh_ok()

    # ── population ───────────────────────────────────────────────────────

    def _set_stage(self, stage: str):
        self._stage = stage
        summary_visible = stage == "summary"
        self._summary.setVisible(summary_visible)
        self._back.setVisible(summary_visible)
        self._url.setEnabled(stage == "input")
        self._path.setEnabled(stage == "input")
        self.findChild(QPushButton, "launcherConfigBrowse").setEnabled(
            stage == "input"
        )
        self._ok.setText("Accept" if summary_visible else "Continue")

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
            self._url.clear()

    def _on_input_changed(self, _text):
        self._error.clear()
        self._error.hide()
        sender = self.sender()
        if sender is self._url and self._url.text().strip():
            self._path.clear()
        elif sender is self._path and self._path.text().strip():
            self._url.clear()
        self._refresh_ok()

    def _refresh_ok(self):
        if self._stage == "summary":
            self._ok.setEnabled(True)
            return
        ready = bool(self._url.text().strip() or self._path.text().strip())
        self._ok.setEnabled(ready)

    def _clear_error(self):
        self._error.clear()
        self._error.hide()

    def _show_error(self, message: str):
        self._error.setText(message)
        self._error.show()
        self._set_stage("input")
        self._refresh_ok()

    def _go_back(self):
        self._pending = None
        self._summary.clear()
        self._set_stage("input")
        self._set_status("Choose a configuration file or enter its https URL.")
        self._refresh_ok()

    # ── submission ───────────────────────────────────────────────────────

    def _submit(self):
        if self._stage == "summary":
            self._accept_pending()
            return
        url = self._url.text().strip()
        path = self._path.text().strip()
        if not url and not path:
            self._show_error(
                "Enter a configuration URL or choose a local "
                "configuration file."
            )
            return
        if url:
            self._submit_url(url)
        else:
            self._submit_file(path)

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
        self._show_summary("file", path, raw, config)

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
        self._set_status("")
        if result.get("err"):
            self._show_error(result["err"])
            return
        config, verr = launcher.validate_dict(result.get("data"))
        if config is None:
            self._show_error(str(verr) or "The configuration is invalid.")
            return
        self._show_summary("url", url, result.get("raw") or "", config)

    # ── summary stage ────────────────────────────────────────────────────

    def _show_summary(self, kind: str, source: str, raw: str, config):
        self._pending = (kind, source, raw, config)
        self._summary.setText(_summary_text(kind, source, config))
        self._set_stage("summary")
        self._set_status(
            "Review this configuration. Only accept it if you trust its "
            "source."
        )
        self._refresh_ok()

    def _accept_pending(self):
        if not self._pending:
            return
        kind, source, raw, _config = self._pending
        if kind == "file":
            self._selection = {"kind": "file", "path": source, "raw": raw}
        else:
            self._selection = {
                "kind": "url",
                "config_url": source,
                "raw": raw,
            }
        self.accept()

    # ── results ──────────────────────────────────────────────────────────

    def selection(self) -> dict | None:
        """The chosen selection: {"kind": "file", "path", "raw"} or
        {"kind": "url", "config_url", "raw"}, or None if cancelled."""
        return self._selection


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _yes(no_value) -> str:
    return "no" if no_value else "yes"


def _catalog_summary(explicit_url: bool, url_count: int, embedded: int) -> str:
    """One-line description of where a content category comes from:
    remote catalog URL(s), embedded entries, or both."""
    if explicit_url and embedded:
        return f"url + {embedded} embedded"
    if explicit_url:
        return "url"
    if embedded:
        return f"{embedded} embedded"
    return "no"


def _addons_catalog_summary(urls: list, cfg) -> str:
    """Addon catalogs always resolve to at least the derived default URL,
    so this shows the configured count plus any embedded entries."""
    embedded = len(cfg.embedded_addons)
    if embedded:
        return f"{len(urls)} (+{embedded} embedded)"
    return str(len(urls))


def _summary_text(kind: str, source: str, config) -> str:
    """Human-readable summary of a validated launcher configuration: what
    it is, where it points, every host the launcher would contact, and
    what will be stored locally on accept."""
    cfg = config
    hosts = sorted(h for h in cfg.download_hosts() if h)
    for url in (
        cfg.news_url,
        cfg.featured_news_url,
        cfg.mods_registry_url,
        *cfg.addons_registry_urls,
        cfg.discord_url or "",
    ):
        h = _host(url)
        if h and h not in hosts:
            hosts.append(h)
    hosts.sort()
    lines = [
        f"Name: {cfg.server_name}",
        f"Source: {source}" if kind == "url" else f"File: {source}",
        f"Server base URL: {cfg.server_url}",
        f"Contacts {len(hosts)} host(s): {', '.join(hosts)}",
        "Client file updates: configured (can be disabled in Settings)",
        f"BitTorrent bulk downloads: {_yes(not cfg.has_torrent())}",
        f"News feed: {_yes(not cfg.news_url)}",
        "Mod catalog: "
        + _catalog_summary(
            cfg.mods_registry_url_explicit,
            1,
            len(cfg.embedded_mods),
        ),
        "Addon catalog(s): "
        + _addons_catalog_summary(cfg.addons_registry_urls, cfg),
        f"Mirrors: {len(cfg.mirrors)}",
    ]
    if cfg.assets_registry_url or cfg.embedded_assets:
        lines.append(
            "Asset catalog: "
            + _catalog_summary(
                bool(cfg.assets_registry_url), 1, len(cfg.embedded_assets)
            )
        )
    saved = []
    for label, count in (
        ("mod", len(cfg.embedded_mods)),
        ("addon", len(cfg.embedded_addons)),
        ("asset", len(cfg.embedded_assets)),
    ):
        if count:
            saved.append(f"{count} {label}{'s' if count != 1 else ''}")
    if saved:
        lines.append("Will store locally: " + ", ".join(saved))
    if cfg.discord_url:
        lines.append(f"Discord button: {_host(cfg.discord_url)}")
    lines.append(
        "This configuration controls where game files, mods, and addons "
        "are downloaded from and can modify the selected game folder."
    )
    return "\n".join(lines)

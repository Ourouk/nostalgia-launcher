"""Octo Updater Qt (PySide6) mods panel.

Renders the mod registry into a scrollable list of `ModRow` widgets —
essential star badge, install state, enable/ignore checkboxes, repo link,
retry/update action and error line — plus an Apply footer and a nav-badge
callback driven by the updates count. Rows are rebuilt from every ModsLoaded
snapshot the bridge forwards; user actions are forwarded straight into the
toolkit-agnostic ModsController.
"""

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from qt_theme import Palette
from ui_events import ModsLoaded


def _clear_layout(layout):
    """Drop every widget a layout owns so a re-render can rebuild the list."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class _LinkLabel(QLabel):
    """A QLabel that opens a URL on left-click."""

    def __init__(self, text, url, parent=None):
        super().__init__(text, parent)
        self._url = url
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._url:
            webbrowser.open(self._url)
        super().mouseReleaseEvent(event)


class ModRow(QWidget):
    """One mod row: star badge, name/version, enable + ignore checkboxes,
    repo link, retry/update action, word-wrapped description and an error
    line under the row."""

    def __init__(self, mod, rec, pend, latest_versions, action, palette,
                 parent=None):
        super().__init__(parent)
        self.mod_id = mod["id"]
        p = palette
        mid = mod["id"]
        self.setObjectName(f"modsRow_{mid}")

        installed_version = rec.installed_version if rec else None
        has_error = rec.error if rec else None
        installed = bool(installed_version)
        enabled = (pend.enabled
                   if pend is not None and pend.enabled is not None
                   else (rec.enabled if rec else False))
        ignore = (pend.ignore_updates
                  if pend is not None and pend.ignore_updates is not None
                  else (rec.ignore_updates if rec else False))
        essential = mod.get("essential", False)

        name_col = p.err if has_error else (p.mod_hl if installed else p.text)
        desc_col = p.text if enabled else p.text_dim
        version = installed_version or latest_versions.get(mid) or "unknown"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 4, 0)
        root.setSpacing(3)

        top = QWidget(self)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # Fixed-width slot keeps names aligned whether or not the star shows.
        self.star_label = QLabel("★" if essential else "", top)
        self.star_label.setObjectName(f"modsStar_{mid}")
        self.star_label.setFixedWidth(20)
        self.star_label.setStyleSheet(f"color: {p.gold.name()};")
        if essential:
            self.star_label.setToolTip("Essential mod")
        top_layout.addWidget(self.star_label, 0, Qt.AlignTop)

        self.name_label = QLabel(mod["name"], top)
        self.name_label.setObjectName(f"modsName_{mid}")
        self.name_label.setStyleSheet(
            f"color: {name_col.name()}; font-weight: bold;")
        top_layout.addWidget(self.name_label, 0, Qt.AlignTop)

        self.version_label = QLabel(f"  {version}", top)
        self.version_label.setObjectName(f"modsVer_{mid}")
        self.version_label.setStyleSheet(f"color: {p.text_dim.name()};")
        top_layout.addWidget(self.version_label, 0, Qt.AlignTop)

        self.enabled_check = QCheckBox(top)
        self.enabled_check.setObjectName(f"modsCheck_{mid}")
        self.enabled_check.setCursor(Qt.PointingHandCursor)
        self.enabled_check.setChecked(enabled)
        top_layout.addWidget(self.enabled_check, 0, Qt.AlignTop)

        top_layout.addStretch(1)

        self.action_button = None
        if action in ("retry", "update"):
            self.action_button = QPushButton(action, top)
            self.action_button.setObjectName(f"modsAction_{mid}")
            self.action_button.setCursor(Qt.PointingHandCursor)
            self.action_button.setStyleSheet(
                f"QPushButton {{ color: {p.gold.name()};"
                f" border: 1px solid {p.gold.name()}; border-radius: 4px;"
                f" background-color: transparent; padding: 1px 10px; }}"
                f"QPushButton:hover {{ background-color: {p.gold.name()};"
                f" color: {p.hdr.name()}; }}")
            top_layout.addWidget(self.action_button)

        self.link_label = _LinkLabel("⧉", mod["repo_url"], top)
        self.link_label.setObjectName(f"modsLink_{mid}")
        self.link_label.setStyleSheet(f"color: {p.text_dim.name()};")
        self.link_label.setToolTip(mod["repo_url"])
        top_layout.addWidget(self.link_label, 0, Qt.AlignTop)

        self.ignore_check = QCheckBox(top)
        self.ignore_check.setObjectName(f"modsIgnore_{mid}")
        self.ignore_check.setCursor(Qt.PointingHandCursor)
        self.ignore_check.setChecked(ignore)
        top_layout.addWidget(self.ignore_check, 0, Qt.AlignTop)

        ignore_label = QLabel("Ignore updates", top)
        ignore_label.setStyleSheet(f"color: {p.text_dim.name()};")
        top_layout.addWidget(ignore_label, 0, Qt.AlignTop)

        root.addWidget(top)

        self.desc_label = QLabel(mod["description"], self)
        self.desc_label.setObjectName(f"modsDesc_{mid}")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet(f"color: {desc_col.name()};")
        root.addWidget(self.desc_label)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName(f"modsError_{mid}")
        self.error_label.setStyleSheet(f"color: {p.err.name()};")
        if has_error:
            self.error_label.setText(f"  \u26a0  {has_error}")
        self.error_label.setVisible(bool(has_error))
        root.addWidget(self.error_label)

        divider = QFrame(self)
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(
            f"background-color: {p.divider.name()};"
            f" border: none; max-height: 1px;")
        root.addWidget(divider)


class ModsPanel(QWidget):
    """The MODS tab: a scrollable mod list with an Apply footer.

    Renders from the controller's registry and state; re-renders on every
    ModsLoaded and forwards checkbox/action clicks into the controller. The
    optional `on_badge` callback receives the updates count after each
    snapshot so the shell can paint a nav-tab badge.
    """

    def __init__(self, mods, bridge, palette: Palette, parent=None,
                 on_badge=None):
        super().__init__(parent)
        self._mods = mods
        self._palette = palette
        self._on_badge = on_badge or (lambda count: None)
        self._running = False
        self._rows: dict[str, ModRow] = {}
        self.setObjectName("modsPanel")
        p = palette

        self.setStyleSheet(
            f"""
            #modsPanel {{ background-color: {p.panel.name()}; }}
            #modsContent {{ background-color: {p.panel.name()}; }}
            #modsScroll {{ background-color: {p.panel.name()}; border: none; }}
            """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        banner = QWidget(self)
        banner.setObjectName("modsBanner")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(16, 12, 16, 8)
        banner_layout.setSpacing(0)
        for text, color in (("Mods marked with ", p.text_dim),
                            ("★", p.gold), (" are essential", p.text_dim)):
            part = QLabel(text, banner)
            part.setStyleSheet(f"color: {color.name()};")
            banner_layout.addWidget(part)
        banner_layout.addStretch(1)
        root.addWidget(banner)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {p.divider.name()};"
                          f" border: none; max-height: 1px;")
        root.addWidget(sep)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("modsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(self.scroll, 1)

        self._content = QWidget()
        self._content.setObjectName("modsContent")
        self._rows_layout = QVBoxLayout(self._content)
        self._rows_layout.setContentsMargins(16, 0, 16, 6)
        self._rows_layout.setSpacing(0)
        self._rows_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self._content)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background-color: {p.divider.name()};"
                          f" border: none; max-height: 1px;")
        root.addWidget(sep)

        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 6, 16, 10)
        self._apply_button = QPushButton("Apply", footer)
        self._apply_button.setObjectName("modsApply")
        self._apply_button.setCursor(Qt.PointingHandCursor)
        self._apply_button.setStyleSheet(
            f"QPushButton {{ color: {p.text.name()};"
            f" border: 1px solid {p.gold.name()}; border-radius: 4px;"
            f" background-color: {p.panel_bdr.name()};"
            f" padding: 5px 26px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: {p.gold.name()};"
            f" color: {p.hdr.name()}; }}")
        self._apply_button.clicked.connect(self._apply)
        self._apply_button.setVisible(False)
        footer_layout.addWidget(self._apply_button)
        footer_layout.addStretch(1)
        root.addWidget(footer)

        bridge.modsLoaded.connect(self._on_mods_loaded)
        bridge.operationFinished.connect(self._on_operation_finished)
        bridge.operationFailed.connect(self._on_operation_failed)

        self._render(self._mods.state)
        self._refresh_apply_visibility()

    # ── rendering ───────────────────────────────────────────────────────────

    def _render(self, state):
        if state is None:
            return
        _clear_layout(self._rows_layout)
        self._rows = {}
        for mod in sorted(self._mods.registry,
                          key=lambda m: m["name"].lower()):
            mid = mod["id"]
            row = ModRow(
                mod, state.records.get(mid), state.pending.get(mid),
                state.latest_versions, self._mods.action_for(mid),
                self._palette, self._content)
            row.enabled_check.toggled.connect(
                lambda checked, m=mid: self._on_enabled_toggled(m, checked))
            row.ignore_check.toggled.connect(
                lambda checked, m=mid: self._on_ignore_toggled(m, checked))
            if row.action_button is not None:
                row.action_button.clicked.connect(
                    lambda checked=False, m=mid: self._on_action(m))
            self._rows[mid] = row
            self._rows_layout.addWidget(row)

    def _refresh_apply_visibility(self):
        """Apply is offered only when there is something to apply: pending
        checkbox changes, or a failed mod the user may want to retry."""
        st = self._mods.state
        self._apply_button.setVisible(
            bool(st.has_pending_changes or st.has_errors))

    # ── actions ─────────────────────────────────────────────────────────────

    def _on_enabled_toggled(self, mid, checked):
        self._mods.toggle(mid, checked)
        self._refresh_apply_visibility()

    def _on_ignore_toggled(self, mid, checked):
        self._mods.set_ignore(mid, checked)
        self._refresh_apply_visibility()

    def _on_action(self, mid):
        self._set_running(True)
        self._mods.apply(only_mod_id=mid)

    def _apply(self):
        self._set_running(True)
        self._mods.apply()

    def _set_running(self, running: bool):
        self._running = running
        self._apply_button.setEnabled(not running)

    # ── event wiring ────────────────────────────────────────────────────────

    def _on_mods_loaded(self, event):
        if not isinstance(event, ModsLoaded) or event.state is None:
            return
        self._render(event.state)
        self._on_badge(event.state.updates_count)
        self._refresh_apply_visibility()

    def _on_operation_finished(self, kind: str, ok: bool, message: str):
        if kind != "mods":
            return
        self._set_running(False)
        self._refresh_apply_visibility()

    def _on_operation_failed(self, kind: str, message: str):
        if kind != "mods":
            return
        self._set_running(False)
        self._refresh_apply_visibility()

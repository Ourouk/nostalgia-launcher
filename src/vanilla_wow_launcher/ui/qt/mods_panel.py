"""Vanilla WoW Launcher Qt (PySide6) mods panel.

Renders the mod registry into a scrollable list of `ModRow` widgets —
essential star badge, install state, enable/ignore checkboxes, repo link,
retry/update action and error line — plus an Apply footer and a nav-badge
callback driven by the updates count. Rows are rebuilt from every ModsLoaded
snapshot the bridge forwards; user actions are forwarded straight into the
toolkit-agnostic ModsController. The list shell is shared with the addons
panel via `list_panel.ScrollListPanel`.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from .theme import Palette
from .list_panel import (
    ScrollListPanel,
    add_row_divider,
    add_row_error,
    add_row_link,
    add_star,
    make_row_shell,
)


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

        root, top, top_layout = make_row_shell(self)

        # Fixed-width slot keeps names aligned whether or not the star shows.
        self.star_label = add_star(top_layout, f"modsStar_{mid}", essential,
                                   "Essential mod", p)

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

        self.link_label = add_row_link(top_layout, f"modsLink_{mid}",
                                       mod["repo_url"], p)

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

        self.error_label = add_row_error(root, f"modsError_{mid}", has_error, p)

        add_row_divider(root, p)


class ModsPanel(ScrollListPanel):
    """The MODS tab: a scrollable mod list with an Apply footer.

    Renders from the controller's registry and state; re-renders on every
    ModsLoaded and forwards checkbox/action clicks into the controller. The
    optional `on_badge` callback receives the updates count after each
    snapshot so the shell can paint a nav-tab badge.
    """

    def __init__(self, mods, bridge, palette: Palette, parent=None,
                 on_badge=None):
        super().__init__("mods", bridge.modsLoaded, palette, bridge, on_badge,
                         parent)
        self._mods = mods
        self._op_kind = "mods"
        self._running = False
        self._build_header()
        self._add_scroll_list()
        self._build_footer()
        self._render(self._mods.state)
        self._refresh_apply_visibility()

    # ── shell ─────────────────────────────────────────────────────────────

    def _build_header(self):
        p = self._palette
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
        self._root_layout.addWidget(banner)
        self._add_hsep()

    def _build_footer(self):
        p = self._palette
        self._add_hsep()
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
        self._root_layout.addWidget(footer)

    # ── rendering ───────────────────────────────────────────────────────────

    def _render(self, state):
        if state is None:
            return
        self._clear_rows()
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
            self._add_row(row)

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

    # ── event hooks ────────────────────────────────────────────────────────

    def _after_loaded(self):
        self._refresh_apply_visibility()

    def _after_operation(self):
        self._set_running(False)
        self._refresh_apply_visibility()

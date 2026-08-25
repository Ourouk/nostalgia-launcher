"""Nostalgia Launcher Qt (PySide6) assets panel.

Renders the asset registry (server content patches such as MPQs) into a
scrollable list of `AssetRow` widgets — install checkbox, essential star
badge, name/version, repo link, retry/update action and error line — plus
an Apply footer and a nav-badge callback driven by the updates count. Rows
are rebuilt from every AssetsLoaded snapshot the bridge forwards; user
actions go straight into the toolkit-agnostic AssetsController. The list
shell is shared with the mods/addons panels via
`list_panel.ScrollListPanel`.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

from .list_panel import (
    ScrollListPanel,
    add_row_divider,
    add_row_error,
    add_row_link,
    add_star,
    make_row_shell,
)
from .theme import Palette


class AssetRow(QWidget):
    """One asset row: install checkbox, star badge, name/version, repo
    link, retry/update action, word-wrapped description and an error line
    under the row."""

    def __init__(self, asset, rec, pend, action, palette, parent=None):
        super().__init__(parent)
        self.asset_id = asset["id"]
        p = palette
        aid = asset["id"]
        self.setObjectName(f"assetsRow_{aid}")

        installed_version = rec.installed_version if rec else None
        has_error = rec.error if rec else None
        installed = rec.present if rec is not None else False
        enabled = (
            pend.enabled
            if pend is not None and pend.enabled is not None
            else (rec.enabled if rec else False)
        )
        essential = asset.get("essential", False)

        name_col = p.err if has_error else (p.mod_hl if installed else p.text)
        desc_col = p.text if enabled else p.text_dim
        version = installed_version or "unknown"

        root, top, top_layout = make_row_shell(self)

        # The install/enable checkbox leads the row, before the name.
        self.enabled_check = QCheckBox(top)
        self.enabled_check.setObjectName(f"assetsCheck_{aid}")
        self.enabled_check.setCursor(Qt.PointingHandCursor)
        self.enabled_check.setChecked(enabled)
        self.enabled_check.setToolTip(
            "Enable or disable this asset for the next launch"
        )
        top_layout.addWidget(self.enabled_check, 0, Qt.AlignTop)

        # Fixed-width slot keeps names aligned whether or not the star shows.
        self.star_label = add_star(
            top_layout, f"assetsStar_{aid}", essential, "Essential asset", p
        )

        self.name_label = QLabel(asset["name"], top)
        self.name_label.setObjectName(f"assetsName_{aid}")
        self.name_label.setStyleSheet(
            f"color: {name_col.name()}; font-weight: bold;"
        )
        top_layout.addWidget(self.name_label, 0, Qt.AlignTop)

        self.version_label = QLabel(f"  {version}", top)
        self.version_label.setObjectName(f"assetsVer_{aid}")
        self.version_label.setStyleSheet(f"color: {p.text_dim.name()};")
        top_layout.addWidget(self.version_label, 0, Qt.AlignTop)

        top_layout.addStretch(1)

        self.action_button = None
        if action in ("retry", "update"):
            # Display labels are Title Case per panel-action convention;
            # the action *kind* itself ("retry"/"update") is machine-facing.
            self.action_button = QPushButton(action.capitalize(), top)
            self.action_button.setObjectName(f"assetsAction_{aid}")
            self.action_button.setCursor(Qt.PointingHandCursor)
            self.action_button.setProperty("variant", "compact")
            self.action_button.setToolTip(
                "Retry the last failed action"
                if action == "retry"
                else "Update this asset from the server"
            )
            top_layout.addWidget(self.action_button)

        if asset.get("repo_url"):
            self.link_label = add_row_link(
                top_layout, f"assetsLink_{aid}", asset["repo_url"], p
            )
        else:
            self.link_label = None

        root.addWidget(top)

        self.desc_label = QLabel(asset["description"], self)
        self.desc_label.setObjectName(f"assetsDesc_{aid}")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet(f"color: {desc_col.name()};")
        root.addWidget(self.desc_label)

        self.error_label = add_row_error(
            root, f"assetsError_{aid}", has_error, p
        )

        add_row_divider(root, p)


class AssetsPanel(ScrollListPanel):
    """The ASSETS tab: a scrollable asset list with an Apply footer.

    Renders from the controller's registry and state; re-renders on every
    AssetsLoaded and forwards checkbox/action clicks into the controller.
    The optional `on_badge` callback receives the updates count after each
    snapshot so the shell can paint a nav-tab badge. Emits
    `customAssetRequested` from the banner's add button.
    """

    customAssetRequested = Signal()

    def __init__(
        self, assets, bridge, palette: Palette, parent=None, on_badge=None
    ):
        super().__init__(
            "assets", bridge.assetsLoaded, palette, bridge, on_badge, parent
        )
        self._assets = assets
        self._op_kind = "assets"
        self._running = False
        self._build_header()
        self._add_scroll_list()
        self._build_footer()
        self._render(self._assets.state)
        self._refresh_apply_visibility()

    # ── shell ─────────────────────────────────────────────────────────────

    def _build_header(self):
        p = self._palette
        banner = QWidget(self)
        banner.setObjectName("assetsBanner")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(16, 12, 16, 8)
        banner_layout.setSpacing(6)
        for text, color in (
            ("Server content marked with ", p.text_dim),
            ("★", p.gold),
            (" is essential", p.text_dim),
        ):
            part = QLabel(text, banner)
            part.setStyleSheet(f"color: {color.name()};")
            banner_layout.addWidget(part)
        banner_layout.addStretch(1)

        custom = QToolButton(banner)
        custom.setObjectName("assetsCustomAdd")
        custom.setText("+  Add custom asset")
        custom.setCursor(Qt.PointingHandCursor)
        custom.setStyleSheet(f"color: {p.pink.name()}; font-weight: bold;")
        custom.clicked.connect(self.customAssetRequested.emit)
        banner_layout.addWidget(custom)

        refresh = QToolButton(banner)
        refresh.setObjectName("assetsCatalogRefresh")
        refresh.setText("⟳")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setToolTip("Reload the assets catalog from the server")
        refresh.setAccessibleName("Reload assets catalog")
        refresh.clicked.connect(self._on_refresh_catalog)
        banner_layout.addWidget(refresh)

        self._root_layout.addWidget(banner)
        self._add_hsep()

    def _on_refresh_catalog(self):
        self._assets.reload_catalog()

    def _after_loaded(self):
        self._refresh_apply_visibility()
        self._refresh_essential_visibility()

    def _build_footer(self):
        self._add_hsep()
        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 6, 16, 10)
        self._essential_button = QPushButton("★  Install Essential", footer)
        self._essential_button.setObjectName("assetsInstallEssential")
        self._essential_button.setCursor(Qt.PointingHandCursor)
        self._essential_button.setProperty("variant", "primary")
        self._essential_button.clicked.connect(self._on_install_essential)
        footer_layout.addWidget(self._essential_button)

        self._apply_button = QPushButton("Apply", footer)
        self._apply_button.setObjectName("assetsApply")
        self._apply_button.setCursor(Qt.PointingHandCursor)
        self._apply_button.setProperty("variant", "primary")
        self._apply_button.clicked.connect(self._apply)
        self._apply_button.setVisible(False)
        footer_layout.addWidget(self._apply_button)
        footer_layout.addStretch(1)
        self._root_layout.addWidget(footer)

    # ── rendering ────────────────────────────────────────────────────────

    def _render(self, state):
        if state is None:
            return
        self._clear_rows()
        if not self._assets.registry:
            p = self._palette
            empty = QLabel(
                "No server content available.\n"
                "This server does not publish downloadable assets "
                "(client patches such as MPQs).",
                self._content,
            )
            empty.setObjectName("assetsEmptyState")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {p.text_dim.name()};")
            self._add_row(empty)
        for asset in sorted(
            self._assets.registry, key=lambda a: a["name"].lower()
        ):
            aid = asset["id"]
            row = AssetRow(
                asset,
                state.records.get(aid),
                state.pending.get(aid),
                self._assets.action_for(aid),
                self._palette,
                self._content,
            )
            row.enabled_check.toggled.connect(
                lambda checked, a=aid: self._on_enabled_toggled(a, checked)
            )
            if row.action_button is not None:
                row.action_button.clicked.connect(
                    lambda checked=False, a=aid: self._on_action(a)
                )
            self._rows[aid] = row
            self._add_row(row)
        self._refresh_essential_visibility()

    def _refresh_apply_visibility(self):
        """Apply is offered only when there is something to apply: pending
        checkbox changes, or a failed asset the user may want to retry."""
        st = self._assets.state
        self._apply_button.setVisible(
            bool(st.has_pending_changes or st.has_errors)
        )

    def _essential_remaining(self) -> list:
        """Essential assets (registry flag) not yet present on disk."""
        remaining = []
        for asset in self._assets.registry:
            if not asset.get("essential", False):
                continue
            rec = self._assets.state.records.get(asset["id"])
            if rec is not None and rec.present:
                continue
            remaining.append(asset["id"])
        return remaining

    def _refresh_essential_visibility(self):
        """The 'Install Essential' button is enabled only while there is at
        least one essential asset not yet installed and no install is
        running — otherwise it greys out."""
        self._essential_button.setEnabled(
            bool(self._essential_remaining())
            and not self._running
            and not self._assets.busy
        )

    # ── actions ──────────────────────────────────────────────────────────

    def _on_enabled_toggled(self, aid, checked):
        self._assets.toggle(aid, checked)
        self._refresh_apply_visibility()

    def _on_action(self, aid):
        self._set_running(True)
        self._assets.apply(only_asset_id=aid)

    def _apply(self):
        self._set_running(True)
        self._assets.apply()

    def _on_install_essential(self):
        if self._assets.apply_essential_assets():
            self._set_running(True)
        else:
            self._refresh_essential_visibility()

    def _set_running(self, running: bool):
        self._running = running
        self._apply_button.setEnabled(not running)
        if running:
            self._apply_button.setText("Applying…")
        else:
            self._apply_button.setText("Apply")
        for row in self._rows.values():
            if row.action_button is not None:
                row.action_button.setEnabled(not running)
        self._refresh_essential_visibility()

    # ── event hooks ───────────────────────────────────────────────────────

    def _after_operation(self):
        self._set_running(False)
        self._refresh_apply_visibility()

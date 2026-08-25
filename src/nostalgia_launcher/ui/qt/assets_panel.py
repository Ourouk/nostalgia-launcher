"""Nostalgia Launcher Qt (PySide6) assets panel.

Renders the asset registry (server content patches such as MPQs) into a
scrollable list of `AssetRow` widgets — install checkbox, essential star
badge, name/version, repo link, retry/update action and error line — plus
an Apply footer and a nav-badge callback driven by the updates count. Rows
are rebuilt from every AssetsLoaded snapshot the bridge forwards; user
actions go straight into the toolkit-agnostic AssetsController. The list
shell is shared with the mods/addons panels via
`list_panel.ScrollListPanel`.

A Data/ scan block sits above the asset rows: on every render the panel
classifies the client folder's MPQs for the selected game version
(`services.mpq`) into stock / launcher-managed / foreign, with confirmed
removal offered for foreign files.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QWidget,
)

from ...core.log_sink import log
from ...services import mpq
from .list_panel import (
    ScrollListPanel,
    add_row_divider,
    add_row_error,
    add_row_link,
    add_star,
    make_hairline,
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

        self._version = QComboBox(banner)
        self._version.setObjectName("assetsScanVersion")
        for v in mpq.SUPPORTED_VERSIONS:
            self._version.addItem(v)
        self._version.setToolTip(
            "The client's game version — decides which Data/ archives "
            "count as stock"
        )
        self._version.currentTextChanged.connect(self._on_version_changed)
        banner_layout.addWidget(self._version)

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
        # The Data/ scan runs inside every render (init, AssetsLoaded,
        # apply completion, version change) so its verdicts can never go
        # stale — it is a single read-only walk of the client's Data/.
        scan = self._assets.data_scan(self._version.currentText())
        self._render_scan_block(scan)
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

    # ── Data/ scan block ─────────────────────────────────────────────────

    def _managed_by_basename(self) -> dict:
        """dest basename (lowercased) → display name, so managed customs
        parked inside a locale subfolder still resolve to their entry."""
        index = {}
        for entry in self._assets.registry or []:
            dest = entry.get("dest") if isinstance(entry, dict) else None
            if not dest:
                continue
            base = str(dest).replace("\\", "/").rsplit("/", 1)[-1]
            index[base.lower()] = (
                f"{entry.get('name', entry.get('id', '?'))} (launcher asset)"
            )
        return index

    def _render_scan_block(self, scan: dict):
        """The Data/ classification: a count line plus Foreign/managed
        sections. Nothing renders when no game folder is configured —
        except the hint telling the user how to get a scan."""
        p = self._palette
        client_dir = self._assets.client_dir()
        head = QLabel(
            "Set the game folder in Settings to scan its Data/ folder."
            if not client_dir
            else (
                f"Data/ scan ({scan['version']}): "
                f"{len(scan['stock'])} stock Blizzard archive(s), "
                f"{len(scan['custom_managed'])} launcher-managed, "
                f"{len(scan['custom_foreign'])} foreign/untracked."
            ),
            self._content,
        )
        head.setObjectName("mpqStockCount")
        head.setWordWrap(True)
        head.setStyleSheet(f"color: {p.text_dim.name()};")
        head.setContentsMargins(0, 6, 0, 2)
        self._add_row(head)
        if client_dir and scan["data_dir"]:
            self._add_row(make_hairline(self._content))
        managed_names = self._managed_by_basename()
        for kind, header in (
            ("custom_foreign", "Foreign / untracked"),
            ("custom_managed", "Launcher-managed custom"),
        ):
            entries = scan.get(kind) or []
            if not client_dir or not entries:
                continue
            section = QLabel(header, self._content)
            section.setObjectName(f"mpqHeader_{kind}")
            color = p.err if kind == "custom_foreign" else p.gold
            section.setStyleSheet(f"color: {color.name()}; font-weight: bold;")
            section.setContentsMargins(0, 10, 0, 2)
            self._add_row(section)
            for info in entries:
                self._add_scan_row(kind, info, managed_names)

    def _add_scan_row(self, kind: str, info: dict, managed_names: dict):
        p = self._palette
        path = info["path"]
        shell = QWidget(self._content)
        shell.setObjectName(
            f"mpq{'Foreign' if kind == 'custom_foreign' else 'Managed'}"
            f"Row_{path}"
        )
        root, top, top_layout = make_row_shell(shell)

        label = QLabel(path, shell)
        label.setObjectName(f"mpqPath_{path}")
        color = p.err if kind == "custom_foreign" else p.text
        label.setStyleSheet(f"color: {color.name()};")
        top_layout.addWidget(label, 0, Qt.AlignTop)

        meta = mpq.human_size(info.get("size"))
        if kind == "custom_managed":
            base = path.rsplit("/", 1)[-1]
            note = managed_names.get(base.lower(), "")
            meta = "  ·  ".join(x for x in (meta, note) if x)
        if meta:
            meta_label = QLabel(meta, shell)
            meta_label.setObjectName(f"mpqMeta_{path}")
            meta_label.setStyleSheet(f"color: {p.text_dim.name()};")
            top_layout.addWidget(meta_label, 0, Qt.AlignTop)

        top_layout.addStretch(1)

        if kind == "custom_foreign":
            remove = QPushButton("Remove", shell)
            remove.setObjectName(f"mpqForeignRemove_{path}")
            remove.setProperty("variant", "outline")
            remove.setCursor(Qt.PointingHandCursor)
            remove.setToolTip("Delete this untracked MPQ from Data/")
            remove.clicked.connect(
                lambda checked=False, rel=path: self._on_remove_foreign(rel)
            )
            top_layout.addWidget(remove, 0, Qt.AlignTop)

        root.addWidget(top)
        add_row_divider(root, p)
        self._add_row(shell)

    def _on_remove_foreign(self, rel_path: str):
        answer = QMessageBox.question(
            self,
            "Remove custom MPQ",
            f"Delete {rel_path} from the client folder?",
        )
        if answer != QMessageBox.Yes:
            return
        error = mpq.remove_custom_mpq(self._assets.client_dir(), rel_path)
        if error:
            log(f"  {error}", "err")
        self._render(self._assets.state)

    def _on_version_changed(self, _text):
        self._render(self._assets.state)

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

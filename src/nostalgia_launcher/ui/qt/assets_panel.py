"""Nostalgia Launcher Qt (PySide6) assets panel.

The ASSETS tab: the shared catalog-row rendering from `content_panel` plus
a banner with a game-version selector and a Data/ scan block. On every
render the panel classifies the client folder's MPQs for the selected game
version (`services.mpq`) into stock / launcher-managed / foreign, with
confirmed removal offered for foreign files.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
from .content_panel import ContentListPanel, ContentRow
from .list_panel import add_row_divider, make_hairline, make_row_shell
from .theme import Palette


class AssetsPanel(ContentListPanel):
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
            "assets",
            assets,
            bridge.assetsLoaded,
            palette,
            bridge,
            on_badge,
            parent,
        )
        self._build_header()
        self._add_scroll_list()
        self._build_footer("assetsInstallEssential")
        self._render(self._content_ctrl.state)
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
        self._content_ctrl.reload_catalog()

    # ── content-panel hooks ──────────────────────────────────────────────

    def _empty_state_text(self) -> str:
        return (
            "No server content available.\n"
            "This server does not publish downloadable assets "
            "(client patches such as MPQs)."
        )

    def _before_render(self, state):
        # The Data/ scan runs inside every render (init, AssetsLoaded,
        # apply completion, version change) so its verdicts can never go
        # stale — it is a single read-only walk of the client's Data/.
        scan = self._content_ctrl.data_scan(self._version.currentText())
        self._render_scan_block(scan)

    def _make_row(self, entry, state, action) -> ContentRow:
        aid = entry["id"]
        rec = state.records.get(aid)
        return ContentRow(
            entry,
            rec,
            state.pending.get(aid),
            action,
            self._palette,
            prefix=self._prefix,
            noun="asset",
            update_tip="Update this asset from the server",
            version=(rec.installed_version if rec else None) or "unknown",
            parent=self._content,
        )

    def _apply_one(self, eid):
        self._content_ctrl.apply(only_asset_id=eid)

    def _install_essential(self) -> bool:
        return self._content_ctrl.apply_essential_assets()

    # ── Data/ scan block ─────────────────────────────────────────────────

    def _managed_by_basename(self) -> dict:
        """dest basename (lowercased) → display name, so managed customs
        parked inside a locale subfolder still resolve to their entry."""
        index = {}
        for entry in self._content_ctrl.registry or []:
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
        client_dir = self._content_ctrl.client_dir()
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
        error = mpq.remove_custom_mpq(
            self._content_ctrl.client_dir(), rel_path
        )
        if error:
            log(f"  {error}", "err")
        self._render(self._content_ctrl.state)

    def _on_version_changed(self, _text):
        self._render(self._content_ctrl.state)

"""Nostalgia Launcher Qt (PySide6) mods panel.

The MODS tab: the shared catalog-row rendering from `content_panel` plus a
banner (required legend, custom-mod entry, catalog age, reload) and a
"Detected (not in catalog)" section for dlls.txt entries no catalog mod
claims. Rows are rebuilt from every ModsLoaded snapshot the bridge
forwards; user actions are forwarded straight into the toolkit-agnostic
ModsController.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

from ...core.helpers import relative_age
from ...services import mods as mods_service
from .content_panel import ContentListPanel, ContentRow
from .list_panel import add_row_divider, make_row_shell
from .theme import Palette


class ModsPanel(ContentListPanel):
    """The MODS tab: a scrollable mod list with an Apply footer.

    Renders from the controller's registry and state; re-renders on every
    ModsLoaded and forwards checkbox/action clicks into the controller. The
    optional `on_badge` callback receives the updates count after each
    snapshot so the shell can paint a nav-tab badge. Emits
    `customModRequested` from the banner's add button.
    """

    customModRequested = Signal()

    essential_button_text = "★  Install Required"

    def _is_required(self, entry) -> bool:
        return entry.get("installation") == "required"

    def __init__(
        self, mods, bridge, palette: Palette, parent=None, on_badge=None
    ):
        super().__init__(
            "mods", mods, bridge.modsLoaded, palette, bridge, on_badge, parent
        )
        self._build_header()
        self._add_scroll_list()
        # The mods tab keeps its historical objectName for this button.
        self._build_footer("modsInstallRecommended")
        self._render(self._content_ctrl.state)
        self._refresh_apply_visibility()

    # ── shell ─────────────────────────────────────────────────────────────

    def _build_header(self):
        p = self._palette
        banner = QWidget(self)
        banner.setObjectName("modsBanner")
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(16, 12, 16, 8)
        banner_layout.setSpacing(6)
        for text, color in (
            ("Mods marked with ", p.text_dim),
            ("★", p.gold),
            (" are required", p.text_dim),
        ):
            part = QLabel(text, banner)
            part.setStyleSheet(f"color: {color.name()};")
            banner_layout.addWidget(part)
        banner_layout.addStretch(1)

        custom = QToolButton(banner)
        custom.setObjectName("modsCustomAdd")
        custom.setText("+  Add custom mod")
        custom.setCursor(Qt.PointingHandCursor)
        custom.setStyleSheet(f"color: {p.pink.name()}; font-weight: bold;")
        custom.clicked.connect(self.customModRequested.emit)
        banner_layout.addWidget(custom)

        self._age_label = QLabel("", banner)
        self._age_label.setObjectName("modsCatalogAge")
        self._age_label.setStyleSheet(f"color: {p.text_dim.name()};")
        self._age_label.hide()
        banner_layout.addWidget(self._age_label)

        refresh = QToolButton(banner)
        refresh.setObjectName("modsCatalogRefresh")
        refresh.setText("⟳")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setToolTip("Reload the mod catalog from the server")
        refresh.setAccessibleName("Reload mod catalog")
        refresh.clicked.connect(self._on_refresh_catalog)
        banner_layout.addWidget(refresh)

        self._root_layout.addWidget(banner)
        self._add_hsep()
        self._refresh_age_label()

    def _refresh_age_label(self):
        ts = mods_service.catalog_timestamp()
        if ts:
            self._age_label.setText(f"Catalog updated {relative_age(ts)}")
            self._age_label.show()
        else:
            self._age_label.hide()

    def _on_refresh_catalog(self):
        self._content_ctrl.reload_catalog()

    # ── content-panel hooks ──────────────────────────────────────────────

    def _empty_state_text(self) -> str:
        return (
            "No mods catalog available.\n"
            "Configure a catalog URL under Settings → Catalog "
            "registries, then use Reload."
        )

    def _make_row(self, entry, state, action) -> ContentRow:
        mid = entry["id"]
        rec = state.records.get(mid)
        version = (
            (rec.installed_version if rec else None)
            or state.latest_versions.get(mid)
            or "unknown"
        )
        return ContentRow(
            entry,
            rec,
            state.pending.get(mid),
            action,
            self._palette,
            prefix=self._prefix,
            noun="mod",
            update_tip="Update this mod to the latest version",
            version=version,
            badge=entry.get("installation") == "required",
            badge_tip="Required mod",
            parent=self._content,
        )

    def _after_render(self, state):
        self._render_unknown(state)
        super()._after_render(state)

    def _apply_one(self, eid) -> bool:
        return self._content_ctrl.apply(only_mod_id=eid)

    def _install_essential(self) -> bool:
        return self._content_ctrl.apply_essential_mods()

    def _extra_after_loaded(self):
        self._refresh_age_label()

    # ── unknown-dll section ──────────────────────────────────────────────

    def _render_unknown(self, state):
        """Rows for dlls.txt entries no catalog mod claims — mods the client
        loads that the launcher doesn't track — each with a filesystem-first
        Remove button."""
        unknown = getattr(state, "unknown", None)
        if not unknown:
            return
        p = self._palette
        head = QLabel("Detected (not in catalog)", self._content)
        head.setObjectName("modsUnknownHeader")
        head.setStyleSheet(f"color: {p.text_dim.name()}; font-weight: bold;")
        head.setContentsMargins(0, 10, 0, 2)
        self._add_row(head)
        for name in unknown:
            shell = QWidget(self._content)
            shell.setObjectName(f"modsUnknownRow_{name}")
            root, top, top_layout = make_row_shell(shell)
            label = QLabel(name, shell)
            label.setObjectName(f"modsUnknownName_{name}")
            label.setStyleSheet(f"color: {p.text_dim.name()};")
            top_layout.addWidget(label, 0, Qt.AlignTop)
            top_layout.addStretch(1)
            remove = QPushButton("Remove", shell)
            remove.setObjectName(f"modsUnknownRemove_{name}")
            remove.setCursor(Qt.PointingHandCursor)
            remove.setToolTip("Delete this file and its dlls.txt entry")
            remove.setProperty("variant", "outline")
            remove.clicked.connect(
                lambda checked=False, n=name: self._on_remove_unknown(n)
            )
            top_layout.addWidget(remove, 0, Qt.AlignTop)
            root.addWidget(top)
            add_row_divider(root, p)
            self._add_row(shell)

    def _on_remove_unknown(self, name):
        self._content_ctrl.remove_unknown(name)

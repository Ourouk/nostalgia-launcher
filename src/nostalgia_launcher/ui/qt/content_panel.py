"""Shared plumbing for the content panels (MODS, ASSETS).

Both tabs render a validated registry of catalog entries into rows with an
install checkbox, required/essential star badge, name/version, repo link,
retry/update action and error line, between a banner header and an
★-Install/Apply footer (each panel words the star and the install button
itself). This module holds that shared row widget and panel skeleton; each
panel supplies its own banner, empty-state text and any extra blocks (the
mods panel's unknown-DLL section, the assets panel's Data/ scan).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
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


class ContentRow(QWidget):
    """One catalog-entry row: install checkbox, star badge, name/version,
    repo link, retry/update action, word-wrapped description and an error
    line under the row — identical layout for mods and assets."""

    def __init__(
        self,
        entry,
        rec,
        pend,
        action,
        palette: Palette,
        *,
        prefix: str,
        noun: str,
        update_tip: str,
        version: str,
        badge: bool | None = None,
        badge_tip: str = "",
        parent=None,
    ):
        super().__init__(parent)
        eid = entry["id"]
        self.entry_id = eid
        p = palette
        self.setObjectName(f"{prefix}Row_{eid}")

        has_error = rec.error if rec else None
        installed = rec.present if rec is not None else False
        enabled = (
            pend.enabled
            if pend is not None and pend.enabled is not None
            else (rec.enabled if rec else False)
        )
        # ``badge`` lets a panel drive the star from its own schema
        # (mods: installation == "required"); None falls back to the
        # legacy ``essential`` boolean both catalogs still carry.
        essential = entry.get("essential", False) if badge is None else badge

        name_col = p.err if has_error else (p.mod_hl if installed else p.text)
        desc_col = p.text if enabled else p.text_dim

        root, top, top_layout = make_row_shell(self)

        # The install/enable checkbox leads the row, before the name.
        self.enabled_check = QCheckBox(top)
        self.enabled_check.setObjectName(f"{prefix}Check_{eid}")
        self.enabled_check.setCursor(Qt.PointingHandCursor)
        self.enabled_check.setChecked(enabled)
        self.enabled_check.setToolTip(
            f"Enable or disable this {noun} for the next launch"
        )
        top_layout.addWidget(self.enabled_check, 0, Qt.AlignTop)

        # Fixed-width slot keeps names aligned whether or not the star shows.
        self.star_label = add_star(
            top_layout,
            f"{prefix}Star_{eid}",
            essential,
            badge_tip or f"Essential {noun}",
            p,
        )

        self.name_label = QLabel(entry["name"], top)
        self.name_label.setObjectName(f"{prefix}Name_{eid}")
        self.name_label.setStyleSheet(
            f"color: {name_col.name()}; font-weight: bold;"
        )
        top_layout.addWidget(self.name_label, 0, Qt.AlignTop)

        self.version_label = QLabel(f"  {version}", top)
        self.version_label.setObjectName(f"{prefix}Ver_{eid}")
        self.version_label.setStyleSheet(f"color: {p.text_dim.name()};")
        top_layout.addWidget(self.version_label, 0, Qt.AlignTop)

        top_layout.addStretch(1)

        self.action_button = None
        if action in ("retry", "update"):
            # Display labels are Title Case per panel-action convention;
            # the action *kind* itself ("retry"/"update") is machine-facing.
            self.action_button = QPushButton(action.capitalize(), top)
            self.action_button.setObjectName(f"{prefix}Action_{eid}")
            self.action_button.setCursor(Qt.PointingHandCursor)
            self.action_button.setProperty("variant", "compact")
            self.action_button.setToolTip(
                "Retry the last failed action"
                if action == "retry"
                else update_tip
            )
            top_layout.addWidget(self.action_button)

        if entry.get("repo_url"):
            self.link_label = add_row_link(
                top_layout, f"{prefix}Link_{eid}", entry["repo_url"], p
            )
        else:
            self.link_label = None

        root.addWidget(top)

        self.desc_label = QLabel(entry["description"], self)
        self.desc_label.setObjectName(f"{prefix}Desc_{eid}")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet(f"color: {desc_col.name()};")
        root.addWidget(self.desc_label)

        self.error_label = add_row_error(
            root, f"{prefix}Error_{eid}", has_error, p
        )

        add_row_divider(root, p)


class ContentListPanel(ScrollListPanel):
    """Base for the MODS/ASSETS tabs: registry rows + Apply footer.

    Subclasses build their banner (`_build_header`), supply the empty-state
    text (`_empty_state_text`) and a `_make_row` factory, optionally hook
    `_before_render` / `_extra_after_loaded`, and forward actions into their
    controller via `_apply_one` / `_install_essential`.
    """

    #: Footer label for the one-click install of every remaining required /
    #: essential entry; subclasses reword it (mods say "Required").
    essential_button_text = "★  Install Essential"

    def __init__(
        self,
        prefix,
        controller,
        loaded_signal,
        palette,
        bridge,
        on_badge=None,
        parent=None,
    ):
        super().__init__(
            prefix, loaded_signal, palette, bridge, on_badge, parent
        )
        self._content_ctrl = controller
        self._op_kind = prefix
        self._running = False

    # ── footer ──────────────────────────────────────────────────────────────

    def _build_footer(self, essential_objname: str):
        """The shared footer: '★ Install …' + Apply buttons."""
        self._add_hsep()
        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 6, 16, 10)
        self._essential_button = QPushButton(
            self.essential_button_text, footer
        )
        self._essential_button.setObjectName(essential_objname)
        self._essential_button.setCursor(Qt.PointingHandCursor)
        self._essential_button.setProperty("variant", "primary")
        self._essential_button.clicked.connect(self._on_install_essential)
        footer_layout.addWidget(self._essential_button)

        self._apply_button = QPushButton("Apply", footer)
        self._apply_button.setObjectName(f"{self._prefix}Apply")
        self._apply_button.setCursor(Qt.PointingHandCursor)
        self._apply_button.setProperty("variant", "primary")
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
        self._before_render(state)
        ctrl = self._content_ctrl
        if not ctrl.registry:
            empty = QLabel(self._empty_state_text(), self._content)
            empty.setObjectName(f"{self._prefix}EmptyState")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {self._palette.text_dim.name()};")
            self._add_row(empty)
        for entry in sorted(ctrl.registry, key=lambda e: e["name"].lower()):
            eid = entry["id"]
            row = self._make_row(entry, state, ctrl.action_for(eid))
            row.enabled_check.toggled.connect(
                lambda checked, i=eid: self._on_enabled_toggled(i, checked)
            )
            if row.action_button is not None:
                row.action_button.clicked.connect(
                    lambda checked=False, i=eid: self._on_action(i)
                )
            self._rows[eid] = row
            self._add_row(row)
        self._after_render(state)

    def _refresh_apply_visibility(self):
        """Apply is offered only when there is something to apply: pending
        checkbox changes, or a failed entry the user may want to retry."""
        st = self._content_ctrl.state
        self._apply_button.setVisible(
            bool(st.has_pending_changes or st.has_errors)
        )

    def _is_required(self, entry) -> bool:
        """Which entries the ★ button installs: the legacy ``essential``
        boolean by default (assets); mods override with their schema."""
        return bool(entry.get("essential", False))

    def _essential_remaining(self) -> list:
        """Entries flagged by `_is_required` not yet present on disk."""
        remaining = []
        for entry in self._content_ctrl.registry:
            if not self._is_required(entry):
                continue
            rec = self._content_ctrl.state.records.get(entry["id"])
            if rec is not None and rec.present:
                continue
            remaining.append(entry["id"])
        return remaining

    def _refresh_essential_visibility(self):
        """The '★ Install …' button is enabled only while there is at least
        one flagged entry not yet installed and no install is running —
        otherwise it greys out."""
        self._essential_button.setEnabled(
            bool(self._essential_remaining())
            and not self._running
            and not self._content_ctrl.busy
        )

    # ── actions ─────────────────────────────────────────────────────────────

    def _on_enabled_toggled(self, eid, checked):
        self._content_ctrl.toggle(eid, checked)
        self._refresh_apply_visibility()

    def _on_action(self, eid):
        self._set_running(True)
        self._apply_one(eid)

    def _apply(self):
        self._set_running(True)
        self._content_ctrl.apply()

    def _on_install_essential(self):
        if self._install_essential():
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

    # ── event hooks ────────────────────────────────────────────────────────

    def _after_operation(self):
        self._set_running(False)
        self._refresh_apply_visibility()

    # ── subclass hooks ──────────────────────────────────────────────────────

    def _before_render(self, state):
        """Extra rows/blocks rendered before the registry rows."""

    def _after_render(self, state):
        """Extra rows/blocks after the registry rows + essential refresh."""
        self._refresh_essential_visibility()

    def _empty_state_text(self) -> str:
        raise NotImplementedError

    def _make_row(self, entry, state, action) -> ContentRow:
        raise NotImplementedError

    def _apply_one(self, eid):
        raise NotImplementedError

    def _install_essential(self) -> bool:
        raise NotImplementedError

    def _extra_after_loaded(self):
        """Hook run at the end of every XLoaded re-render."""

    def _after_loaded(self):
        self._refresh_apply_visibility()
        self._refresh_essential_visibility()
        self._extra_after_loaded()

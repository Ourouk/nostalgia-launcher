"""Octo Updater Qt (PySide6) addons panel.

Renders the controller's AddonsState into a searchable, collapsible two-section
list of `AddonRow` widgets — recommendation star, colored title, word-wrapped
description, repo link, status text and an install/update/remove action — plus
a check-for-updates / custom-addon footer and a nav-badge callback driven by
the out-of-date count. Rows are rebuilt from every AddonsLoaded snapshot the
bridge forwards; user actions are forwarded straight into the toolkit-agnostic
AddonsController.
"""

import webbrowser

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from helpers import parse_wow_colored, strip_wow_colors
from qt_theme import Palette
from ui_events import AddonsLoaded

_INTERFACE_VERSION = "11200"


def _clear_layout(layout):
    """Drop every widget a layout owns so a re-render can rebuild the list."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class _ClickableLabel(QLabel):
    """A QLabel that emits clicked on a left mouse release."""

    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _LinkLabel(_ClickableLabel):
    """A QLabel that opens a URL on left-click."""

    def __init__(self, text, url, parent=None):
        super().__init__(text, parent)
        self._url = url

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._url:
            webbrowser.open(self._url)
        super().mouseReleaseEvent(event)


class AddonRow(QWidget):
    """One addon row: star badge, colored title, description, repo link,
    status text and an install/update/remove action."""

    def __init__(self, rec, installed, recommended, installed_names,
                 palette: Palette, on_install, on_remove, parent=None):
        super().__init__(parent)
        self.setObjectName(f"addonsRow_{rec.folder}")
        p = palette
        toc = rec.toc or {}

        warnings = []
        if toc.get("Interface") and toc["Interface"] != _INTERFACE_VERSION:
            warnings.append(f"Made for client {toc['Interface']}")
        # pfUI bundles its own modules, so its .toc dependencies aren't real
        # missing addons — never warn about them.
        if installed and rec.folder != "pfUI":
            deps = [d.strip() for d in
                    (toc.get("Dependencies") or "").replace(";", ",").split(",")
                    if d.strip()]
            missing = [d for d in deps if d not in installed_names]
            if missing:
                warnings.append("Missing deps: " + ", ".join(missing))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 4, 0)
        root.setSpacing(3)

        top = QWidget(self)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # Fixed-width slot keeps titles aligned whether or not the star shows.
        self.star_label = QLabel("★" if recommended else "", top)
        self.star_label.setObjectName(f"addonsStar_{rec.folder}")
        self.star_label.setFixedWidth(20)
        self.star_label.setStyleSheet(f"color: {p.gold.name()};")
        if recommended:
            self.star_label.setToolTip("Recommended addon")
        top_layout.addWidget(self.star_label, 0, Qt.AlignTop)

        # Title honouring WoW colour escapes — one label per colour segment.
        title = toc.get("Title") or rec.folder
        title_box = QWidget(top)
        title_layout = QHBoxLayout(title_box)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(0)
        for i, (seg, col) in enumerate(parse_wow_colored(title)[:6]):
            seg_label = QLabel(seg, title_box)
            if i == 0:
                seg_label.setObjectName(f"addonsName_{rec.folder}")
            seg_label.setStyleSheet(
                f"color: {col or p.text.name()}; font-weight: bold;")
            title_layout.addWidget(seg_label)
        top_layout.addWidget(title_box, 0, Qt.AlignTop)

        top_layout.addStretch(1)

        # Right side pinned to the edge: status text, repo link, action.
        if rec.status == "downloading":
            status = QLabel("downloading…", top)
            status.setStyleSheet(f"color: {p.text_dim.name()};")
        elif rec.status == "invalid" or rec.error:
            status = QLabel("⛔ Addon error", top)
            status.setStyleSheet(f"color: {p.err.name()};")
        elif rec.status == "outOfDate" and installed:
            status = _ClickableLabel("Update", top)
            status.setStyleSheet(
                f"color: {p.gold.name()}; font-weight: bold;")
            status.clicked.connect(lambda: on_install(rec))
        elif warnings:
            status = QLabel(f"⚠ {warnings[0]}", top)
            status.setStyleSheet(f"color: {p.warn.name()};")
        elif rec.status == "upToDate":
            status = QLabel("Up to date", top)
            status.setStyleSheet(f"color: {p.text_dim.name()};")
        else:
            status = QLabel("Not versioned", top)
            status.setStyleSheet(f"color: {p.text_dim.name()};")
        status.setObjectName(f"addonsStatus_{rec.folder}")
        top_layout.addWidget(status, 0, Qt.AlignTop)

        if rec.git:
            repo_url = (rec.git[:-4] if rec.git.endswith(".git")
                        else rec.git)
            link = _LinkLabel("⧉", repo_url, top)
            link.setObjectName(f"addonsLink_{rec.folder}")
            link.setStyleSheet(f"color: {p.text_dim.name()};")
            link.setToolTip(repo_url)
            top_layout.addWidget(link, 0, Qt.AlignTop)

        if installed:
            action = QPushButton("🗑", top)
            action.setToolTip("Remove addon")
            action.setStyleSheet(
                f"QPushButton {{ color: {p.err.name()};"
                f" border: 1px solid {p.panel_bdr.name()}; border-radius: 4px;"
                f" background-color: {p.hdr.name()}; padding: 2px 8px; }}"
                f"QPushButton:hover {{ border-color: {p.err.name()}; }}")
            action.clicked.connect(lambda: on_remove(rec.folder))
        else:
            action = QPushButton("⬇", top)
            action.setToolTip("Install addon")
            action.setStyleSheet(
                f"QPushButton {{ color: {p.ok.name()};"
                f" border: 1px solid {p.panel_bdr.name()}; border-radius: 4px;"
                f" background-color: {p.hdr.name()}; padding: 2px 8px; }}"
                f"QPushButton:hover {{ border-color: {p.ok.name()}; }}")
            action.clicked.connect(lambda: on_install(rec))
        action.setObjectName(f"addonsAction_{rec.folder}")
        action.setCursor(Qt.PointingHandCursor)
        top_layout.addWidget(action, 0, Qt.AlignTop)

        root.addWidget(top)

        desc = strip_wow_colors(toc.get("Notes") or rec.description or "")
        self.desc_label = QLabel(desc, self)
        self.desc_label.setObjectName(f"addonsDesc_{rec.folder}")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet(f"color: {p.text_dim.name()};")
        root.addWidget(self.desc_label)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName(f"addonsError_{rec.folder}")
        self.error_label.setStyleSheet(f"color: {p.err.name()};")
        if rec.error:
            self.error_label.setText(f"  ⚠  {rec.error}")
        self.error_label.setVisible(bool(rec.error))
        root.addWidget(self.error_label)

        divider = QFrame(self)
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(
            f"background-color: {p.divider.name()};"
            f" border: none; max-height: 1px;")
        root.addWidget(divider)


class AddonsPanel(QWidget):
    """The ADDONS tab: a searchable, collapsible addon list with a footer.

    Renders from the controller's state; re-renders on every AddonsLoaded and
    on filter/section changes. The optional `on_badge` callback receives the
    out-of-date count after each snapshot so the shell can paint a nav-tab
    badge. The custom-addon footer button only emits `customAddonRequested` —
    the dialog wiring lands in C21.
    """

    customAddonRequested = Signal()

    def __init__(self, addons, bridge, palette: Palette, parent=None,
                 on_badge=None):
        super().__init__(parent)
        self._addons = addons
        self._palette = palette
        self._on_badge = on_badge or (lambda count: None)
        self._rows: dict[str, AddonRow] = {}
        self.setObjectName("addonsPanel")
        p = palette

        self.setStyleSheet(
            f"""
            #addonsPanel {{ background-color: {p.panel.name()}; }}
            #addonsContent {{ background-color: {p.panel.name()}; }}
            #addonsScroll {{ background-color: {p.panel.name()}; border: none; }}
            """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top = QWidget(self)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(16, 8, 16, 6)
        top_layout.setSpacing(8)

        legend = QWidget(top)
        legend_layout = QHBoxLayout(legend)
        legend_layout.setContentsMargins(0, 0, 0, 0)
        legend_layout.setSpacing(0)
        for text, color in (("Addons marked with ", p.text_dim),
                            ("★", p.gold), (" are recommended", p.text_dim)):
            part = QLabel(text, legend)
            part.setStyleSheet(f"color: {color.name()};")
            legend_layout.addWidget(part)
        top_layout.addWidget(legend)

        top_layout.addStretch(1)

        self._filter = QLineEdit(top)
        self._filter.setObjectName("addonsFilter")
        self._filter.setPlaceholderText("⌕  Search addons")
        self._filter.setClearButtonEnabled(True)
        self._filter.setFixedWidth(240)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._render)
        self._filter.textChanged.connect(
            lambda *_: self._debounce.start())
        top_layout.addWidget(self._filter)
        root.addWidget(top)

        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(
            f"background-color: {p.divider.name()};"
            f" border: none; max-height: 1px;")
        root.addWidget(sep)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("addonsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        root.addWidget(self.scroll, 1)

        self._content = QWidget()
        self._content.setObjectName("addonsContent")
        self._rows_layout = QVBoxLayout(self._content)
        self._rows_layout.setContentsMargins(16, 0, 16, 6)
        self._rows_layout.setSpacing(0)
        self._rows_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self._content)

        self._build_footer()

        bridge.addonsLoaded.connect(self._on_addons_loaded)
        bridge.operationFinished.connect(self._on_operation_finished)
        bridge.operationFailed.connect(self._on_operation_failed)

        self._render(self._addons.state)

    # ── rendering ───────────────────────────────────────────────────────────

    def _matches(self, rec: dict) -> bool:
        """Space-insensitive filter both ways: "sell value" finds SellValue,
        "sellvalue" finds "Sell Value"."""
        flt = self._filter.text().strip().lower()
        if not flt:
            return True
        title = strip_wow_colors((rec.get("toc") or {}).get("Title") or "")
        hay = f"{rec['folder']} {title}".lower()
        return flt in hay or flt.replace(" ", "") in hay.replace(" ", "")

    def _render(self, state=None):
        state = state or self._addons.state
        _clear_layout(self._rows_layout)
        self._rows = {}

        installed = [r for r in state.addons.values()
                     if self._matches(r.to_dict())]
        installed.sort(key=lambda r: r.folder.lower())
        available = [a for a in state.available
                     if a.folder not in state.addons
                     and self._matches(a.to_dict())]
        # Recommended addons sort first, then by folder name.
        available.sort(key=lambda a: (a.folder not in self._addons.recommended,
                                      a.folder.lower()))

        installed_names = set(state.addons)
        for title, rows in (("INSTALLED", installed),
                            ("AVAILABLE", available)):
            self._add_section_header(title, rows, state)
            if state.sections_open.get(title, True):
                for rec in rows:
                    row = AddonRow(
                        rec,
                        installed=rec.folder in installed_names,
                        recommended=rec.folder in self._addons.recommended,
                        installed_names=installed_names,
                        palette=self._palette,
                        on_install=self._on_install,
                        on_remove=self._on_remove,
                        parent=self._content)
                    self._rows[rec.folder] = row
                    self._rows_layout.addWidget(row)

        self._refresh_footer()

    def _add_section_header(self, title: str, rows: list, state):
        p = self._palette
        is_open = state.sections_open.get(title, True)

        hdr = QWidget(self._content)
        hdr.setObjectName(f"addonsSection_{title}")
        layout = QHBoxLayout(hdr)
        layout.setContentsMargins(0, 10, 0, 2)
        layout.setSpacing(4)

        toggle = QToolButton(hdr)
        toggle.setObjectName(f"addonsToggle_{title}")
        toggle.setText("▾" if is_open else "▸")
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.clicked.connect(lambda: self._toggle_section(title))
        layout.addWidget(toggle)

        label = _ClickableLabel(title, hdr)
        label.setStyleSheet(
            f"color: {p.gold.name()}; font-weight: bold; font-size: 11pt;")
        label.clicked.connect(lambda: self._toggle_section(title))
        layout.addWidget(label)

        count = QLabel(f"  {len(rows)}", hdr)
        count.setStyleSheet(f"color: {p.text_dim.name()};")
        layout.addWidget(count)

        layout.addStretch(1)
        self._rows_layout.addWidget(hdr)

        if is_open and not rows:
            msg = ("Verifying…" if state.state == "verifying"
                   else "Nothing here.")
            empty = QLabel(msg, self._content)
            empty.setStyleSheet(f"color: {p.text_dim.name()};")
            self._rows_layout.addWidget(empty)

    def _toggle_section(self, title: str):
        self._addons.state.sections_open[title] = \
            not self._addons.state.sections_open.get(title, True)
        self._render()

    # ── actions ─────────────────────────────────────────────────────────────

    def _on_install(self, rec):
        if self._addons.apply([rec.to_dict()]):
            self._render()

    def _on_remove(self, folder: str):
        ret = QMessageBox.question(
            self, "Remove addon", f"Delete {folder} and all of its files?")
        if ret != QMessageBox.Yes:
            return
        self._addons.remove(folder)

    def _on_check(self):
        if self._addons.verify(force=True):
            self._refresh_footer()

    def _on_update_all(self):
        if self._addons.apply(self._addons.update_all()):
            self._render()

    # ── footer ──────────────────────────────────────────────────────────────

    def _build_footer(self):
        p = self._palette
        sep = QFrame(self)
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(
            f"background-color: {p.divider.name()};"
            f" border: none; max-height: 1px;")
        self.layout().addWidget(sep)

        footer = QWidget(self)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 6, 16, 10)

        check = _ClickableLabel("⟳  Check for updates", footer)
        check.setObjectName("addonsCheck")
        check.setStyleSheet(f"color: {p.text_dim.name()};")
        check.clicked.connect(self._on_check)
        footer_layout.addWidget(check)

        footer_layout.addStretch(1)

        custom = _ClickableLabel("+  Add custom git addon", footer)
        custom.setObjectName("addonsCustom")
        custom.setStyleSheet(f"color: {p.pink.name()}; font-weight: bold;")
        custom.clicked.connect(self.customAddonRequested.emit)
        footer_layout.addWidget(custom)

        footer_layout.addStretch(1)

        self._footer_label = _ClickableLabel("", footer)
        self._footer_label.setObjectName("addonsFooter")
        self._footer_label.clicked.connect(self._on_update_all)
        footer_layout.addWidget(self._footer_label)

        self.layout().addWidget(footer)

    def _refresh_footer(self):
        text, fg, cursor = self._addons.footer_state()
        self._footer_label.setText(text)
        self._footer_label.setStyleSheet(f"color: {fg}; font-weight: bold;")
        clickable = cursor == "hand2"
        self._footer_label.setEnabled(clickable)
        self._footer_label.setCursor(
            Qt.PointingHandCursor if clickable else Qt.ArrowCursor)

    # ── event wiring ────────────────────────────────────────────────────────

    def _on_addons_loaded(self, event):
        if not isinstance(event, AddonsLoaded) or event.state is None:
            return
        self._render(event.state)
        self._on_badge(event.state.updates_count)

    def _on_operation_finished(self, kind: str, ok: bool, message: str):
        if kind == "addons":
            self._refresh_footer()

    def _on_operation_failed(self, kind: str, message: str):
        if kind == "addons":
            self._refresh_footer()

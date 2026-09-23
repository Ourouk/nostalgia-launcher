"""Nostalgia Launcher QML application shell (`NOSTALGIA_UI_BACKEND=qml`).

Mirrors `ui.qt.app.QtNostalgiaLauncherApp` so `cli.main()` works unchanged:
construction wires the toolkit-agnostic `ControllerHub` (shared dispatcher
+ controllers) into QML view-models, `show()`/`run()` drive the engine.
The QtWidgets shell stays the default backend — this one is opt-in until
the per-panel migration (News → Update → content lists → dialogs) lands.

QML sources resolve from `ui/qml/qml/` in dev and from `sys._MEIPASS/qml`
in frozen builds (bundled via the PyInstaller specs' `datas`).
"""

import os
import sys

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from ...core import launcher, platform_support, profiles
from ...core.helpers import relative_age
from ...services import addons as addons_service
from ...state.events import LogMessage
from ..qt.bridge import ControllerHub
from ..qt.profiles_ui import switch_profile
from ..qt.theme import palette_for_config
from .addons import AddonsModel, build_items, expected_interface
from .content import ContentListModel, build_rows, essential_pending
from .settings import LogModel, SettingsModel
from .viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateState,
)

_MODS_EMPTY = (
    "No mods catalog available.\n"
    "Configure a catalog URL under Settings → Catalog "
    "registries, then use Reload."
)
_ASSETS_EMPTY = (
    "No server content available.\n"
    "This server does not publish downloadable assets "
    "(client patches such as MPQs)."
)


def _repo_file(*parts: str) -> str:
    """Path under the repo root (dev) for bundled resources."""
    here = os.path.abspath(__file__)
    root = here
    for _ in range(6):
        root = os.path.dirname(root)
    return os.path.join(root, *parts)


def qml_dir() -> str:
    """Directory holding the bundled `.qml` sources (dev or frozen)."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "qml")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")


def _load_bundled_font():
    """Load the bundled STIX Two Math font so ⌕ and ⧉ glyphs render."""
    if getattr(sys, "frozen", False):
        font_path = os.path.join(
            sys._MEIPASS, "fonts", "STIXTwoMath-Regular.otf"
        )
    else:
        font_path = _repo_file("packaging", "fonts", "STIXTwoMath-Regular.otf")
    if os.path.isfile(font_path):
        QFontDatabase.addApplicationFont(font_path)


def _icon_path() -> str:
    """Resolve bundled launcher icon for frozen and dev builds."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "icons", "NostalgiaLauncher.png")
    return _repo_file("packaging", "icons", "NostalgiaLauncher.png")


def create_qml_app():
    """Return the process-wide QApplication, creating it exactly once.

    QApplication (not QGuiApplication) so the widget shell can later host
    migrated views via QQuickWidget during the strangler period. Pins the
    QtQuick.Controls style to Material (the modern one); the brand
    dark/gold itself comes from `appTheme` (QML sets Material Dark +
    gold accent on top of it).
    """
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Material")
    QQuickStyle.setStyle("Material")
    app = QApplication.instance()
    if app is not None:
        return app
    app = QApplication([])
    _load_bundled_font()
    ip = _icon_path()
    if os.path.isfile(ip):
        app.setWindowIcon(QIcon(ip))
    return app


class QmlNostalgiaLauncherApp:
    """QML application shell — hub + view-models + QQmlApplicationEngine."""

    def __init__(self, open_log: bool = False):
        self._open_log = bool(open_log)
        self._app = create_qml_app()
        self._hub = ControllerHub()
        self._state = LauncherState()
        self._state.attach(self._hub.bridge)
        palette = palette_for_config(launcher.config())
        self._theme = ThemeBridge(palette)
        self._news = NewsFeedModel()
        self._news.set_refresh_handler(
            lambda: self._hub.news.refresh_announcements(force=True)
        )
        self._hub.bridge.newsLoaded.connect(self._news.on_event)
        self._update = UpdateState()
        self._update.set_primary_handler(self._primary_action)
        self._update.set_recheck_handler(self._force_recheck)
        bridge = self._hub.bridge
        bridge.updateProgressChanged.connect(self._update.on_progress)
        bridge.updateFilesList.connect(self._update.on_files)
        bridge.statusChanged.connect(self._update.on_status)
        bridge.operationFinished.connect(self._on_operation_finished)
        bridge.operationFailed.connect(self._on_operation_failed)
        bridge.gameLaunched.connect(self._refresh_primary)
        bridge.gameExited.connect(self._refresh_primary)
        self._mods = ContentListModel(_MODS_EMPTY)
        self._assets = ContentListModel(_ASSETS_EMPTY)
        self._wire_content(
            self._mods, self._hub.mods, "mods", bridge.modsLoaded
        )
        self._wire_content(
            self._assets, self._hub.assets, "assets", bridge.assetsLoaded
        )
        self._addons_qml = AddonsModel()
        self._wire_addons()
        self._settings = SettingsModel()
        self._log_model = LogModel()
        self._wire_settings()
        self._engine = QQmlApplicationEngine()
        self._engine.rootContext().setContextProperty(
            "launcherState", self._state
        )
        self._engine.rootContext().setContextProperty("appTheme", self._theme)
        self._engine.rootContext().setContextProperty("newsModel", self._news)
        self._engine.rootContext().setContextProperty(
            "updateState", self._update
        )
        self._engine.rootContext().setContextProperty("modsModel", self._mods)
        self._engine.rootContext().setContextProperty(
            "assetsModel", self._assets
        )
        self._engine.rootContext().setContextProperty(
            "addonsModel", self._addons_qml
        )
        self._engine.rootContext().setContextProperty(
            "settingsModel", self._settings
        )
        self._engine.rootContext().setContextProperty(
            "logModel", self._log_model
        )
        self._engine.load(
            QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml"))
        )
        # Same background fetch the widget shell schedules: cached news
        # stays visible, TTL decides the refetch (threads, never blocks).
        self._hub.news.load()
        self._refresh_primary()
        if self._open_log:
            self.open_session_log()

    def _on_operation_finished(self, kind: str, ok: bool, message: str):
        self._update.on_finished(kind, ok, message)
        self._refresh_primary()

    def _on_operation_failed(self, kind: str, message: str):
        self._update.on_failed(kind, message)
        self._refresh_primary()

    def _refresh_primary(self, *_args):
        """Footer button state from the updater readiness decision."""
        ready = self._hub.updater.compute_readiness(
            addons_installing=self._hub.addons.installing
        )
        if ready.mode in ("play", "update", "download", "terminate"):
            self._update.update_primary(ready.label, True)
        else:
            self._update.update_primary(ready.label, False)

    def _need_game_folder(self) -> bool:
        """Guard + error post when no folder is confirmed (widget parity)."""
        if (self._hub.settings.state.path or "").strip():
            return False
        self._hub.dispatcher.post(
            LogMessage("\u2717  Please set the game folder first.\n", "err")
        )
        return True

    def _primary_action(self):
        """Footer click — mirrors MainWindow._on_update_button_clicked.

        Realm-mismatch prompting stays widget-only until the Phase 6 QML
        dialogs land; play launches directly (as answering "No" would).
        """
        updater = self._hub.updater
        if updater.running:
            return
        ready = updater.compute_readiness(
            addons_installing=self._hub.addons.installing
        )
        if ready.mode == "play":
            updater.launch_game()
        elif ready.mode == "update":
            if self._need_game_folder():
                return
            updater.start_update()
        elif ready.mode == "download":
            if self._need_game_folder():
                return
            updater.start_client_download()
        elif ready.mode == "terminate":
            updater.terminate_game()
        self._refresh_primary()

    def _force_recheck(self):
        """UPDATE-tab Force recheck (mirrors _on_force_recheck)."""
        if self._need_game_folder():
            return
        self._hub.settings.verify_files()
        self._refresh_primary()

    # ── content tabs (shared MODS/ASSETS core) ────────────────────────

    def _wire_content(self, model, ctrl, kind, loaded_signal):
        """Snapshot provider + action callbacks for one content tab."""
        if kind == "mods":

            def required_of(entry):
                return entry.get("installation") == "required"

            def version_of(entry, rec, state):
                return (
                    (rec.installed_version if rec else None)
                    or state.latest_versions.get(entry["id"])
                    or "unknown"
                )

            def apply_one(eid):
                return ctrl.apply(only_mod_id=eid)

            def apply_all():
                return ctrl.apply()

            def essential():
                return ctrl.apply_essential_mods()

            op_kind = "mods"
        else:

            def required_of(entry):
                return bool(entry.get("essential", False))

            def version_of(entry, rec, state):
                return (rec.installed_version if rec else None) or "unknown"

            def apply_one(eid):
                return ctrl.apply(only_asset_id=eid)

            def apply_all():
                return ctrl.apply()

            def essential():
                return ctrl.apply_essential_assets()

            op_kind = "assets"

        def snapshot():
            state = ctrl.state
            rows = build_rows(
                ctrl.registry,
                state,
                required_of=required_of,
                version_of=version_of,
                action_for=ctrl.action_for,
            )
            return (
                rows,
                state.updates_count,
                state.has_pending_changes,
                state.has_errors,
                essential_pending(
                    ctrl.registry, state, required_of=required_of
                ),
            )

        def on_toggle(eid, checked):
            ctrl.toggle(eid, checked)
            model.refresh()

        model.set_snapshot_provider(snapshot)
        model.set_handlers(
            toggle=on_toggle,
            action=apply_one,
            apply=apply_all,
            essential=essential,
            reload=ctrl.reload_catalog,
        )
        loaded_signal.connect(lambda _e: model.refresh())
        self._hub.bridge.operationFinished.connect(
            lambda k, ok, m: self._after_content_op(model, k, op_kind)
        )
        self._hub.bridge.operationFailed.connect(
            lambda k, m: self._after_content_op(model, k, op_kind)
        )
        model.refresh()

    def _after_content_op(self, model, kind: str, op_kind: str):
        """Drop the busy chrome when this tab's operation settles."""
        if kind == op_kind:
            model.set_running(False)
            model.refresh()

    # ── addons tab (git flows, own model) ─────────────────────────────

    def _wire_addons(self):
        """Snapshot provider + action callbacks for the ADDONS tab."""
        model = self._addons_qml
        ctrl = self._hub.addons

        def snapshot():
            state = ctrl.state
            footer_text, footer_color, cursor = ctrl.footer_state()
            ts = addons_service.catalog_last_updated()
            return {
                "items": build_items(
                    state,
                    recommended=ctrl.recommended,
                    expected=expected_interface(launcher.client_version()),
                    needle=model.current_filter(),
                ),
                "updates_count": state.updates_count,
                "apply_visible": bool(state.pending),
                "recommended_enabled": bool(
                    set(ctrl.recommended) - set(state.addons)
                )
                and not state.busy,
                "footer_text": footer_text,
                "footer_color": footer_color,
                "footer_clickable": cursor == "hand2",
                "age_text": (
                    f"Catalog updated {relative_age(ts)}" if ts else ""
                ),
            }

        def on_toggle(folder, checked):
            is_installed = folder in ctrl.state.addons
            if checked == is_installed:
                ctrl.state.pending.pop(folder, None)
            else:
                ctrl.toggle(folder, checked)
            model.refresh()

        def on_section(title):
            sections = ctrl.state.sections_open
            sections[title] = not sections.get(title, True)
            model.refresh()

        def on_update_one(folder):
            rec = ctrl.state.addons.get(folder)
            if rec is None:
                return False
            return ctrl.apply([rec.to_dict()])

        def on_update_all():
            return ctrl.apply(ctrl.update_all())

        model.set_snapshot_provider(snapshot)
        model.set_handlers(
            toggle=on_toggle,
            section=on_section,
            update_one=on_update_one,
            update_all=on_update_all,
            apply=ctrl.apply_pending,
            recommended=ctrl.apply_recommended_addons,
            check=lambda: ctrl.verify(force=True),
        )
        bridge = self._hub.bridge
        bridge.addonsLoaded.connect(lambda _e: model.refresh())
        bridge.operationFinished.connect(
            lambda k, ok, m: self._after_addons_op(k)
        )
        bridge.operationFailed.connect(lambda k, m: self._after_addons_op(k))
        model.refresh()

    def _after_addons_op(self, kind: str):
        if kind == "addons":
            self._addons_qml.set_running(False)
            self._addons_qml.refresh()

    # ── settings dialog + session log ─────────────────────────────────

    def _wire_settings(self):
        """Snapshot provider + action callbacks for the Settings tabs."""
        model = self._settings
        settings = self._hub.settings

        def snapshot():
            names = settings._source_names()
            cfg = settings.state.config
            return {
                "game_path": settings.state.path,
                "game_suggestion": settings.state.suggestion,
                "sources": [
                    {
                        "name": name,
                        "status": settings.source_statuses.get(name, ""),
                    }
                    for name in names
                ],
                "clear_wdb": bool(cfg.get("clear_wdb_on_launch", False)),
                "close_on_launch": bool(cfg.get("close_on_launch", False)),
                "client_updates": settings.client_update_enabled,
                "can_launch": platform_support.can_launch_client(),
                "can_antivirus": (platform_support.can_manage_antivirus()),
                "addons_url": settings.addons_registry_url(),
                "mods_url": settings.mods_registry_url(),
                "addons_default": settings.addons_default_enabled,
                "mods_default": settings.mods_default_enabled,
                "addons_default_avail": (settings.addons_default_available()),
                "mods_default_avail": (settings.mods_default_available()),
                "profiles": profiles.list_profiles(),
                "active_profile": profiles.active().name,
            }

        def delete_profile(name):
            if not name:
                return ""
            was_active = name == profiles.active().name
            err = profiles.delete(name)
            if err:
                return err
            if was_active:
                target = profiles.load_index()["active"]
                if target and not switch_profile(target):
                    return "Restart the launcher manually to switch."
            return ""

        model.set_snapshot_provider(snapshot)
        model.set_handlers(
            open_folder=settings.open_client_folder,
            set_path=settings.set_path,
            check_sources=settings.check_source,
            verify=settings.verify_files,
            antivirus=settings.allow_through_antivirus,
            clear_wdb=settings.set_clear_wdb,
            close_on_launch=settings.set_close_on_launch,
            client_updates=settings.set_client_update_enabled,
            addons_default=settings.set_addons_default_enabled,
            mods_default=settings.set_mods_default_enabled,
            set_addons_url=settings.set_addons_registry_url,
            set_mods_url=settings.set_mods_registry_url,
            reset_addons_url=settings.reset_addons_registry_url,
            reset_mods_url=settings.reset_mods_registry_url,
            reload_addons=settings.reload_addons_registry,
            reload_mods=settings.reload_mods_registry,
            open_addons_custom=settings.open_addons_custom_file,
            open_mods_custom=settings.open_mods_custom_file,
            clear_addons_custom=settings.clear_addons_custom,
            clear_mods_custom=settings.clear_mods_custom,
            delete_profile=delete_profile,
        )
        bridge = self._hub.bridge
        bridge.sourceStatusChanged.connect(lambda _ok, _text: model.refresh())
        model.logsRequested.connect(self.toggle_session_log)
        bridge.logMessage.connect(self._log_model.appendLine)
        model.refresh()

    def toggle_session_log(self):
        """Show/Hide logs request from the Troubleshooting tab."""
        roots = self._engine.rootObjects()
        if not roots:
            return
        dialog = roots[0].findChild(QObject, "qmlLogDialog")
        if dialog is None:
            return
        if dialog.property("visible"):
            dialog.setProperty("visible", False)
        else:
            self._log_model.refresh()
            dialog.setProperty("visible", True)

    def open_session_log(self):
        """CLI --show-log: bring the session log up immediately."""
        roots = self._engine.rootObjects()
        if not roots:
            return
        dialog = roots[0].findChild(QObject, "qmlLogDialog")
        if dialog is not None:
            self._log_model.refresh()
            dialog.setProperty("visible", True)

    @property
    def engine(self) -> QQmlApplicationEngine:
        return self._engine

    def show(self):
        roots = self._engine.rootObjects()
        if roots:
            roots[0].show()

    def raise_to_front(self):
        """Bring the QML window to the front (single-instance "raise")."""
        roots = self._engine.rootObjects()
        if not roots:
            return
        window = roots[0]
        if window.visibility() == QGuiApplication.Visibility.Minimized:
            window.showNormal()
        window.raise_()
        window.requestActivate()

    def run(self):
        """Show the window if needed, then start the event loop."""
        roots = self._engine.rootObjects()
        if roots and not roots[0].isVisible():
            roots[0].show()
        return self._app.exec()

    def close(self):
        self._hub.close()

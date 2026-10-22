"""Headless QML shell tests (Phase 0 spikes + Phase 1 wiring).

QT_QPA_PLATFORM=offscreen + QT_QUICK_BACKEND=software are set before
PySide6 is imported so the QQmlApplicationEngine loads without a display.
Covers: engine loads main.qml error-free, the chrome objects exist, the
view-models expose sane defaults, and the CLI backend selector routes
``qml`` (unknown names still return None).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from nostalgia_launcher import cli
from nostalgia_launcher.state.events import ProgressChanged, StatusChanged
from nostalgia_launcher.ui.qml.addons import AddonsModel
from nostalgia_launcher.ui.qml.app import QmlNostalgiaLauncherApp, qml_dir
from nostalgia_launcher.ui.qml.content import ContentListModel
from nostalgia_launcher.ui.qml.custom import (
    CustomAddonModel,
    CustomAssetModel,
    CustomModModel,
)
from nostalgia_launcher.ui.qml.linux import LinuxModel
from nostalgia_launcher.ui.qml.settings import LogModel, SettingsModel
from nostalgia_launcher.ui.qml.viewmodels import (
    LauncherState,
    NewsFeedModel,
    ThemeBridge,
    UpdateState,
)
from nostalgia_launcher.ui.qml.wizard import WizardModel
from nostalgia_launcher.ui.theme import HEX, Palette


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture()
def engine(qapp):
    state = LauncherState()
    theme = ThemeBridge(Palette())
    news = NewsFeedModel()
    update = UpdateState()
    eng = QQmlApplicationEngine()
    eng.rootContext().setContextProperty("launcherState", state)
    eng.rootContext().setContextProperty("appTheme", theme)
    eng.rootContext().setContextProperty("newsModel", news)
    _mods_shell = ContentListModel("mods empty")
    eng.rootContext().setContextProperty("modsModel", _mods_shell)
    _assets_shell = ContentListModel("assets empty")
    eng.rootContext().setContextProperty("assetsModel", _assets_shell)
    _addons_shell = AddonsModel()
    eng.rootContext().setContextProperty("addonsModel", _addons_shell)
    eng.rootContext().setContextProperty("settingsModel", SettingsModel())
    eng.rootContext().setContextProperty("logModel", LogModel())
    eng.rootContext().setContextProperty("wizard", WizardModel())
    eng.rootContext().setContextProperty("customModModel", CustomModModel())
    eng.rootContext().setContextProperty(
        "customAddonModel", CustomAddonModel()
    )
    eng.rootContext().setContextProperty(
        "customAssetModel", CustomAssetModel()
    )
    eng.rootContext().setContextProperty("linuxModel", LinuxModel())
    eng.rootContext().setContextProperty("updateState", update)
    eng.load(QUrl.fromLocalFile(os.path.join(qml_dir(), "main.qml")))
    yield eng, state, theme


def test_main_qml_loads_without_errors(engine):
    eng, _state, _theme = engine
    assert eng.rootObjects(), "main.qml failed to load (see warnings)"


def test_shell_chrome_objects_exist(engine):
    eng, _state, _theme = engine
    root = eng.rootObjects()[0]
    assert root.objectName() == "qmlMainWindow"
    assert root.property("color").name() == HEX["C_BG"]
    for name in (
        "qmlHeader",
        "qmlWordmark",
        "qmlLogo",
        "qmlNavBar",
        "qmlFooter",
        "qmlStatusLabel",
        "qmlProgressBar",
        "qmlPrimaryButton",
        # Installer-parity header X buttons (BrowseView already had one).
        "qmlSettingsClose",
        "qmlLogHeaderClose",
        "qmlLinuxClose",
        "qmlCustomModClose",
        "qmlCustomAddonClose",
        "qmlCustomAssetClose",
        "qmlWizardClose",
    ):
        assert root.findChild(QObject, name) is not None, name


def test_wordmark_shows_server_name_text(engine):
    """Header shows the server name (text), logo hidden until fetched."""
    eng, _state, _theme = engine
    root = eng.rootObjects()[0]
    label = root.findChild(QObject, "qmlWordmark")
    logo = root.findChild(QObject, "qmlLogo")
    # Fixture LauncherState() has no server name: fallback wordmark text.
    assert label.property("text") == "NOSTALGIA LAUNCHER"
    assert label.property("visible") is True
    assert logo.property("visible") is False


def test_header_logo_replaces_wordmark_when_set(engine):
    """A fetched logo URL hides the text and shows the image."""
    eng, state, _theme = engine
    root = eng.rootObjects()[0]
    label = root.findChild(QObject, "qmlWordmark")
    logo = root.findChild(QObject, "qmlLogo")
    state.setServerName("Test Server")
    assert label.property("text") == "Test Server"
    state.setLogoSource("file:///tmp/launcher_logo.img")
    assert logo.property("visible") is True
    assert label.property("visible") is False


def test_header_state_defaults(qapp):
    state = LauncherState(server_name="My Server")
    assert state.property("serverName") == "My Server"
    assert state.property("logoSource") == ""
    state.setLogoSource("file:///tmp/logo.img")
    assert state.property("logoSource") == "file:///tmp/logo.img"


def test_status_binding_follows_view_model(engine):
    eng, state, _theme = engine
    root = eng.rootObjects()[0]
    label = root.findChild(QObject, "qmlStatusLabel")
    state.setStatusText("Fetching torrent…")
    assert label.property("text") == "Fetching torrent…"


def test_view_model_ingests_dispatcher_events(qapp):
    state = LauncherState()
    state.on_event(StatusChanged("Verifying…"))
    state.on_event(ProgressChanged(0.5, "halfway"))
    assert state.property("statusText") == "Verifying…"
    assert state.property("progressValue") == 0.5
    assert state.property("progressLabel") == "halfway"


def test_theme_bridge_exposes_palette_slots(qapp):
    theme = ThemeBridge(Palette())
    colors = theme.property("colors")
    assert colors["C_GOLD"] == HEX["C_GOLD"]
    assert theme.color("C_BG") == HEX["C_BG"]
    assert theme.color("NOPE") == ""
    assert theme.property("themed") is True


def test_backend_selector_routes_qml():
    assert cli.resolve_backend("qml") is QmlNostalgiaLauncherApp
    assert cli.resolve_backend("bogus") is None
    assert "PySide6" in cli.backend_error_message("qml", ImportError("x"))


def test_update_tab_follows_client_updates_setting(engine, qapp):
    """UPDATE tab disables when client updates turn off, and a disabled
    tab in use falls back to NEWS."""
    eng, _state, _theme = engine
    root = eng.rootObjects()[0]
    # The fixture sets context models as temporaries (no Python ref), so
    # install a live SettingsModel; the function-scoped engine rebinds.
    settings = SettingsModel()
    eng.rootContext().setContextProperty("settingsModel", settings)
    qapp.processEvents()
    nav = root.findChild(QObject, "qmlNavBar")
    tab = root.findChild(QObject, "qmlUpdateTab")
    assert tab is not None
    assert tab.property("enabled") is True
    nav.setProperty("currentIndex", 1)
    settings.set_snapshot_provider(lambda: {"client_updates": False})
    settings.refresh()
    for _ in range(50):
        qapp.processEvents()
        if tab.property("enabled") is False:
            break
    assert settings.property("clientUpdates") is False
    assert tab.property("enabled") is False
    assert nav.property("currentIndex") == 0
    settings.set_snapshot_provider(lambda: {"client_updates": True})
    settings.refresh()
    for _ in range(50):
        qapp.processEvents()
        if tab.property("enabled") is True:
            break
    assert tab.property("enabled") is True


def test_refresh_footer_syncs_button_and_status(qapp):
    """Footer button and status text both follow compute_readiness."""
    from nostalgia_launcher.ui.bridge import ControllerHub

    hub = ControllerHub()
    shell = QmlNostalgiaLauncherApp.__new__(QmlNostalgiaLauncherApp)
    shell._hub = hub
    shell._state = LauncherState()
    shell._update = UpdateState()
    shell._refresh_footer()
    ready = hub.updater.compute_readiness()
    assert shell._state.property("statusText") == ready.status
    assert shell._update.property("primaryLabel") == ready.label


def test_tabs_open_browse_dialog(engine, qapp):
    """Empty-state Browse buttons reach the Browse dialogs."""
    eng, _state, _theme = engine
    root = eng.rootObjects()[0]
    browse = root.findChild(QObject, "qmlBrowseModsDialog")
    assert browse is not None
    assert browse.property("visible") is False
    assert root.findChild(QObject, "qmlContentBrowse") is not None
    assert root.findChild(QObject, "qmlAddonsBrowse") is not None
    # Fixture context models are temporaries (may be GC'd): rebind a held
    # model so the view resolves a live object, mirroring app.py.
    from nostalgia_launcher.ui.qml.content import ContentListModel

    mods = ContentListModel("mods empty")
    eng.rootContext().setContextProperty("modsModel", mods)
    qapp.processEvents()
    views = root.findChildren(QObject, "qmlContentView")
    assert views[0].property("contentModel") is mods
    opened = []
    mods.browseRequested.connect(lambda: opened.append(True))
    mods.requestBrowse()
    qapp.processEvents()
    assert opened == [True]
    assert opened == [True]


def test_updates_only_toggle(engine, qapp):
    """Chips collapsed to a single Updates-only toggle per tab."""
    eng, _state, _theme = engine
    root = eng.rootObjects()[0]
    assert root.findChild(QObject, "qmlContentUpdatesOnly") is not None
    assert root.findChild(QObject, "qmlContentBrowseMore") is not None
    assert root.findChild(QObject, "qmlAddonsUpdatesOnly") is not None
    assert root.findChild(QObject, "qmlAddonsUpdatesOnly") is not None


def _browse_shell(qapp):
    """Bare app shell with hub + browse models (no engine)."""
    from nostalgia_launcher.ui.bridge import ControllerHub
    from nostalgia_launcher.ui.qml.browse import BrowseModel

    hub = ControllerHub()
    shell = QmlNostalgiaLauncherApp.__new__(QmlNostalgiaLauncherApp)
    shell._hub = hub
    shell._browse_mods = BrowseModel()
    shell._browse_assets = BrowseModel()
    shell._browse_addons = BrowseModel()
    shell._browse_on_settle = {}
    return hub, shell


def test_wire_settings_only_uses_settings_model_api(qapp):
    """Regression: _wire_settings must not call content-model methods
    (set_running/set_scanning) on the SettingsModel — that crashed
    startup with AttributeError before the engine even loaded."""
    import inspect

    from nostalgia_launcher.ui.qml import app as app_mod
    from nostalgia_launcher.ui.qml.settings import SettingsModel

    src = inspect.getsource(app_mod.QmlNostalgiaLauncherApp._wire_settings)
    for meth in ("set_running", "set_scanning", "checkForUpdates"):
        assert f"model.{meth}" not in src, meth
    # SettingsModel genuinely lacks the content-model chrome: wiring
    # against it must stay limited to the snapshot/handlers contract.
    model_attrs = set(dir(SettingsModel))
    assert {"set_snapshot_provider", "set_handlers", "refresh"} <= model_attrs
    assert "set_running" not in model_attrs
    assert "set_scanning" not in model_attrs


def test_browse_mods_resets_when_operation_settles(qapp):
    """Second install from Browse must work: settle clears busy and the
    installed entry leaves the Browse list."""
    from types import SimpleNamespace

    hub, shell = _browse_shell(qapp)
    registry = [
        {"id": "a", "name": "Alpha", "description": "", "repo_url": ""},
        {"id": "b", "name": "Beta", "description": "", "repo_url": ""},
    ]
    state = SimpleNamespace(
        records={},
        pending={},
        latest_versions={},
        unknown=None,
        updates_count=0,
        has_pending_changes=False,
        has_errors=False,
    )
    applied = []
    ctrl = SimpleNamespace(
        registry=registry,
        state=state,
        apply=lambda only_mod_id=None: applied.append(only_mod_id) or True,
        reload_catalog=lambda: None,
        remove_unknown=lambda s, n: None,
        action_for=lambda eid: "",
        toggle=lambda eid, checked: None,
    )
    model = ContentListModel("x")
    shell._wire_content(model, ctrl, "mods", hub.bridge.modsLoaded)
    browse = shell._browse_mods
    assert browse.rowCount() == 2
    browse.install("b")
    assert applied == ["b"]
    assert browse.property("busy") is True
    state.records["b"] = SimpleNamespace(
        present=True, enabled=True, installed_version="1.0", error=None
    )
    shell._after_content_op(model, "mods", "mods")
    assert browse.property("busy") is False
    assert browse.rowCount() == 1


def test_browse_addons_resets_when_operation_settles(qapp):
    """Same settle contract for ADDONS: busy clears, installed leaves."""
    from types import SimpleNamespace

    from nostalgia_launcher.ui.qml.addons import AddonsModel

    hub, shell = _browse_shell(qapp)
    _addons_settle = AddonsModel()
    shell._addons_qml = _addons_settle
    rec = SimpleNamespace(
        folder="New",
        status="upToDate",
        git="https://example.test/new.git",
        toc={},
        description="fresh",
        error=None,
        to_dict=lambda: {"folder": "New"},
    )
    state = SimpleNamespace(
        addons={},
        available=[rec],
        pending={},
        sections_open={},
        updates_count=0,
        busy=False,
    )
    applied = []
    ctrl = SimpleNamespace(
        state=state,
        recommended=set(),
        footer_state=lambda: ("", "", ""),
        apply=lambda recs: applied.extend(r["folder"] for r in recs) or True,
        verify=lambda force=False: True,
        apply_pending=lambda: False,
        apply_recommended_addons=lambda: False,
    )
    hub.addons = ctrl
    shell._wire_addons()
    browse = shell._browse_addons
    assert browse.rowCount() == 1
    browse.install("New")
    assert applied == ["New"]
    assert browse.property("busy") is True
    state.addons["New"] = rec
    state.available = []
    shell._after_addons_op("addons")
    assert browse.property("busy") is False
    assert browse.rowCount() == 0

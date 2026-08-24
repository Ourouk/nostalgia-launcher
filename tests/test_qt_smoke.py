"""End-to-end headless Qt smoke suite — the full QtNostalgiaLauncherApp.

QT_QPA_PLATFORM=offscreen is set before PySide6 is imported so the module
runs without a display. The QApplication is created once and shared through
the create_qt_app() singleton. Each test builds a real QtNostalgiaLauncherApp
(window + ControllerHub + all four panels) with every network/disk backend
monkeypatched and the config redirected into tmp_path, then drives it through
the real event loop with QTest.qWait — no display, no network, no filesystem
writes outside tmp_path. run()/exec() is never called; QTest.qWait and the
bridge's QTimer deliver dispatcher events exactly as in production.
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox

import nostalgia_launcher.cli as cli_module
import nostalgia_launcher.controllers.news as news_controller
import nostalgia_launcher.controllers.update as update_controller
import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.core.constants as constants
import nostalgia_launcher.core.launcher as launcher
import nostalgia_launcher.core.platform_support as platform_support
import nostalgia_launcher.core.profiles as profiles
import nostalgia_launcher.services.addons as addons_module
import nostalgia_launcher.services.mods as mods_module
import nostalgia_launcher.services.news as news_module
import nostalgia_launcher.ui.qt.main_window as mw
from nostalgia_launcher.state.events import (
    AddonsLoaded,
    LogMessage,
    ModsLoaded,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
)
from nostalgia_launcher.state.models import (
    AddonsState,
    AddonState,
    ModsState,
)
from nostalgia_launcher.ui.qt.app import (
    QtNostalgiaLauncherApp,
    create_qt_app,
)
from nostalgia_launcher.ui.qt.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def _offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    return create_qt_app()


@pytest.fixture()
def qt_env(monkeypatch, tmp_path):
    """Redirect the config/cache files into tmp_path so nothing touches the
    real per-user config, and swap every network/disk backend for a fake.

    The news fetchers are patched both on the `news` module and on
    `news_controller` (which `from news import …`-ed them at import time);
    mods/addons are called through their modules so one patch each suffices.
    The updater's verify/update/launch entry points are patched per-hub in
    build_app() so their worker threads never start.
    """
    cfg = tmp_path / "config.json"
    cache = tmp_path / "hash_cache.json"
    config_store.configure(str(cfg), str(cache))
    monkeypatch.setattr(constants, "CONFIG_FILE", str(cfg))
    monkeypatch.setattr(constants, "CACHE_FILE", str(cache))

    featured = {
        "title": "1.16.2 is live",
        "author": "Staff",
        "date": "2026-08-13",
        "html": "<p>Patch is out.</p>",
        "url": "https://example.invalid/news/1",
    }
    items = [
        {
            "title": "Patch notes",
            "date": "2026-08-13",
            "author": "Staff",
            "body": "Full notes here",
            "url": "https://example.invalid/news/2",
        }
    ]

    monkeypatch.setattr(news_module, "fetch_featured_post", lambda: featured)
    monkeypatch.setattr(news_module, "fetch_news_items", lambda: items)
    monkeypatch.setattr(
        news_controller, "fetch_featured_post", lambda: featured
    )
    monkeypatch.setattr(news_controller, "fetch_news_items", lambda: items)

    monkeypatch.setattr(
        mods_module,
        "fetch_mod_latest_version_cached",
        lambda mod, force=False: "1.2.3",
    )
    monkeypatch.setattr(
        mods_module,
        "mods_registry",
        lambda *a, **k: [
            {
                "id": "VanillaFixes",
                "name": "VanillaFixes",
                "essential": True,
                "description": "Fixes stutter",
                "repo_url": "https://example.invalid/vf",
                "source": {
                    "kind": "github_release",
                    "owner": "o",
                    "repo": "r",
                    "asset_pattern": "*.zip",
                    "prefer_no": None,
                    "extract_map": None,
                },
            }
        ],
    )

    catalog = [
        {
            "name": "pfUI",
            "git": "https://github.com/brues-code/pfUI",
            "branch": "master",
            "ref": "HEAD",
            "toc": {},
            "description": "Everything you need",
        }
    ]
    monkeypatch.setattr(
        addons_module, "fetch_addons_catalog", lambda force=False: catalog
    )
    monkeypatch.setattr(
        addons_module,
        "addon_remote_sha",
        lambda git_url, branch=None, ref=None, force=False, raise_errors=False: (
            "deadbeef"
        ),
    )
    monkeypatch.setattr(
        addons_module,
        "addon_cached_sha",
        lambda git_url, branch=None, ref=None: "deadbeef",
    )

    monkeypatch.setattr(
        update_controller, "fetch_updater_latest_tag", Mock(return_value="1.2")
    )
    monkeypatch.setattr(
        update_controller, "updater_update_available", lambda tag: False
    )
    monkeypatch.setattr(update_controller, "can_launch_client", lambda: True)
    monkeypatch.setattr(platform_support, "can_launch_client", lambda: True)
    monkeypatch.setattr(
        platform_support, "can_manage_antivirus", lambda: False
    )

    yield cfg
    config_store.configure("", "")


@pytest.fixture()
def build_app(qapp, monkeypatch, qt_env):
    """A factory for full QtNostalgiaLauncherApp instances with safe backends.

    A non-first-run config is pre-seeded with the game folder and the flags
    that arm the background mod/addon checks, so the whole startup schedule
    runs. When first_run=True the config file is left absent so Settings
    auto-opens and verification is deferred, exactly like a real first run.
    """

    def _build(*, startup=True, first_run=False):
        cfg = qt_env
        if not first_run:
            config_store.save_config(
                {
                    "out_dir": str(cfg.parent / "game"),
                    "mod_release_cache": {
                        "VanillaFixes": {"timestamp": 0, "release": {}}
                    },
                    "addons": {},
                }
            )
        app = QtNostalgiaLauncherApp()
        app._window.show()
        hub = app._hub
        monkeypatch.setattr(hub.updater, "start_verify", Mock())
        monkeypatch.setattr(hub.updater, "start_update", Mock())
        monkeypatch.setattr(hub.updater, "check_updater_update", Mock())
        monkeypatch.setattr(
            hub.updater, "launch_game", Mock(return_value=(True, False))
        )
        if not startup:
            app._window._stop_timers()
        return app

    return _build


@pytest.fixture()
def app(build_app):
    app = build_app()
    yield app
    app.close()
    app._hub.close()


@pytest.fixture()
def app_no_startup(build_app):
    app = build_app(startup=False)
    yield app
    app.close()
    app._hub.close()


def _wait_until(predicate, timeout_ms=4000):
    """Pump the Qt event loop until `predicate` holds (worker threads post
    dispatcher events the bridge drains on the main thread)."""
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        QTest.qWait(25)
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")


# ── construction ──────────────────────────────────────────────────────────


def test_construction_builds_full_app(qapp, app):
    win = app._window
    assert win.windowTitle() == "Nostalgia Launcher"
    assert win._stack.count() == 5
    assert win._pages["UPDATE"] == mw.MainWindow.TABS.index("UPDATE")
    assert win._stack.currentIndex() == 0
    assert win._navButtons["NEWS"].isChecked()
    assert win._gearButton is not None

    # Footer chrome is wired up. The client isn't verified yet and no manifest
    # has been fetched, but launch is available (umu on PATH), so the button
    # offers PLAY — the game folder may already be ready to run.
    assert win._updateButton.objectName() == "updateButton"
    assert win._updateButton.text() == "PLAY"
    assert win._updateButton.isEnabled()
    assert win._statusLabel.text() == "Manifest unavailable"
    assert win._progressLabel is not None

    for name, obj in (
        ("NEWS", "newsPanel"),
        ("TWEAKS", "tweaksPanel"),
        ("ADDONS", "addonsPanel"),
        ("MODS", "modsPanel"),
    ):
        assert win._stack.widget(win._pages[name]).objectName() == obj


def test_news_panel_auto_renders_loading_state(qapp, app):
    win = app._window
    news_panel = win._stack.widget(0)
    assert news_panel.objectName() == "newsPanel"
    assert news_panel.featured_panel.status_label.text() == "Loading…"
    assert news_panel.featured_panel.status_label.isVisible()
    assert news_panel.announcements_panel.status_label.text() == "Loading…"
    assert news_panel.announcements_panel.status_label.isVisible()


# ── startup schedule ──────────────────────────────────────────────────────


def test_startup_schedule_runs_the_full_launch_chain(qapp, app):
    win = app._window
    hub = app._hub
    news_panel = win._stack.widget(mw.MainWindow.TABS.index("NEWS"))
    mods_panel = win._stack.widget(mw.MainWindow.TABS.index("MODS"))
    addons_panel = win._stack.widget(mw.MainWindow.TABS.index("ADDONS"))

    # 300 ms → background verify; 2000 ms → self-update check (the real
    # check_updater_update runs — the timer captured the bound method — so
    # the assertion is on the patched fetch it calls).
    _wait_until(lambda: hub.updater.start_verify.call_count == 1)
    hub.updater.start_verify.assert_called_once_with(False)
    _wait_until(lambda: update_controller.fetch_updater_latest_tag.called)

    # 600 ms → news load: loading state is replaced by the fetched post.
    _wait_until(lambda: hub.news.state.featured is not None)
    assert hub.news.state.items == [
        {
            "title": "Patch notes",
            "date": "2026-08-13",
            "author": "Staff",
            "body": "Full notes here",
            "url": "https://example.invalid/news/2",
        }
    ]
    assert "Patch is out." in news_panel.featured_panel.body.toPlainText()
    assert not news_panel.featured_panel.status_label.isVisible()
    assert news_panel.announcements_panel.scroll.isVisible()

    # 900 ms → mod latest-version fetch (mod_release_cache present).
    _wait_until(lambda: bool(hub.mods.state.latest_versions))
    assert hub.mods.state.latest_versions["VanillaFixes"] == "1.2.3"
    assert "VanillaFixes" in mods_panel._rows
    assert "1.2.3" in mods_panel._rows["VanillaFixes"].version_label.text()

    # 1500 ms → addons verify against the fake catalog.
    _wait_until(lambda: hub.addons.state.state == "done")
    assert addons_panel._rows.get("pfUI") is not None


def test_first_run_defers_verify_until_settings_close(
    qapp, build_app, monkeypatch, qt_env
):
    app = build_app(first_run=True)
    try:
        win = app._window
        hub = app._hub
        assert hub.settings.state.first_run is True
        assert hub.settings.state.first_run_verify_pending is True

        # Settings auto-opens at 500 ms; verification stays deferred.
        _wait_until(lambda: win._settingsDialog is not None)
        assert win._settingsDialog.isVisible()
        hub.updater.start_verify.assert_not_called()

        # Strict confirmation: closing WITHOUT confirming a folder verifies
        # nothing — the footer says so explicitly.
        win._settingsDialog.close()
        _wait_until(
            lambda: hub.settings.state.first_run_verify_pending is False
        )
        hub.updater.start_verify.assert_not_called()
        assert win._folderLabel.text() == "Game folder not set"

        # Confirming a folder in Settings is what arms the verify.
        game = qt_env.parent / "game"
        game.mkdir(exist_ok=True)
        assert hub.settings.set_path(str(game)) is True
        hub.updater.start_verify.assert_called_once_with(overwrite_config=True)
        # The footer mirrors the active folder whenever Settings closes.
        win._sync_folder_label()
        assert (
            win._folderLabel.text()
            == f"Game folder: {os.path.normpath(str(game))}"
        )
    finally:
        app.close()
        app._hub.close()


# ── tab switching ─────────────────────────────────────────────────────────


def test_nav_buttons_switch_tabs_and_expose_panels(qapp, app_no_startup):
    win = app_no_startup._window
    for name, obj in (
        ("NEWS", "newsPanel"),
        ("TWEAKS", "tweaksPanel"),
        ("ADDONS", "addonsPanel"),
        ("MODS", "modsPanel"),
    ):
        index = mw.MainWindow.TABS.index(name)
        QTest.mouseClick(win._navButtons[name], Qt.LeftButton)
        assert win._stack.currentIndex() == index
        assert win._navButtons[name].isChecked()
        assert win._stack.widget(index).objectName() == obj
    assert not win._navButtons["NEWS"].isChecked()


# ── settings dialog ───────────────────────────────────────────────────────


def test_settings_dialog_opens_from_gear_and_closes(qapp, app_no_startup):
    win = app_no_startup._window
    assert win._settingsDialog is None
    QTest.mouseClick(win._gearButton, Qt.LeftButton)
    dialog = win._settingsDialog
    assert isinstance(dialog, SettingsDialog)
    assert dialog.isVisible()
    assert dialog.windowTitle() == "Settings"

    dialog.close()
    QTest.qWait(50)
    assert not dialog.isVisible()


# ── update status → progress → finish cycle ───────────────────────────────


def test_update_status_progress_finish_cycle(qapp, app_no_startup):
    win = app_no_startup._window
    hub = app_no_startup._hub
    # No manifest fetched yet, but launch is available → PLAY (the folder
    # may already be ready to run).
    assert win._updateButton.text() == "PLAY"

    hub.dispatcher.post(StatusChanged("Ready to update"))
    QTest.qWait(120)
    assert win._statusLabel.text() == "Ready to update"
    assert win._updateButton.text() == "PLAY"

    hub.dispatcher.post(ProgressChanged(0.5, "Downloading…"))
    QTest.qWait(120)
    # Footer shows the phase text only — no mini progress bar.
    assert win._progressLabel.text() == "Downloading…"

    # The update worker sets client_ready and posts 100% progress before the
    # finish event; the footer mirrors that real controller state.
    hub.updater.state.client_ready = True
    hub.updater.state.manifest_available = True
    hub.dispatcher.post(ProgressChanged(1.0, ""))
    QTest.qWait(120)

    hub.dispatcher.post(OperationFinished("update", True, ""))
    QTest.qWait(120)
    assert win._updateButton.text() == "PLAY"
    assert win._updateButton.isEnabled()
    assert win._statusLabel.text() == "Everything up to date!"


# ── force recheck (UPDATE panel) ─────────────────────────────────────────


def test_force_recheck_click_invokes_settings_verify(
    qapp, app_no_startup, monkeypatch
):
    win = app_no_startup._window
    hub = app_no_startup._hub
    panel = win._stack.widget(win._pages["UPDATE"])
    btn = panel._recheck
    assert btn.objectName() == "updateRecheck"
    assert btn.isEnabled()

    verify = Mock()
    monkeypatch.setattr(hub.settings, "verify_files", verify)
    QTest.mouseClick(btn, Qt.LeftButton)

    assert verify.call_count == 1
    # Readiness was re-evaluated after the click: the controller is running
    # (a verify started), so the recheck control grays out.
    hub.updater.state.running = True
    win._refresh_ready_state()
    assert not btn.isEnabled()


def test_force_recheck_disabled_while_busy_or_disabled(qapp, app_no_startup):
    win = app_no_startup._window
    hub = app_no_startup._hub
    btn = win._stack.widget(win._pages["UPDATE"])._recheck

    hub.updater.state.running = True
    win._refresh_ready_state()
    assert not btn.isEnabled()
    hub.updater.state.running = False

    hub.addons.state.installing = True
    win._refresh_ready_state()
    assert not btn.isEnabled()
    hub.addons.state.installing = False

    hub.settings.set_client_update_enabled(False)
    win._refresh_ready_state()
    assert not btn.isEnabled()
    hub.settings.set_client_update_enabled(True)

    win._refresh_ready_state()
    assert btn.isEnabled()


def test_force_recheck_without_folder_refuses(
    qapp, app_no_startup, monkeypatch
):
    win = app_no_startup._window
    hub = app_no_startup._hub
    hub.settings.state.path = ""
    btn = win._stack.widget(win._pages["UPDATE"])._recheck

    verify = Mock()
    monkeypatch.setattr(hub.settings, "verify_files", verify)
    post = Mock()
    monkeypatch.setattr(hub.dispatcher, "post", post)
    QTest.mouseClick(btn, Qt.LeftButton)

    assert verify.call_count == 0
    posted = [c.args[0] for c in post.call_args_list]
    assert LogMessage("✗  Please set the game folder first.\n", "err") in (
        posted
    )


# ── mods / addons snapshots → rows + nav badges ───────────────────────────


def test_mods_loaded_renders_rows_and_updates_badge(qapp, app_no_startup):
    win = app_no_startup._window
    hub = app_no_startup._hub
    panel = win._stack.widget(win._pages["MODS"])

    state = ModsState(latest_versions={"VanillaFixes": "2.0"}, updates_count=3)
    hub.mods.state = state
    hub.dispatcher.post(ModsLoaded(state))
    QTest.qWait(200)

    assert "VanillaFixes" in panel._rows
    assert "2.0" in panel._rows["VanillaFixes"].version_label.text()
    badge = win._tabBadges["MODS"]
    assert badge.text() == "3"
    assert badge.isVisible()


def test_addons_loaded_renders_rows_and_updates_badge(qapp, app_no_startup):
    win = app_no_startup._window
    hub = app_no_startup._hub
    panel = win._stack.widget(win._pages["ADDONS"])

    state = AddonsState(
        addons={
            "SellValue": AddonState.from_dict(
                {
                    "folder": "SellValue",
                    "status": "outOfDate",
                    "git": "https://github.com/octo/SellValue",
                    "toc": {"Title": "Sell Value", "Interface": "11200"},
                }
            )
        },
        available=[],
        updates_count=1,
    )
    hub.addons.state = state
    hub.dispatcher.post(AddonsLoaded(state))
    QTest.qWait(200)

    row = panel._rows["SellValue"]
    status = row.findChild(QLabel, "addonsStatus_SellValue")
    assert status.text() == "Update"
    badge = win._tabBadges["ADDONS"]
    assert badge.text() == "1"
    assert badge.isVisible()


# ── bundled font ────────────────────────────────────────────────────


def test_bundled_font_file_exists():
    """STIXTwoMath-Regular.otf must be present in packaging/fonts/."""
    font_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "packaging",
        "fonts",
        "STIXTwoMath-Regular.otf",
    )
    assert os.path.isfile(font_path)


def test_bundled_font_loads_into_qt(qapp):
    """The bundled STIX Two Math font must be registered with Qt."""
    from PySide6.QtGui import QFontDatabase

    font_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "packaging",
        "fonts",
        "STIXTwoMath-Regular.otf",
    )
    assert os.path.isfile(font_path)
    font_id = QFontDatabase.addApplicationFont(font_path)
    assert font_id != -1
    families = QFontDatabase.applicationFontFamilies(font_id)
    assert len(families) > 0
    assert "STIX Two Math" in families


# ── single-instance guard (QLocalServer + store lock) ──────────────────


def test_guard_roundtrip_delivers_one_json_line(qapp):
    """A second instance connects to the served key and its single JSON
    line arrives as a dispatched dict."""
    from nostalgia_launcher.core import app_lock
    from nostalgia_launcher.ui.qt import app_lock_qt

    key = app_lock.state_key("smoke-test-state.json")
    received = []
    server, _relay = app_lock_qt.serve(key, received.append)
    try:
        sock = app_lock_qt.try_connect_existing(key)
        assert sock is not None
        app_lock_qt.send_json_line(sock, {"op": "raise"})
        _wait_until(lambda: received != [])
        assert received == [{"op": "raise"}]
        assert app_lock_qt.try_connect_existing(key) is not None
    finally:
        server.close()
        app_lock_qt.stop_server(key)


def test_raise_message_raises_window(qapp, app, monkeypatch):
    """The relay dispatches on the main thread into the window-raise
    handler; unknown ops are ignored."""
    from unittest.mock import Mock

    from nostalgia_launcher.core import app_lock
    from nostalgia_launcher.ui.qt import app_lock_qt

    key = app_lock.state_key(str(config_store.config_file))
    window = app._window
    monkeypatch.setattr(window, "activateWindow", Mock())
    monkeypatch.setattr(window, "raise_", Mock())
    server, relay = app_lock_qt.serve(key, cli_module._make_raise_handler(app))
    try:
        sock = app_lock_qt.try_connect_existing(key)
        app_lock_qt.send_json_line(sock, {"op": "raise"})
        _wait_until(lambda: window.activateWindow.called)
        assert window.raise_.called
    finally:
        server.close()
        app_lock_qt.stop_server(key)


def test_run_backend_second_instance_returns_0_without_app(
    qapp, fake_home, monkeypatch, capsys
):
    """Pre-existing server for the profile key: main() forwards the raise
    and exits 0 WITHOUT constructing the Qt app shell."""
    from nostalgia_launcher.core import app_lock
    from nostalgia_launcher.ui.qt import app_lock_qt

    os.makedirs(platform_support.config_dir(), exist_ok=True)
    with open(launcher.user_config_path(), "w", encoding="utf-8") as f:
        f.write('{"server": {"base_url": "https://launcher.test"}}')

    def boom(*a, **kw):
        raise AssertionError("QtNostalgiaLauncherApp must not be built")

    monkeypatch.setattr(
        "nostalgia_launcher.ui.qt.app.QtNostalgiaLauncherApp", boom
    )

    key = app_lock.state_key(constants.CONFIG_FILE)
    server, _relay = app_lock_qt.serve(key)
    try:
        rc = cli_module.main([])
    finally:
        server.close()
        app_lock_qt.stop_server(key)
    assert rc == 0
    assert "Already running" in capsys.readouterr().err


def test_busy_store_exits_6(qapp, fake_home, monkeypatch, capsys):
    """When another process holds the profile's store lock (and no guard
    server answers), startup fails with exit code 6 and a message."""
    import nostalgia_launcher.core.app_lock as app_lock_module

    prof, err = profiles.create(
        "busy", '{"server": {"base_url": "https://launcher.test"}}'
    )
    assert err == ""

    def boom(state_path, *a, **kw):
        raise app_lock_module.AcquireError(state_path)

    monkeypatch.setattr(
        "nostalgia_launcher.core.app_lock.acquire_with_grace", boom
    )
    assert cli_module.main(["--profile", "busy"]) == 6
    assert "already holds this profile's store" in capsys.readouterr().err


def test_profile_keys_and_locks_differ_across_profiles(qapp, fake_home):
    """Cross-profile parallelism precondition: distinct state paths give
    distinct guard keys AND distinct lock files."""
    from nostalgia_launcher.core import app_lock

    profiles.create("alpha")
    profiles.create("beta")
    ka = app_lock.state_key(profiles.resolve("alpha").state_path())
    kb = app_lock.state_key(profiles.resolve("beta").state_path())
    assert ka != kb
    assert app_lock.lock_file_for(
        profiles.resolve("alpha").state_path()
    ) != app_lock.lock_file_for(profiles.resolve("beta").state_path())


# ── profiles UI: chip, PROFILES section, switch & restart ─────────────


def _open_settings_window(build_app):
    """A running app (safe backends) with the non-modal Settings dialog
    open. Returns the app; tests close it via _close()."""
    app = build_app()
    app._window._open_settings_dialog()
    return app


def test_header_chip_shows_active_profile_name(qapp, build_app):
    from PySide6.QtWidgets import QLabel

    app = build_app(startup=False)
    try:
        chip = app._window.findChild(QLabel, "profileChip")
        assert chip is not None
        assert chip.text() == "default"
        assert chip.toolTip()
    finally:
        app.close()
        app._hub.close()


def test_header_chip_reflects_non_default_profile(qapp, build_app, fake_home):
    from PySide6.QtWidgets import QLabel

    profiles.create("chipster")
    profiles.activate(profiles.resolve("chipster"))
    app = build_app(startup=False)
    try:
        chip = app._window.findChild(QLabel, "profileChip")
        assert chip.text() == "chipster"
    finally:
        app.close()
        app._hub.close()


def test_settings_profiles_section_renders(qapp, build_app, fake_home):
    """The PROFILES section lists every profile with the active one
    preselected plus all management buttons."""
    from PySide6.QtWidgets import QPushButton

    profiles.create("alpha")
    app = _open_settings_window(build_app)
    try:
        dlg = app._window._settingsDialog
        combo = dlg._profiles_combo
        names = [combo.itemText(i) for i in range(combo.count())]
        assert names == ["default", "alpha"]
        assert combo.currentText() == "default"
        for obj_name in (
            "profilesNew",
            "profilesDuplicate",
            "profilesRename",
            "profilesDelete",
            "profilesSwitch",
        ):
            assert dlg.findChild(QPushButton, obj_name) is not None
    finally:
        app._window._settingsDialog.close()
        app.close()
        app._hub.close()


def test_switch_restart_persists_pointer_and_quits(
    qapp, build_app, monkeypatch, fake_home
):
    profiles.create("beta")
    detached = Mock(return_value=True)
    monkeypatch.setattr(
        "nostalgia_launcher.ui.qt.main_window.QProcess.startDetached",
        detached,
    )
    quit_calls = []
    monkeypatch.setattr(
        "nostalgia_launcher.ui.qt.settings_dialog.QApplication.quit",
        lambda *a, **kw: quit_calls.append(1),
    )
    monkeypatch.setattr(
        "nostalgia_launcher.ui.qt.settings_dialog.QMessageBox.question",
        lambda *a, **kw: QMessageBox.Yes,
    )
    app = _open_settings_window(build_app)
    try:
        dlg = app._window._settingsDialog
        dlg._refresh_profiles_combo(select="beta")
        dlg._on_profile_switch()

        assert profiles.load_index()["active"] == "beta"
        assert detached.called
        assert quit_calls == [1]
    finally:
        app.close()
        app._hub.close()


def test_switch_restart_failure_asks_for_manual_restart(
    qapp, build_app, monkeypatch, fake_home
):
    profiles.create("gamma")
    monkeypatch.setattr(
        "nostalgia_launcher.ui.qt.main_window.QProcess.startDetached",
        Mock(return_value=False),
    )
    monkeypatch.setattr(
        "nostalgia_launcher.ui.qt.settings_dialog.QMessageBox.question",
        lambda *a, **kw: QMessageBox.Yes,
    )
    app = _open_settings_window(build_app)
    try:
        dlg = app._window._settingsDialog
        dlg._refresh_profiles_combo(select="gamma")
        dlg._on_profile_switch()

        assert profiles.load_index()["active"] == "gamma"
        assert "manually" in dlg._profiles_status.text().lower()
    finally:
        app.close()
        app._hub.close()


def test_delete_active_resets_pointer_and_offers_switch(
    qapp, build_app, monkeypatch, fake_home
):
    profiles.create("gone")
    profiles.set_active("gone")
    answers = iter([QMessageBox.Yes, QMessageBox.No])
    monkeypatch.setattr(
        "nostalgia_launcher.ui.qt.settings_dialog.QMessageBox.question",
        lambda *a, **kw: next(answers),
    )
    app = _open_settings_window(build_app)
    try:
        dlg = app._window._settingsDialog
        dlg._refresh_profiles_combo(select="gone")
        dlg._on_profile_delete()

        idx = profiles.load_index()
        assert idx["active"] == "default"
        assert "gone" not in idx["order"]
        assert not os.path.exists(
            os.path.join(platform_support.config_dir(), "profiles", "gone")
        )
    finally:
        app.close()
        app._hub.close()


def test_delete_default_is_refused_inline(qapp, build_app, fake_home):
    app = _open_settings_window(build_app)
    try:
        dlg = app._window._settingsDialog
        dlg._refresh_profiles_combo(select="default")
        dlg._on_profile_delete()
        assert "cannot be deleted" in dlg._profiles_status.text()
        assert "default" in profiles.list_profiles()
    finally:
        app.close()
        app._hub.close()

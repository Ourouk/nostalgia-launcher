# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Octo Updater — single-file, windowed PySide6 app.

Build with ``pyinstaller OctoUpdater.spec`` (or ``uv run pyinstaller
OctoUpdater.spec`` after ``uv sync --dev``). Produces ``dist/OctoUpdater``
(windowed) from the ``octo_updater.py`` entry point.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("PySide6")

# The Qt backend is imported at runtime from octo_updater.py (and the
# panels/dialogs are constructed by qt_main_window), so list every app
# module explicitly to be safe under a frozen build.
hiddenimports += [
    "config_store",
    "qt_app",
    "qt_main_window",
    "qt_bridge",
    "qt_theme",
    "qt_news_panel",
    "qt_tweaks_panel",
    "qt_mods_panel",
    "qt_addons_panel",
    "qt_settings_dialog",
    "qt_log_window",
    "qt_custom_addon_dialog",
    "update_controller",
    "news_controller",
    "mods_controller",
    "addons_controller",
    "settings_controller",
    "tweaks_controller",
    "ui_state",
    "ui_events",
    "ui_metrics",
    "platform_support",
    "constants",
    "log_sink",
    "security_http",
    "helpers",
    "filesystem",
    "news",
    "mods",
    "addons",
    "tweaks",
    "client_update",
    "self_update",
    "errors",
]

a = Analysis(
    ["octo_updater.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OctoUpdater",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="OctoUpdater.ico",
)

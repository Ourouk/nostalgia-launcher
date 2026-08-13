# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Linux AppImage build — a *directory* bundle.

AppImage already provides the outer single-file bundle, so the app itself is
built as a PyInstaller onedir (`COLLECT`): nesting a onefile executable inside
an AppImage is slower to start and harder to debug. The resulting
``dist/OctoUpdater/`` directory is copied into the AppDir as ``usr/bin/`` by
``packaging/linux/build-appimage.sh``.

Build with ``uv run pyinstaller --noconfirm --clean OctoUpdater-linux.spec``.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("PySide6")

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
    [],
    exclude_binaries=True,
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OctoUpdater",
)

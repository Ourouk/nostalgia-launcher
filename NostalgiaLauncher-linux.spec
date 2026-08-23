# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Linux AppImage build — a *directory* bundle.

AppImage already provides the outer single-file bundle, so the app itself is
built as a PyInstaller onedir (`COLLECT`): nesting a onefile executable inside
an AppImage is slower to start and harder to debug. The resulting
``dist/NostalgiaLauncher/`` directory is copied into the AppDir as ``usr/bin/`` by
``packaging/linux/build-appimage.sh``.

Build with ``uv run pyinstaller --noconfirm --clean NostalgiaLauncher-linux.spec``.
"""

from PyInstaller.utils.hooks import collect_all

pyside_datas, pyside_binaries, pyside_hiddenimports = collect_all("PySide6")
shiboken_datas, shiboken_binaries, shiboken_hiddenimports = collect_all("shiboken6")
lt_datas, lt_binaries, lt_hiddenimports = collect_all("libtorrent")
datas = pyside_datas + shiboken_datas + lt_datas + [
    ("packaging/fonts/STIXTwoMath-Regular.otf", "fonts")
]
binaries = pyside_binaries + shiboken_binaries + lt_binaries
hiddenimports = pyside_hiddenimports + shiboken_hiddenimports + lt_hiddenimports

hiddenimports += [
    "nostalgia_launcher.core.constants",
    "nostalgia_launcher.core.config_store",
    "nostalgia_launcher.core.errors",
    "nostalgia_launcher.core.filesystem",
    "nostalgia_launcher.core.helpers",
    "nostalgia_launcher.core.launcher",
    "nostalgia_launcher.core.log_sink",
    "nostalgia_launcher.core.platform_support",
    "nostalgia_launcher.core.security_http",
    "nostalgia_launcher.core.themes",
    "nostalgia_launcher.services.addons",
    "nostalgia_launcher.services.catalog",
    "nostalgia_launcher.services.logo",
    "nostalgia_launcher.services.mods",
    "nostalgia_launcher.services.news",
    "nostalgia_launcher.services.self_update",
    "nostalgia_launcher.services.server_index",
    "nostalgia_launcher.services.tweaks",
    "nostalgia_launcher.services.umu",
    "nostalgia_launcher.services.update_backend.http_update",
    "nostalgia_launcher.services.update_backend.torrent_update",
    "nostalgia_launcher.controllers.addons",
    "nostalgia_launcher.controllers.mods",
    "nostalgia_launcher.controllers.news",
    "nostalgia_launcher.controllers.settings",
    "nostalgia_launcher.controllers.tweaks",
    "nostalgia_launcher.controllers.update",
    "nostalgia_launcher.state.models",
    "nostalgia_launcher.state.events",
    "nostalgia_launcher.ui.qt.metrics",
    "nostalgia_launcher.ui.qt.addons_panel",
    "nostalgia_launcher.ui.qt.app",
    "nostalgia_launcher.ui.qt.bridge",
    "nostalgia_launcher.ui.qt.custom_addon_dialog",
    "nostalgia_launcher.ui.qt.launcher_config_dialog",
    "nostalgia_launcher.ui.qt.list_panel",
    "nostalgia_launcher.ui.qt.log_window",
    "nostalgia_launcher.ui.qt.main_window",
    "nostalgia_launcher.ui.qt.mods_panel",
    "nostalgia_launcher.ui.qt.news_panel",
    "nostalgia_launcher.ui.qt.settings_dialog",
    "nostalgia_launcher.ui.qt.theme",
    "nostalgia_launcher.ui.qt.tweaks_panel",
    "nostalgia_launcher.ui.qt.update_panel",
]


a = Analysis(
    ["packaging/pyinstaller_entry.py"],
    pathex=["src"],
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
    name="NostalgiaLauncher",
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
    name="NostalgiaLauncher",
)

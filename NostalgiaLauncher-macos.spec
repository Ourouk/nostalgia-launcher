# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS .app bundle — *universal2* (arm64 + x86_64).

Build on macOS with a universal-capable Python/PySide6 environment:

    uv sync --dev
    uv run pyinstaller --noconfirm --clean NostalgiaLauncher-macos.spec

Produces ``dist/NostalgiaLauncher.app``; ``packaging/macos/build-dmg.sh``
verifies both architectures with ``lipo`` and wraps it into a .dmg. The app
is unsigned by default; pass ``CODESIGN_IDENTITY`` to the build script for
ad-hoc/Developer-ID signing. UPX is disabled (not supported on macOS) and
``target_arch="universal2"`` requires every bundled binary (incl. the Qt
frameworks) to be multi-arch — a single-arch PySide6 install yields a
single-arch or failed build.
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

# The panels/dialogs are constructed by the Qt main window at runtime, so
# list every app module explicitly to be safe under a frozen build.
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="universal2",
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NostalgiaLauncher",
)

app = BUNDLE(
    coll,
    name="NostalgiaLauncher.app",
    icon="packaging/macos/NostalgiaLauncher.icns",
    bundle_identifier="be.ourouk.nostalgia-launcher",
    version="1.2",
    info_plist={
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "LSMinimumSystemVersion": "11.0",
    },
)

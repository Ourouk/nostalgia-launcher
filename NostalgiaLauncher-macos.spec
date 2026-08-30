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

import os

from PyInstaller.utils.hooks import collect_all


# Keep the Info.plist version in lockstep with the app version
# (tests/test_baseline.py enforces the pyproject <-> UPDATER_VERSION half).
import re as _re

with open("pyproject.toml", "r", encoding="utf-8") as _f:
    _m = _re.search(r'^version = "(.*?)"', _f.read(), _re.MULTILINE)
APP_VERSION = _m.group(1) if _m else "0.0.0"




pyside_datas, pyside_binaries, pyside_hiddenimports = collect_all("PySide6")
shiboken_datas, shiboken_binaries, shiboken_hiddenimports = collect_all("shiboken6")
lt_datas, lt_binaries, lt_hiddenimports = collect_all("libtorrent")
datas = pyside_datas + shiboken_datas + lt_datas + [
    ("packaging/fonts/STIXTwoMath-Regular.otf", "fonts"),
    ("packaging/icons/NostalgiaLauncher.png", "icons"),
]
binaries = pyside_binaries + shiboken_binaries + lt_binaries
hiddenimports = pyside_hiddenimports + shiboken_hiddenimports + lt_hiddenimports

# The panels/dialogs are constructed by the Qt main window at runtime, so
# list every app module explicitly to be safe under a frozen build.
# The panels/dialogs are constructed by the Qt main window at runtime, so
# list every app module explicitly to be safe under a frozen build. The
# list is generated from the module tree (no importlib/__import__ exists
# in src/, so static analysis would also find these — this is belt and
# braces that can no longer drift).
def _app_modules():
    mods = []
    pkg_root = os.path.join("src", "nostalgia_launcher")
    for dirpath, dirnames, filenames in os.walk(pkg_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), "src")
            mod = rel[:-3].replace(os.sep, ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            mods.append(mod)
    return sorted(mods)


hiddenimports += _app_modules()

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
    version=APP_VERSION,
    info_plist={
        "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication",
        "LSMinimumSystemVersion": "11.0",
    },
)

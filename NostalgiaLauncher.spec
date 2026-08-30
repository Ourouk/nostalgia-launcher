# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Nostalgia Launcher — single-file, windowed PySide6 app.

Build with ``pyinstaller NostalgiaLauncher.spec`` (or ``uv run pyinstaller
NostalgiaLauncher.spec`` after ``uv sync --dev``). Produces ``dist/NostalgiaLauncher``
(windowed) from the ``packaging/pyinstaller_entry.py`` shim.
"""

import os

from PyInstaller.utils.hooks import collect_all

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
    a.binaries,
    a.datas,
    [],
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
    icon="NostalgiaLauncher.ico",
)

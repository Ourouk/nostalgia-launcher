"""PyInstaller spec for Nostalgia Launcher — single-file, windowed PySide6 app.

Build with ``pyinstaller NostalgiaLauncher.spec`` (or ``uv run pyinstaller
NostalgiaLauncher.spec`` after ``uv sync --dev``). Produces ``dist/NostalgiaLauncher``
(windowed) from the ``packaging/pyinstaller_entry.py`` shim.
"""

import os

from PyInstaller.utils.hooks import collect_all

pyside_datas, pyside_binaries, pyside_hiddenimports = collect_all("PySide6")
shiboken_datas, shiboken_binaries, shiboken_hiddenimports = collect_all(
    "shiboken6"
)
lt_datas, lt_binaries, lt_hiddenimports = collect_all("libtorrent")
datas = (
    pyside_datas
    + shiboken_datas
    + lt_datas
    + [
        ("packaging/fonts/STIXTwoMath-Regular.otf", "fonts"),
        ("packaging/icons/NostalgiaLauncher.png", "icons"),
        ("src/nostalgia_launcher/ui/qml/qml", "qml"),
    ]
)
binaries = pyside_binaries + shiboken_binaries + lt_binaries
# Bundled 7z console binary for the Windows onefile build (staged by
# packaging/fetch-7z-windows.py into packaging/vendor/windows/). The
# (src, ".") dest extracts it to the bundle root, where
# services/sources/deploy.py::_bundled_seven_z probes for it. A missing
# vendor dir is not an error: local dev builds fall back to system 7z.
_seven_z_bin = os.path.join("packaging", "vendor", "windows", "7zr.exe")
_seven_z_license = os.path.join(
    "packaging", "vendor", "windows", "License.txt"
)
if os.path.isfile(_seven_z_bin):
    binaries.append((_seven_z_bin, "."))
else:
    print(
        "WARNING: packaging/vendor/windows/7zr.exe not staged "
        "(run packaging/fetch-7z-windows.py); "
        "build falls back to system 7z"
    )
if os.path.isfile(_seven_z_license):
    datas.append((_seven_z_license, "licenses"))
else:
    print(
        "WARNING: packaging/vendor/windows/License.txt not staged; "
        "7-Zip license text will not be bundled"
    )
hiddenimports = (
    pyside_hiddenimports + shiboken_hiddenimports + lt_hiddenimports
)


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

a = Analysis(  # noqa: F821 - provided by the PyInstaller runtime
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

pyz = PYZ(a.pure)  # noqa: F821 - provided by the PyInstaller runtime

exe = EXE(  # noqa: F821 - provided by the PyInstaller runtime
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

# Agent guide: packaging & release

Scope: PyInstaller specs, per-OS bundles, CI/CD, version bumps.

## Build commands

```bash
uv run pyinstaller --noconfirm --clean NostalgiaLauncher.spec        # Windows onefile
./packaging/linux/build-appimage.sh                                   # → dist/NostalgiaLauncher-$(uname -m).AppImage
./packaging/macos/build-dmg.sh                                        # macOS only → dist/NostalgiaLauncher-universal2.dmg
```

## Gotchas

- The PyInstaller specs (`NostalgiaLauncher.spec` = Windows onefile,
  `NostalgiaLauncher-linux.spec` = onedir for AppImage,
  `NostalgiaLauncher-macos.spec` = universal2 onedir + `.app` BUNDLE) freeze
  the entry script as **`packaging/pyinstaller_entry.py`** (a top-level shim),
  NOT `src/nostalgia_launcher/cli.py` directly — relative imports inside the
  package fail if `cli.py` is run as the frozen script. Specs use
  `pathex=["src"]` and package-qualified hidden imports.
- AppImage uses `linuxdeploy`, which names the output after the desktop entry's
  `Name` (spaces→underscores) and drops it in CWD; `build-appimage.sh` relocates
  it to `dist/`. Requires `magick` (IMv7) and `linuxdeploy` on PATH or
  `LINUXDEPLOY=` pointing at it.
- macOS: `build-dmg.sh` must run on macOS with a *universal* Python/PySide6
  (`lipo -archs` verifies both arm64+x86_64 and fails otherwise). UPX is off in
  `NostalgiaLauncher-macos.spec` (unsupported on macOS); the `.icns` is built
  by `build-icons.sh` from `packaging/icons/NostalgiaLauncher.png`. The result
  is unsigned by default — signing/notarization are opt-in via env vars.

## CI/CD

GitHub Actions: `ci.yml` = pytest on push/PR (**no ruff step — the
lint/format gate is local-only**); `release.yml` = on `v*` tag push, builds
Windows/Linux/macOS and creates a GitHub Release.

## Version consistency

`UPDATER_VERSION` in `src/nostalgia_launcher/core/constants.py` MUST equal
`pyproject.toml` `[project] version` — `tests/test_baseline.py` enforces it.
Keep them in sync when bumping.

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
  by `build-icons.sh` from `packaging/icons/NostalgiaLauncher.png`. The PNG
  is human-managed from `NostalgiaLauncher.svg` (the `generate_icon.py` "N"
  monogram is legacy and not used — all platforms share the same vector-sourced
  icon). The result is unsigned by default — signing/notarization are opt-in
  via env vars.

## Vendored 7z (Windows/macOS bundles)

`.7z` mods extract via the system 7z binary — linuxdeploy-style,
the Windows onefile and macOS `.app` ship their own so users never
install anything:

- `packaging/fetch-7z-windows.py` → `packaging/vendor/windows/7zr.exe`
  (standalone single-file upstream asset, no extraction step).
- `packaging/fetch-7z-macos.py` → `packaging/vendor/macos/7zz`
  (upstream `7z2603-mac` is a genuine x86_64+arm64 fat binary —
  verified with `file(1)` — so the universal2 story holds).
- Both scripts pin URL + SHA-256 (same supply-chain pattern as
  `LINUXDEPLOY_SHA256` in `release.yml`), are idempotent, stdlib-only,
  and also stage the upstream `License.txt` (LGPL attribution,
  bundled under `licenses/`).
- `packaging/vendor/` is gitignored; the specs bundle the binary only
  when staged, else print a warning and fall back to system 7z (local
  dev builds keep working). `release.yml` runs the fetch before
  pyinstaller and fails the job when the binary is missing.
- Runtime lookup is `services/sources/deploy.py::find_seven_z`:
  bundled first (`sys._MEIPASS` → exe dir → `_internal/`), then PATH.
  Linux never bundles — the AppImage relies on the system package and
  the missing-backend error carries per-distro install commands.

## CI/CD

GitHub Actions:

- `.github/workflows/tests.yml` — reusable quality gates: ruff lint +
  format check, and pytest (`-m "not e2e"`) on a
  ubuntu/windows/macos matrix (Qt tests self-force offscreen). Called by
  both workflows below.
- `ci.yml` — push to main / PRs; calls `tests.yml`, cancels superseded
  runs via a concurrency group.
- `release.yml` — on `v*` tag push: verifies the tag matches
  `pyproject.toml`'s version, builds Windows/Linux/macOS (linuxdeploy is
  sha256-pinned; every artifact gets an SLSA provenance attestation), and
  creates a GitHub Release immediately. Only the final release job has
  `contents: write`.
- `.github/dependabot.yml` — weekly GitHub Actions version bumps.

The lint/format gate is therefore enforced in CI too; run
`uv run ruff format .` locally before pushing.

## Version consistency

`UPDATER_VERSION` in `src/nostalgia_launcher/core/constants.py` MUST equal
`pyproject.toml` `[project] version` — `tests/test_baseline.py` enforces it.
Keep them in sync when bumping.

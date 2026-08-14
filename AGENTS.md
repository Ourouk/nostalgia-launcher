# AGENTS.md

Vanilla WoW Launcher — a PySide6 desktop app (updater + mod manager for the
Vanilla WoW client). Pure stdlib business logic; only third-party runtime dep
is PySide6.

## Commands

```bash
uv sync                          # installs the package editable + PySide6
uv run vanilla-wow-launcher      # run the app
uv run python -m vanilla_wow_launcher # equivalent
uv run pytest                    # full suite (508 pass, 3 display-only skips)
```

- Qt widget tests set `QT_QPA_PLATFORM=offscreen` themselves; no display needed.
- Real-display checks are opt-in and skipped by default:
  `QT_QPA_PLATFORM=xcb RUN_QT_DISPLAY_TESTS=1 uv run pytest tests/test_qt_display.py -k display`
- Windows build: `uv run pyinstaller --noconfirm --clean VanillaWoWLauncher.spec`
- Linux AppImage: `./packaging/linux/build-appimage.sh` → `dist/VanillaWoWLauncher-$(uname -m).AppImage`
- macOS DMG (universal2, build on macOS): `./packaging/macos/build-dmg.sh` → `dist/VanillaWoWLauncher-universal2.dmg`
- CI/CD (GitHub Actions): `ci.yml` = pytest on push/PR; `release.yml` = on `v*` tag push, builds Windows/Linux/macOS and creates a GitHub Release
- Manual run against a real server config (the only example in the repo):
  `uv run vanilla-wow-launcher --launcher-config examples/octowow.json`

## Layout (`src/` layout)

```
src/vanilla_wow_launcher/
  cli.py          # entry point: config wiring + window loop
  core/           # constants, config_store, launcher, security_http, filesystem, helpers, log_sink, platform_support, errors
  services/       # catalog, addons, mods, news, tweaks, client_update, self_update
  controllers/    # update, news, mods, addons, settings, tweaks (toolkit-agnostic)
  state/          # models.py (state dataclasses), events.py (dispatcher)
  ui/qt/          # app, main_window, bridge, theme, panels, dialogs
```

- Inside the package use **relative** imports; tests import via
  `vanilla_wow_launcher.*` absolute paths (e.g.
  `from vanilla_wow_launcher.services.mods import ...`).
- Tests monkeypatch by dotted path with the FULL package name (e.g.
  `"vanilla_wow_launcher.ui.qt.addons_panel.QMessageBox.question"`), not the
  bare module name.
- `core/constants.py` computes `APP_DIR`: repo root (3 dirs up from the file)
  when run from source, exe dir when frozen. Config and cache live in separate
  per-user dirs via `platform_support.config_dir()/cache_dir()`: Linux config
  `~/.vanilla-wow-launcher`, Windows `%APPDATA%\VanillaWoWLauncher`, macOS
  `~/Library/Application Support`; cache is Linux XDG / `%LOCALAPPDATA%` /
  `~/Library/Caches`. Superseded (next-to-exe, old XDG) and pre-rename
  (`octo-updater`) files are migrated on first run via the `LEGACY_*_FILES`
  tuples in `core/constants.py`, plus `legacy_custom_pairs()` for the custom
  catalog files that move with the config dir.
- **There are no hardcoded server/mod/addon values.** Everything is configured
  by `core/launcher.py` reading `vanilla_wow_launcher.json` (server, news,
  realm, registry URLs, mirrors; auto-discovered next to the exe / repo root,
  or `--launcher-config`). Missing/invalid config with no `--launcher-config`
  opens a **modal first-launch wizard** (`ui/qt/launcher_config_dialog.py`,
  driven by `cli._pick_launcher_config()`); an explicit `--launcher-config`
  that is missing/invalid is a hard `cli.main()` error (no wizard). The wizard
  validates via `launcher.validate_path()` (no global-state side effect) and
  the selection is persisted to `launcher.user_config_path()` (the per-user
  config dir) via `launcher.persist()`, taking precedence over auto-discovery
  on later runs. The download host allowlist
  (`security_http.allowed_download_hosts()`) is built from the launcher's
  server+mirror hosts plus the git hosts.
- The mods/addons lists come from remote JSON catalogs (`services/catalog.py`
  holds the shared validation/merge logic; the fetch entry points live in
  `services/mods.py` / `services/addons.py`). `mods.mods_registry()` is
  network-free on non-forced calls (cache → empty list); only Settings
  "Reload" forces a fetch. There is no bundled registry/recommended list —
  tests provide one by monkeypatching `mods.mods_registry()`.
- Tests get a launcher config from the autouse `_launcher_env` fixture in
  `tests/conftest.py` (server `https://launcher.test` + a "Backup" mirror) —
  never rely on real network in tests. Launcher state is **process-global**:
  `_launcher_env` calls `launcher.reset()` + `launcher.configure_from_dict(...)`
  before and after each test, so override `launcher.*` the same way.

## Architecture rules

- Controllers are toolkit-agnostic: they post dataclass *events* to a shared
  `EventDispatcher` (`state/events.py`) from worker threads; they never touch
  widgets. The Qt side (`ui/qt/bridge.py`) converts events to Qt signals on the
  main thread.
- **Update workers are queue-based**: `UpdateController.start_verify()/start_update()`
  write to internal queues drained by `UpdateController.poll()`. The Qt
  `MainWindow._pollTimer` calls `hub.updater.poll()` every 50 ms — if you add a
  new path that bypasses the window, remember the controller is not polled
  automatically. Completion markers (`__DONE__` etc.) only clear the busy state
  via poll.
- Keep the poll/log-drain timers stopped and workers cancelled in
  `MainWindow` teardown (idempotent `_teardown()`).

## Packaging gotchas

- The PyInstaller specs (`VanillaWoWLauncher.spec` = Windows onefile,
  `VanillaWoWLauncher-linux.spec` = onedir for AppImage,
  `VanillaWoWLauncher-macos.spec` = universal2 onedir + `.app` BUNDLE) freeze
  the entry script as **`packaging/pyinstaller_entry.py`** (a top-level shim),
  NOT `src/vanilla_wow_launcher/cli.py` directly — relative imports inside the
  package fail if `cli.py` is run as the frozen script. Specs use
  `pathex=["src"]` and package-qualified hidden imports.
- AppImage uses `linuxdeploy`, which names the output after the desktop entry's
  `Name` (spaces→underscores) and drops it in CWD; `build-appimage.sh` relocates
  it to `dist/`. Requires `magick` (IMv7) and `linuxdeploy` on PATH or
  `LINUXDEPLOY=` pointing at it.
- macOS: `build-dmg.sh` must run on macOS with a *universal* Python/PySide6
  (`lipo -archs` verifies both arm64+x86_64 and fails otherwise). UPX is off in
  `VanillaWoWLauncher-macos.spec` (unsupported on macOS); the `.icns` is built
  by `build-icons.sh` from `packaging/icons/VanillaWoWLauncher.png`. The result
  is unsigned by default — signing/notarization are opt-in via env vars.

## Version consistency

`UPDATER_VERSION` in `src/vanilla_wow_launcher/core/constants.py` MUST equal
`pyproject.toml` `[project] version` — `tests/test_baseline.py` enforces it.
Keep them in sync when bumping.

## Test quirks

- Known flaky: `tests/test_addons_controller.py::test_apply_failure_records_error_and_posts_finished`
  times out intermittently under full-suite load but passes in isolation.
  Do not "fix" by disabling.
- Qt tests share one `QApplication` via `create_qt_app()` (a second instance
  aborts Qt); widget assertions use `objectName`s set in the widgets.
- Tests redirect config to `tmp_path` via `config_store.configure(...)` and
  monkeypatch `CONFIG_FILE`/`CACHE_FILE` on both `core.constants` and
  `controllers.settings` (that module imports them by name).

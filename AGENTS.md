# AGENTS.md

Nostalgia Launcher — a PySide6 desktop app (updater + mod manager for the
Vanilla WoW client). Runtime deps: PySide6 (GUI) and libtorrent (the
BitTorrent backend for client updates, imported lazily — the client update
path degrades to per-file HTTP downloads when it isn't installed, so tests
never need it); business logic is otherwise pure stdlib.

Thematic guides (**read the relevant one before touching that area**):

| File | Read before working on |
|------|------------------------|
| `docs/agents-architecture.md` | launcher config, **profiles & single-instance guard**, catalogs/content repos, update backends, torrent, umu launch, typed event lifecycle |
| `docs/agents-testing.md` | writing/running tests, fixtures, monkeypatch seams |
| `docs/agents-packaging.md` | PyInstaller specs, AppImage/DMG, CI/CD, version bumps |
| `docs/agents-ui.md` | anything in `ui/qt/` — QSS, dialogs, widget conventions |
| `docs/BITTORRENT_UPDATER_NOTES.md` | libtorrent-specific pitfalls |
| `docs/CODEBASE_REVIEW.md` | deep map of the codebase (verified claims w/ file:line) |

## Commands

```bash
uv sync                            # installs the package editable + PySide6
uv run nostalgia-launcher          # run the app
uv run python -m nostalgia_launcher  # equivalent
uv run pytest                      # full suite locally (6 e2e tests skip unless RUN_E2E=1)
uv run pytest -m "not e2e"         # exactly what CI gates (see .github/workflows/tests.yml)
uv run ruff format .               # pep8-style 79-col wrapping ([tool.ruff])
uv run ruff check .                # lint gate — run after every edit batch
```

- **Ruff is the CI lint/format gate**; `pyproject.toml` also configures
  `pyright` (`typeCheckingMode = "standard"`) for local checks. Selects
  E4/E7/E9/F/I/W/UP/B, `line-length = 79`, `target-version = "py310"`.
- Manual run against a real server config (the only full launcher-config
  example; `examples/` also carries mods/addons catalog examples):
  `uv run nostalgia-launcher --launcher-config examples/community.example.json`

## Non-negotiable conventions

- Inside the package use **relative** imports; tests import via
  `nostalgia_launcher.*` absolute paths (e.g.
  `from nostalgia_launcher.services.mods import ...`).
- Tests monkeypatch by dotted path with the FULL package name (e.g.
  `"nostalgia_launcher.ui.qt.addons_panel.QMessageBox.question"`), not the
  bare module name.
- **Layering**: `core/`, `services/`, `controllers/`, `state/` stay free of
  PySide6 (tests import them without Qt). Qt imports live in `ui/qt/`;
  where a module must be importable without Qt (e.g. `ui/qt/app_lock_qt`)
  they stay inside functions.
- **Profiles**: one active profile per process, pinned at CLI startup;
  EVERY per-user artifact resolves through `profiles.active()` — never
  `os.path.join(config_dir(), …)` directly for launcher/state/cache/local
  content repos/custom catalogs/torrents/logo. Unknown `--profile` exits 2;
  busy store lock exits 6; a second same-profile launch forwards
  `{op:"raise"}` and exits 0 (headless busy → exit 7, documented only).
  Switching = confirm → persist pointer → detached relaunch via
  `ui/qt/profiles_ui.py::switch_profile` — don't spawn app restarts
  anywhere else. Details: `docs/agents-architecture.md`.
- Workers post typed lifecycle events from `state/events.py`
  (e.g. `ManifestAvailable`, `TorrentDiffReady`); never use string markers
  (`__DONE__` etc. — deleted with `services/update_backend/markers.py`).
- Git-ignored, hands-off: `context/` (third-party reference sources + real
  client data for e2e — never execute/lint it) and `todo/` (work-order
  consigns; annotate, never commit).
- **Testing discipline**: don't overtest. Run only the test(s) covering the
  changed code while iterating; run the full `-m "not e2e"` suite once right
  before committing. Gotchas that bite:
  - conftest autouse `_local_repos_env` redirects the content-repo/custom
    seams wholesale; tests verifying REAL profile-aware routing must restore
    them via the `real_repo_seams` fixture (see `tests/test_profiles.py`,
    `tests/test_qt_smoke.py`).
  - `fake_home` sets HOME/USERPROFILE/**APPDATA/LOCALAPPDATA** — Windows CI
    resolves config paths through %APPDATA% first, so partial redirections
    leak state across tests.
  - Known flaky (do NOT "fix"):
    `tests/test_addons_controller.py::test_apply_failure_records_error_and_posts_finished`
    — passes in isolation, times out under full-suite load (also flakes on
    `main`'s CI; rerun the failed job).

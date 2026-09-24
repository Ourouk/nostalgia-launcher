# AGENTS.md

Nostalgia Launcher — PySide6 desktop app (updater + mod manager for Vanilla/TBC/WotLK WoW). Runtime deps: PySide6 + libtorrent (lazily imported; Linux client launch requires `umu-run`). Incremental updates are **torrent-only** (piece hashes); first install falls back to single-zip HTTP (`server.download.http.fallback` + `content.type` zip/rar/folder). No per-file HTTP `traverse()`. One client per profile; `server.client_version` is one of `1.12.1`/`2.4.3`/`3.3.5a`.

Read the relevant guide before touching that area:

| Guide | When to read |
|---|---|
| `docs/agents-architecture.md` | launcher config discovery, profiles & single-instance guard, catalogs/content repos, update backends, torrent, umu launch, event lifecycle |
| `docs/update-workflow.md` | verify/update flowchart: torrent-vs-fallback branching and event order |
| `docs/agents-testing.md` | fixtures, monkeypatch seams, running e2e |
| `docs/agents-packaging.md` | PyInstaller specs, AppImage/DMG, CI/CD, version bumps |
| `docs/agents-ui.md` | `ui/qml/` — views, view-models, theme tokens |
| `docs/bittorrent-notes.md` | libtorrent pitfalls P1–P10 |
| `docs/codebase-review.md` | verified map (file:line) |

Keep AGENTS.md compact and put details in the guides.

## Commands

```bash
uv sync --dev                                 # CI uses --dev (dependency-groups)
uv run nostalgia-launcher                    # or: uv run python -m nostalgia_launcher
uv run nostalgia-launcher --launcher-config examples/community.example.json
uv run pytest tests/test_foo.py::test_bar   # single test while iterating
uv run pytest -m "not e2e"                  # CI gate (also: ruff + pyright)
uv run pytest                               # full local (e2e skipped unless RUN_E2E=1 + context/client)
uv run ruff check . && uv run ruff format . # 79-col, py312, selects E4/E7/E9/F/I/W/UP/B — run after every edit batch
uv run pyright                              # typeCheckingMode standard, ui excluded (317 stub errors), 0 errors expected
```

Verification order for CI parity: `ruff check` → `ruff format --check` → `pyright` → `pytest -m "not e2e"` (see `.github/workflows/tests.yml`; `ci.yml`/`release.yml` call it). Pyright `include` is only `src/nostalgia_launcher`. CI pins CPython 3.12 (libtorrent ships wheels for 3.10–3.13 only; 3.14+ silently loses the torrent path).

- **Headless debugging:** `--print-log [N]` dumps the retained session log to stdout and exits without ever importing Qt; `--show-log` opens the Session-log window at startup.
- **Only ruff/pyright/pytest are CI gates.** `pylint` and `vulture` sit in the dev group for local triage — their findings are not blocking.

Git wrapper blocks `git commit --no-verify` / `git push --no-verify`; use `python3 -c "import subprocess; subprocess.run(['git','commit',...])"` to bypass `commit-msg`/`pre-push` if needed.

## Architecture & conventions

- **Entry points:** the `nostalgia-launcher` console script and `python -m nostalgia_launcher` both reach `cli.main()`; the PyInstaller specs freeze `packaging/pyinstaller_entry.py` instead (relative imports break if `cli.py` is the frozen script).
- **Imports:** relative inside `src/nostalgia_launcher/`; tests use `nostalgia_launcher.*` absolute. Monkeypatch by full dotted path (e.g. `"nostalgia_launcher.services.umu.launch"`).
- **Layering:** `core/`, `services/`, `controllers/`, `state/` stay PySide6-free (tests import without Qt). Qt lives in `ui/` only (`qml/` view-models, `bridge.py`, `theme.py`, `app_lock_qt.py`, `relaunch.py`); keep Qt imports inside functions where a module must stay importable without Qt.
- **Profiles:** one active profile per process, pinned at `cli.main()` via `profiles.activate()`. All per-user artifacts through `profiles.active()` — never `config_dir()/...` directly. One profile per server, named after it (`profile_name_for` + `unique_name`; no reserved default — empty registry opens the import wizard). Unknown `--profile` exit 2 (zero profiles wizards instead); busy store lock exit 6; second same-profile launch forwards `{"op":"raise"}` exit 0. Switch = confirm → persist pointer → detached relaunch via `ui/relaunch.py::switch_profile` only.
- **Config:** no hardcoded endpoints. `core/launcher.py` validates `nostalgia_launcher.json`; missing/invalid with no `--launcher-config` opens first-launch wizard (`ui/qml/wizard.py` → `launcher.validate_path()`), explicit bad `--launcher-config` is hard error. `server.download.http.manifest/client` hard-removed — only `http.fallback` (single zip) + `torrent.{torrent_url,magnet}`.
- **Client version:** `server.client_version` is a pinned enum (`ALLOWED_CLIENT_VERSIONS` = `1.12.1`/`2.4.3`/`3.3.5a`, default `1.12.1`); declarative source is `launcher.client_version()` — `filesystem.get_client_version(out_dir)` is a shim kept for call-site compatibility. `platform_support.server_games_dir(name, client_version)` is flavor-aware (`VanillaWoW`/`TbcWoW`/`WrathWoW`); thread `client_version` into `default_game_folder()` suggestions.
- **Game folder:** strictly user-confirmed (`out_dir_user_set`). Two writers only: Settings apply + wizard folder stage (`config_store.apply_confirmed_out_dir`). Never reintroduce silent defaults.
- **Game exe:** `server.client_executable` filename (default `WoW.exe`); case-insensitive — TBC/WotLK ship `Wow.exe`, Vanilla `WoW.exe` (identical on Windows, missed on Linux). Always use `filesystem.game_executable_exists()` / `pick_game_executable()`; never `os.path.join(dir, "WoW.exe")` directly. `server.torrent_root_marker` inherits it when absent.
- **Update lifecycle:** workers (`services/update/workflow.py` `VerifyWorker`/`UpdateWorker`) post typed dataclass events from `state/events.py` to `EventDispatcher`; `controllers/update.py::_on_event` mutates `UpdateState`; `ui/bridge.py` drains every 50 ms to Qt signals, exposed to QML as view-model properties. Never use string markers (deleted `markers.py`).
- **Security/transfer:** all downloads via `core/security_http.py:secure_urlopen` (HTTPS-only every hop, TLS ≥1.2 + `certifi`, capped reads). `httpx` + `tenacity` for retries. `core/safety.py` guards archive extraction (`safe_relpath`/`safe_folder`).
- **User-facing docs:** `README.md` and `docs/developer-guide.md` are written for players/community hosts — keep them non-technical and put internals in the `docs/agents-*.md` guides.
- **Hands-off:** `context/` (third-party refs, `server_list/*.json` reference server configs, real client for e2e) and `todo/` (local planning notes) are gitignored — never lint, execute, or commit them.

## Testing quirks

- Run only covering test(s) while iterating; full `-m "not e2e"` once before commit.
- `tests/conftest.py` autouse fixtures: `_launcher_env` resets global `launcher` config; `_local_repos_env` redirects content-repo seams; `_log_sink_env` redirects `LOG_FILE`; `_profiles_env` activates an unregistered scratch profile; `_single_instance_env` stops stray `QLocalServer` guards. Tests needing real profile routing must restore via `real_repo_seams` (see `tests/test_profiles.py`).
- Shared helpers live in conftest, not per-module: `wait_for_event` (drain/poll a dispatcher), `fake_secure_urlopen` (URL→bytes/exception table), `dispatcher`, `controller_cfg`. Reuse them instead of writing local copies.
- `fake_home` sets `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA`/`XDG_*` — partial redirects leak state on Windows CI which resolves via `%APPDATA%` first. Use `hermetic_cli` for tests that drive `cli.main()`.
- Offscreen: QML tests set `QT_QPA_PLATFORM=offscreen` + `QT_QUICK_BACKEND=software` themselves. Real-display validation follows `docs/display-test-matrix.md`.
- E2E: `RUN_E2E=1 uv run pytest -m e2e` requires `context/client` + `context/wow-client.torrent`; CI runs `-m "not e2e"` only.
- Known flaky: `test_addons_controller.py::test_apply_failure_records_error_and_posts_finished` times out under full-suite load, passes in isolation — do not "fix" by disabling.

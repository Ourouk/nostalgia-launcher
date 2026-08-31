# AGENTS.md

Nostalgia Launcher — PySide6 desktop app (updater + mod manager for Vanilla WoW). Runtime deps: PySide6 + libtorrent (lazily imported; Linux client launch requires `umu-run`). Incremental updates are **torrent-only** (piece hashes); first install falls back to single-zip HTTP (`server.download.http.fallback` + `content.type` zip/rar/folder). No per-file HTTP `traverse()`.

Read the relevant guide before touching that area:

| Guide | When to read |
|---|---|
| `docs/agents-architecture.md` | launcher config discovery, profiles & single-instance guard, catalogs/content repos, update backends, torrent, umu launch, event lifecycle |
| `docs/agents-testing.md` | fixtures, monkeypatch seams, running e2e |
| `docs/agents-packaging.md` | PyInstaller specs, AppImage/DMG, CI/CD, version bumps |
| `docs/agents-ui.md` | `ui/qt/` — QSS, dialogs, widget tokens |
| `docs/bittorrent-notes.md` | libtorrent pitfalls P1–P10 |
| `docs/codebase-review.md` | verified map (file:line) |

`opencode.json` loads these as instructions; keep AGENTS.md compact and put details in the guides.

## Commands

```bash
uv sync                                    # editable install + PySide6
uv run nostalgia-launcher                    # or: uv run python -m nostalgia_launcher
uv run nostalgia-launcher --launcher-config examples/community.example.json
uv run pytest tests/test_foo.py::test_bar   # single test while iterating
uv run pytest -m "not e2e"                  # CI gate (also: ruff + pyright)
uv run pytest                               # full local (e2e skipped unless RUN_E2E=1 + context/client)
uv run ruff format . && uv run ruff check . # 79-col, py312, selects E4/E7/E9/F/I/W/UP/B — run after every edit batch
uv run pyright                              # typeCheckingMode standard, ui excluded (317 stub errors), 0 errors expected
```

Verification order for CI parity: `ruff format --check` → `ruff check` → `pyright` → `pytest -m "not e2e"` (see `.github/workflows/tests.yml`; `ci.yml`/`release.yml` call it). Pyright `include` is only `src/nostalgia_launcher`.

Git wrapper blocks `git commit --no-verify` / `git push --no-verify`; use `python3 -c "import subprocess; subprocess.run(['git','commit',...])"` to bypass `commit-msg`/`pre-push` if needed (permission in `opencode.json`).

## Architecture & conventions

- **Imports:** relative inside `src/nostalgia_launcher/`; tests use `nostalgia_launcher.*` absolute. Monkeypatch by full dotted path (e.g. `"nostalgia_launcher.services.umu.launch"`).
- **Layering:** `core/`, `services/`, `controllers/`, `state/` stay PySide6-free (tests import without Qt). Qt only in `ui/qt/`; if `ui/qt/` must be importable without Qt, keep Qt imports inside functions (e.g. `ui/qt/app_lock_qt.py`).
- **Profiles:** one active profile per process, pinned at `cli.main()` via `profiles.activate()`. All per-user artifacts through `profiles.active()` — never `config_dir()/...` directly. Unknown `--profile` exit 2; busy store lock exit 6; second same-profile launch forwards `{"op":"raise"}` exit 0. Switch = confirm → persist pointer → detached relaunch via `ui/qt/profiles_ui.py::switch_profile` only.
- **Config:** no hardcoded endpoints. `core/launcher.py` validates `nostalgia_launcher.json`; missing/invalid with no `--launcher-config` opens first-launch wizard (`launcher_config_dialog.py` → `launcher.validate_path()`), explicit bad `--launcher-config` is hard error. `server.download.http.manifest/client` hard-removed — only `http.fallback` (single zip) + `torrent.{torrent_url,magnet}`.
- **Game folder:** strictly user-confirmed (`out_dir_user_set`). Two writers only: Settings apply + wizard folder stage (`config_store.apply_confirmed_out_dir`). Never reintroduce silent defaults.
- **Update lifecycle:** workers (`services/update/workflow.py` `VerifyWorker`/`UpdateWorker`) post typed dataclass events from `state/events.py` to `EventDispatcher`; `controllers/update.py::_on_event` mutates `UpdateState`; `ui/qt/bridge.py` drains every 50 ms to Qt signals. Never use string markers (deleted `markers.py`).
- **Security/transfer:** all downloads via `core/security_http.py:secure_urlopen` (HTTPS-only, TLS ≥1.2 + `certifi`, host allowlist per-hop, capped reads). `httpx` + `tenacity` for retries. `core/safety.py` guards archive extraction (`safe_relpath`/`safe_destination`).
- **Hands-off:** `context/` (third-party refs + real client for e2e — never lint/execute) and `todo/` (work-order consigns; annotate, never commit). Don't run `ruff`/`pyright` on them.

## Testing quirks

- Run only covering test(s) while iterating; full `-m "not e2e"` once before commit.
- `tests/conftest.py` autouse fixtures: `_launcher_env` resets global `launcher` config; `_local_repos_env` redirects content-repo seams; `_log_sink_env` redirects `LOG_FILE`. Tests needing real profile routing must restore via `real_repo_seams` (see `tests/test_profiles.py`).
- `fake_home` sets `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA`/`XDG_*` — partial redirects leak state on Windows CI which resolves via `%APPDATA%` first.
- Offscreen: Qt tests set `QT_QPA_PLATFORM=offscreen` themselves. Real-display tests need `QT_QPA_PLATFORM=xcb RUN_QT_DISPLAY_TESTS=1`.
- E2E: `RUN_E2E=1 uv run pytest -m e2e` requires `context/client` + `context/wow-client.torrent`; CI runs `-m "not e2e"` only.

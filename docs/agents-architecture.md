# Agent guide: architecture & update pipeline

Scope: package layout, launcher config, catalogs, client-update backends,
game launch. Read together with `AGENTS.md` (commands + conventions).
The libtorrent pitfall list lives in `docs/BITTORRENT_UPDATER_NOTES.md`.

## Layout & configuration

```
src/nostalgia_launcher/
  cli.py          # entry point: config wiring + window loop
  core/           # constants, config_store, launcher, security_http, filesystem, helpers, log_sink, platform_support, errors, themes
  services/       # catalog, addons, mods, assets, news, tweaks, self_update, server_index, umu, logo, update_backend/
  controllers/    # update, news, mods, assets, addons, settings, tweaks (toolkit-agnostic)
  state/          # models.py (state dataclasses), events.py (dispatcher)
  ui/qt/          # app, main_window, bridge, theme, panels, dialogs
```

- `core/constants.py` computes `APP_DIR`: repo root (3 dirs up from the file)
  when run from source, exe dir when frozen. Config and cache live in separate
  per-user dirs via `platform_support.config_dir()/cache_dir()`: Linux config
  `~/.nostalgia-launcher`, Windows `%APPDATA%\NostalgiaLauncher`, macOS
  `~/Library/Application Support`; cache is Linux XDG / `%LOCALAPPDATA%` /
  `~/Library/Caches`. All state lives in those current locations — the
  launcher does not read or migrate files from any older directory layout.
- **There are no hardcoded server/mod/addon values.** Everything is configured
  by `core/launcher.py` reading `nostalgia_launcher.json` (server, news,
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
- Game folder is STRICTLY user-confirmed: `out_dir` is written ONLY by
  Settings apply (which also sets `out_dir_user_set`); controllers read it
  stored-or-empty and refuse to operate when empty. The only default-ish
  value left is `platform_support.default_game_folder(server_name)`
  (`~/Games/<Server>`, `""` when unnamed) — a UI placeholder suggestion,
  never persisted or auto-created. Pre-flag installs get the flag backfilled
  once in `SettingsController.__init__`. Don't reintroduce silent defaults.
- **Session log** (`core/log_sink.py`): `log()` is the one thread-safe sink —
  it queues for the GUI, mirrors to stdout under `NOSTALGIA_DEBUG`, and (once
  `configure_file()` ran — only `cli._run_backend()` calls it) appends to
  `LOG_FILE` with size-capped rotation to `.old`. The file sink is disabled
  until configured so library use/tests never write; tests redirect
  `LOG_FILE` via the autouse `_log_sink_env` conftest fixture. The retained
  log is read back headlessly with `--print-log [N]`; `--show-log` starts the
  GUI with the top-level Session-log window open (`open_session_log()`).
  Child processes are captured too: umu-run/Wine (`[umu]`) and WoW.exe
  (`[game]`) output is piped into `log()` by `UpdateController._watch_game`
  / `_drain_child_output` (dedup + `_CHILD_OUTPUT_MAX_LINES` cap).

## Profiles & single-instance guard

**Profiles** (`core/profiles.py`, pure stdlib) give each server/community
fully isolated state. The reserved `default` profile maps byte-identically
onto the legacy top-level files — zero migration. Non-default profiles
live under `<config_dir>/profiles/<name>/`:

```
<config_dir>/
  nostalgia_launcher.json              ┐ default profile = exactly these
  nostalgia_launcher_config.json       │ legacy paths, never moved
  <cache_dir>/…_hash_cache.json        ┘
  profiles.json                        {"active": str, "order": [str]}
  local_<kind>_repo.json               ┐ per-kind content repos
                                       ┘ ({"server", "custom"}), default only
  profiles/<name>/
    launcher.json  state.json  hash_cache.json
    local_<kind>_repo.json             mods/addons/assets content repos
    custom/nostalgia_launcher_{mods,addons,assets}_custom.json
    torrents/<info_hash>.torrent|.resume   launcher_logo.img
```

Resolution order: explicit `--profile NAME` > `profiles.json.active` >
`default`. An unknown `--profile` is a hard CLI error (stderr, exit 2);
a missing/corrupt index or ghost pointer rebuilds silently from a
directory scan — startup never crashes over the registry. One profile is
active per process, pinned once via `profiles.activate(resolve(...))` in
`cli.main()`; everything downstream routes through `profiles.active()`:
`config_store.configure(state/cache)` in `_run_backend`,
`catalog.custom_file()`, `launcher.local_repo_path()` /
`legacy_custom_path()` (the import-time content repos),
`logo.logo_cache_path()`,
`torrent_update.torrent_cache_dir()`, and first-launch wizard persistence
(via `launcher.set_profile_launcher_path`, cleared by `launcher.reset()`).
`controllers/settings` checks `first_run` against
`config_store.config_file` (NOT the constants) so a fresh profile gets
its own wizard flow. Name grammar `[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}` with
no trailing dot/space; `"default"` is reserved. UI: the main-window
header carries a profile **combo box** (`profileCombo`) — picking
another entry confirms ("The launcher will restart using profile …"),
persists the pointer and relaunches detached (`profiles_ui.
switch_profile`; `relaunch_with_profile` strips BOTH `--profile X` and
`--profile=X`, child env gets `NOSTALGIA_RELAUNCH=1`) and quits;
declining or a failed relaunch reverts the selection (a failed relaunch
still leaves the pointer persisted, so a manual start lands on it).
Settings → PROFILES is the **editor** only: New…/Duplicate/Rename…/
Delete acting on its combo selection — deleting the ACTIVE profile
resets the pointer to default and offers an immediate restart.
Concurrency model: one
active profile, restart on switch, NO parallel instances of the SAME
profile — different profiles MAY run side by side.

| Shared across all profiles | Isolated per profile |
|---|---|
| wine prefix (`data_dir()/wineprefix`), self-update release cache, session log (`launcher.log`) | server config, state store (out_dir/mods/addons/tweaks/launch), hash cache, custom catalogs, torrent metadata/resume, logo cache |

**Single-instance guard**: `cli._run_backend` derives the key from the
active profile's state path (`core/app_lock.state_key` =
`"nostalgia-launcher-" + sha1(state_path)[:12]`) and serves a Qt
`QLocalServer` (`ui/qt/app_lock_qt.py`; Qt imports stay inside
functions so core stays PySide6-free). A second launch of the SAME
profile connects, forwards `{"op": "raise"}`, prints "Already running
(profile X) — focusing existing window." and exits 0; the running window
raises/unminimizes via the relay signal. Belt-and-braces, an exclusive
non-blocking advisory lock on `<dir-of-state>/<stem>.lock`
(`fcntl.flock` / Windows `msvcrt.locking` byte 0; file never written)
is held for the process lifetime and freed by the OS on death — busy
store exits 6 ("another instance holds this profile's store").
`--print-log` runs before the guard and stays read-only. A future
headless CLI reuses the same guard with `{op: "busy"}` semantics → exit
7 instead of raising a GUI. Switch & Restart children inherit
`NOSTALGIA_RELAUNCH=1` and retry the lock ~250 ms × 12 (~3 s) before
declaring "already running", so the quitting parent never blocks them.
NFS/network home dirs make flock unreliable (documented, acceptable);
the QLocalServer guard remains authoritative there.

## Catalogs (mods/addons/assets)

- **Import-time content split**: a launcher config may carry three inline
  content sections — top-level `"mods"`, `"addons"` and `"assets"` lists
  (same entry shapes as the remote catalogs). `launcher.persist()` /
  `persist_text()` split them out on import: each lands in its own local
  repo file `local_<kind>_repo.json` in the config dir, shaped
  `{"server": […], "custom": […]}` — "server" mirrors the imported
  document faithfully (a missing section writes `[]`, so re-imports
  replace server content wholesale), "custom" holds user-added entries and
  survives re-imports. The persisted launcher config itself has the three
  sections **stripped** (the repos are the single authority); the
  `validate_*` helpers stay side-effect-free — splitting happens only at
  persist time, after the wizard's explicit Accept. The split is
  transactional: any repo-write or config-write failure rolls every
  already-written repo back to its prior bytes (freshly created files are
  removed) before erroring, so an import is never half-applied. The legacy
  per-user custom files (`nostalgia_launcher_<kind>_custom.json`) seed a
  freshly created repo's "custom" list as a one-time migration and are then
  left as backups **and no longer loaded** (`catalog.legacy_custom_layer`
  returns empty once the repo exists) so stale copies can't shadow repo
  edits; Settings' open/clear-custom buttons manage the repo files, and
  clear wipes only "custom".
- **Registry precedence per vertical** (later wins by id/folder): remote
  catalog < repo "server" < embedded-in-config (only live for direct
  ``--launcher-config`` runs, which never persist) < repo "custom" <
  legacy custom file (active only until the local repo exists — see the
  migration note above). Plumbing: `catalog.read_local_repo` /
  `write_local_repo` / `add_custom_entry` / `clear_custom_entries`;
  offline guards (`catalog_is_stale`) count repo entries as content.
- The mods/addons/assets lists come from remote JSON catalogs
  (`services/catalog.py` holds the shared validation/merge logic; the fetch
  entry points live in `services/mods.py` / `services/addons.py` /
  `services/assets.py`). `mods.mods_registry()` is
  network-free on non-forced calls (cache → empty list). Catalogs auto-refresh
  at most weekly (`catalog.CATALOG_TTL`): startup serves the persisted cache
  instantly (ADDONS via a preview snapshot posted before the verify scan) and
  refetches only when `catalog_is_stale()` / the per-URL TTL says so — explicit
  Settings "Reload" always forces, the MODS panel's header ⟳ uses
  `mods.reload_catalog()`, and the single ADDONS ⟳ ("Check for updates", in
  the header next to the age tag) runs `verify(force=True)`, which refetches
  the online catalog(s) AND rescans SHAs. Panel headers show a "Catalog
  updated …" age tag. Tests provide a registry by monkeypatching
  `mods.mods_registry()`.
- **Custom entries are first-class**: MODS and ASSETS panels have an
  "+ Add custom …" banner button (`custom_mod_dialog.py` covers every
  registered source kind; `custom_asset_dialog.py` the full asset shape);
  both validate with the catalog validators before accepting, persist into
  the repo's "custom" list via `<controller>.add_custom_entry(entry)` and
  republish instantly. The ADDONS dialog additionally persists its entry
  (`AddonsController.add_custom_entry`) so a custom addon survives
  restarts.
- **Content may also be embedded directly in the launcher config**
  (top-level `"mods"` / `"addons"` / `"assets"`; see
  `examples/community.example.json`). Entries are kept raw by
  `core/launcher._derive` (core must not import services) and sanitized by
  the services with exactly the catalog validators (addons go through the
  git-host allowlist too). Embedded-only configs are fully offline-safe:
  when no catalog URL is *explicitly* configured
  (`launcher.mods_registry_url_explicit()` — the base_url-derived default
  does not count; see `services.mods.has_remote_catalog()`),
  `catalog_is_stale()` stays False and the MODS ⟳ / Settings → Reload
  republish silently instead of failing with "Mod catalog URL is not
  configured."
- **Assets are the third content vertical** (`services/assets.py`,
  `controllers/assets.py`, `ui/qt/assets_panel.py`, state in
  `AssetsState`) — single-file server content patches such as MPQs, kept
  strictly separate from mods (DLLs) and addons (Lua/XML folders). The list
  comes from the launcher config's top-level `"assets": […]` entries plus
  the optional remote catalog at `server.assets_registry_url`
  (**explicit-only** — no base_url-derived default), merged with the
  per-user custom file; embedded ids override catalog ids, custom overrides
  both. Every asset download URL and the registry URL join the security
  allowlist (`LauncherConfig._all_urls`). An entry carries its own update
  information — `{url, dest, version?, sha1?, size?, probe?}` — and the
  staleness verdict (`assets.asset_update_available`) uses it in strict
  precedence: version vs installed record → sha1 vs local hash → size vs
  local file → opt-in HEAD probe (`etag`/`last_modified`/`size` compared
  against the snapshot captured at install, cached under
  `"asset_probe_cache"`; any probe failure is conservative: never stale) →
  nothing provided ⇒ never stale. A missing file is an install decision,
  not an update. Downloads stream through the hardened transport with the
  SHA-1 computed on the fly and install via temp-file + rename. Essential
  assets auto-install like essential mods
  (`AssetsController.apply_essential_assets`), and a game-folder change
  wipes the `"assets"`/`"asset_probe_cache"` config keys alongside
  mods/addons records.
- **realmlist.wtf**: `services/tweaks.write_realmlist_wtf(client_dir)`
  writes `SET realmlist "<server.realm>"` into the client root wherever a
  fresh `WTF/Config.wtf` is seeded (verify with overwrite/missing config,
  torrent recovery, tweaks apply on a missing config) — vanilla clients
  read both files.
- The ADDONS list is sectioned, not flat: stale installs get a **NEED
  UPDATE** section rendered above **INSTALLED** (only when non-empty),
  followed by **AVAILABLE**; each header shows its count and collapses
  independently (persisted in `AddonsState.sections_open`, new titles
  default open). There is deliberately no per-row "Up to date" label — the
  categories carry that meaning; stale rows keep their gold clickable
  "Update" action. The row website-link glyph (⧉, shared with MODS via
  `list_panel.add_row_link`) is sized by the `PT_LINK_ICON` metrics token.
  `services/addons.addon_remote_sha()` refuses any git URL whose host is
  outside `ADDON_GIT_HOSTS` before opening an API connection or spawning
  `git ls-remote`.

## Client-update backends

- Controllers are toolkit-agnostic: they post dataclass *events* to a shared
  `EventDispatcher` (`state/events.py`) from worker threads; they never touch
  widgets. The Qt side (`ui/qt/bridge.py`) converts events to Qt signals on the
  main thread.
- Client updates get a second download backend: when the active download
  source advertises a `torrent_url` (launcher config, server or mirror) and
  libtorrent is importable, `UpdateWorker` bulk-downloads the stale files via
  `services/update_backend/torrent_update.py`, then re-verifies exactly the
  delivered files against the manifest's SHA-1 (`_reverify_torrent_files`)
  and HTTP-refetches any mismatch; the whole-client per-file HTTP
  `traverse()` runs only when the torrent backend wasn't used. In the
  manifest-less recovery path there is no manifest to check — the TLS-fetched
  torrent's piece hashes are the guarantee.
- The torrent root is **auto-detected** from the unique `WoW.exe` position in
  the torrent: the parent of `WoW.exe` (case-insensitive) is the root prefix
  stripped from every torrent path when mapping to the selected WoW folder
  (e.g. `client/WoW.exe` → `<wow_folder>/WoW.exe`). A `TorrentLayoutError`
  is raised when `WoW.exe` is missing, duplicated, or any file escapes the root.
  **This stripping is applied to the actual read/write target** via
  `_remap_torrent_to_out_dir()` (in both `verify()` and `download()`), which
  remaps the torrent's file paths to `out_dir/local` with `torrent_info.remap_files`.
  Without it, libtorrent reads at `out_dir/client/...` (double prefix) and the
  whole client reports stale — the bug that spawned
  `docs/BITTORRENT_UPDATER_NOTES.md`. The remap is guarded by
  `hasattr(ti, "remap_files")` so the unit-test fakes (which lack it) are a
  no-op. See that file for the full libtorrent pitfall list.
- **libtorrent 2.x gotchas** (verified against `2.1.1.0`): `torrent_status.pieces`
  is a `list[bool]` → count present with `sum(pieces)`, never `p.count()` (no-arg
  `TypeError`); `force_recheck()` must be followed by `resume()` or the recheck
  never proceeds (Deluge pattern); `verified_pieces` is seed-mode-only and stays
  `0` during verification — use `have_piece()`/piece count for progress.
- **Torrent verification is offline**: the verification session uses an empty
  `listen_interfaces` and disables DHT/LSD/UPnP/NAT-PMP and all peer
  connections. No P2P activity occurs before the user presses UPDATE. Only the
  download session enables networking.
- When the manifest itself can't be fetched, the update falls back to a
  manifest-less **BitTorrent recovery**: if the active source advertises a
  `torrent_url` and libtorrent is importable, `UpdateWorker._recovery_download()`
  downloads the *whole* torrent (`TorrentDownloader.download(url, None)`), whose
  piece hashes (the `.torrent` arrived over TLS) stand in for the manifest's
  per-file SHA-1. It posts `markers.TORRENT_RECOVERY_DONE` (controller keeps
  `manifest_available=False`); a failed verify offers this via an enabled
  UPDATE button when `torrent_recovery_available()` (`LauncherConfig.has_torrent()`
  + libtorrent present, network-free) and the client isn't known-ready.
- Download-source probing lives in `update_backend/sources.py`
  (`DownloadSource`/`_download_source`, re-exported by `http_update` so
  controllers/tests keep importing from there).
- The launcher never binary-patches `WoW.exe` — runtime client fixes are left
  to the VanillaFixes loader mod. The only tweak channel is `Config.wtf`.

## Update lifecycle & game launch

- **Update workers are queue-based**: `UpdateController.start_verify()/start_update()`
  write to internal queues drained by `UpdateController.poll()`. The Qt
  `MainWindow._pollTimer` calls `hub.updater.poll()` every 50 ms — if you add a
  new path that bypasses the window, remember the controller is not polled
  automatically. Completion markers only clear the busy state via poll.
- **Marker protocol is constants-only** (`services/update_backend/markers.py`):
  every worker→controller control string (`__DONE__`, `__TORRENT_*__`,
  `__VERSION__…`) must be referenced via its `markers.*` constant — emit sites
  put `(markers.X, tag)` on the log queue, and
  `UpdateController._handle_log` dispatches through the `_MARKER_HANDLERS`
  dict (one `_on_*` method per marker). Adding a lifecycle outcome = new
  constant + `_on_*` method + table entry; `tests/test_markers.py` fails the
  suite on raw `"__…__"` literals anywhere else in `src/`, on unhandled
  markers, and on table entries without a constant. Never change marker
  strings — they are a wire format shared with tests.
- **Linux game launch goes through umu-launcher** (`services/umu.py`): the PLAY
  button is gated on `core.platform_support.can_launch_client()`, which is now
  True on Windows (native) and on Linux only when `umu.umu_available()` finds
  `umu-run` on PATH (or `~/.local/bin/umu-run`). `controllers/update.py`'s
  `launch_game()` splits into `_launch_game_windows()` (Popen, DXVK notice,
  VanillaFixes.exe preference) and `_launch_game_via_umu()` (WoW.exe under
  Proton in the launcher-wide `data_dir()/wineprefix`, no DXVK notice). All umu
  settings live in the config's `"launch"` key and are edited via
  `SettingsController.set_umu_*`: `umu_proton` (defaults to `UMU-Proton`, the
  newest installed Proton — `services/umu.py` `DEFAULT_PROTON`/`default_proton()`),
  `umu_renderer` (`auto`/`dxvk-d3d8`/`wined3d-opengl`), `umu_gamemode`,
  `umu_wayland`, `umu_binary_path`, `umu_game_id`, `umu_skip_builtin_dxvk`.
  They render in a **dedicated
  `LinuxSettingsDialog`** (`ui/qt/linux_settings_dialog.py`) opened by the
  "Linux (UMU) Settings…" button in the main Settings dialog — *not* a section of
  it. Renderer maps to `PROTON_DXVK_D3D8`/`PROTON_USE_WINED3D` env vars and the
  `Config.wtf` `gxApi`; GameMode wraps launch in `gamemoderun` (only if
  installed); Wayland sets `PROTON_ENABLE_WAYLAND=1` when the `umu_wayland`
  setting is true — `controllers/update.py` passes it as `launch(wayland=...)`,
  and `umu.launch` forwards it to `build_env`.
  With the DXVK renderer, `umu_skip_builtin_dxvk` swaps `PROTON_DXVK_D3D8`
  for `DXVK_GPLASYNC=1`+`DXVK_ASYNC=1`: Proton never stages its stock DXVK,
  so client-folder DLLs installed via MODS (the catalog's dxvk-gplasync mod:
  `d3d9.dll` + `dxvk.conf`) are what Wine loads (default `native,builtin`
  overrides — no staging code here, the mods pipeline owns the files).
  With `close_on_launch`, `_launch_game_via_umu` redirects the child's
  merged output to `umu.game_output_log_path()` (`game-output.log` in the
  cache dir) and skips the watcher thread: the launcher process exits ~1 s
  after spawning, and a pipe whose reader vanished would EPIPE/SIGPIPE-kill
  umu/Proton mid-startup (worst on SteamOS first-run Proton downloads).
  Tests patch the FULL path, e.g. `"nostalgia_launcher.services.umu.launch"`
  (the controller imports the umu module lazily inside the launch method).
- **One game process at a time**: `umu.launch()` returns `(pid, pgid, proc)`;
  `UpdateController` records it in `state.game_*`, posts `GameLaunched`, and
  spawns a daemon `_watch_game()` thread that drains the child's merged
  stdout/stderr into the session log (`[umu] …`, dedup + line cap — see
  `_drain_child_output`), then `proc.wait()`s and posts
  `GameExited` (clearing the running state). The Windows path captures
  WoW.exe output the same way (source tag `[game]`). `compute_readiness()` returns
  mode `"terminate"` while a game runs — the footer shows an enabled red
  TERMINATE button (`_terminate_game()` → `umu.kill_game()`: SIGTERM to the
  process group, SIGKILL after 2 s). A second `launch_game()` while one is
  running is refused.
- Keep the poll/log-drain timers stopped and workers cancelled in
  `MainWindow` teardown (idempotent `_teardown()`).

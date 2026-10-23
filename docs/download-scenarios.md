# Client Download Scenarios

What the launcher does for every combination of `server.download` sources,
client state, and runtime support. All behaviors below are verified against
`services/update/workflow.py` (`VerifyWorker` / `UpdateWorker`).

Legend: ✅ configured · – absent · `has_exe` = playable client present
(configured exe or external-launcher exe on disk). **Launchable** =
playable exe on disk **and** the platform can start it natively
(Windows) or via `umu-run` (Linux); macOS / Linux-without-umu is never
launchable — the footer shows READY instead of PLAY. (Verified:
`compute_readiness()` returns `busy`/`READY` when `can_launch_client()`
is False, `UpdateController.launch_game()` refuses when it is False.)

## 1. Source matrix (no manifest)

| # | `torrent_url` | `magnet` (+`update?`) | `http.fallback` | libtorrent | Install | Verify result | Update result | Launchable |
|---|---|---|---|---|---|---|---|---|
| 1 | – | – | – | – | existing | up-to-date (no incremental) | n/a | ✅ iff platform can launch |
| 2 | – | – | – | – | first | unavailable | fail (`No download source`) | ❌ (nothing on disk) |
| 3 | – | – | ✅ | – | existing | up-to-date (no incremental) | n/a (HTTP never patches an install) | ✅ iff platform can launch |
| 4 | – | – | ✅ | – | first | unavailable | HTTP zip → `UpdateCompleted` | ✅ after download, iff platform can launch |
| 5 | ✅/– | –/✅ | any | ❌ | existing | up-to-date (same as no-torrent) | n/a | ✅ iff platform can launch |
| 6 | ✅/– | –/✅ | ✅ | ❌ | first | unavailable | HTTP zip → `UpdateCompleted` | ✅ after download, iff platform can launch |
| 7 | ✅ | – | any | ✅ | any | offline verify → up-to-date / diff | selective / full via torrent | ✅ whenever an exe is present + platform can launch |
| 8 | – | ✅ (unset/`false`) | any | ✅ | any | verify (one swarm fetch for metadata) | incremental **refused**; full first-install only | ✅ on existing install iff platform can launch; ✅ after first download iff platform can launch |
| 9 | – | ✅ (`true`) | any | ✅ | any | verify | selective / full via torrent | ✅ whenever an exe is present + platform can launch |
| 10 | ✅ | ✅ | any | ✅ | any | `.torrent` used, magnet ignored | same | ✅ whenever an exe is present + platform can launch |
| 11 | corrupt/unreachable | – | ✅ | ✅ | first | `Corrupt`/`Unavailable` | HTTP zip → `UpdateCompleted` | ✅ after download, iff platform can launch |
| 12 | corrupt/unreachable | – | –/existing | ✅ | any | `Corrupt`/`Unavailable` → fail | fail | ✅ iff an exe was already on disk + platform can launch |
| 13 | ✅ + `update:false` | – | any | ✅ | existing | verify reports diff | `UpdateFailed` (footer falls back to PLAY; tab hides once playable) | ✅ iff platform can launch (update refusal never blocks PLAY) |
| 14 | ✅ + `update:false` | – | ✅ | ✅ | first | verify reports diff | HTTP zip via fallback (incremental refused; the flag bypass applies to full recovery / first-acquisition download) | ✅ after download, iff platform can launch |

Notes:

- **HTTP never patches an install.** `http.fallback` runs only when no
  playable exe is present. On an existing install without a torrent path,
  update posts `UpdateFailed("BitTorrent unavailable for incremental
  update")`.
- **Recovery escalation.** Selective torrent download still missing files
  afterwards escalates to a full download; no playable exe after download
  is `UpdateFailed`. Torrent failure on a first install with a fallback
  retries via HTTP.
- **`torrent.update` kill-switch.** `update:false` freezes incremental
  pushes while keeping first-install (full recovery bypasses the flag) —
  no client release needed.

## 2. Manifest overlay (optional, on top of any row above)

Configured via `server.download.manifest` (`url` + `schema` reader-map +
`routes` globs → `asset` / `mod` verticals). The overlay covers only
manifest-listed paths; the base client still follows §1.

| Manifest state | Install | Verify result | Update result | Launchable |
|---|---|---|---|---|
| configured + reachable, all listed files on disk | any | owns verdict → `TorrentUpToDate` | `UpdateCompleted` (manifest-only) / torrent remainder | ✅ iff an exe is on disk + platform can launch |
| configured + reachable, files missing | any | owns verdict → `TorrentDiffReady(manifest:…)` | `manifest:` paths over HTTPS first, then torrent remainder; manifest-only stale → `UpdateCompleted` without torrent | ✅ after overlay lands (when it includes the exe) + platform can launch; ✅ already if an exe was present |
| configured + unreachable | any | falls through to §1 torrent logic | §1 torrent/fallback logic | as §1 row |
| not configured | any | §1 torrent logic | §1 torrent/fallback logic | as §1 row |

Notes:

- The manifest snapshot is fetched over HTTPS (2 MiB cap), cached under
  `__manifest_snapshot__`, and served from cache offline.
- Staleness is **existence-only**: a listed path missing from the client
  is stale. The per-entry `sha256` is parsed and shape-validated but not
  yet enforced on download.
- The installed version cursor is recorded under
  `__manifest_installed__` after a successful overlay download.
- Manifest-routed entries also appear as **bundled entries** in the
  MODS/ASSETS registries (layer order: remote < bundled < repo-server <
  embedded < custom), so the wizard trust panel can list them before
  anything is fetched.

## 3. Bundled registry layer (MODS/ASSETS tabs)

| Manifest routes | MODS tab | ASSETS tab | Wizard trust panel |
|---|---|---|---|
| `as: asset` routes present | – | bundled asset entries (validated, `manifest-<slug>` ids) | `N manifest-managed` in Assets row |
| `as: mod` routes present | bundled mod entries (`direct_file`, overlays: `installation`, `register_dll`, `executable`) | – | `N manifest-managed` in Mods row |
| no manifest | remote/embedded/custom only | remote/embedded/custom only | no manifest counts |

- Counts are network-free (declared routes, never a fetch).
- Bundled ids lose to repo-server, embedded, and custom entries.

# Vanilla WoW Launcher

A standalone desktop updater and mod manager for the **Vanilla WoW** client.
It updates and patches the game client, manages community **mods** and **addons**,
applies client **tweaks**, and shows server **news**.

![Vanilla WoW Launcher](screenshot.png)

The interface is a modern **PySide6/Qt** implementation. The backend is
selected at startup via `VANILLA_WOW_UI_BACKEND` (see
[Backend selection](#backend-selection)).

> ⚠️ **Third-party content disclaimer**
> World of Warcraft is a trademark of Blizzard Entertainment, Inc. This
> project is **not affiliated with, endorsed by, or sponsored by**
> Blizzard Entertainment. The game client, mods, addons, patches and any
> other files handled by this tool are created, hosted and distributed by
> **third parties**; Vanilla WoW Launcher does not create, host, own or
> redistribute them. Vanilla WoW Launcher is **merely an HTTP client and
> local management tool** that retrieves, from the URLs and registries you
> configure, content owned and maintained by others. You are responsible for
> ensuring that your use of the tool and of any downloaded content complies
> with applicable laws, licenses and the terms of the third parties involved.

---

## Attribution

Original project and author:

- **rebasedkon** — original author
  ([`https://github.com/rebasedkon/octo-updater`](https://github.com/rebasedkon/octo-updater))
  — see the [LICENSE](LICENSE).

This project is a derivative of that work, now maintained and renamed as
**Vanilla WoW Launcher**:

- **Andrea Spelgatti** — `<spelgattiandrea@ourouk.be>`

The additional work includes the `src/` package layout, the PySide6 (Qt)
interface and its architecture, the build/packaging pipeline (PyInstaller +
AppImage), the JSON mod/addon catalogs and the per-user custom registry
files, the catalog configuration in Settings, input validation and hardened
HTTP handling, the automated test suite, and this documentation.

---

## Features

### 🔄 Client updates
- Verifies the local client against the server manifest (per-file SHA-1) and
  downloads only what changed.
- Resumable downloads (HTTP range), live speed/size readout, automatic retry
  with backoff, and integrity re-check after each file.
- Reads and displays the installed client version straight from `WoW.exe`.

### 🧩 Mods
Client modifications installed from their release archives and registered in
`dlls.txt`. There is **no built-in mod list** — the MODS tab shows whatever
the configured [mod catalog](#launcher-configuration) ships (essential mods
★ are auto-installed on a fresh game folder), and the catalog decides the
install order:

- Essential mods (★) auto-install on a fresh game folder.
- Per-mod **update** / **retry** actions and an update-count badge on the tab.

### 🎛️ Tweaks
Patches `WoW.exe` and writes `Config.wtf` for common quality-of-life settings:
Field of View, render distance, nameplate range, camera distance, ground
clutter distance, always-auto-loot, background sounds, and more. Invalid values
are clamped; Apply/Reset appear only when something changed.

### 📦 Addons
- Installs addons directly from Git hosts (**GitHub, GitLab, Gitea, Codeberg**)
  by downloading the repo archive pinned to a commit SHA — no Git client needed.
- The ADDONS tab shows everything from the configured addon catalog;
  addons flagged `recommended` get a star (★).
- **Add custom git addon** dialog for any allowed host.
- Update detection by comparing the installed commit against the latest,
  one-click **Update** / **Update all**, and an update-count badge.
- pfUI gets a curated **"Default"** profile injected and added to its firstrun
  picker after each install/update.

### 📰 News
Pulls the live announcements feed and the featured forum post.

### ⚙️ Settings
Change the game folder, check mirror status, verify game files, view session
logs, add the game folder to Defender exclusions, and adjust general options.

### 🔒 Security & robustness
- Hardened TLS (system trust store, hostname check, TLS 1.2+ floor).
- HTTPS-only with per-host allowlists for all downloads; redirects stay HTTPS.
- Atomic config writes (temp + rename) with a lock — safe against concurrent
  workers and interrupted saves.
- Path-traversal-safe archive extraction.
- Automatic self-update check against this repo's GitHub releases (once a day).

---

## Requirements

- **Python 3.10+** — only if running from source.
- **PySide6** (≥ 6.6) — runtime dependency for the Qt interface, which is
  the default backend (`uv sync` installs it, see [Development](#development)).
- **certifi** (optional) — bundles an up-to-date CA store for more robust TLS
  verification on machines with an out-of-date root store (otherwise the
  system trust store is used).
- The prebuilt `VanillaWoWLauncher.exe` needs nothing installed.

### Platform support

- **Windows** — full support: client updates, mods, addons, tweaks (including
  the `WoW.exe` binary patch), game launching, and Defender exclusions.
- **Linux / macOS** — the generic features work: client file updates, mods,
  addons, news, configuration, and `Config.wtf` tweaks. Actions tied to the
  Windows client (game launch, binary `WoW.exe` patching, Defender exclusions)
  are disabled automatically. The Qt backend runs on either (on Linux it needs
  the system Qt libraries — see [Building](#building)).

### Configuration location

Where the config and hash-cache files live depends on the platform (see
`platform_support.config_dir()` / `cache_dir()`). Config and cache are kept in
separate per-user directories, never next to the executable:

| Platform | Config | Hash cache |
|----------|--------|------------|
| Windows | `%APPDATA%\VanillaWoWLauncher` | `%LOCALAPPDATA%\VanillaWoWLauncher` |
| Linux | `~/.vanilla-wow-launcher` | `$XDG_CACHE_HOME/vanilla-wow-launcher` (default: `~/.cache/vanilla-wow-launcher`) |
| macOS | `~/Library/Application Support/VanillaWoWLauncher` | `~/Library/Caches/VanillaWoWLauncher` |

Files from earlier locations (next to the executable, the old XDG paths, and
the pre-rename `octo-updater` dirs) are migrated on first run.

All are safe to delete — they're recreated on next run (deleting the config
re-runs first-time setup).

### Display scaling & resizing

The **Qt** UI relies on Qt's native high-DPI handling: `qt_app.create_qt_app`
enables per-monitor scaling and a PassThrough scale-factor rounding policy
before the `QApplication` exists, so fractional display scales (e.g. 125%)
are preserved. Qt renders in *logical* pixels and applies the device-pixel
ratio at compositor time, so the same window and minimum sizes hold at
100%–200%. For troubleshooting you can override the detected scale with
`QT_SCALE_FACTOR` (and `QT_ENABLE_HIGHDPI_SCALING`). The layout math lives in
`ui_metrics.py` and is covered by unit tests plus the offscreen
`tests/test_qt_display.py` checks; the real-display QA matrix lives in
[docs/DISPLAY_TEST_MATRIX.md](docs/DISPLAY_TEST_MATRIX.md).

---

## Usage

### Prebuilt executable
Download `VanillaWoWLauncher.exe` from the [latest release](../../releases/latest)
and run it. Point the **Game folder** (Settings ⚙) at your Vanilla WoW client
folder — or let the default create one next to the executable — then click
**UPDATE**, and **PLAY** when it finishes.

### From source
```bash
uv sync
uv run vanilla-wow-launcher           # or: uv run python -m vanilla_wow_launcher
```

Vanilla WoW Launcher needs a
[`vanilla_wow_launcher.json`](#launcher-configuration) before it will start —
there is no built-in server or mod/addon list. On the very first launch it
looks next to the executable / in the repo root, and if none is found a
wizard lets you pick one (which is then remembered for later runs); an
explicit `--launcher-config PATH` always takes precedence.

The app keeps three JSON files — see
[Configuration location](#configuration-location) for where each platform
stores them:

| File | Purpose |
|------|---------|
| `vanilla_wow_launcher.json` | The server config (chosen via the wizard or shipped next to the executable); imported by the user, never written by the app |
| `vanilla_wow_launcher_config.json` | Settings, mod/addon install records, caches |
| `vanilla_wow_launcher_hash_cache.json` | Per-file SHA-1 cache to speed up verifies |

Deleting `vanilla_wow_launcher.json` (when it was imported) re-runs the
first-launch wizard; deleting the settings config re-runs first-time setup.

### Launcher configuration

The app ships with **no hardcoded server, mirror or mod/addon list**. All of
it comes from a single JSON file, `vanilla_wow_launcher.json`, that a
distribution provides:

- next to the executable (a packaged build), in the repo root (running from
  source), or passed explicitly with `--launcher-config PATH`.
- On first launch, if none is found, a wizard asks you to pick one; the
  chosen file is copied into the per-user config directory and reused on
  every later launch (until you pass `--launcher-config` again). An explicit
  `--launcher-config` that is missing/invalid is a hard error — the wizard is
  never shown for an explicit path.

Only `server.base_url` is required — every other endpoint is derived from it
unless overridden, and mirrors are optional. The manifest and client files
are fetched from the configured endpoints, so a mirror's `client_url` may
point at a separate CDN host while the manifest stays on the server:

```json
{
  "server": {
    "name": "My Vanilla WoW Server",
    "base_url": "https://server.example",
    "realm": "server.example",
    "manifest_url": "https://server.example/api/file/latest/manifest.json",
    "client_url": "https://server.example/client/latest",
    "news_url": "https://server.example/news",
    "featured_news_url": "https://server.example/news/featured",
    "mods_registry_url": "https://server.example/api/mods.json",
    "addons_registry_url": "https://server.example/api/addons.json"
  },
  "mirrors": [
    {
      "name": "Backup",
      "base_url": "https://mirror.example",
      "client_url": "https://dl.mirror.example/client/latest"
    }
  ]
}
```

A real, working example is bundled at
[`examples/octowow.json`](examples/octowow.json) — copy it to the repo root
(or pass it via `--launcher-config`) and edit the URLs to match your server.

This configures:

- the **client update** manifest and file downloads (with automatic mirror
  failover — the first reachable mirror serves the files, otherwise the
  server),
- the **news** feed endpoints,
- the **realm** written to `Config.wtf`,
- the **mod** and **addon** catalogs,
- the HTTPS download allowlist (built from the configured server + mirror
  hosts plus any custom `manifest_url`/`client_url` endpoints, so a CDN host
  like `dl.example` is allowed automatically; git hosts are always allowed
  for addon/mod downloads).

The per-user config (`vanilla_wow_launcher_config.json`) only holds
preferences and install records — it never overwrites the launcher file.

### Catalog registries (advanced)

The **MODS** and **ADDONS** lists come from two JSON catalogs served over
HTTPS and cached in the config file for offline use. Their URLs come from the
[launcher configuration](#launcher-configuration); a savvy user may point
them elsewhere (or override just one) via **Settings → Catalog registries**:

- `Apply` / `Reset` the per-user catalog URL (an empty field uses the
  launcher URL again),
- `Reload` the catalog immediately and refresh the tab,
- open the per-user **custom JSON file** (created empty on first use) and
  clear its entries.

Custom files live in the config directory (see
[Configuration location](#configuration-location)):

| File | Purpose |
|------|---------|
| `vanilla_wow_launcher_mods_custom.json` | Per-user mod entries |
| `vanilla_wow_launcher_addons_custom.json` | Per-user addon entries |

Custom entries are merged with the remote catalog — same id/folder wins, new
ones are appended. An addon entry may set `recommended` / `blocked` to add a
star or hide an entry:

```json
[
  {
    "folder": "MyCustomAddon",
    "git": "https://github.com/yourname/MyCustomAddon",
    "branch": null,
    "ref": null,
    "description": "Optional description shown in the ADDONS tab.",
    "recommended": false,
    "blocked": false
  }
]
```

Mod entries use the same shape the mod catalog uses (`id`, `name`,
`essential`, `source.kind`, `source.owner`/`repo` + `asset_pattern` or a
`direct_file`/`direct_tar` URL, `extract_map`, `register_dll`,
`installed_files`). Only the allowlisted source kinds (`github_release`,
`codeberg_release`, `direct_file`, `direct_tar`) and the single post-install
hook (`write_dxvk_conf`) are accepted — catalog data is never executed, and
downloads are still restricted to the HTTPS host allowlist regardless of what
a registry or custom file contains. Invalid entries are skipped and logged,
never fatal.

### Backend selection

The UI is selected at startup via the `VANILLA_WOW_UI_BACKEND` environment
variable (`qt` is the only/default value):

| Value | Backend |
|-------|---------|
| `qt` / `pyside6` | PySide6/Qt interface (`vanilla_wow_launcher.ui.qt`) — default |

```bash
uv run vanilla-wow-launcher           # Qt (PySide6, default)
```

- An unknown value prints `Unknown VANILLA_WOW_UI_BACKEND: <value>` and exits.
- If the Qt toolkit can't be imported, the entry point prints *"Vanilla WoW
  Launcher needs PySide6 (Qt) to run. Install it with `uv sync` or `pip
  install PySide6`."* and exits.
- Starting on a machine without a graphical display prints
  *"A graphical display (X11/Wayland) is required."* and exits.

---

## Building

### PyInstaller

Compile a single-file, windowed `VanillaWoWLauncher` executable with
[PyInstaller](https://pyinstaller.org/). The build is driven by
`VanillaWoWLauncher.spec`, which collects all of PySide6 (plugins + Qt
libraries), lists the app modules as hidden imports, bundles the
`VanillaWoWLauncher.ico` icon and produces a windowed app from the
`packaging/pyinstaller_entry.py` shim:

```bash
uv sync --dev                    # installs pyinstaller into the dev environment
uv run pyinstaller --noconfirm --clean VanillaWoWLauncher.spec
```

Or, with a plain `pip` environment:

```bash
pip install pyinstaller certifi
pyinstaller VanillaWoWLauncher.spec
```

### pyside6-deploy (alternative)

PySide6 ships its own
[deployment tool](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
(`pyside6-deploy`), which drives PyInstaller and handles the Qt plugin /
config collection automatically:

```bash
uv run pyside6-deploy packaging/pyinstaller_entry.py
```

### AppImage (Linux)

The Linux build is a PyInstaller **directory** bundle wrapped into an
AppImage, so it runs on any modern Linux distribution without installing Qt.
Everything lives in `packaging/linux/`:

| File | Purpose |
|------|---------|
| `VanillaWoWLauncher-linux.spec` | PyInstaller onedir spec (no outer onefile — AppImage provides that) |
| `build-appimage.sh` | Full build: PyInstaller → AppDir assembly → `linuxdeploy` |
| `AppRun` | Launcher script resolved relative to the AppImage mount point |
| `VanillaWoWLauncher.desktop` | Desktop entry used by linuxdeploy + for app-menu integration |

Prerequisites: `uv`, ImageMagick (`magick`), and a
[`linuxdeploy`](https://github.com/linuxdeploy/linuxdeploy) AppImage matching
your architecture (set `LINUXDEPLOY=/path/to/linuxdeploy-x86_64.AppImage` if
it isn't on `PATH`).

```bash
uv sync --dev
./packaging/linux/build-appimage.sh        # → dist/VanillaWoWLauncher-$(uname -m).AppImage
```

Build on (or inside a container of) the oldest distribution you want to
support — AppImages do not solve glibc compatibility. Test both session
types:

```bash
QT_QPA_PLATFORM=xcb     ./dist/VanillaWoWLauncher-x86_64.AppImage
QT_QPA_PLATFORM=wayland ./dist/VanillaWoWLauncher-x86_64.AppImage
```

### DMG (macOS)

The macOS build is a universal2 PyInstaller `.app` bundle (arm64 + x86_64)
wrapped into a `.dmg`, so it runs on both Apple Silicon and Intel Macs.
Everything lives in `packaging/macos/`:

| File | Purpose |
|------|---------|
| `VanillaWoWLauncher-macos.spec` | PyInstaller onedir spec with `target_arch="universal2"` + `.app` bundle (Info.plist, icon) |
| `build-dmg.sh` | Full build: icon → PyInstaller → `lipo` arch check → optional sign/notarize → `hdiutil` DMG |
| `build-icons.sh` | Render `packaging/icons/VanillaWoWLauncher.png` into the `.icns` via `iconutil` |

**The build must run on macOS** with a *universal* Python/PySide6
environment (single-arch Python produces a single-arch or failed build):

```bash
uv sync --dev
./packaging/macos/build-dmg.sh        # → dist/VanillaWoWLauncher-universal2.dmg
```

The bundle is **unsigned by default** — Gatekeeper will warn on first open.
Optional hooks (all env-gated, none required):

```bash
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
NOTARY_APPLE_ID="you@example.com" \
NOTARY_TEAM_ID="TEAMID" \
NOTARY_PASSWORD="app-specific-password" \
./packaging/macos/build-dmg.sh
```

The launcher configuration is discovered next to the `.app` bundle (e.g. in
the DMG root) in addition to the normal locations; game launching and
`WoW.exe` patching stay disabled on macOS.

### Continuous integration / releases

GitHub Actions builds and ships the launcher (`.github/workflows/`):

- **CI** — on every push/PR: `uv sync --dev` + the full pytest suite.
- **Release** — on a `v*` tag push (`git tag v1.3 && git push origin v1.3`):
  builds Windows (onefile exe), Linux (AppImage) and macOS (universal2 DMG)
  in parallel, verifies the macOS binary contains both `arm64` and `x86_64`
  via `lipo`, uploads `SHA256SUMS` + the `octowow-config-example.json`
  sample, and creates a GitHub Release with auto-generated notes.

Linux AppImage tooling (`imagemagick`, `linuxdeploy`) is installed on the
runner; the macOS job installs a universal2 CPython from python.org so
PyInstaller can produce a universal2 bundle. Signing/notarization stay
opt-in — wire the workflow's `CODESIGN_IDENTITY` / `NOTARY_*` env vars to
repository secrets when you have an Apple Developer account.

### Notes

- Installing `certifi` before building bundles an up-to-date CA certificate
  set into the executable, so TLS verification works even on machines whose
  Windows root store is stale.
- **Linux runtime dependencies** — the Qt backend needs the system Qt
  libraries. On Debian/Ubuntu install `libegl1 libxcb-cursor0
  libxkbcommon-x11-0` plus the xcb platform-plugin dependencies (e.g.
  `libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0
  libxcb-shape0 libxcb-xinerama0`). Both X11 and Wayland sessions are
  supported.

---

## Development

The code lives in a classical `src/` package (`src/vanilla_wow_launcher/`)
split by layer. The only third-party runtime dependency is `PySide6`;
everything else is the standard library.

Architecture: the business logic lives in **toolkit-agnostic controllers**
that publish their results as dataclass *state* (`state/models.py`) and
*events* on a thread-safe dispatcher (`state/events.py`). The Qt side
(`ui/qt/bridge.py`) converts the events into Qt signals on the main thread.

| Package | Responsibility |
|---------|----------------|
| `vanilla_wow_launcher.cli` | Entry point; `config_store` wiring, backend selection, window loop |
| `vanilla_wow_launcher.core` | Constants, config store, TLS/HTTP, filesystem, helpers, logging, platform support, errors |
| `vanilla_wow_launcher.services` | Mods/addons/news/tweaks/client-update/self-update engines |
| `vanilla_wow_launcher.controllers` | Toolkit-agnostic orchestration: update, news, mods, addons, settings, tweaks |
| `vanilla_wow_launcher.state` | `models.py` (state dataclasses) + `events.py` (thread-safe dispatcher) |
| `vanilla_wow_launcher.ui.qt` | Qt UI: app shell, main window, bridge, theme, panels, dialogs |

The UI is Qt (PySide6) only — panels, dialogs, footer workflows and the
startup schedule are covered by tests.

### Running

```bash
uv run vanilla-wow-launcher             # console script
uv run python -m vanilla_wow_launcher   # equivalent module invocation
```

### Testing

Requires `uv`:

```bash
uv sync --dev
uv run pytest            # full suite; the Qt tests run headless (offscreen)
```

- **Qt offscreen tests** — all `tests/test_qt_*.py` run headlessly by default
  (they set `QT_QPA_PLATFORM=offscreen` before importing PySide6), so the
  whole Qt UI is exercised on any host / CI without a display.
- **Real-display checks** — `tests/test_qt_display.py` Part B only runs on a
  real X11/Wayland session when opted in:

  ```bash
  QT_QPA_PLATFORM=xcb RUN_QT_DISPLAY_TESTS=1 \
      uv run pytest tests/test_qt_display.py -k display
  ```

The human QA matrix for Qt display scaling is in
[docs/DISPLAY_TEST_MATRIX.md](docs/DISPLAY_TEST_MATRIX.md).

---

## Support the Developer

If Vanilla WoW Launcher is useful to you, consider supporting its
development:

- 💜 [Ko-fi](https://ko-fi.com/rebased)
- ☕ [Buy Me a Coffee](https://buymeacoffee.com/rebased)
- ☕ [Buy Me a Coffee — Ourouk](https://buymeacoffee.com/ourouk)

---

## License

See [LICENSE](LICENSE).

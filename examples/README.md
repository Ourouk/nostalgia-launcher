# Examples

This directory contains reference JSON for every file the launcher consumes. All
examples are **synthetic** — replace `launcher.example.com` / `example-community`
with your own hosts. Every URL must be `https://` (non-https is rejected) and
every file must be valid JSON that passes the validators in
`src/nostalgia_launcher/core/launcher.py:376` and
`src/nostalgia_launcher/services/catalog.py:222`.

## File map

| File | Purpose | Shape |
|------|---------|-------|
| `community.example.json` | Minimal `nostalgia_launcher.json` (quick-start) | object: `server` + optional `mods`/`addons`/`assets` |
| `community.example.full.json` | **Exhaustive** `nostalgia_launcher.json` — every possible field | same, all optional fields populated |
| `community.mods.example.json` | Minimal mod catalog (GitHub release + external-launcher) | `[{id, source, ...}]` |
| `community.mods.full.example.json` | Exhaustive mod catalog — one entry per `source.kind` | same |
| `community.addons.example.json` | Minimal addon catalog | `[{name, git, branch}]` |
| `community.addons.full.example.json` | Exhaustive addon catalog | same |
| `community.assets.example.json` | Minimal asset catalog | `[{id, url, dest}]` |
| `community.assets.full.example.json` | Exhaustive asset catalog | same |

The **minimal** files are the ones to copy when starting a new community.
The **full** files are the schema reference — they demonstrate every optional
field at least once.

## Launcher config (`nostalgia_launcher.json`)

Validated by `core/launcher._derive()` (`launcher.py:376-554`). The top-level
object may carry:

### `server` (required object)

| Field | Type | Validation | Notes |
|-------|------|------------|-------|
| `name` | `string` | trimmed, falls back to host | Display name |
| `url` | `https URL` | `launcher.py:310-324` | Identity/display only; `base_url` is a deprecated alias still honoured (`launcher.py:524`) |
| `realm` | `string` | trimmed | Shown to the user; may also be the `realm` for `tweaks` `Config.wtf` |
| `news_url` | `https URL` |  | News feed JSON |
| `featured_news_url` | `https URL` |  | Featured post JSON |
| `mods_registry_url` | `https URL` |  | Mod catalog URL |
| `addons_registry_url` | `https URL` |  | Singular addon catalog URL |
| `addons_registry_urls` | `[https URL]` | each `_https_url` | Ordered list — later entries override earlier by addon `name` (`services/addons.py:225`). When present and non-empty it wins over the singular. |
| `assets_registry_url` | `https URL` |  | Asset catalog URL |
| `torrent_root_marker` | `string` filename | `launcher.py:299-307` | File used to auto-detect the torrent root (parent of marker). Default `WoW.exe`. Must be a bare filename (no `/`, `\`, `..`). |
| `trusted_hosts` | `[string]` | `launcher.py:423-457` | Extra download hosts beyond auto-derived ones. Each entry may be a plain hostname (`cdn.example.com`) or a full `https://` URL (hostname extracted). Invalid entries are logged and ignored. |
| `download.update` | `bool` | default `true` | Server-level “should verify/update client” flag. Per-profile `client_update_enabled` wins (`launcher.py:982-995`). |
| `download.torrent.torrent_url` | `https URL` `.torrent` |  | BitTorrent snapshot — HTTPS wins over `magnet` when both present |
| `download.torrent.magnet` | `magnet:?xt=urn:btih/btmh:` | `launcher.py:326-366` validates `xt` topics | Magnet URI; `magnet`-only defaults to first-time-only (`torrent.update` absent → `bool(torrent_url)`) |
| `download.torrent.update` | `bool\|null` | omitted → inferred | When `false`, torrent is first-time-only even if `torrent_url` present. When `true`, forces incremental torrent updates. |
| `download.http.fallback` | `https URL` | `launcher.py:512` (`url` alias still accepted) | Single zip/rar fallback for first install when no `WoW.exe` present. `manifest`/`client` under `http` are hard-removed (`launcher.py:507-511`) |
| `download.content.type` | `"zip"\|"rar"\|"folder"` | `launcher.py:498-500` | How `fallback` is packaged. `folder` = already extracted; `zip`/`rar` = extracted by the launcher |

All endpoint URLs must be `https://` with a host; `http://`, missing-host,
or credential-embedded URLs are rejected. Hosts feed
`core/security_http.allowed_download_hosts()` (`launcher.py:173-182`).

### Top-level (outside `server`)

| Field | Type | Validation | Notes |
|-------|------|------------|-------|
| `discord_url` | `https URL\|null\|""` | `launcher.py:385-397` | When blank/missing → `null`. Non-empty non-https → hard error |
| `theme` | `{C_*: "#rrggbb", logo: https}` | `launcher.py:415-416` soft-ignored; `core/themes.py:19-48` `DEFAULT_COLORS` (28 keys) + `LOGO_KEY` | Any unknown key or non-hex value makes the whole theme fall back to defaults (`themes.py:106-127`). Cosmetic — never fails startup |
| `addon_git_hosts` | `[hostname]` | `launcher.py:268-283` validates `host` (no `/`, `\`, `:`, `..`, only alnum `.-`) | Extra git hosts beyond the built-in allowlist (`github.com`, `raw.githubusercontent.com`, `gitlab.com`, `gitea.com`, `codeberg.org` at `launcher.py:259-265`). **Top-level** in the current code; docs historically said `server.addon_git_hosts` — both are documented here for compat. |
| `mods` | `[object]` | only `dict` kept, sanitized in `services/mods` | Embedded catalog entries — same shape as the remote catalog. Override remote catalog by `id`; repo `custom` overrides both (`services/catalog.py:538-560`). |
| `addons` | `[object]` | same | Embedded addons — same shape as remote |
| `assets` | `[object]` | same | Embedded assets — same shape as remote |

At import time `launcher.persist()` splits `mods`/`addons`/`assets` out of the
document into per-profile local repos
(`local_<kind>_repo.json` shaped `{"server": [...], "custom": [...]}` at
`launcher.py:614-717`) — the persisted `nostalgia_launcher.json` keeps only
`server`/`theme`/`discord_url` etc.

### Minimal vs full

* **Minimal** (`community.example.json`): `server.name`+`url`+`download` with one
  torrent + HTTP fallback, one embedded entry per kind, singular
  `addons_registry_url`. Enough to run offline.
* **Full** (`community.example.full.json`): every field above populated,
  including plural `addons_registry_urls`, `trusted_hosts` (plain + URL form),
  `torrent_root_marker`, `addon_git_hosts`, `discord_url`, `theme` with
  several `C_*` slots + `logo`, `magnet` with tracker param, and 10
  embedded mods covering all 5 `source.kind` values (see below) plus varied
  addon/asset examples.

## Mod catalog entries

Validated by `services/catalog.validate_mod()` (`catalog.py:283-376`). Every
catalog — remote, embedded, or repo `custom` — must pass it.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | `string` safe_folder | yes | `core/safety.safe_folder` (`safety.py:74-85`) — no `/`/`\`, no `.`/`..`, not empty |
| `name` | `string` | no (defaults to `id`) | Display name |
| `description` | `string` | no | Falls back to `""` |
| `repo_url` | `https URL` | no | Homepage; non-https → `null` |
| `type` | `"mod"\|"external-launcher"` | no (default `mod`) | `catalog.py:54` `MOD_TYPES` |
| `installation` | `"required"\|"user_opt_in"` | no (default `user_opt_in`) | `catalog.py:55` `MOD_INSTALLATIONS`; `required` auto-installs (user may still disable) |
| `source` | `object` | yes | Dispatches to `services/sources` registry (`sources/base.py:84-94`). `kind` must be in `MOD_SOURCE_KINDS`. |
| `source.post_install` | `[hook]` | no | Only `["write_dxvk_conf"]` is currently allowlisted (`sources/hooks.py:43-69`), and only `mods` accept hooks (`sources/__init__.py:29-33`). |
| `register_dll` | `[safe_relative_path]` | no | Non-empty list (`catalog.py:346-356`); each entry `core/safety.safe_relative_path` (`safety.py:49-68`). Legacy single-string form is rejected. |
| `installed_files` | `[safe_relative_path]` | no | Files the mod owns (for uninstall/cleanup) |
| `executable` | `safe_relative_path` | no | For `type: external-launcher` — the exe the launcher starts |
| `client_versions` | `[string]` | no | WoW versions the mod supports |

### `source` by kind

**`github_release` / `codeberg_release`** (`sources/github_release.py:137-159`):

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `kind` | `"github_release"` / `"codeberg_release"` | yes | |
| `owner` | `string` safe_slug | yes | `core/safety.safe_slug` (`safety.py:197-206`) |
| `repo` | `string` safe_slug | yes | |
| `asset_pattern` | `string` glob | yes | `fnmatch` pattern (e.g. `mod-*.zip`) |
| `prefer_no` | `string\|null` | no | When set, candidates containing this substring are deprioritised |
| `extract_map` | `{pattern: dest}` | no | `core/safety.valid_extract_map` — each `dest` must be `safe_relative_path`. `fnmatch` used for tar (`sources/deploy.py:101-134`). |
| `version_from` | `"asset"\|null` | no | When `"asset"`, version is derived from the matched asset filename instead of `tag_name` (for repos with a static tag) |

**`direct_file` / `direct_tar`** (`sources/direct_file.py:41-76`):

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `kind` | `"direct_file"` / `"direct_tar"` | yes | `direct_tar` **requires** `extract_map` |
| `url` | `https URL` | yes | Validated `https_url` |
| `dest` | `safe_relative_path` | conditional | Required for streaming single-file mode; at least one of `dest` / `extract_map` must be present |
| `extract_map` | `{pattern: dest}` | conditional | Archive mode; whole archive fetched and members extracted |
| `pinned_version` | `string` | no | Version string for offline resolution |
| `sha1` | `string` 40-hex | no | `core/safety.valid_sha1` — malformed → whole entry rejected |
| `size` | `int>0` | no | Declared size — download rejected on mismatch |

`direct_file` streaming computes SHA-1 and enforces `size` while writing
(`sources/direct_file.py:97-142`).

**`git_archive`** (`sources/git_archive.py:124-139`):

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `kind` | `"git_archive"` | yes | |
| `git` | `https URL` | yes | Must be an allowlisted git host (`services/sources/git_archive.is_allowed_git_url`) |
| `branch` | `string` | no | Whitespace-free, no `..` |
| `ref` | `string` | no | Pin (tag/commit/branch) — wins over `branch` |

## Addon catalog entries

Validated by `services/catalog.validate_addon()` via `services/catalog_models.AddonModel` (`catalog.py:222-254`, `catalog_models.py:48-85`).

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | `string` safe_folder | yes (or `folder` legacy alias) | Addon folder name under `Interface/AddOns/` |
| `folder` | `string` safe_folder | alias | Legacy alias for `name`; `name` takes precedence |
| `git` | `string` https URL | no (but required to install) | Allowlisted hosts + `addon_git_hosts` |
| `branch` | `string` | no | Whitespace-free, no `..` |
| `ref` | `string` | no | Pin — wins over `branch` |
| `description` | `string` | no | |
| `toc` | `{Title, Notes, Interface}` | no | Only these three keys kept; others dropped (`catalog_models.py:80-84`) |
| `recommended` | `bool` | no (default `false`) | Shown as recommended |
| `blocked` | `bool` | no (default `false`) | When `true`, launcher refuses to install |

## Asset catalog entries

Validated by `services/catalog.validate_asset()` via `catalog_models.AssetModel` (`catalog.py:438-484`, `catalog_models.py:87-163`).

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | `string` safe_folder | yes | |
| `name` | `string` | no (defaults to `id`) | Display name |
| `description` | `string` | no | |
| `repo_url` | `https URL` | no | Homepage |
| `url` | `https URL` | yes | Download URL |
| `dest` | `string` safe_relative_path | yes | Relative install path inside the game folder |
| `version` | `string` | no | Version tag |
| `sha1` | `string` 40-hex | no | Integrity pin |
| `size` | `int>0` | no | Integrity pin |
| `essential` | `bool` | no (default `false`) | Auto-enabled when `true` |
| `probe` | `bool` | no (default `false`) | When `true`, response headers (`Last-Modified`/`ETag`) are captured for drift detection |

## Validation

```bash
uv run python -c "
import json
from nostalgia_launcher.core import launcher
from nostalgia_launcher.services import catalog
for path in ['examples/community.example.json','examples/community.example.full.json']:
    cfg, err = launcher.validate_path(path)
    assert err == '', f'{path}: {err}'
    print(path, 'OK', cfg.server_name)
for path in ['examples/community.mods.example.json','examples/community.mods.full.example.json']:
    for e in json.load(open(path)):
        assert catalog.validate_mod(e), f'bad mod {e.get(\"id\")}'
    print(path, 'OK')
for path in ['examples/community.addons.example.json','examples/community.addons.full.example.json']:
    for e in json.load(open(path)):
        assert catalog.validate_addon(e), f'bad addon {e.get(\"name\") or e.get(\"folder\")}'
    print(path, 'OK')
for path in ['examples/community.assets.example.json','examples/community.assets.full.example.json']:
    for e in json.load(open(path)):
        assert catalog.validate_asset(e), f'bad asset {e.get(\"id\")}'
    print(path, 'OK')
"
uv run pytest -m "not e2e"  # full gate
```

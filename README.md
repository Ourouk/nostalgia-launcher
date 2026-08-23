# Nostalgia Launcher

Nostalgia Launcher is a desktop application that helps you verify, update,
and configure a game installation — for example a Vanilla WoW (1.12.1)
client — against a **configuration that you supply yourself**. It is a local
tool: it does not include, host, or distribute any game files, and it does
not maintain or recommend any server directory.

> **What this project is not.** Nostalgia Launcher does not ship World of
> Warcraft game files, Blizzard artwork, or private-server lists. It does not
> operate game servers and does not point you at any particular community.
> You bring the installation and the configuration; the launcher applies it.

## What it does

- Verifies a selected game folder and updates only the files that changed,
  against a manifest **you configure**. The folder must be confirmed once in
  Settings — the launcher never downloads anywhere until you choose where,
  and the active folder stays visible in the footer.
- Bulk-downloads changed files over BitTorrent when your configuration
  advertises a snapshot, falling back to plain HTTP automatically.
- Installs and updates mods and addons from catalogs **your configuration
  points at** (Git hosts, validated by host allowlist).
- Applies common graphics, camera, sound, and gameplay preferences via
  `Config.wtf`.
- Shows news and announcements **only if your configuration provides a
  feed**.

The launcher starts from a **generic capability**, not a specific game
client: select a folder, import a configuration, review it, verify it, apply
it.

## Trust boundary

```
Nostalgia Launcher (local tool)
        │
        ▼
  a game installation you already own / selected
        │
        ▼
  a configuration you explicitly import
  (local .json file, or an https URL you type)
        │
        ▼
  optional, validated content/update sources
  (manifest, catalogs, news — all named by the config)
```

The core repository does not know which servers or communities exist. A
configuration is an untrusted input: it is validated (schema, HTTPS-only
URLs, host allowlist, path-traversal guards) before anything is downloaded
or written.

## Getting the app

Download the latest release from:

<https://github.com/Ourouk/nostalgia-launcher/releases/latest>

Each release includes per-platform packages plus a matching `.sha256`
checksum file. Verify the checksum when downloading from an untrusted or
mirrored source.

## First launch — import a configuration

The launcher has **no built-in server list**. On first launch it asks you to
import a launcher configuration:

- **Local file** — choose a `nostalgia_launcher.json` file supplied by your
  community.
- **URL** — paste an `https://` configuration URL your community provides.

Before anything is saved, the launcher shows a **summary** of the
configuration: the server name, the base URL, every host it will contact,
and which features are enabled (client updates, BitTorrent, news, mod/addon
catalogs, mirrors). Only accept a configuration from a source you trust.

You can also supply a configuration explicitly:

```text
NostalgiaLauncher --launcher-config PATH_OR_URL
```

(A URL here is treated like the wizard's URL field: fetched, validated, and
shown before use. An explicit file path that is missing or invalid is a
hard error.)

## Using the launcher

### Verification and updates

The launcher compares the files in your game folder against the manifest
**your configuration points at**, and downloads only the missing or changed
files. Downloads resume after an interruption and are re-checked after
downloading. This is a generic installation-verification capability; the
launcher never creates or distributes game content on its own.

### Mods and addons

The **MODS** and **ADDONS** tabs list the catalogs named by your
configuration. There is no universal built-in list — what you see depends
entirely on the configuration you imported.

### Tweaks

The **TWEAKS** tab writes preferences to `Config.wtf` only. The launcher
never binary-patches game executables; runtime client fixes (where used)
are left to loader mods installed by your configuration.

### News

The **NEWS** tab shows announcements **only if your configuration provides
a news feed**. With no feed configured, the tab is empty.

### Linux

On Linux the play action runs the Windows client through
[umu-launcher](https://github.com/Open-Wine-Components/umu-launcher) (the
Unified Launcher for Windows Games). The play button appears only when
`umu-run` is detected.

## Security and privacy

- Downloads use HTTPS with host restrictions derived from your
  configuration; redirects stay HTTPS-only; TLS is verified (≥ TLS 1.2).
- Metadata responses (manifests, catalogs, news, logos) are size-capped.
- Extracted archives are guarded against path traversal; installed files
  are confined to the selected game folder.
- The launcher retrieves content only from the URLs your configuration
  names. Review those URLs and hosts before using the launcher, and only
  import configurations from sources you trust.
- No telemetry or tracking is included.

## Data files

Settings, installation records, and caches live in your user profile:
Linux `~/.nostalgia-launcher`, Windows `%APPDATA%\NostalgiaLauncher`, macOS
`~/Library/Application Support/NostalgiaLauncher`. Deleting the settings
directory resets the launcher.

## Legal notices

World of Warcraft and Blizzard are trademarks or registered trademarks of
Blizzard Entertainment. Nostalgia Launcher is independent and is **not
affiliated with, endorsed by, or sponsored by Blizzard Entertainment**.

Nostalgia Launcher is a local configuration and update tool. It does not
create, host, own, or redistribute game files, mods, addons, or patches —
those are provided by the configuration you import and by the third parties
that operate the endpoints it names.

You are responsible for ensuring that your use of game software and of any
third-party services complies with applicable laws, licenses, and the terms
of those third parties. A technical design can reduce legal exposure, but it
does not guarantee legal compliance; compatibility with private servers may
still violate contractual terms or raise legal issues depending on your
jurisdiction.

## Attribution and license

Licensed under the Apache License, Version 2.0 — see [LICENSE](LICENSE).

This project was inspired by Octo Updater by rebasedkon
(<https://github.com/rebasedkon/octo-updater>).

Nostalgia Launcher is maintained by Andrea Spelgatti
(<spelgattiandrea@ourouk.be>).

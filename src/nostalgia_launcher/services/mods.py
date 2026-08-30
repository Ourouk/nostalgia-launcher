"""Mods engine: release lookup, install/uninstall, DLL wiring.

Mods are installed from their release archives and registered in dlls.txt.
The mod list comes from the launcher config's embedded "mods" list (when
present) and the mod catalog (launcher-configured or user-set URL), merged
with the per-user custom file on top — embedded entries override same-id
catalog entries, custom entries override both.

Payload acquisition is delegated to the shared source backends
(`services/sources`): this module orchestrates — backend fetch, deployment
by entry shape, dll registration and post-install hooks.
"""

import os
import time

from ..core.config_store import load_config
from ..core.filesystem import atomic_write_text as _atomic_write
from ..core.log_sink import log
from . import catalog
from .sources import deploy
from .sources import get as _source_get
from .sources import hooks as _hooks


def _checked_rel(dest_rel) -> str:
    """Validate a client-dir-relative install target (a release-asset name
    or a catalog `dest`) before it is joined onto `client_dir`. A
    compromised mod upstream must not be able to write outside the client
    folder via a crafted filename (`../../evil.dll`)."""
    return deploy.checked_rel(dest_rel)


# The per-user custom mod file (a JSON list, one entry per mod, using the
# same shape the mod catalog uses). Written empty on first use via Settings.


def catalog_timestamp() -> float | None:
    """When the mod catalog cache was last fetched (epoch), or None."""
    return catalog.catalog_timestamp("mods")


def has_remote_catalog() -> bool:
    """Whether a mod catalog is actually fetchable: a user URL override, or
    an explicitly launcher-configured URL. The base_url-derived default does
    not count — a config that embeds its mod list has nothing to refetch."""
    if catalog.get_registry_url("mods"):
        return True
    from ..core import launcher

    return launcher.mods_registry_url_explicit()


def embedded_mods() -> list:
    """Mods defined inline in the active launcher config (top-level
    "mods": […]), sanitized with the exact same validator as remote catalog
    entries; unusable entries are skipped with a logged warning. Network-
    free."""
    from ..core import launcher

    out = []
    for entry in launcher.embedded_mods():
        cleaned = catalog.validate_mod(entry)
        if cleaned is None:
            log(
                "  Launcher config: skipping invalid embedded mod"
                f" {entry.get('id')!r}.",
                "err",
            )
            continue
        out.append(cleaned)
    return out


def catalog_is_stale(now: float | None = None) -> bool:
    """Whether the cached catalog is missing or older than the weekly
    `catalog.CATALOG_TTL` — i.e. a background refresh is due. Always False
    when nothing is fetchable (no catalog URL) but mods exist locally:
    embedded in the launcher config or in the local repo file. Network-
    free."""
    if not has_remote_catalog() and (
        embedded_mods() or catalog.local_repo_has_entries("mods")
    ):
        return False
    ts = catalog_timestamp()
    if ts is None:
        return True
    now = now if now is not None else time.time()
    return (now - ts) >= catalog.CATALOG_TTL


def fetch_mods_catalog(force=False) -> list | None:
    """Mod catalog, cached in the config file ({"mods_catalog_cache":
    {"timestamp": epoch, "catalog": [...]}}) — see
    ``catalog.fetch_url_catalog`` for the caching/offline semantics."""
    return catalog.fetch_url_catalog(
        "mods", catalog.validate_mod, registry_url(), force=force
    )


def mods_registry(force=False) -> list:
    """The effective mod registry — see ``catalog.layered_registry`` for
    the layer order. Empty when nothing is configured."""
    return catalog.layered_registry(
        "mods",
        catalog.validate_mod,
        catalog.MOD_MERGE_FIELDS,
        embedded_mods(),
        remote=fetch_mods_catalog(force=force),
    )


def registry_url() -> str:
    """The active mod catalog URL: a user override (Settings), else the
    launcher-configured URL, else ''."""
    return catalog.get_registry_url("mods") or mods_registry_default_url()


def mods_registry_default_url() -> str:
    """The launcher-configured mod catalog URL ('' when not configured)."""
    from ..core import launcher

    return launcher.mods_registry_url()


def set_registry_url(url: str) -> str | None:
    """Validate and store a per-user catalog URL override (empty clears it);
    returns an error string or None on success."""
    return catalog.set_registry_url("mods", url)


def reset_registry_url():
    """Drop the per-user override so the launcher-configured URL is used."""
    catalog.reset_registry_url("mods")


# ── version lookup / install ─────────────────────────────────────────────────


def fetch_mod_latest_version_cached(
    mod: dict, force: bool = False
) -> str | None:
    """Latest version for one mod, via its source backend (release kinds
    serve the persistent cache within the TTL; direct kinds answer offline
    from their pin)."""
    src = mod["source"]
    try:
        backend = _source_get(src["kind"])
    except KeyError:
        return None
    return backend.resolve_version(mod, force=force)


def _fetch_release_cached(mod: dict, force: bool = False) -> dict | None:
    """Slim latest-release object for release-kind mods (persistent cache
    within the TTL). Helper for the controller's update worker."""
    src = mod["source"]
    if src["kind"] == "github_release":
        from .sources.codeberg_release import codeberg_latest  # noqa: F401
        from .sources.github_release import fetch_release_cached, github_latest

        return fetch_release_cached(
            mod["id"],
            lambda: github_latest(src["owner"], src["repo"]),
            force=force,
        )
    if src["kind"] == "codeberg_release":
        from .sources.codeberg_release import (
            codeberg_latest,
            fetch_release_cached,
        )

        return fetch_release_cached(
            mod["id"],
            lambda: codeberg_latest(src["owner"], src["repo"]),
            force=force,
        )
    return None


def install_mod(
    mod: dict, client_dir: str, release: dict | None = None
) -> list:
    """Install one mod: fetch via its source backend, deploy by entry shape,
    then run any declared post-install hooks. Returns the written relative
    paths."""
    src = mod["source"]
    # Validate any plain-dest target BEFORE downloading so a crafted
    # catalog path can't make us write outside the client folder.
    if src.get("extract_map") is None and src.get("dest"):
        deploy.checked_rel(src["dest"])
    backend = _source_get(src["kind"])
    result = backend.fetch(mod, client_dir=client_dir, release=release)

    written: list[str] = []
    emap = src.get("extract_map")
    if result.file is not None:
        # Streamed single-file payload already staged beside its destination.
        written.append(
            deploy.move_into_place(result.file.path, client_dir, src["dest"])
        )
    elif result.data is not None:
        if emap is None:
            written.append(
                deploy.install_plain(
                    client_dir,
                    result.data,
                    deploy.checked_rel(result.name),
                )
            )
        elif (result.name or "").endswith((".tar.gz", ".tgz")):
            written += deploy.extract_tar_map(client_dir, result.data, emap)
        else:
            written += deploy.extract_zip_map(
                client_dir, result.data, mod["id"], emap
            )
    else:
        raise RuntimeError(f"{src['kind']} produced no payload")

    # The controller records this as the installed version (release kinds
    # derive it from the tag or, with version_from:"asset", the filename).
    if result.version:
        mod["_resolved_version"] = result.version

    for hook_name in src.get("post_install", []):
        written += _hooks.run(hook_name, client_dir)

    return written


def uninstall_mod(mod: dict, client_dir: str):
    cfg = load_config()
    state = cfg.get("mods", {}).get(mod["id"], {})
    files = state.get("installed_files", mod.get("installed_files", []))
    if not isinstance(files, list):
        files = []
    for rel in files:
        # Recorded paths are bookkeeping data — re-validate before they are
        # ever joined onto the client dir (same gate as install).
        if not isinstance(rel, str) or not catalog.safe_relpath(rel):
            continue
        full = os.path.join(client_dir, rel)
        if os.path.exists(full):
            os.remove(full)
            log(f"  Removed {rel}")


def _dlls_txt_path(client_dir: str) -> str:
    return os.path.join(client_dir, "dlls.txt")


def read_dlls_entries(client_dir: str) -> set:
    """The lowercase, stripped names registered in dlls.txt — the entries the
    client actually loads. Empty when the file is absent or unreadable."""
    try:
        with open(_dlls_txt_path(client_dir), encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}
    except (OSError, UnicodeDecodeError):
        return set()


def registered_dlls(mod: dict) -> list:
    """The DLL names a catalog mod wires into dlls.txt, normalized to a
    list."""
    reg = mod.get("register_dll")
    if not isinstance(reg, list):
        return []
    return [d for d in reg if isinstance(d, str) and d]


def scan_unknown_mods(client_dir: str, registry: list) -> list:
    """dlls.txt entries no catalog mod claims (by register_dll, case-
    insensitive) — mods the client loads that the launcher doesn't track."""
    known = {
        d.strip().lower() for mod in registry for d in registered_dlls(mod)
    }
    return sorted(n for n in read_dlls_entries(client_dir) if n not in known)


def remove_unknown_mod(client_dir: str, name: str):
    """Uninstall an untracked mod straight from the filesystem: drop its
    dlls.txt line and delete the matching file when one exists."""
    name = name.strip()
    if not name:
        return
    path = _dlls_txt_path(client_dir)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            return
        lines = [
            line
            for line in content.splitlines()
            if line.strip().lower() != name.lower()
        ]
        try:
            if lines:
                _atomic_write(path, "\n".join(lines) + "\n")
            else:
                os.remove(path)
        except OSError as e:
            log(f"  Could not update dlls.txt: {e}", "err")
            return
    # dlls.txt is mod-written, so its entries are untrusted: never resolve
    # one to a path outside client_dir.
    if catalog.safe_relpath(name):
        full = os.path.join(client_dir, name)
        if os.path.exists(full):
            os.remove(full)
            log(f"  Removed {name}")


def add_dll(client_dir: str, name: str):
    path = _dlls_txt_path(client_dir)
    lines = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError) as e:
            log(f"  Could not read dlls.txt: {e}", "err")
            return
    if any(line.strip().lower() == name.lower() for line in lines):
        return
    if not catalog.safe_relpath(name.strip()):
        log(f"  Refusing unsafe dlls.txt entry: {name!r}")
        return
    lines = [line for line in lines if line.strip()] + [name]
    try:
        _atomic_write(path, "\n".join(lines) + "\n")
    except OSError as e:
        log(f"  Could not update dlls.txt: {e}", "err")


def remove_dll(client_dir: str, name: str):
    path = _dlls_txt_path(client_dir)
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            lines = [
                line
                for line in f.read().splitlines()
                if line.strip().lower() != name.lower()
            ]
    except (OSError, UnicodeDecodeError) as e:
        log(f"  Could not read dlls.txt: {e}", "err")
        return
    try:
        if not lines:
            os.remove(path)
        else:
            _atomic_write(path, "\n".join(lines) + "\n")
    except OSError as e:
        log(f"  Could not update dlls.txt: {e}", "err")


def mod_installed_files_present(mod: dict, client_dir: str) -> bool:
    """Filesystem-truth installed check for a catalog mod.

    The filesystem — not the config record — is the source of truth for what
    the client loads: a mod is installed when every file it declares (catalog
    ``installed_files`` first, then the config record) exists on disk, and a
    mod that registers DLLs has them all listed in dlls.txt. Falls back to
    the recorded version when nothing is verifiable on disk.
    """
    files = mod.get("installed_files")
    if not files:
        files = (
            load_config()
            .get("mods", {})
            .get(mod["id"], {})
            .get("installed_files", [])
        )
    regs_raw = registered_dlls(mod)
    regs = [d.lower() for d in regs_raw]
    entries = read_dlls_entries(client_dir)
    if files:
        if not all(os.path.exists(os.path.join(client_dir, f)) for f in files):
            return False
        return all(reg in entries for reg in regs)
    if (
        regs
        and all(reg in entries for reg in regs)
        and all(os.path.exists(os.path.join(client_dir, d)) for d in regs_raw)
    ):
        return True
    return bool(
        load_config()
        .get("mods", {})
        .get(mod["id"], {})
        .get("installed_version")
    )


def external_launcher_executables(client_dir: str) -> list:
    """Game executables provided by installed external-launcher mods, in
    registry order (first = highest precedence).

    Active means: ``type == "external-launcher"`` with a non-empty
    ``executable``, the installation policy satisfied ("required" mods are
    always active; "user_opt_in" only when their per-user record is enabled),
    AND the executable exists on disk. Anything else is excluded so launch
    falls back to WoW.exe — no implicit loader preference.
    """
    out = []
    records = load_config().get("mods", {})
    for mod in mods_registry():
        if mod.get("type") != "external-launcher":
            continue
        exe = mod.get("executable")
        if not exe:
            continue
        if mod.get("installation") != "required" and not records.get(
            mod["id"], {}
        ).get("enabled", False):
            continue
        if os.path.exists(os.path.join(client_dir, exe)):
            out.append(exe)
    return out


def mod_supports_update_check(mod: dict) -> bool:
    return mod["source"]["kind"] not in ("direct_file", "direct_tar")


def mod_update_available(mod: dict, state: dict, live: dict | None) -> bool:
    if not mod_supports_update_check(mod):
        return False
    if not state.get("enabled", False):
        return False
    installed_ver = state.get("installed_version")
    if not installed_ver:
        return False
    latest_ver = (live or {}).get("latest_version")
    if not latest_ver:
        return False
    return latest_ver != installed_ver

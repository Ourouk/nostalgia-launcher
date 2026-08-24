"""Shared catalog plumbing for the content verticals (mods/addons/assets).

All three registries follow the same model:

  * a remote HTTPS JSON catalog (fetched by the services in `services/mods.py`,
    `services/addons.py` and `services/assets.py`, cached in the config file
    so startup works offline),
  * a local repo file per kind (`local_<kind>_repo.json`, written by the
    import-time split) holding the server-imported entries plus user-added
    "custom" entries that survive re-imports,
  * entries embedded directly in the launcher config (only live for direct
    ``--launcher-config`` runs, which never persist), and
  * the optional per-user custom JSON file in the config directory — the
    legacy hand-edit escape hatch. Its entries are migrated into the
    repo's "custom" list when the repo is first created, after which the
    file is no longer loaded (it stays on disk as a backup) so stale copies
    can never shadow repo edits.

Merge precedence per kind: legacy custom file (only until the local repo
exists) > repo custom > embedded > repo server > remote catalog.

This module holds only the toolkit-agnostic, network-free pieces: catalog-URL
storage, custom-file resolution, local-repo access, entry validation and
merge precedence. Nothing from a JSON file is ever executed — the only
special behaviours a mod catalog may name are the registered source backends
/ post-install hooks (see `services/sources`), and download hosts are still
vetted by `security_http` at fetch time.
"""

import json
import os
from urllib.parse import urlsplit

from ..core import config_store, launcher
from ..core.log_sink import log
from ..core.platform_support import config_dir
from .sources import hooks as _hooks
from .sources import kinds as _source_kinds
from .sources.safety import safe_folder, safe_relpath  # noqa: F401 (re-export)

# Allowlisted mod source kinds / post-install hooks. A remote or custom mod
# entry can only reference these — it cannot name arbitrary code. Both come
# from the shared backend/hook registries (`services/sources`).
MOD_SOURCE_KINDS = set(_source_kinds())
MOD_POST_INSTALL_HOOKS = set(_hooks.names())

CUSTOM_FILE_TEMPLATE = "[\n]\n"

# Catalogs auto-refresh at most once a week: startup and panel loads serve
# the persisted cache instantly, and only a cache older than this TTL (or an
# explicit Settings → Reload / ⟳ refresh) hits the network again.
CATALOG_TTL = 7 * 86400


# ── catalog URL storage ──────────────────────────────────────────────────────


def get_registry_url(kind: str) -> str:
    """The per-user catalog URL override, or '' when the launcher-configured
    URL should be used instead."""
    return config_store.load_config().get(f"{kind}_registry_url") or ""


def set_registry_url(kind: str, url: str) -> str | None:
    """Validate and persist a per-user catalog URL override (HTTPS, no
    credentials). An empty value clears the override so the launcher URL is
    used again. Returns an error message, or None on success."""
    url = (url or "").strip().rstrip("/")
    if not url:
        config_store.update_config(
            lambda c: c.pop(f"{kind}_registry_url", None)
        )
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return "Invalid URL."
    if parts.scheme != "https":
        return "Catalog URL must use https."
    if not parts.hostname:
        return "Catalog URL is missing a host."
    if parts.username or parts.password:
        return "Catalog URL must not embed credentials."
    config_store.update_config(
        lambda c: c.__setitem__(f"{kind}_registry_url", url)
    )
    return None


def reset_registry_url(kind: str):
    """Drop the per-user override so the launcher-configured URL is used."""
    config_store.update_config(lambda c: c.pop(f"{kind}_registry_url", None))


# ── custom-file helpers ──────────────────────────────────────────────────────


def custom_file(kind: str) -> str:
    """Path of the per-user custom JSON file for a catalog kind."""
    return os.path.join(config_dir(), f"nostalgia_launcher_{kind}_custom.json")


def load_custom(kind: str, validator) -> list:
    """Load and validate the per-user custom file.

    Returns the validated entries (empty on a missing file or malformed
    JSON). Invalid entries are skipped with a logged warning rather than
    failing the whole load.
    """
    path = custom_file(kind)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        log(f"  {kind} custom file unreadable: {e}", "err")
        return []
    if not isinstance(raw, list):
        log(f"  {kind} custom file must contain a JSON list.", "err")
        return []
    out = []
    for entry in raw:
        cleaned = validator(entry) if isinstance(entry, dict) else None
        if cleaned is None:
            log(
                f"  {kind} custom file: skipping invalid entry {entry!r}",
                "err",
            )
            continue
        out.append(cleaned)
    return out


def write_custom_template(kind: str, template: str) -> bool:
    """Create the custom file from ``template`` when it doesn't exist yet."""
    path = custom_file(kind)
    if os.path.exists(path):
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(template)
    except OSError as e:
        log(f"  Could not create {kind} custom file: {e}", "err")
        return False
    return True


def clear_custom(kind: str) -> bool:
    """Delete the custom file. Returns True when something was removed."""
    path = custom_file(kind)
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError as e:
        log(f"  Could not clear {kind} custom file: {e}", "err")
    return False


# ── local repo files ─────────────────────────────────────────────────────────
# Each content kind (mods/addons/assets) lives in a single on-disk repo,
# written by the import-time split (`core.launcher._persist_data`) and
# extended in place by user-added entries: {"server": [...], "custom":
# [...]} — "server" mirrors the imported document (rewritten wholesale on
# every re-import), "custom" is user-owned and survives re-imports.


def read_local_repo(kind: str) -> dict:
    """The raw local repo for a content kind as ``{"server": [...],
    "custom": [...]}`` — entries are unvalidated; each service applies its
    own validator per layer. A missing repo file is seeded from the legacy
    per-user custom file (one-time migration; that file stays as a backup).
    A malformed repo degrades to empty lists with a logged error rather
    than breaking catalog loads."""
    if not os.path.exists(launcher.local_repo_path(kind)):
        legacy = launcher.legacy_custom_entries(kind)
        if legacy:
            try:
                launcher.store_local_repo(kind, [], legacy)
                log(
                    f"  Migrated {len(legacy)} {kind} entr"
                    f"{'y' if len(legacy) == 1 else 'ies'} from the legacy "
                    "custom file into the local repo."
                )
            except Exception as e:
                log(f"  Could not migrate the legacy {kind} customs: {e}")
    try:
        server, custom = launcher.load_local_repo(kind)
    except ValueError as e:
        log(f"  {kind} local repo unreadable: {e}", "err")
        return {"server": [], "custom": []}
    return {"server": server, "custom": custom}


def legacy_custom_layer(kind: str, validator) -> list:
    """The pre-repo custom-file layer. Empty once the kind's local repo
    exists — its "custom" list was seeded from that file at creation, so
    loading it afterwards would shadow repo edits and survives-clears with
    stale copies. The file itself stays on disk as a backup."""
    if os.path.exists(launcher.local_repo_path(kind)):
        return []
    return load_custom(kind, validator)


def write_local_repo(kind: str, server: list, custom: list) -> str | None:
    """Persist both lists back to a repo file. Returns an error message,
    or None on success."""
    try:
        launcher.store_local_repo(kind, server, custom)
    except Exception as e:
        return f"Could not save the local {kind} repo: {e}"
    return None


def add_custom_entry(kind: str, entry: dict) -> str | None:
    """Add (or replace, matching id/name) a user-added entry in a repo's
    "custom" list. Callers validate the entry first. Returns an error
    message, or None on success."""
    repo = read_local_repo(kind)

    def _key(e):
        return e.get("id") or e.get("name")

    key = _key(entry)
    custom = [e for e in repo["custom"] if _key(e) != key]
    custom.append(entry)
    return write_local_repo(kind, repo["server"], custom)


def clear_custom_entries(kind: str) -> bool:
    """Wipe only the user-added "custom" list of a repo — imported server
    entries are never touched. Returns True on success."""
    repo = read_local_repo(kind)
    return write_local_repo(kind, repo["server"], []) is None


def local_repo_has_entries(kind: str) -> bool:
    """Whether any entry — server-imported or user-custom — exists in a
    kind's local repo. Network-free."""
    repo = read_local_repo(kind)
    return bool(repo["server"] or repo["custom"])


def validate_entries(entries: list, validator, label: str) -> list:
    """Sanitize raw repo/embedded entries with a kind validator, skipping
    unusable ones with a logged warning rather than failing the load."""
    out = []
    for entry in entries:
        cleaned = validator(entry) if isinstance(entry, dict) else None
        if cleaned is None:
            log(f"  {label}: skipping invalid entry {entry!r}", "err")
            continue
        out.append(cleaned)
    return out


# ── shared validation helpers ────────────────────────────────────────────────
# safe_folder / safe_relpath are re-exported from sources.safety (imported
# above) so every consumer keeps its historical dotted path.


def safe_ref(v) -> str | None:
    """A branch/tag/ref string (whitespace-free, no traversal), else None."""
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not v or any(ch.isspace() for ch in v) or ".." in v:
        return None
    return v


def _https_url(u) -> str | None:
    from .sources.safety import https_url

    return https_url(u)


# ── addon entries ────────────────────────────────────────────────────────────


def validate_addon(entry: dict) -> dict | None:
    """Sanitize one addon catalog entry; None when unusable.

    The slim output carries the keys the ADDONS panel/installer consume,
    plus the optional ``recommended`` / ``blocked`` flags. Git hosts are
    vetted by the addons service (which owns the host allowlist), so they
    are only length-checked here.
    """
    name = ((entry.get("name") or entry.get("folder")) or "").strip()
    if not safe_folder(name):
        return None
    rec = {
        "name": name,
        "git": None,
        "branch": None,
        "ref": None,
        "description": None,
        "toc": {},
        "recommended": False,
        "blocked": False,
    }
    git = entry.get("git")
    if isinstance(git, str) and git.strip():
        rec["git"] = git.strip()
    rec["branch"] = safe_ref(entry.get("branch"))
    rec["ref"] = safe_ref(entry.get("ref"))
    desc = entry.get("description")
    rec["description"] = desc if isinstance(desc, str) else None
    toc = entry.get("toc")
    if isinstance(toc, dict):
        rec["toc"] = {
            k: toc[k] for k in ("Title", "Notes", "Interface") if k in toc
        }
    rec["recommended"] = bool(entry.get("recommended", False))
    rec["blocked"] = bool(entry.get("blocked", False))
    return rec


def merge_addons(remote: list, custom: list) -> list:
    """Custom addon entries override remote ones by folder name; new folders
    are appended. ``recommended`` / ``blocked`` flags OR together so a custom
    file can only add them."""
    by_folder = {a.get("name"): a for a in remote}
    for entry in custom:
        folder = entry.get("name")
        if not folder:
            continue
        base = by_folder.get(folder)
        if base is None:
            by_folder[folder] = dict(entry)
            continue
        for key in ("git", "branch", "ref", "description"):
            if entry.get(key) is not None:
                base[key] = entry[key]
        base["recommended"] = base.get("recommended") or entry.get(
            "recommended", False
        )
        base["blocked"] = base.get("blocked") or entry.get("blocked", False)
    return list(by_folder.values())


# ── mod entries ──────────────────────────────────────────────────────────────


def validate_mod(entry: dict) -> dict | None:
    """Sanitize one mod catalog entry into the shape the mod installer uses;
    None when unusable or when a field would break the installer.

    Kind-specific source validation is delegated to the registered backend
    (`services/sources`), so a new backend becomes catalog-usable without
    touching this function.
    """
    if not isinstance(entry, dict):
        return None
    mid = (entry.get("id") or "").strip()
    if not safe_folder(mid):
        return None
    name = (entry.get("name") or mid).strip()
    if not name:
        return None
    source = entry.get("source")
    if not isinstance(source, dict):
        return None
    kind = source.get("kind")
    if kind not in MOD_SOURCE_KINDS:
        return None

    mod = {
        "id": mid,
        "name": name,
        "essential": bool(entry.get("essential", False)),
        "description": (
            entry.get("description")
            if isinstance(entry.get("description"), str)
            else ""
        ),
        "repo_url": _https_url(entry.get("repo_url")),
        "source": {},
    }
    hooks = source.get("post_install") or []
    if hooks:
        if not isinstance(hooks, list) or not all(
            h in MOD_POST_INSTALL_HOOKS for h in hooks
        ):
            return None
        mod["source"]["post_install"] = list(hooks)

    from .sources import get as _source_get

    try:
        cleaned_source = _source_get(kind).validate(source)
    except Exception:
        return None
    if cleaned_source is None:
        return None
    # The backend owns everything under "source"; keep only its cleaned
    # view plus any hooks validated above.
    mod["source"].update(cleaned_source)
    if hooks and "post_install" not in mod["source"]:
        mod["source"]["post_install"] = list(hooks)

    register = entry.get("register_dll")
    if register is not None:
        if not isinstance(register, str) or not safe_relpath(register):
            return None
        mod["register_dll"] = register
    files = entry.get("installed_files")
    if files is not None:
        if not isinstance(files, list) or not all(
            isinstance(f, str) and safe_relpath(f) for f in files
        ):
            return None
        mod["installed_files"] = list(files)
    return mod


def merge_mods(remote: list, custom: list) -> list:
    """Custom mod entries override remote ones by id; new ids are appended."""
    by_id = {m["id"]: m for m in remote}
    for entry in custom:
        mid = entry.get("id")
        if not mid:
            continue
        base = by_id.get(mid)
        if base is None:
            by_id[mid] = dict(entry)
            continue
        for key in (
            "name",
            "description",
            "repo_url",
            "essential",
            "source",
            "register_dll",
            "installed_files",
        ):
            if entry.get(key) is not None:
                base[key] = entry[key]
    return list(by_id.values())


# ── asset entries ────────────────────────────────────────────────────────────


def _valid_sha1(value) -> str | None:
    """A lowercase 40-hex SHA-1 digest, or None when absent/invalid."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if len(v) != 40 or any(c not in "0123456789abcdef" for c in v):
        return None
    return v


def validate_asset(entry: dict) -> dict | None:
    """Sanitize one asset catalog entry (server content patches such as
    MPQs); None when unusable.

    An asset is a single file fetched from a pinned HTTPS URL into a safe
    relative destination inside the client folder. The optional integrity /
    update metadata (`sha1` / `size` / `version` / `probe`) drives both the
    download check and the staleness verdict — see `services/assets.py`
    for the exact precedence.
    """
    if not isinstance(entry, dict):
        return None
    aid = (entry.get("id") or "").strip()
    if not safe_folder(aid):
        return None
    name = (entry.get("name") or aid).strip()
    if not name:
        return None
    url = _https_url(entry.get("url"))
    dest = entry.get("dest")
    if not url or not (isinstance(dest, str) and safe_relpath(dest)):
        return None
    sha1 = _valid_sha1(entry.get("sha1"))
    raw_sha1 = entry.get("sha1")
    if raw_sha1 is not None and sha1 is None:
        return None  # a pin was given but it is malformed — refuse the entry
    size = entry.get("size")
    if size is not None and (
        isinstance(size, bool) or not isinstance(size, int) or size <= 0
    ):
        return None
    version = entry.get("version")
    version = version.strip() if isinstance(version, str) else None
    desc = entry.get("description")
    return {
        "id": aid,
        "name": name,
        "essential": bool(entry.get("essential", False)),
        "description": desc if isinstance(desc, str) else "",
        "repo_url": _https_url(entry.get("repo_url")),
        "url": url,
        "dest": dest,
        "version": version,
        "sha1": sha1,
        "size": size,
        "probe": bool(entry.get("probe", False)),
    }


def merge_assets(remote: list, custom: list) -> list:
    """Custom asset entries override remote ones by id; new ids append."""
    by_id = {a["id"]: a for a in remote}
    for entry in custom:
        aid = entry.get("id")
        if not aid:
            continue
        base = by_id.get(aid)
        if base is None:
            by_id[aid] = dict(entry)
            continue
        for key in (
            "name",
            "description",
            "repo_url",
            "essential",
            "url",
            "dest",
            "version",
            "sha1",
            "size",
            "probe",
        ):
            if entry.get(key) is not None:
                base[key] = entry[key]
    return list(by_id.values())

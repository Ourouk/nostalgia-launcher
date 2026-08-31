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
import time
import urllib.request
from urllib.parse import urlsplit

from ..core import config_store, launcher
from ..core.constants import UA
from ..core.log_sink import log
from ..core.safety import safe_folder, safe_relpath
from ..core.security_http import read_capped, secure_urlopen
from .sources import hooks as _hooks
from .sources import kinds as _source_kinds

# Allowlisted mod source kinds / post-install hooks. A remote or custom mod
# entry can only reference these — it cannot name arbitrary code. Both come
# from the shared backend/hook registries (`services/sources`).
MOD_SOURCE_KINDS = set(_source_kinds())
MOD_POST_INSTALL_HOOKS = set(_hooks.names())

# Mod discriminators. ``type`` says what a mod provides (a DLL drop-in vs
# the game executable itself); ``installation`` replaces the legacy
# ``essential`` boolean ("required" mods auto-install by default — an
# explicit user opt-out is always respected). A catalog entry carrying the
# legacy ``essential: true`` is translated to ``installation: "required"``.
MOD_TYPES = ("mod", "external-launcher")
MOD_INSTALLATIONS = ("required", "user_opt_in")

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


# ── local repo files ─────────────────────────────────────────────────────────
# Each content kind (mods/addons/assets) lives in a single on-disk repo,
# written by the import-time split (`core.launcher._persist_data`) and
# extended in place by user-added entries: {"server": [...], "custom":
# [...]} — "server" mirrors the imported document (rewritten wholesale on
# every re-import), "custom" is user-owned and survives re-imports.


def read_local_repo(kind: str) -> dict:
    """The raw local repo for a content kind as ``{"server": [...],
    "custom": [...]}`` — entries are unvalidated; each service applies its
    own validator per layer. A malformed repo degrades to empty lists with
    a logged error rather than breaking catalog loads."""
    try:
        server, custom = launcher.load_local_repo(kind)
    except ValueError as e:
        log(f"  {kind} local repo unreadable: {e}", "err")
        return {"server": [], "custom": []}
    return {"server": server, "custom": custom}


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
    unusable ones with a logged warning rather than failing the load. A
    validator crash on a poisoned entry is contained the same way."""
    out = []
    for entry in entries:
        cleaned = _validate_entry_safe(entry, validator, label)
        if cleaned is not None:
            out.append(cleaned)
    return out


def _validate_entry_safe(entry, validator, label: str):
    """`validator(entry)` with log-and-skip on any failure: one malformed
    entry must never take down a whole catalog/repo load."""
    try:
        cleaned = validator(entry) if isinstance(entry, dict) else None
    except Exception as e:
        log(f"  {label}: skipping invalid entry {entry!r} ({e})", "err")
        return None
    if cleaned is None:
        log(f"  {label}: skipping invalid entry {entry!r}", "err")
        return None
    return cleaned


# ── shared validation helpers ────────────────────────────────────────────────
# safe_folder / safe_relpath are re-exported from sources.safety (imported
# above) so every consumer keeps its historical dotted path.


def _text(v) -> str:
    """A catalog string field coerced to a stripped str: truthy non-strings
    (numbers, bools) become "" so the `or` fallbacks below stay effective
    instead of crashing on .strip()."""
    return v.strip() if isinstance(v, str) else ""


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

    Delegates to :class:`catalog_models.AddonModel` (Pydantic v2) which
    composes ``safe_folder``/``safe_ref`` via ``AfterValidator``.
    """
    try:
        from .catalog_models import AddonModel

        name = _text(entry.get("name") or entry.get("folder"))
        payload = {
            "name": name,
            "git": entry.get("git"),
            "branch": entry.get("branch"),
            "ref": entry.get("ref"),
            "description": entry.get("description"),
            "toc": entry.get("toc"),
            "recommended": bool(entry.get("recommended", False)),
            "blocked": bool(entry.get("blocked", False)),
        }
        m = AddonModel.model_validate(payload)
        return {
            "name": m.name,
            "git": m.git,
            "branch": m.branch,
            "ref": m.ref,
            "description": m.description,
            "toc": m.toc or {},
            "recommended": bool(m.recommended),
            "blocked": bool(m.blocked),
        }
    except Exception:
        return None


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
    mid = _text(entry.get("id"))
    if not safe_folder(mid):
        return None
    name = _text(entry.get("name") or mid)
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
        # Explicit null behaves like absence (the documented "default mod").
        "type": entry.get("type") or "mod",
        "installation": _mod_installation(entry),
        "description": (
            entry.get("description")
            if isinstance(entry.get("description"), str)
            else ""
        ),
        "repo_url": _https_url(entry.get("repo_url")),
        "source": {},
    }
    if mod["type"] not in MOD_TYPES:
        return None
    if mod["installation"] not in MOD_INSTALLATIONS:
        return None
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
        if (
            not isinstance(register, list)
            or not register
            or not all(
                isinstance(d, str) and safe_relpath(d) for d in register
            )
        ):
            return None
        mod["register_dll"] = list(register)
    files = entry.get("installed_files")
    if files is not None:
        if not isinstance(files, list) or not all(
            isinstance(f, str) and safe_relpath(f) for f in files
        ):
            return None
        mod["installed_files"] = list(files)
    executable = entry.get("executable")
    if executable is not None:
        if not isinstance(executable, str) or not safe_relpath(executable):
            return None
        mod["executable"] = executable
    client_versions = entry.get("client_versions")
    if client_versions is not None:
        if not isinstance(client_versions, list) or not all(
            isinstance(v, str) for v in client_versions
        ):
            return None
        mod["client_versions"] = list(client_versions)
    return mod


def _mod_installation(entry: dict) -> str:
    """The effective installation policy: the ``installation`` field when
    it is a string, else the "user_opt_in" default."""
    installation = entry.get("installation")
    if isinstance(installation, str):
        return installation.lower()
    return "user_opt_in"


# Fields a kind's custom layer may override on an existing (remote/server)
# entry; absent or null values leave the base copy untouched.
MOD_MERGE_FIELDS = (
    "name",
    "description",
    "repo_url",
    "type",
    "installation",
    "source",
    "register_dll",
    "installed_files",
    "executable",
    "client_versions",
)

ASSET_MERGE_FIELDS = (
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
)


def merge_by_key(remote: list, custom: list, fields) -> list:
    """Custom entries override remote ones by id (only `fields`, when set);
    new ids are appended."""
    by_id = {e["id"]: e for e in remote}
    for entry in custom:
        eid = entry.get("id")
        if not eid:
            continue
        base = by_id.get(eid)
        if base is None:
            by_id[eid] = dict(entry)
            continue
        for key in fields:
            if entry.get(key) is not None:
                base[key] = entry[key]
    return list(by_id.values())


# ── asset entries ────────────────────────────────────────────────────────────


def validate_asset(entry: dict) -> dict | None:
    """Sanitize one asset catalog entry; None when unusable.

    Delegates to :class:`catalog_models.AssetModel` (Pydantic v2) which
    composes ``safe_folder``/``safe_relpath``/``valid_sha1`` via validators.
    """
    if not isinstance(entry, dict):
        return None
    try:
        from .catalog_models import AssetModel

        payload = {
            "id": _text(entry.get("id")),
            "name": _text(entry.get("name") or entry.get("id")),
            "essential": bool(entry.get("essential", False)),
            "description": entry.get("description"),
            "repo_url": entry.get("repo_url"),
            "url": entry.get("url"),
            "dest": entry.get("dest"),
            "version": entry.get("version"),
            "sha1": entry.get("sha1"),
            "size": entry.get("size"),
            "probe": bool(entry.get("probe", False)),
        }
        # Preserve malformed-sha1 rejection: raw present but normalized None
        raw_sha1 = entry.get("sha1")
        if raw_sha1 is not None:
            from ..core.safety import valid_sha1 as _vs

            if _vs(raw_sha1) is None:
                return None
        m = AssetModel.model_validate(payload)
        return {
            "id": m.id,
            "name": m.name,
            "essential": bool(m.essential),
            "description": m.description or "",
            "repo_url": m.repo_url,
            "url": m.url,
            "dest": m.dest,
            "version": m.version,
            "sha1": m.sha1,
            "size": m.size,
            "probe": bool(m.probe),
        }
    except Exception:
        return None


def merge_assets(remote: list, custom: list) -> list:
    """Custom asset entries override remote ones by id; new ids append."""
    return merge_by_key(remote, custom, ASSET_MERGE_FIELDS)


# ── generic catalog fetch + layered registry ─────────────────────────────────


def fetch_url_catalog(
    kind: str, validator, url: str, *, force: bool = False
) -> list | None:
    """JSON-list catalog at ``url``, validated per-entry and cached in the
    config file ({"<kind>_catalog_cache": {"timestamp": epoch,
    "catalog": [...]}}).

    Non-forced calls never hit the network when a cached copy exists, and
    return None (→ an empty registry) when there is none yet, so a first run
    is fully offline-safe. Forced calls always fetch and raise when the URL
    is unset or the network fails with nothing cached.
    """
    now = time.time()
    key = f"{kind}_catalog_cache"
    entry = config_store.load_config().get(key)
    if not isinstance(entry, dict):
        entry = {}
    cached = entry.get("catalog")
    if not force:
        return cached if cached is not None else None
    if not url:
        raise RuntimeError(
            f"{kind.capitalize()} catalog URL is not configured."
        )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            raw = json.loads(read_capped(r, 2 * 1024 * 1024))
    except Exception:
        if cached is not None:
            return cached
        raise
    validated = []
    for e in raw if isinstance(raw, list) else []:
        cleaned = _validate_entry_safe(e, validator, kind)
        if cleaned is not None:
            validated.append(cleaned)
    config_store.update_config(
        lambda c: c.__setitem__(key, {"timestamp": now, "catalog": validated})
    )
    return validated


def layered_registry(
    kind: str,
    validator,
    fields,
    embedded_entries: list,
    *,
    remote: list | None,
) -> list:
    """The effective registry for a content kind, in override order (later
    wins by id): the remote/cached catalog < the local repo's server-imported
    entries < the launcher config's embedded entries < the repo's user-custom
    entries. Empty when nothing is configured."""
    base = [] if remote is None else remote
    repo = read_local_repo(kind)
    label = f"local {kind} repo"
    merged = base
    for layer in (
        validate_entries(repo["server"], validator, label),
        embedded_entries,
        validate_entries(repo["custom"], validator, label),
    ):
        merged = merge_by_key(merged, layer, fields)
    return merged


def catalog_timestamp(kind: str) -> float | None:
    """When the kind's catalog cache was last fetched (epoch), or None."""
    entry = config_store.load_config().get(f"{kind}_catalog_cache")
    if not isinstance(entry, dict):
        return None
    ts = entry.get("timestamp")
    return ts if isinstance(ts, (int, float)) and ts > 0 else None

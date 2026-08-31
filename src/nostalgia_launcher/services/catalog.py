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
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from ..core import config_store, launcher
from ..core.log_sink import log
from ..core.security_http import _check_url, make_secure_client
from .sources import hooks as _hooks
from .sources import kinds as _source_kinds
from .sources.safety import (  # noqa: F401 (safe_folder re-exported)
    HttpsUrlStr,
    SafeFolderStr,
    SafeRelPathStr,
    Sha1Str,
    safe_folder,
    safe_relpath,
)
from .sources.safety import (
    valid_sha1 as _valid_sha1,
)

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


# ── Pydantic models — authoritative schema for each catalog kind ────────────


class _AddonModel(BaseModel):
    """Pydantic model for an addon entry. Mirrors ``validate_addon``."""

    model_config = ConfigDict(extra="ignore")

    name: SafeFolderStr
    git: str | None = None
    branch: str | None = None
    ref: str | None = None
    description: str | None = None
    toc: dict = Field(default_factory=dict)
    recommended: bool = False
    blocked: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: object) -> object:
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        raw = dict(data)
        # name may be in "folder" for legacy entries
        name = _text(raw.get("name") or raw.get("folder"))
        raw["name"] = name
        # git: any non-empty string trimmed, otherwise None
        git = raw.get("git")
        if isinstance(git, str) and git.strip():
            raw["git"] = git.strip()
        else:
            raw["git"] = None
        # branch/ref normalized via safe_ref (invalid → None)
        raw["branch"] = safe_ref(raw.get("branch"))
        raw["ref"] = safe_ref(raw.get("ref"))
        # description: keep only str, else None
        desc = raw.get("description")
        raw["description"] = desc if isinstance(desc, str) else None
        # toc: keep only allowlisted keys
        toc = raw.get("toc")
        if isinstance(toc, dict):
            raw["toc"] = {
                k: toc[k] for k in ("Title", "Notes", "Interface") if k in toc
            }
        else:
            raw["toc"] = {}
        raw["recommended"] = bool(raw.get("recommended", False))
        raw["blocked"] = bool(raw.get("blocked", False))
        return raw


class _AssetModel(BaseModel):
    """Pydantic model for an asset entry. Mirrors ``validate_asset``."""

    model_config = ConfigDict(extra="ignore")

    id: SafeFolderStr
    name: str
    essential: bool = False
    description: str = ""
    repo_url: str | None = None
    url: HttpsUrlStr
    dest: SafeRelPathStr
    version: str | None = None
    sha1: str | None = None
    size: int | None = None
    probe: bool = False

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: object) -> object:
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        raw = dict(data)
        aid = _text(raw.get("id"))
        raw["id"] = aid
        # name defaults to id
        name = _text(raw.get("name") or aid)
        raw["name"] = name
        # repo_url: https only or None
        raw["repo_url"] = _https_url(raw.get("repo_url"))
        # url/dest handled by field types; keep raw for validation
        # version: strip str or None
        ver = raw.get("version")
        raw["version"] = ver.strip() if isinstance(ver, str) else None
        # sha1: normalize, but malformed pin must fail validation
        sha = _valid_sha1(raw.get("sha1"))
        raw_sha = raw.get("sha1")
        if raw_sha is not None and sha is None:
            raise ValueError("malformed sha1")
        raw["sha1"] = sha
        # size: must be int >0, not bool, else None; invalid fails
        size = raw.get("size")
        if size is not None:
            if isinstance(size, bool) or not isinstance(size, int):
                raise ValueError("size must be int")
            if size <= 0:
                raise ValueError("size must be >0")
        # description: str or ""
        desc = raw.get("description")
        raw["description"] = desc if isinstance(desc, str) else ""
        raw["essential"] = bool(raw.get("essential", False))
        raw["probe"] = bool(raw.get("probe", False))
        return raw


class _ModModel(BaseModel):
    """Pydantic model for a mod entry. Mirrors ``validate_mod``."""

    model_config = ConfigDict(extra="ignore")

    id: SafeFolderStr
    name: str
    type: Literal["mod", "external-launcher"] = "mod"
    installation: Literal["required", "user_opt_in"] = "user_opt_in"
    description: str = ""
    repo_url: str | None = None
    source: dict
    register_dll: list[SafeRelPathStr] | None = None
    installed_files: list[SafeRelPathStr] | None = None
    executable: SafeRelPathStr | None = None
    client_versions: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: object) -> object:
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        raw = dict(data)
        mid = _text(raw.get("id"))
        raw["id"] = mid
        name = _text(raw.get("name") or mid)
        if not name:
            raise ValueError("missing name")
        raw["name"] = name
        # type / installation with defaults & normalization
        t = raw.get("type") or "mod"
        raw["type"] = t if isinstance(t, str) else "mod"
        inst = raw.get("installation")
        if isinstance(inst, str):
            raw["installation"] = inst.lower()
        else:
            raw["installation"] = "user_opt_in"
        # description
        desc = raw.get("description")
        raw["description"] = desc if isinstance(desc, str) else ""
        raw["repo_url"] = _https_url(raw.get("repo_url"))
        # source kind must be allowlisted and validated via backend
        src = raw.get("source")
        if not isinstance(src, dict):
            raise ValueError("missing source")
        kind = src.get("kind")
        if kind not in MOD_SOURCE_KINDS:
            raise ValueError("unknown source kind")
        # delegate to backend validator
        from .sources import get as _source_get

        try:
            cleaned = _source_get(kind).validate(src)
        except Exception as e:
            raise ValueError(f"source validate failed: {e}") from e
        if cleaned is None:
            raise ValueError("source invalid")
        # Merge hooks validation
        hooks = src.get("post_install") or []
        if hooks:
            if not isinstance(hooks, list) or not all(
                h in MOD_POST_INSTALL_HOOKS for h in hooks
            ):
                raise ValueError("invalid post_install")
            # backend may have already cleaned; ensure present
            cleaned = dict(cleaned)
            if "post_install" not in cleaned:
                cleaned["post_install"] = list(hooks)
            else:
                # keep backend's cleaned version
                pass
        raw["source"] = cleaned
        # optional list fields with validation
        reg = raw.get("register_dll")
        if reg is not None:
            if (
                not isinstance(reg, list)
                or not reg
                or not all(isinstance(d, str) and safe_relpath(d) for d in reg)
            ):
                raise ValueError("invalid register_dll")
        files = raw.get("installed_files")
        if files is not None:
            if not isinstance(files, list) or not all(
                isinstance(f, str) and safe_relpath(f) for f in files
            ):
                raise ValueError("invalid installed_files")
        exe = raw.get("executable")
        if exe is not None:
            if not isinstance(exe, str) or not safe_relpath(exe):
                raise ValueError("invalid executable")
        cvs = raw.get("client_versions")
        if cvs is not None:
            if not isinstance(cvs, list) or not all(
                isinstance(v, str) for v in cvs
            ):
                raise ValueError("invalid client_versions")
        return raw


# ── addon entries ────────────────────────────────────────────────────────────


def validate_addon(entry: dict) -> dict | None:
    """Sanitize one addon catalog entry; None when unusable.

    Delegates structural validation to :class:`_AddonModel` — one
    authoritative schema instead of duplicated procedural checks.
    """
    try:
        return _AddonModel.model_validate(entry).model_dump()
    except (ValidationError, ValueError, TypeError):
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
    """Sanitize one mod catalog entry via :class:`_ModModel`."""
    try:
        m = _ModModel.model_validate(entry)
        data = m.model_dump(exclude_none=True)
        # ``repo_url`` was historically always present (None when invalid)
        # — ensure it survives ``exclude_none``.
        if "repo_url" not in data:
            data["repo_url"] = None
        return data
    except (ValidationError, ValueError, TypeError):
        return None


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
    """Sanitize one asset catalog entry via :class:`_AssetModel`."""
    try:
        return _AssetModel.model_validate(entry).model_dump()
    except (ValidationError, ValueError, TypeError):
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
        _check_url(url, None)
        with make_secure_client(timeout=10) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if len(resp.content) > 2 * 1024 * 1024:
                raise RuntimeError(
                    f"Response exceeded the {2 * 1024} KiB limit."
                )
            raw = json.loads(resp.content)
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

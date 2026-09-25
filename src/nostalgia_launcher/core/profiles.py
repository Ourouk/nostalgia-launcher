"""Launcher profiles — one profile per server, fully isolated state.

A profile owns its own server config (`launcher.json`), state store
(`state.json`), hash cache, custom catalogs, torrent metadata and logo
cache under ``<config_dir>/profiles/<name>/``. The profile name always
derives from its server's name (``profile_name_for`` + ``unique_name``);
there is no reserved default — a fresh install has no profiles at all
and the import wizard creates the first one.

Which profile an artifact resolves to is decided once per process by
``activate(resolve(...))`` at CLI startup; services consult
``profiles.active()``. One active profile at a time — switching restarts
the app. Pure stdlib, mirrors the `config_store` style (module-global
resolved once + atomic tmp+rename writes).
"""

import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from .filesystem import atomic_write_text as _atomic_write_text
from .launcher import CONTENT_KINDS
from .platform_support import config_dir

DEFAULT_PROFILE = "default"

# Legacy directory name from when installs started on an implicit
# "default" profile. Only used by `adopt_legacy_default()`; "default"
# is otherwise an ordinary profile name with no special semantics.

# 1–32 chars: start alphanumeric, then letters/digits/space/._- . Trailing
# dot or space is rejected separately (Windows path-hostile).
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}\Z")

_INDEX_LOCK = threading.RLock()

_ACTIVE: "Profile | None" = None


class ProfileError(Exception):
    """Unknown or invalid profile name."""


@dataclass(frozen=True)
class Profile:
    """A named profile and its artifact paths.

    ``root`` is ``<config_dir>/profiles/<name>`` for every profile.
    """

    name: str
    root: str

    def launcher_path(self) -> str:
        """The profile's server-config file (LAUNCHER_FILE schema)."""
        return os.path.join(self.root, "launcher.json")

    def state_path(self) -> str:
        """The profile's state store."""
        return os.path.join(self.root, "state.json")

    def cache_path(self) -> str:
        """The profile's hash-cache file."""
        return os.path.join(self.root, "hash_cache.json")

    def local_repo_path(self, kind: str) -> str:
        """The content-kind local repo file (launcher CONTENT_KINDS):
        `{"server": [...], "custom": [...]}` written by the import-time
        split."""
        return os.path.join(self.root, f"local_{kind}_repo.json")

    def torrents_dir(self) -> str:
        """Torrent metadata/resume directory for this profile."""
        return os.path.join(self.root, "torrents")

    def logo_path(self) -> str:
        """Cached server-logo image for this profile."""
        return os.path.join(self.root, "launcher_logo.img")


# ── name validation ──────────────────────────────────────────────────────────


def validate_name(name: str) -> str:
    """Return "" when ``name`` is a valid profile name, else a
    human-readable error message."""
    if not name:
        return "Profile name cannot be empty."
    if name[-1] in ". ":
        return "Profile name cannot end with a dot or space."
    if not _NAME_RE.match(name):
        return (
            "Use 1-32 characters: letters, digits, spaces, dots, "
            "underscores or hyphens, starting with a letter or digit."
        )
    return ""


# ── server-derived names ──────────────────────────────────────────────────


def slugify(raw: object) -> str:
    """Collapse an arbitrary server name into a valid profile-name stem.

    Whitespace runs become single spaces, characters outside the name
    grammar are dropped, and the result is stripped/trailing-cleaned and
    truncated to the 32-char cap. Returns "" when nothing usable remains.
    The output always passes ``validate_name`` (or is "").
    """
    if not isinstance(raw, str):
        return ""
    s = re.sub(r"\s+", " ", raw).strip()
    s = "".join(c for c in s if c.isascii() and (c.isalnum() or c in " _.-"))
    s = s.strip().lstrip(".").strip()
    # Must start alphanumeric: drop leading separators left over.
    s = re.sub(r"^[^A-Za-z0-9]+", "", s)
    s = s[:32].rstrip(". ")
    if not s or not _NAME_RE.match(s):
        return ""
    return s


def profile_name_for(server_name: object = "", host: object = "") -> str:
    """Derive a profile-name stem from a server name, else its host.

    Falls back to ``"Server"`` when neither yields a usable stem (an
    unnamed config with no host). Always passes ``validate_name``.
    """
    return slugify(server_name) or slugify(host) or "Server"


def unique_name(base: str) -> str:
    """First free ``<base>``, ``<base> 2``, … (truncated to the 32-char
    cap). A second import of the same server lands on ``<Server> 2``
    instead of colliding."""
    base = (base or "").strip() or "Server"
    if not _NAME_RE.match(base):  # defensive: callers pass slug output
        base = slugify(base) or "Server"
    if not os.path.isdir(profile_root(base)):
        return base
    i = 2
    while True:
        suffix = f" {i}"
        stem = base[: 32 - len(suffix)].rstrip(". ")
        candidate = (stem + suffix) or f"Server{suffix}"
        if not os.path.isdir(profile_root(candidate)):
            return candidate
        i += 1


# ── index (profiles.json) ────────────────────────────────────────────────────


def profiles_root() -> str:
    """Directory holding every profile."""
    return os.path.join(config_dir(), "profiles")


def index_path() -> str:
    """The profile registry: {"active": <name>, "order": [<names>]}."""
    return os.path.join(config_dir(), "profiles.json")


def profile_root(name: str) -> str:
    return os.path.join(profiles_root(), name)


def _scan_profile_dirs() -> list:
    """Names backed by a directory under profiles/ (sorted). Every
    directory counts — there is no implicit or reserved profile."""
    try:
        entries = sorted(os.listdir(profiles_root()))
    except OSError:
        return []
    return [e for e in entries if os.path.isdir(profile_root(e))]


def load_index() -> dict:
    """Tolerant index load. A missing/corrupt file or ghost pointers are
    recovered from a directory scan — startup must never crash over the
    index. ``active`` is "" when nothing on disk can back it."""
    active = ""
    order = []
    try:
        with open(index_path(), encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            if isinstance(raw.get("active"), str):
                active = raw["active"]
            if isinstance(raw.get("order"), list):
                order = [n for n in raw["order"] if isinstance(n, str) and n]
    except (OSError, ValueError):
        pass
    disk = _scan_profile_dirs()
    merged = []
    for n in order + disk:
        # Keep only names that exist on disk.
        if n in disk and n not in merged:
            merged.append(n)
    if active not in disk:
        active = merged[0] if merged else ""
    return {"active": active, "order": merged}


def save_index(idx: dict):
    """Atomically persist the index (last-writer-wins on races — single-
    user desktop tool; see docs)."""
    payload = {
        "active": idx.get("active", ""),
        "order": list(idx.get("order", [])),
    }
    _atomic_write_text(index_path(), json.dumps(payload, indent=2))


# ── management API ───────────────────────────────────────────────────────────


def list_profiles() -> list:
    """All known profile names: union of index.order and directories on
    disk (index order first, then any stray directories)."""
    with _INDEX_LOCK:
        names: list = []
        for n in load_index()["order"] + _scan_profile_dirs():
            if n not in names:
                names.append(n)
        return names


def create(name: str, launcher_json_text: str = "") -> tuple:
    """Create a profile directory (optionally seeding launcher.json with
    already-validated config *text*). Returns (profile, ""); exactly one
    of profile/error-message is set."""
    err = validate_name(name)
    if err:
        return None, err
    root = profile_root(name)
    with _INDEX_LOCK:
        if os.path.exists(root):
            return None, f"Profile already exists: {name}"
        try:
            os.makedirs(root)
        except OSError as e:
            return None, f"Could not create the profile directory: {e}"
        if launcher_json_text:
            try:
                _atomic_write_text(
                    os.path.join(root, "launcher.json"), launcher_json_text
                )
            except OSError as e:
                shutil.rmtree(root, ignore_errors=True)
                return None, f"Could not write the profile config: {e}"
        idx = load_index()
        if name not in idx["order"]:
            idx["order"].append(name)
            save_index(idx)
    return Profile(name, root), ""


def delete(name: str) -> str:
    """Delete a profile. Resets the active pointer BEFORE removing the
    directory so a failed rmtree can never leave the index pointing at
    deleted state; the pointer falls back to the first remaining
    profile ("" when none is left — the next launch opens the import
    wizard). Returns "" on success."""
    if _existing_or_none(name) is None:
        return f"Unknown profile: {name}"
    with _INDEX_LOCK:
        idx = load_index()
        idx["order"] = [n for n in idx["order"] if n != name]
        if idx["active"] == name:
            idx["active"] = idx["order"][0] if idx["order"] else ""
        save_index(idx)
        try:
            shutil.rmtree(profile_root(name))
        except OSError as e:
            # Pointer is already safe; the leftover dir resurfaces in
            # list_profiles() until removed, which is honest.
            return f"Could not delete the profile directory: {e}"
    return ""


def reset(name: str) -> str:
    """Wipe a profile's per-profile artifacts (state, hash cache,
    launcher config, content repos, torrents, logo) but keep its
    directory and registry entry so it stays reconfigurable. Returns
    "" on success, else an error message."""
    prof = _existing_or_none(name)
    if prof is None:
        return f"Unknown profile: {name}"
    root = prof.root
    for path in (
        os.path.join(root, "launcher.json"),
        prof.state_path(),
        prof.cache_path(),
        prof.logo_path(),
        prof.torrents_dir(),
    ):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
    for kind in CONTENT_KINDS:
        repo = prof.local_repo_path(kind)
        try:
            if os.path.exists(repo):
                os.remove(repo)
        except OSError:
            pass
    return ""


def set_active(name: str):
    """Point the index at an existing profile (persisted; picked up by
    the next launch's resolve())."""
    if _existing_or_none(name) is None:
        raise ProfileError(f"Unknown profile: {name}")
    with _INDEX_LOCK:
        idx = load_index()
        idx["active"] = name
        save_index(idx)


def resolve(override=None) -> "Profile | None":
    """Profile for this run, or None when no profile exists yet.

    Explicit override > index.active > first known profile. An unknown
    override raises ProfileError (hard CLI error); a missing override
    with nothing on disk returns None so the caller opens the import
    wizard instead of failing."""
    if override:
        if not _clean_name(override):
            raise ProfileError(f"Unknown profile: {override}")
        root = profile_root(override)
        if not os.path.isdir(root):
            raise ProfileError(
                f"Unknown profile: {override} "
                f"(no directory under {profiles_root()})"
            )
        return Profile(override, root)
    idx = load_index()
    for name in ([idx["active"]] if idx["active"] else []) + idx["order"]:
        if name and os.path.isdir(profile_root(name)):
            return Profile(name, profile_root(name))
    disk = _scan_profile_dirs()
    if disk:
        return Profile(disk[0], profile_root(disk[0]))
    return None


def _clean_name(name: str) -> bool:
    """Plain directory name only: no path separators, no leading dot —
    management APIs and the untrusted --profile override must never
    address anything outside profiles/."""
    return (
        bool(name)
        and "/" not in name
        and "\\" not in name
        and not name.startswith(".")
    )


def _existing_or_none(name) -> "Profile | None":
    """Profile for a name backed by a directory under profiles/, else
    None. Non-string names (a caller passing an index instead of a name)
    resolve to None so set_active raises its clean ProfileError."""
    if not isinstance(name, str):
        return None
    if not _clean_name(name):
        return None
    if name and os.path.isdir(profile_root(name)):
        return Profile(name, profile_root(name))
    return None


def adopt_legacy_default() -> str:
    """One-time migration for installs that started on the implicit
    "default" profile: rename ``profiles/default/`` after its server
    (``profile_name_for`` + ``unique_name``) and fix the index pointer.
    A "default" directory without a launcher.json is left alone when it
    holds anything (user data — it is just an ordinary profile now) and
    removed when empty. Returns the adopted name, or "" when there was
    nothing to adopt. Never deletes a config or state file."""
    legacy = profile_root(DEFAULT_PROFILE)
    if not os.path.isdir(legacy):
        return ""
    cfg_path = os.path.join(legacy, "launcher.json")
    if not os.path.exists(cfg_path):
        try:
            if not os.listdir(legacy):
                os.rmdir(legacy)
                with _INDEX_LOCK:
                    idx = load_index()
                    idx["order"] = [
                        n for n in idx["order"] if n != DEFAULT_PROFILE
                    ]
                    save_index(idx)
        except OSError:
            pass
        return ""
    server_name, host = "", ""
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            server = data.get("server")
            if isinstance(server, dict):
                name = server.get("name")
                if isinstance(name, str):
                    server_name = name
                for key in ("url", "base_url"):
                    url = server.get(key)
                    if isinstance(url, str) and url:
                        try:
                            host = urlsplit(url).hostname or ""
                        except ValueError:
                            host = ""
                        if host:
                            break
            if not host:
                dl = (
                    server.get("download")
                    if isinstance(server, dict)
                    else None
                )
                if isinstance(dl, dict):
                    for section in ("torrent", "http"):
                        part = dl.get(section)
                        if not isinstance(part, dict):
                            continue
                        for key in ("torrent_url", "fallback", "url"):
                            url = part.get(key)
                            if isinstance(url, str) and url:
                                try:
                                    host = urlsplit(url).hostname or ""
                                except ValueError:
                                    host = ""
                                if host:
                                    break
                        if host:
                            break
    except (OSError, ValueError):
        pass
    with _INDEX_LOCK:
        idx = load_index()  # snapshot BEFORE the move ("default" on disk)
        was_active = idx["active"] == DEFAULT_PROFILE
        name = unique_name(profile_name_for(server_name, host))
        try:
            os.rename(legacy, profile_root(name))
        except OSError:
            return ""
        idx["order"] = [
            name if n == DEFAULT_PROFILE else n for n in idx["order"]
        ]
        if name not in idx["order"]:
            idx["order"].append(name)
        if was_active:
            idx["active"] = name
        save_index(idx)
    return name


# ── process-global active profile ────────────────────────────────────────


def activate(p: Profile):
    """Pin the process-wide active profile (called once at startup)."""
    global _ACTIVE
    with _INDEX_LOCK:
        _ACTIVE = p


def active() -> Profile:
    """The active profile — must be activated via ``activate()`` first."""
    with _INDEX_LOCK:
        if _ACTIVE is None:
            raise RuntimeError(
                "profiles.active() called before profiles.activate() — "
                "no active profile (call profiles.activate(resolve(...)) "
                "at startup)"
            )
        return _ACTIVE

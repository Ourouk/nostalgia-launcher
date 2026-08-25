"""Launcher profiles — named, fully isolated per-user configurations.

A profile owns its own server config (`launcher.json`), state store
(`state.json`), hash cache, custom catalogs, torrent metadata and logo
cache under ``<config_dir>/profiles/<name>/``. The reserved ``default``
profile has NO directory: it maps byte-identically onto the legacy
top-level files, so existing installations keep working unchanged
(``zero migration``).

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

from .constants import CACHE_FILE, CONFIG_FILE
from .launcher import CONTENT_KINDS, LAUNCHER_FILE
from .platform_support import cache_dir, config_dir

DEFAULT_PROFILE = "default"

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

    ``root`` is "" for the default profile (legacy top-level files) and
    ``<config_dir>/profiles/<name>`` otherwise.
    """

    name: str
    root: str = ""

    def launcher_path(self) -> str:
        """The profile's server-config file (LAUNCHER_FILE schema)."""
        if not self.root:
            return os.path.join(config_dir(), LAUNCHER_FILE)
        return os.path.join(self.root, "launcher.json")

    def state_path(self) -> str:
        """The profile's CONFIG_FILE-schema state store."""
        if not self.root:
            return CONFIG_FILE
        return os.path.join(self.root, "state.json")

    def cache_path(self) -> str:
        """The profile's hash-cache file (CACHE_FILE schema)."""
        if not self.root:
            return CACHE_FILE
        return os.path.join(self.root, "hash_cache.json")

    def custom_dir(self) -> str:
        """Directory holding the per-user custom catalog JSON files."""
        if not self.root:
            return config_dir()
        return os.path.join(self.root, "custom")

    def custom_catalog_path(self, kind: str) -> str:
        """Custom-catalog file for a kind ("mods"/"addons"); same file
        naming as the legacy top-level layout."""
        return os.path.join(
            self.custom_dir(), f"nostalgia_launcher_{kind}_custom.json"
        )

    def local_repo_path(self, kind: str) -> str:
        """The content-kind local repo file (launcher CONTENT_KINDS):
        `{"server": [...], "custom": [...]}` written by the import-time
        split."""
        if not self.root:
            return os.path.join(config_dir(), f"local_{kind}_repo.json")
        return os.path.join(self.root, f"local_{kind}_repo.json")

    def torrents_dir(self) -> str:
        """Torrent metadata/resume directory for this profile."""
        if not self.root:
            return os.path.join(cache_dir(), "torrents")
        return os.path.join(self.root, "torrents")

    def logo_path(self) -> str:
        """Cached server-logo image for this profile."""
        if not self.root:
            return os.path.join(cache_dir(), "launcher_logo.img")
        return os.path.join(self.root, "launcher_logo.img")


#: Shared instance of the default profile (frozen dataclass).
DEFAULT = Profile(DEFAULT_PROFILE, "")


# ── name validation ──────────────────────────────────────────────────────────


def validate_name(name: str) -> str:
    """Return "" when ``name`` is a valid new-profile name, else a
    human-readable error message."""
    if not name:
        return "Profile name cannot be empty."
    if name == DEFAULT_PROFILE:
        return '"default" is reserved for the built-in profile.'
    if name[-1] in ". ":
        return "Profile name cannot end with a dot or space."
    if not _NAME_RE.match(name):
        return (
            "Use 1-32 characters: letters, digits, spaces, dots, "
            "underscores or hyphens, starting with a letter or digit."
        )
    return ""


# ── index (profiles.json) ────────────────────────────────────────────────────


def profiles_root() -> str:
    """Directory holding every non-default profile."""
    return os.path.join(config_dir(), "profiles")


def index_path() -> str:
    """The profile registry: {"active": <name>, "order": [<names>]}."""
    return os.path.join(config_dir(), "profiles.json")


def _atomic_write_text(path: str, text: str):
    """Write via temp file + rename so a crash can't truncate the file;
    creates the parent directory (first write into a fresh config dir)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def profile_root(name: str) -> str:
    return os.path.join(profiles_root(), name)


def _scan_profile_dirs() -> list:
    """Names backed by a directory under profiles/ (sorted, default never
    listed — it has no directory by construction)."""
    try:
        entries = sorted(os.listdir(profiles_root()))
    except OSError:
        return []
    return [
        e
        for e in entries
        if e != DEFAULT_PROFILE and os.path.isdir(profile_root(e))
    ]


def load_index() -> dict:
    """Tolerant index load. A missing/corrupt file or ghost pointers are
    recovered from a directory scan — startup must never crash over the
    index. ``order`` never contains the default profile (implicit first)."""
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
        # Keep only names that exist on disk; default stays implicit.
        if n != DEFAULT_PROFILE and n in disk and n not in merged:
            merged.append(n)
    if active != DEFAULT_PROFILE and active not in disk:
        active = DEFAULT_PROFILE
    return {"active": active, "order": merged}


def save_index(idx: dict):
    """Atomically persist the index (last-writer-wins on races — single-
    user desktop tool; see docs)."""
    payload = {
        "active": idx.get("active", DEFAULT_PROFILE),
        "order": [n for n in idx.get("order", []) if n != DEFAULT_PROFILE],
    }
    _atomic_write_text(index_path(), json.dumps(payload, indent=2))


# ── management API ───────────────────────────────────────────────────────────


def list_profiles() -> list:
    """All known profile names: union of index.order and directories on
    disk, default always first."""
    with _INDEX_LOCK:
        names = [DEFAULT_PROFILE]
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


def duplicate(src: str, dst: str) -> str:
    """Copy a profile's server config AND its content repos (mods/addons/
    assets server + custom entries — the pre-split embedded content)
    into a new profile. Runtime state, caches and install records stay
    per-machine and are NOT copied. Returns "" on success, else an
    error message."""
    src_prof = _existing_or_none(src)
    if src_prof is None:
        return f"Unknown profile: {src}"
    err = validate_name(dst)
    if err:
        return err
    text = ""
    try:
        with open(src_prof.launcher_path(), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        pass  # unconfigured source duplicates as unconfigured
    new_prof, err = create(dst, launcher_json_text=text)
    if err:
        return err
    for kind in CONTENT_KINDS:
        src_repo = src_prof.local_repo_path(kind)
        if not os.path.exists(src_repo):
            continue
        try:
            shutil.copyfile(src_repo, new_prof.local_repo_path(kind))
        except OSError as e:
            shutil.rmtree(new_prof.root, ignore_errors=True)
            return f"Could not copy the {kind} content repo: {e}"
    return ""


def rename(src: str, dst: str) -> str:
    """Rename a profile directory, fixing the index pointer when the
    renamed profile was active. Returns "" on success."""
    if _existing_or_none(src) is None:
        return f"Unknown profile: {src}"
    err = validate_name(dst)
    if err:
        return err
    with _INDEX_LOCK:
        if os.path.exists(profile_root(dst)):
            return f"Profile already exists: {dst}"
        idx = load_index()  # snapshot BEFORE the dir move (pointer check)
        try:
            os.rename(profile_root(src), profile_root(dst))
        except OSError as e:
            return f"Could not rename the profile: {e}"
        idx["order"] = [dst if n == src else n for n in idx["order"]]
        if idx["active"] == src:
            idx["active"] = dst
        save_index(idx)
    return ""


def delete(name: str) -> str:
    """Delete a non-default profile. Refuses the default; resets the
    active pointer BEFORE removing the directory so a failed rmtree can
    never leave the index pointing at deleted state. Returns "" on
    success."""
    if name == DEFAULT_PROFILE:
        return "The default profile cannot be deleted."
    if _existing_or_none(name) is None:
        return f"Unknown profile: {name}"
    with _INDEX_LOCK:
        idx = load_index()
        if idx["active"] == name:
            idx["active"] = DEFAULT_PROFILE
        idx["order"] = [n for n in idx["order"] if n != name]
        save_index(idx)
        try:
            shutil.rmtree(profile_root(name))
        except OSError as e:
            # Pointer is already safe; the leftover dir resurfaces in
            # list_profiles() until removed, which is honest.
            return f"Could not delete the profile directory: {e}"
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


def resolve(override=None) -> Profile:
    """Profile for this run: explicit override > index.active > default.
    An unknown override raises ProfileError (hard CLI error); an invalid
    persisted pointer silently falls back to default."""
    name = override or load_index()["active"] or DEFAULT_PROFILE
    if name == DEFAULT_PROFILE:
        return DEFAULT
    root = profile_root(name)
    if not os.path.isdir(root):
        raise ProfileError(
            f"Unknown profile: {name} (no directory under {{}})".format(
                profiles_root()
            )
        )
    return Profile(name, root)


def _existing_or_none(name) -> "Profile | None":
    """Profile for a known name (default included), else None."""
    if name == DEFAULT_PROFILE:
        return DEFAULT
    if name and os.path.isdir(profile_root(name)):
        return Profile(name, profile_root(name))
    return None


# ── process-global active profile ────────────────────────────────────────


def activate(p: Profile):
    """Pin the process-wide active profile (called once at startup)."""
    global _ACTIVE
    with _INDEX_LOCK:
        _ACTIVE = p


def active() -> Profile:
    """The active profile; lazily the default when nothing was activated
    (library/test callers get today's legacy behavior unchanged)."""
    with _INDEX_LOCK:
        return _ACTIVE if _ACTIVE is not None else DEFAULT

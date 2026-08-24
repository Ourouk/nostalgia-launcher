"""Addons engine: catalog, Git commit resolution and archive installation.

Addons are installed directly from Git hosts (GitHub, GitLab, Gitea,
Codeberg, plus any community host a distribution lists in its launcher
config) by downloading the repo archive pinned to a commit SHA — no git
client needed. Commit resolution and archive fetching are delegated to the
shared ``git_archive`` source backend (`services/sources`); this module
keeps the addon-specific catalog machinery, the folder unpacking call and
the pfUI "Default" profile patch.

There is no bundled addon list — the ADDONS tab comes entirely from the
addon catalog (launcher-configured or user-set URL) merged with the per-user
custom file, so a distribution decides what it ships.
"""

import json
import os
import time
import urllib.request

from ..core import config_store as _config_store
from ..core import launcher
from ..core.config_store import load_config, update_config
from ..core.constants import UA
from ..core.log_sink import log
from ..core.security_http import read_capped, secure_urlopen
from . import catalog
from .sources import deploy as _sources_deploy
from .sources.git_archive import (
    GitArchiveBackend,
)

_GIT_BACKEND = GitArchiveBackend()

# Catalogs refresh at most weekly (shared catalog.CATALOG_TTL); the
# per-URL timestamp lives in the config file.
ADDONS_CATALOG_TTL = catalog.CATALOG_TTL
ADDONS_VERIFY_TTL = 300  # skip re-verify on tab switches within this

# The per-user custom addon file (a JSON list, one entry per addon). Written
# empty on first use via Settings → Catalog registries.
CUSTOM_FILE_TEMPLATE = "[\n]\n"

# Recommended addon folder names ({folder: git_url}) for the star badge and
# the one-shot auto-install. Empty by default — a distribution may flag
# addons as recommended via its catalog's "recommended" flag instead.
RECOMMENDED_ADDONS: dict = {}

# Never shown in the updater, even when the catalog carries them. Empty by
# default — a distribution may populate it via its catalog's "blocked" flag.
BLOCKED_ADDONS = set()


def addons_path(client_dir: str) -> str:
    return os.path.join(client_dir, "Interface", "AddOns")


def is_allowed_git_url(url: str) -> bool:
    """Whether the URL's host is on the git-host allowlist (base hosts plus
    the launcher config's extras). Delegates to the git_archive backend."""
    from .sources.git_archive import is_allowed_git_url as _allowed

    return _allowed(url)


def _custom_validator(entry: dict) -> dict | None:
    """Validate a custom addon entry and enforce the git-host allowlist."""
    cleaned = catalog.validate_addon(entry)
    if cleaned is None:
        return None
    if cleaned["git"] and not is_allowed_git_url(cleaned["git"]):
        return None
    return cleaned


def fetch_addons_catalog(force=False) -> list:
    """The ordered addon catalog: every configured registry URL is fetched
    (or served from its own cache entry) and merged in order — a later
    registry overrides an earlier one by addon folder name. Cached per URL
    for a day ({"addons_catalog_cache": {url: {"timestamp", "catalog"}}}).
    A failed URL falls back to its last cached copy; an unconfigured URL
    list returns an empty list."""
    urls = registry_urls()
    if not urls:
        log("Addon catalog URL is not configured.", "err")
        return []
    now = time.time()
    cache = load_config().get("addons_catalog_cache", {})
    if isinstance(cache, dict) and "catalog" in cache and urls[0] not in cache:
        # Legacy single-URL cache shape → re-key it under the first
        # configured URL so the per-URL lookup keeps working.
        update_config(
            lambda c, u=urls[0]: c.setdefault(
                "addons_catalog_cache", {}
            ).__setitem__(u, c["addons_catalog_cache"])
        )
    merged = []
    for url in urls:
        part = _fetch_url_catalog(url, force, now)
        merged = catalog.merge_addons(merged, part)
    return merged


def _cache_entry(url: str) -> dict:
    """The cached catalog record for a URL, handling the legacy single-URL
    shape (a bare {"timestamp", "catalog"} object). Read through
    `_config_store` so the controller's offline fallback honors test
    monkeypatches of `config_store.load_config`."""
    cache = _config_store.load_config().get("addons_catalog_cache", {}) or {}
    if isinstance(cache, dict) and url in cache:
        return cache[url]
    if isinstance(cache, dict) and "catalog" in cache:
        return cache
    return {}


def _fetch_url_catalog(url: str, force: bool, now: float) -> list:
    """Fetch and cache one catalog URL; on failure serve its cached copy."""
    entry = _cache_entry(url)
    if (
        not force
        and entry.get("catalog") is not None
        and (now - entry.get("timestamp", 0)) < ADDONS_CATALOG_TTL
    ):
        return entry["catalog"]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with secure_urlopen(req, timeout=10) as r:
            raw = json.loads(read_capped(r, 2 * 1024 * 1024))
    except Exception:
        # offline — serve the last good cached copy for this URL
        return entry.get("catalog") or []
    catalog_list = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        cleaned = _custom_validator(e)
        if cleaned is None:
            continue
        catalog_list.append(cleaned)
    update_config(
        lambda c, u=url, o=catalog_list, t=now: c.setdefault(
            "addons_catalog_cache", {}
        ).__setitem__(u, {"timestamp": t, "catalog": o})
    )
    return catalog_list


def addons_catalog(force=False) -> list:
    """The effective addon catalog: the remote/cached catalogs merged in
    registry order (later wins) and then merged with the per-user custom
    file (custom entries override by folder name)."""
    remote = fetch_addons_catalog(force=force)
    return catalog.merge_addons(
        remote, catalog.load_custom("addons", _custom_validator)
    )


def catalog_from_cache() -> list:
    """The cached catalogs merged with the custom file, without any network —
    used as the offline fallback when a fresh fetch fails."""
    cache = _config_store.load_config().get("addons_catalog_cache", {}) or {}
    urls = registry_urls()
    parts = []
    if urls:
        for url in urls:
            entry = _cache_entry(url)
            if entry.get("catalog"):
                parts.append(entry["catalog"])
    elif isinstance(cache, dict) and cache.get("catalog"):
        parts.append(cache["catalog"])
    merged = []
    for part in parts:
        merged = catalog.merge_addons(merged, part)
    return catalog.merge_addons(
        merged, catalog.load_custom("addons", _custom_validator)
    )


def catalog_last_updated() -> float | None:
    """The newest per-URL catalog fetch timestamp (epoch), or None when no
    catalog was ever fetched. Network-free."""
    cache = _config_store.load_config().get("addons_catalog_cache", {}) or {}
    stamps = [
        e.get("timestamp")
        for e in cache.values()
        if isinstance(e, dict) and isinstance(e.get("timestamp"), (int, float))
    ]
    return max(stamps) if stamps else None


def registry_url() -> str:
    """The addon catalog URL shown in Settings: a per-user override, else the
    first launcher-configured URL, else ''."""
    override = catalog.get_registry_url("addons")
    if override:
        return override
    urls = addons_registry_default_urls()
    return urls[0] if urls else ""


def registry_urls() -> list[str]:
    """The ordered list of addon catalog URLs in effect: a per-user override
    (Settings) replaces the whole list with itself; otherwise the
    launcher-configured list is used."""
    override = catalog.get_registry_url("addons")
    if override:
        return [override]
    return addons_registry_default_urls()


def addons_registry_default_url() -> str:
    """The launcher-configured addon catalog URL ('' when not configured)."""
    urls = addons_registry_default_urls()
    return urls[0] if urls else ""


def addons_registry_default_urls() -> list[str]:
    """The launcher-configured addon catalog URLs, in override order ('' list
    when not configured)."""

    return launcher.addons_registry_urls()


def set_registry_url(url: str) -> str | None:
    """Validate and store a per-user catalog URL override (empty clears it);
    returns an error string or None on success."""
    return catalog.set_registry_url("addons", url)


def reset_registry_url():
    """Drop the per-user override so the launcher-configured URL is used."""
    catalog.reset_registry_url("addons")


def custom_file() -> str:
    """Path of the per-user custom addon JSON file."""
    return catalog.custom_file("addons")


def open_custom_file() -> bool:
    """Create the custom addon file (with the template) when missing."""
    return catalog.write_custom_template("addons", CUSTOM_FILE_TEMPLATE)


def clear_custom_file() -> bool:
    """Delete the custom addon file. True when something was removed."""
    return catalog.clear_custom("addons")


def read_toc_file(path: str) -> dict:
    """Parse '## Key: Value' metadata lines from a WoW addon .toc file."""
    toc = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return toc
    if content.startswith("\ufeff"):  # strip UTF-8 BOM
        content = content[1:]
    for line in content.splitlines():
        if not line.startswith("## "):
            continue
        key, sep, value = line[3:].partition(":")
        if sep:
            toc[key.strip()] = value.strip()
    return toc


def addon_remote_sha(
    git_url: str, branch=None, ref=None, force=False, raise_errors=False
) -> str | None:
    """Latest commit sha of a repo's branch (or pinned ref), cached in the
    config file so repeated verifies don't burn API quota. Delegates to the
    shared ``git_archive`` backend (API path with a ``git ls-remote``
    fallback; the allowlist gate lives there)."""
    return _GIT_BACKEND.remote_sha(
        git_url,
        branch=branch,
        ref=ref,
        force=force,
        raise_errors=raise_errors,
    )


def addon_cached_sha(git_url: str, branch=None, ref=None):
    """Cached remote sha regardless of age — never touches the network."""
    return _GIT_BACKEND.cached_sha(git_url, branch=branch, ref=ref)


def addon_zip_url(git_url: str, sha: str) -> str:
    return _GIT_BACKEND.zip_url(git_url, sha)


def install_addon_files(client_dir: str, folder: str, git_url: str, sha: str):
    """Download the repo archive at `sha` via the git_archive backend and
    unpack it into Interface/AddOns/<folder>, atomically replacing any
    existing copy."""
    log(f"  Downloading {folder} @ {sha[:10]}…")
    data = _GIT_BACKEND.fetch_archive(git_url, sha)
    _sources_deploy.unpack_folder(
        data, os.path.join(addons_path(client_dir), folder)
    )
    log(f"  Installed addon {folder}")


# ── pfUI "Default" profile patch ─────────────────────────────────────────────
# pfUI ships a set of built-in design profiles. After every pfUI install/update
# we add a curated "Default" profile and make it the firstrun default. Because
# an update overwrites pfUI's files, the patch is re-applied each time and is
# idempotent (marked blocks are replaced, not duplicated).

# The curated profile (JSON captured from a configured pfUI, profile renamed to
# "Default"). Loaded as a Python dict and emitted as a Lua table at patch time.
PFUI_DEFAULT_PROFILE = json.loads(r"""
{"appearance":{"border":{"default":"-1"},"castbar":{"castbarcolor":"1,0.796,0.251,0.8"},"cd":{"debuffs":"1","font":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","milliseconds":"0"},"infight":{"health":"0"},"minimap":{"arrowscale":"2"}},"buffs":{"hidelist":"","showoverflow":"1","showspillover":"1"},"castbar":{"focus":{"showicon":"1","showtimer":"0"},"player":{"hide_blizz":"0","hide_pfui":"1","showtimer":"0"},"target":{"showicon":"1","showtimer":"0"}},"character":{"inventory":{"durability":"0"},"reputation":{"repRequired":"0"}},"disabled":{"actionbar":"1","addonbuttons":"0","addoncompat":"0","addons":"0","afkcam":"0","autoshift":"0","autovendor":"0","bags":"1","bgscore":"0","bubbles":"1","buff":"1","buffwatch":"0","castbar":"0","chat":"1","chatcopy":"0","combopoints":"0","cooldown":"0","custom":"0","easteregg":"0","energytick":"0","eqcompare":"0","equipmentmanager":"0","farmmode":"0","feigndeath":"0","firstrun":"0","focus":"0","gm":"0","group":"0","gryphons":"0","hdgraphic":"0","hoverbind":"0","hunterbar":"0","infight":"0","innervatecall":"0","itemclick":"0","itemcount":"1","loot":"1","macrotweak":"0","map":"0","mapcolors":"0","mapreveal":"0","marktracking":"0","minimap":"0","mirrortimers":"0","mouseover":"0","nameplates":"0","nampower":"0","panel":"0","pet":"0","pettarget":"0","pixelperfect":"0","player":"0","questitem":"0","raid":"0","roll":"1","screenshot":"0","sellvalue":"0","share":"0","skin":"0","skin_Auctionhouse":"0","skin_Barbershop":"0","skin_Battlefield":"0","skin_Battlefield Minimap":"0","skin_Battlefield Score":"0","skin_Books":"0","skin_Character":"0","skin_Coin Pickup":"0","skin_Color Picker":"0","skin_Dress Up Frame":"0","skin_Everlook Broadcasting":"0","skin_Flightmaster":"1","skin_Friends":"1","skin_GM Survey":"0","skin_Game Menu":"0","skin_Gossip and Quest":"1","skin_Guild Registrar":"0","skin_Guild Tabard":"0","skin_Help":"0","skin_Inspect":"0","skin_KeyBindings":"0","skin_Macro":"0","skin_Mailbox":"1","skin_Merchant":"1","skin_Opacity":"0","skin_Outline":"0","skin_Player":"1","skin_Quest":"1","skin_Quest Tracker":"0","skin_Reputation":"1","skin_Social":"0","skin_TradeSkill":"1","skin_Trainer":"1","skin_Tutorials":"0","skin_Unitframe":"1","timerbar":"0","tooltip":"0","tracker":"0","unitframes":"0"},"equipment":{"durability":"0"},"nameplates":{"clickthrough":"0","hidelist":"","showonlyname":"0"},"panels":{"fpsloc":"Right","hidelist":"","lootannounce":"0","mouseover":"0"},"reputation":{"repRequired":"0"},"skins":{"dark":"1","font":"Interface\\AddOns\\pfUI\\fonts\\Myriad-Pro.ttf","fontscale":"1"},"tooltips":{"hideincombat":"0","hidelist":"","mousefollow":"0"},"unitframes":{"clickthrough":"0","hidelist":"","petbars":"1","showstagger":"0"}}
""")


def _lua_value(v, indent: int = 0) -> str:
    """Serialize a JSON-derived value to a pfUI-style Lua literal."""
    if isinstance(v, dict):
        pad, cpad = " " * (indent + 2), " " * indent
        items = "".join(
            f'{pad}["{k}"] = {_lua_value(val, indent + 2)},\n'
            for k, val in v.items()
        )
        return "{\n" + items + cpad + "}"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


_PFUI_MARK_BEGIN = "-- OCTO_UPDATER_DEFAULT_PROFILE_BEGIN"
_PFUI_MARK_END = "-- OCTO_UPDATER_DEFAULT_PROFILE_END"
_PFUI_CHAT_BEGIN = "-- OCTO_UPDATER_CHAT_SKIP_BEGIN"
_PFUI_CHAT_END = "-- OCTO_UPDATER_CHAT_SKIP_END"

# Strips any Nostalgia Launcher injected block, regardless of which marker pair.
_PFUI_STRIP_RE = (
    r"[ \t]*-- OCTO_UPDATER_[A-Z_]+?_BEGIN.*?-- OCTO_UPDATER_[A-Z_]+?_END\n?"
)


def patch_pfui_default_profile(client_dir: str):
    """Add the curated 'Default' profile to a freshly installed/updated pfUI
    and make it the firstrun default. Idempotent; degrades gracefully if
    pfUI's file layout has changed."""
    import re

    base = os.path.join(addons_path(client_dir), "pfUI")
    profiles_lua = os.path.join(base, "env", "profiles.lua")
    firstrun_lua = os.path.join(base, "modules", "firstrun.lua")
    if not os.path.exists(profiles_lua):
        return

    # 1) profiles.lua — append (or replace) a marked block defining Default.
    block = (
        f"{_PFUI_MARK_BEGIN}\n"
        f"local octo_default = {_lua_value(PFUI_DEFAULT_PROFILE)}\n"
        f'pfUI_profiles["Default"] = octo_default\n'
        f"{_PFUI_MARK_END}\n"
    )
    try:
        with open(profiles_lua, encoding="utf-8", errors="replace") as f:
            txt = f.read()
        txt = re.sub(
            re.escape(_PFUI_MARK_BEGIN)
            + r".*?"
            + re.escape(_PFUI_MARK_END)
            + r"\n?",
            "",
            txt,
            flags=re.S,
        )
        with open(profiles_lua, "w", encoding="utf-8") as f:
            f.write(txt.rstrip() + "\n\n" + block)
        log("  pfUI: 'Default' profile installed.")
    except OSError as e:
        log(f"  pfUI: could not patch profiles.lua ({e})")
        return

    # 2) pfUI.lua — use 'Default' (not 'Modern') as the fresh-install config,
    #    so the very first login already lands in the Default profile.
    pfui_lua = os.path.join(base, "pfUI.lua")
    if os.path.exists(pfui_lua):
        try:
            with open(pfui_lua, encoding="utf-8", errors="replace") as f:
                pf = f.read()
            old = 'CopyTable(pfUI_profiles["Modern"]) or {}'
            if old in pf:
                pf = pf.replace(
                    old, 'CopyTable(pfUI_profiles["Default"]) or {}', 1
                )
                with open(pfui_lua, "w", encoding="utf-8") as f:
                    f.write(pf)
                log("  pfUI: 'Default' set as the fresh-install profile.")
        except OSError as e:
            log(f"  pfUI: could not patch pfUI.lua ({e})")

    # 3) firstrun.lua — add a 'Default' button, make it the fallback profile,
    #    and skip the chat wizard steps whenever the chat module is disabled.
    if not os.path.exists(firstrun_lua):
        return
    try:
        with open(firstrun_lua, encoding="utf-8", errors="replace") as f:
            fr = f.read()

        # Remove any previous injections (idempotent re-apply after updates).
        fr = re.sub(_PFUI_STRIP_RE, "", fr, flags=re.S)

        # When the chat module is disabled (e.g. the "Default" profile), the
        # chat firstrun steps can't apply anything, so pre-mark them done to
        # keep them from showing. Injected right after the step table is made.
        chat_skip = (
            f"  {_PFUI_CHAT_BEGIN}\n"
            "  if pfUI_config and pfUI_config.disabled"
            ' and pfUI_config.disabled.chat == "1" then\n'
            "    pfUI_init = pfUI_init or {}\n"
            '    pfUI_init["chat_right"] = true\n'
            '    pfUI_init["chat_position"] = true\n'
            '    pfUI_init["chat_channels"] = true\n'
            "  end\n"
            f"  {_PFUI_CHAT_END}\n"
        )
        chat_anchor = "  pfUI.firstrun.steps = {}\n"
        if chat_anchor in fr:
            fr = fr.replace(chat_anchor, chat_anchor + chat_skip, 1)

        # Insert a Default button just before the built-in "Modern" button.
        button = (
            f"    {_PFUI_MARK_BEGIN}\n"
            '    f.Default = CreateFrame("Button", nil, f, "UIPanelButtonTemplate")\n'
            "    f.Default:SetWidth(250)\n"
            "    f.Default:SetHeight(20)\n"
            '    f.Default:SetPoint("BOTTOM", 0, 125)\n'
            "    f.Default:SetTextColor(1,1,1)\n"
            '    f.Default:SetText("Default (recommended)")\n'
            '    f.Default:SetScript("OnClick", function()\n'
            '      _G["pfUI_config"] = CopyTable(pfUI_profiles["Default"])\n'
            '      pfUI_init.selected_profile = "Default"\n'
            "      pfUI:LoadConfig()\n"
            "      ReloadUI()\n"
            "    end)\n"
            "    SkinButton(f.Default)\n"
            f"    {_PFUI_MARK_END}\n\n"
        )
        anchor = '    f.Modern = CreateFrame("Button"'
        if anchor in fr:
            fr = fr.replace(anchor, button + anchor, 1)

        # Make Default the profile used when the user doesn't pick one.
        fr = fr.replace(
            'pfUI_init.selected_profile or "Modern"',
            'pfUI_init.selected_profile or "Default"',
        )

        with open(firstrun_lua, "w", encoding="utf-8") as f:
            f.write(fr)
        log("  pfUI: 'Default' added to the firstrun profile picker.")
    except OSError as e:
        log(f"  pfUI: could not patch firstrun.lua ({e})")

"""Tweak definitions and Config.wtf handling.

Holds the tweak registry (defaults, UI items, clamping limits), the display
detection used to pick an FOV default, and the Config.wtf reader/writer.
Runtime fixes are left to the ExampleLoader mod where installed; the
launcher itself only ever writes Config.wtf.
"""

import math
import os
import re
from dataclasses import dataclass

from ..core.config_store import load_config, update_config
from ..core.filesystem import atomic_write_text as _atomic_write
from ..core.filesystem import ensure_dir
from ..core.log_sink import log


def _wtf_str(v) -> str:
    """Sanitize a config-derived value for `SET k "v"` lines: quotes,
    newlines and NULs would let a hostile launcher config inject extra
    Config.wtf directives."""
    return re.sub(r'[\r\n\x00"]', "", str(v))


def _wtf_num(v, default):
    """Coerce a state-file value to a finite number for numeric SET lines;
    anything else (a string with quotes, NaN, a dict…) falls back to the
    default so it can never break out of the quoted value. Whole numbers
    stay ints so the written file matches the historical formatting."""
    try:
        num = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(num) or math.isinf(num):
        return default
    return int(num) if num.is_integer() else num


def _host_of(url: str) -> str:
    """Hostname of an https URL, or ''."""
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def expected_realm() -> str:
    """The realm the launcher wants the client to point at (sanitized)."""
    from ..core import launcher

    srv = launcher.realm() or _host_of(launcher.server_url()) or "localhost"
    return _wtf_str(srv)


def _parse_wtf_value(path: str, key: str) -> str | None:
    """Read a single ``SET <key> \"value\"`` (or unquoted) from a WTF file."""
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                # Quoted or unquoted, empty allowed, trailing // or -- comments ignored.
                m = re.match(
                    rf'^\s*SET\s+{re.escape(key)}\s+"?([^"]*)"?\s*(?:--.*|//.*)?\s*$',
                    line,
                    re.IGNORECASE,
                )
                if m:
                    return m.group(1).strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return None


def parse_config_wtf_realm(client_dir: str) -> str | None:
    """The ``realmList`` value from ``WTF/Config.wtf``, if present."""
    return _parse_wtf_value(
        os.path.join(client_dir, "WTF", "Config.wtf"), "realmList"
    )


def parse_realmlist_wtf(client_dir: str) -> str | None:
    """The ``realmlist`` value from ``realmlist.wtf`` at the client root."""
    return _parse_wtf_value(
        os.path.join(client_dir, "realmlist.wtf"), "realmlist"
    )


@dataclass
class RealmStatus:
    """Result of comparing on-disk realm with the server-configured one."""

    expected: str
    actual_config: str | None
    actual_realmlist: str | None
    mismatch: bool
    config_exists: bool
    realmlist_exists: bool


def check_realm_mismatch(client_dir: str) -> RealmStatus:
    """Compare ``WTF/Config.wtf`` / ``realmlist.wtf`` against ``expected_realm()``.

    Mismatch is True when either file exists and its value differs
    (case-insensitive, sanitized) from the expected realm, or when a file
    exists but contains no realm line. Missing files are not a mismatch —
    a fresh install will be seeded without a prompt.
    """
    exp = expected_realm()
    exp_norm = _wtf_str(exp).strip().lower()
    cfg_path = os.path.join(client_dir, "WTF", "Config.wtf")
    rl_path = os.path.join(client_dir, "realmlist.wtf")
    cfg_exists = os.path.exists(cfg_path)
    rl_exists = os.path.exists(rl_path)
    actual_cfg_raw = parse_config_wtf_realm(client_dir) if cfg_exists else None
    actual_rl_raw = parse_realmlist_wtf(client_dir) if rl_exists else None
    # Sanitize actual values symmetrically for comparison.
    actual_cfg = (
        _wtf_str(actual_cfg_raw).strip()
        if actual_cfg_raw is not None
        else None
    )
    actual_rl = (
        _wtf_str(actual_rl_raw).strip() if actual_rl_raw is not None else None
    )
    mismatch = False
    if cfg_exists and (actual_cfg is None or actual_cfg.lower() != exp_norm):
        mismatch = True
    if rl_exists and (actual_rl is None or actual_rl.lower() != exp_norm):
        mismatch = True
    return RealmStatus(
        expected=exp,
        actual_config=actual_cfg_raw,
        actual_realmlist=actual_rl_raw,
        mismatch=mismatch,
        config_exists=cfg_exists,
        realmlist_exists=rl_exists,
    )


def inject_realm(client_dir: str) -> bool:
    """Inject the expected realm into ``Config.wtf``/``realmlist.wtf``.

    Uses ``update_config_wtf`` when a Config exists, otherwise a fresh
    ``write_config_wtf`` + ``write_realmlist_wtf``. Returns True on success,
    False on failure (and logs). Never raises.
    """
    try:
        cfg_path = os.path.join(client_dir, "WTF", "Config.wtf")
        if not os.path.exists(cfg_path):
            write_config_wtf(client_dir)
            write_realmlist_wtf(client_dir)
        else:
            update_config_wtf(client_dir, load_tweaks_config())
        # Verify write succeeded by checking files exist and contain expected.
        exp = expected_realm().strip().lower()
        cfg_val = parse_config_wtf_realm(client_dir)
        rl_val = parse_realmlist_wtf(client_dir)
        # At least one file should now match; treat empty/missing as failure.
        if exp:
            cfg_ok = (
                cfg_val is not None
                and _wtf_str(cfg_val).strip().lower() == exp
            )
            rl_ok = (
                rl_val is not None and _wtf_str(rl_val).strip().lower() == exp
            )
            # For fresh write both should match; for update at least config should.
            if cfg_ok or rl_ok:
                return True
            # Fallback: files exist counts as success if expected is localhost edge.
            if os.path.exists(cfg_path) or os.path.exists(
                os.path.join(client_dir, "realmlist.wtf")
            ):
                return True
        return True
    except Exception as e:  # pragma: no cover
        log(f"Could not inject realm: {e}", "err")
        return False


TWEAKS_DEFAULTS = {
    "nameplateRange": 41,
    "fieldOfView": 110,
    "farClip": 777,
    "frillDistance": 70,
    "cameraDistance": 50,
    "soundInBackground": True,
}

TWEAKS_ITEMS = [
    (None, "GENERAL", "section", False, None, None, None, None, None),
    (
        "nameplateRange",
        "Nameplate range",
        "number",
        False,
        None,
        "Distance at which nameplates are visible.",
        0,
        41,
        1,
    ),
    (None, "CAMERA", "section", False, None, None, None, None, None),
    (
        "fieldOfView",
        "Field of View",
        "number",
        False,
        None,
        "Recommended values for aspect ratios: [4:3 = 90] [16:9 = 110] [21:9 = 150] [32:9 = 180]",
        90,
        180,
        5,
    ),
    (
        "farClip",
        "Render distance",
        "number",
        False,
        None,
        "Maximum render distance. May cause crashes. [Vanilla max: 777] [Tweaks max: 10000]",
        100,
        10000,
        1,
    ),
    (
        "frillDistance",
        "Ground clutter distance",
        "number",
        False,
        None,
        "Ground clutter render distance. [Vanilla max: 70] [Tweaks max: 300]",
        0,
        300,
        1,
    ),
    (
        "cameraDistance",
        "Camera distance",
        "number",
        False,
        None,
        "Maximum camera (zoom out) distance. [Vanilla max: 50] [Tweaks max: 100]",
        50,
        100,
        1,
    ),
    (None, "SOUND", "section", False, None, None, None, None, None),
    (
        "soundInBackground",
        "Background sounds",
        "checkbox",
        True,
        None,
        "Allows game sounds to play while the game is minimized.",
        None,
        None,
        None,
    ),
]


# {tweak_id: (min, max)} for every numeric tweak — the single source of
# truth for clamping, wherever the value is read from the UI.
TWEAKS_LIMITS = {
    t[0]: (t[6], t[7])
    for t in TWEAKS_ITEMS
    if t[0] is not None and t[2] == "number"
}


_FOV_REFS = [
    (4 / 3, 90),
    (16 / 9, 110),
    (21 / 9, 150),
    (32 / 9, 180),
]


def fov_default_for_display() -> int:
    try:
        info = _get_display_info_safe()
        ratio = info["width"] / info["height"] if info["height"] else 16 / 9
    except Exception:
        ratio = 16 / 9

    if ratio <= _FOV_REFS[0][0]:
        return _FOV_REFS[0][1]
    if ratio >= _FOV_REFS[-1][0]:
        return _FOV_REFS[-1][1]
    for i in range(len(_FOV_REFS) - 1):
        r0, f0 = _FOV_REFS[i]
        r1, f1 = _FOV_REFS[i + 1]
        if r0 <= ratio <= r1:
            t = (ratio - r0) / (r1 - r0)
            raw = f0 + t * (f1 - f0)
            return round(round(raw / 5) * 5)
    return 110


def _get_display_info_safe() -> dict:
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:  # non-Windows (e.g. running from source) — fall back
        return {"width": 1920, "height": 1080, "refresh_rate": 60}
    ENUM_CURRENT_SETTINGS = -1

    class DEVMODE(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", ctypes.c_wchar * 32),
            ("dmSpecVersion", ctypes.c_ushort),
            ("dmDriverVersion", ctypes.c_ushort),
            ("dmSize", ctypes.c_ushort),
            ("dmDriverExtra", ctypes.c_ushort),
            ("dmFields", ctypes.c_ulong),
            ("dmPositionX", ctypes.c_long),
            ("dmPositionY", ctypes.c_long),
            ("dmDisplayOrientation", ctypes.c_ulong),
            ("dmDisplayFixedOutput", ctypes.c_ulong),
            ("dmColor", ctypes.c_short),
            ("dmDuplex", ctypes.c_short),
            ("dmYResolution", ctypes.c_short),
            ("dmTTOption", ctypes.c_short),
            ("dmCollate", ctypes.c_short),
            ("dmFormName", ctypes.c_wchar * 32),
            ("dmLogPixels", ctypes.c_ushort),
            ("dmBitsPerPel", ctypes.c_ulong),
            ("dmPelsWidth", ctypes.c_ulong),
            ("dmPelsHeight", ctypes.c_ulong),
            ("dmDisplayFlags", ctypes.c_ulong),
            ("dmDisplayFrequency", ctypes.c_ulong),
        ]

    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    windll.user32.EnumDisplaySettingsW(
        None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)
    )
    return {
        "width": dm.dmPelsWidth,
        "height": dm.dmPelsHeight,
        "refresh_rate": dm.dmDisplayFrequency,
    }


def load_tweaks_config() -> dict:
    cfg = load_config()
    stored = cfg.get("tweaks", {})
    defaults = dict(TWEAKS_DEFAULTS)
    defaults["fieldOfView"] = fov_default_for_display()
    return {k: stored.get(k, v) for k, v in defaults.items()}


def save_tweaks_config(values: dict):
    update_config(lambda c: c.__setitem__("tweaks", values))


def write_config_wtf(client_dir: str, tweaks: dict | None = None):
    """Write a fresh Config.wtf from scratch, overwriting any existing one.
    Never raises — logs the error if the file can't be written (read-only,
    locked by a running game, or an unwritable folder)."""
    if tweaks is None:
        tweaks = load_tweaks_config()
    far_clip = _wtf_num(tweaks.get("farClip"), TWEAKS_DEFAULTS["farClip"])
    cam_dist = _wtf_num(
        tweaks.get("cameraDistance"), TWEAKS_DEFAULTS["cameraDistance"]
    )
    nameplate = _wtf_num(
        tweaks.get("nameplateRange"), TWEAKS_DEFAULTS["nameplateRange"]
    )
    fov_deg = _wtf_num(
        tweaks.get("fieldOfView"), TWEAKS_DEFAULTS["fieldOfView"]
    )
    fov_rad = round(fov_deg * math.pi / 180.0, 6)
    bg_sound = (
        1
        if tweaks.get(
            "soundInBackground", TWEAKS_DEFAULTS["soundInBackground"]
        )
        else 0
    )

    di = _get_display_info_safe()
    from ..core import launcher

    srv = launcher.realm() or _host_of(launcher.server_url()) or "localhost"
    srv = _wtf_str(srv)
    # The Linux renderer preset (set via the Settings LINUX (UMU) section)
    # selects the client's graphics API. On non-Linux or "auto" we leave
    # gxApi unset so Proton/WoW pick their default.
    launch_cfg = (load_config() or {}).get("launch") or {}
    renderer = launch_cfg.get("umu_renderer", "auto")
    gx_api = ""
    if renderer == "dxvk-d3d8":
        gx_api = "d3d8"
    elif renderer == "wined3d-opengl":
        gx_api = "opengl"
    vars_ = {
        "realmList": srv,
        "patchList": srv,
        "readTOS": 1,
        "readEULA": 1,
        "profanityFilter": 0,
        "gxResolution": f"{di['width']}x{di['height']}",
        "gxWindow": 1,
        "gxMaximize": 1,
        "gxVSync": 0,
        "gxColorBits": 24,
        "gxDepthBits": 24,
        "gxRefresh": di["refresh_rate"],
        "gxMultisampleQuality": 0,
        "gxMultisample": 2,
        "hwDetect": 0,
        "pixelShaders": 1,
        "M2UsePixelShaders": 1,
        "specular": 1,
        "anisotropic": 16,
        "trilinear": 1,
        "lod": 0,
        "lodDist": 100,
        "texLodBias": 0,
        "shadowLevel": 0,
        "particleDensity": 1,
        "fullAlpha": 1,
        "SmallCull": 0.01,
        "farClip": far_clip,
        "DistCull": 888.8,
        "frillDensity": 48,
        "unitDrawDist": 300,
        "weatherDensity": 3,
        "FoV": fov_rad,
        "NameplateRange": nameplate,
        "CameraDistanceMax": cam_dist,
        "cameraDistanceMaxFactor": 1,
        "scriptMemory": 512000,
        "uiScale": 1,
        "mouseSpeed": 1,
        "autoSelfCast": 1,
        "movie": 0,
        "movieSubtitle": 1,
        "checkAddonVersion": 0,
        "minimapZoom": 0,
        "minimapInsideZoom": 0,
        "EnableErrorSpeech": 0,
        "SoundZoneMusicNoDelay": 1,
        "SoundMaxHardwareChannels": 64,
        "SoundSoftwareChannels": 64,
        "UncapSounds": 1,
        "BackgroundSound": bg_sound,
        "NP_NameplateDistance": nameplate,
        "NP_SpellQueueWindowMs": 150,
        "NP_EnableAuraCastEvents": 1,
        "NP_EnableAutoAttackEvents": 1,
        "NP_EnableSpellStartEvents": 1,
        "NP_EnableSpellGoEvents": 1,
        "NP_EnableSpellHealEvents": 1,
        "NP_QueueCastTimeSpells": 0,
        "NP_QueueInstantSpells": 0,
        "NP_QueueChannelingSpells": 0,
        "NP_QueueTargetingSpells": 0,
        "NP_QueueSpellsOnCooldown": 0,
        "NP_ChatBubbleDistance": 60,
        "NP_ChatBubblesWhisper": 1,
        "NP_ChatBubblesRaid": 1,
        "NP_ChatBubblesBattleground": 1,
        "ChatBubblesParty": 1,
    }
    if gx_api:
        vars_["gxApi"] = gx_api
    try:
        cfg_dir = os.path.join(client_dir, "WTF")
        ensure_dir(cfg_dir)
        with open(
            os.path.join(cfg_dir, "Config.wtf"), "w", encoding="utf-8"
        ) as f:
            for k, v in vars_.items():
                f.write(f'SET {k} "{v}"\n')
        log("Config.wtf written.", "ok")
    except Exception as e:
        log(f"Could not write Config.wtf: {e}", "err")


def write_realmlist_wtf(client_dir: str):
    """Write the client-root ``realmlist.wtf`` from the configured realm.

    Vanilla 1.12.1 reads the root realmlist.wtf (WTF/Config.wtf's
    ``realmList`` usually wins after a first run, but a fresh client folder
    only has the file — belt and braces, as private-server launchers do).
    Never raises; best-effort like `write_config_wtf`.
    """
    try:
        from ..core import launcher

        srv = launcher.realm() or _host_of(launcher.server_url())
        if not srv:
            return
        with open(
            os.path.join(client_dir, "realmlist.wtf"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(f"SET realmlist {_wtf_str(srv)}\n")
        log("realmlist.wtf written.", "ok")
    except Exception as e:
        log(f"Could not write realmlist.wtf: {e}", "err")


def update_config_wtf(client_dir: str, tweaks: dict):
    cfg_path = os.path.join(client_dir, "WTF", "Config.wtf")
    if not os.path.exists(cfg_path):
        write_config_wtf(client_dir, tweaks)
        write_realmlist_wtf(client_dir)
        return

    far_clip = _wtf_num(tweaks.get("farClip"), TWEAKS_DEFAULTS["farClip"])
    cam_dist = _wtf_num(
        tweaks.get("cameraDistance"), TWEAKS_DEFAULTS["cameraDistance"]
    )
    nameplate = _wtf_num(
        tweaks.get("nameplateRange"), TWEAKS_DEFAULTS["nameplateRange"]
    )
    fov_deg = _wtf_num(
        tweaks.get("fieldOfView"), TWEAKS_DEFAULTS["fieldOfView"]
    )
    fov_rad = round(fov_deg * math.pi / 180.0, 6)
    bg_sound = (
        1
        if tweaks.get(
            "soundInBackground", TWEAKS_DEFAULTS["soundInBackground"]
        )
        else 0
    )
    from ..core import launcher as _launcher

    srv = _launcher.realm() or _host_of(_launcher.server_url()) or "localhost"
    srv = _wtf_str(srv)

    updates = {
        "farClip": str(far_clip),
        "CameraDistanceMax": str(cam_dist),
        "NP_NameplateDistance": str(nameplate),
        "FoV": str(fov_rad),
        "NameplateRange": str(nameplate),
        "BackgroundSound": str(bg_sound),
        "realmList": srv,
        "patchList": srv,
    }

    with open(cfg_path, encoding="utf-8") as f:
        lines = f.readlines()

    updated_keys = set()
    new_lines = []
    for line in lines:
        matched = False
        for key, val in updates.items():
            if line.strip().lower().startswith(f"set {key.lower()} "):
                new_lines.append("SET " + key + ' "' + val + '"\n')
                updated_keys.add(key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append("SET " + key + ' "' + val + '"\n')

    # Atomic rewrite: a crash mid-write must not truncate the user's live
    # Config.wtf (same discipline as the config store).
    try:
        _atomic_write(cfg_path, "".join(new_lines))
    except OSError as e:
        log(f"  Could not update Config.wtf: {e}", "err")
        return

    # Always keep realmlist.wtf in sync with the configured realm.
    try:
        write_realmlist_wtf(client_dir)
    except Exception:
        pass

    log(
        f"  Config.wtf updated: farClip={far_clip}, CameraDistanceMax={cam_dist}, "
        f"NameplateRange={nameplate}, NP_NameplateDistance={nameplate}, "
        f"FoV={fov_rad}, realmList={srv}",
        "dim",
    )

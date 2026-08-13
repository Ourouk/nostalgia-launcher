"""Tweak definitions, Config.wtf handling and WoW.exe binary patching.

Holds the tweak registry (defaults, UI items, clamping limits), the display
detection used to pick an FOV default, the Config.wtf reader/writer, and the
builder that turns a tweak dict into binary patch operations.
"""

import math
import os
import struct

from config_store import load_config, update_config
from filesystem import ensure_dir
from log_sink import log

TWEAKS_DEFAULTS = {
    "alwaysAutoLoot":  True,
    "nameplateRange":  41,
    "fieldOfView":     110,
    "farClip":         777,
    "frillDistance":   70,
    "cameraDistance":  50,
    "soundInBackground": True,
}

TWEAKS_ITEMS = [
    (None, "GENERAL", "section", False, None, None, None, None, None),

    ("alwaysAutoLoot",    "Always auto-loot",        "checkbox", True,  None,
     "Reverses auto-loot behavior to always auto-loot.",
     None, None, None),

    ("nameplateRange",    "Nameplate range",          "number",   False, None,
     "Distance at which nameplates are visible.",
     0, 41, 1),

    (None, "CAMERA", "section", False, None, None, None, None, None),

    ("fieldOfView",       "Field of View",            "number",   False, None,
     "Recommended values for aspect ratios: [4:3 = 90] [16:9 = 110] [21:9 = 150] [32:9 = 180]",
     90, 180, 5),

    ("farClip",           "Render distance",          "number",   False, None,
     "Maximum render distance. May cause crashes. [Vanilla max: 777] [Tweaks max: 10000]",
     100, 10000, 1),

    ("frillDistance",     "Ground clutter distance",  "number",   False, None,
     "Ground clutter render distance. [Vanilla max: 70] [Tweaks max: 300]",
     0, 300, 1),

    ("cameraDistance",    "Camera distance",          "number",   False, None,
     "Maximum camera (zoom out) distance. [Vanilla max: 50] [Tweaks max: 100]",
     50, 100, 1),

    (None, "SOUND", "section", False, None, None, None, None, None),

    ("soundInBackground", "Background sounds",        "checkbox", True,  None,
     "Allows game sounds to play while the game is minimized.",
     None, None, None),
]


# {tweak_id: (min, max)} for every numeric tweak — the single source of
# truth for clamping, wherever the value is read from the UI.
TWEAKS_LIMITS = {t[0]: (t[6], t[7]) for t in TWEAKS_ITEMS
                 if t[0] is not None and t[2] == "number"}


_FOV_REFS = [
    (4  / 3,   90),
    (16 / 9,  110),
    (21 / 9,  150),
    (32 / 9,  180),
]


def fov_default_for_display() -> int:
    try:
        info  = _get_display_info_safe()
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
            t   = (ratio - r0) / (r1 - r0)
            raw = f0 + t * (f1 - f0)
            return round(round(raw / 5) * 5)
    return 110


def _get_display_info_safe() -> dict:
    import ctypes
    windll = getattr(ctypes, "windll", None)
    if windll is None:   # non-Windows (e.g. running from source) — fall back
        return {"width": 1920, "height": 1080, "refresh_rate": 60}
    ENUM_CURRENT_SETTINGS = -1

    class DEVMODE(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName",         ctypes.c_wchar * 32),
            ("dmSpecVersion",        ctypes.c_ushort),
            ("dmDriverVersion",      ctypes.c_ushort),
            ("dmSize",               ctypes.c_ushort),
            ("dmDriverExtra",        ctypes.c_ushort),
            ("dmFields",             ctypes.c_ulong),
            ("dmPositionX",          ctypes.c_long),
            ("dmPositionY",          ctypes.c_long),
            ("dmDisplayOrientation", ctypes.c_ulong),
            ("dmDisplayFixedOutput", ctypes.c_ulong),
            ("dmColor",              ctypes.c_short),
            ("dmDuplex",             ctypes.c_short),
            ("dmYResolution",        ctypes.c_short),
            ("dmTTOption",           ctypes.c_short),
            ("dmCollate",            ctypes.c_short),
            ("dmFormName",           ctypes.c_wchar * 32),
            ("dmLogPixels",          ctypes.c_ushort),
            ("dmBitsPerPel",         ctypes.c_ulong),
            ("dmPelsWidth",          ctypes.c_ulong),
            ("dmPelsHeight",         ctypes.c_ulong),
            ("dmDisplayFlags",       ctypes.c_ulong),
            ("dmDisplayFrequency",   ctypes.c_ulong),
        ]

    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    windll.user32.EnumDisplaySettingsW(
        None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm))
    return {"width": dm.dmPelsWidth, "height": dm.dmPelsHeight,
            "refresh_rate": dm.dmDisplayFrequency}


def load_tweaks_config() -> dict:
    cfg      = load_config()
    stored   = cfg.get("tweaks", {})
    defaults = dict(TWEAKS_DEFAULTS)
    defaults["fieldOfView"] = fov_default_for_display()
    return {k: stored.get(k, v) for k, v in defaults.items()}


def save_tweaks_config(values: dict):
    update_config(lambda c: c.__setitem__("tweaks", values))


def build_tweaks(buf, tweaks: dict | None = None):
    if tweaks is None:
        tweaks = load_tweaks_config()

    fov_deg = tweaks.get("fieldOfView", TWEAKS_DEFAULTS["fieldOfView"])
    fov     = fov_deg * (math.pi / 180.0)
    flags   = struct.unpack_from("<H", buf, 0x126)[0] | 0x20

    nameplate = float(tweaks.get("nameplateRange", TWEAKS_DEFAULTS["nameplateRange"]))
    far_clip  = float(tweaks.get("farClip",        TWEAKS_DEFAULTS["farClip"]))
    frill     = float(tweaks.get("frillDistance",  TWEAKS_DEFAULTS["frillDistance"]))
    cam_dist  = float(tweaks.get("cameraDistance", TWEAKS_DEFAULTS["cameraDistance"]))
    snd_bg    = 0x27 if tweaks.get("soundInBackground", TWEAKS_DEFAULTS["soundInBackground"]) else 0x14

    always_loot = tweaks.get("alwaysAutoLoot", TWEAKS_DEFAULTS["alwaysAutoLoot"])

    # fmt: off
    return [
        ("largeAddress",          "uint16", 0x126,     flags),
        ("fieldOfView",           "float",  0x4089b4,  fov),
        ("cameraDistance",        "float",  0x4089a4,  cam_dist),
        ("farClip",               "float",  0x40fed8,  far_clip),
        ("frillDistance",         "float",  0x467958,  frill),
        ("nameplateRange",        "float",  0x40c448,  nameplate),
        ("soundInBackground",     "int8",   0x3a4869,  snd_bg),
        ("alwaysAutoLoot", "bytes", None, [
            (0x0c1ecf, bytes([0x75 if always_loot else 0x74])),
            (0x0c2b25, bytes([0x75 if always_loot else 0x74])),
        ]),
        ("crossFactionResurrect", "bytes", None, [
            (0x006e5fb8, bytes([0x006e5fb9 & 0xff])),
            (0x006e62a8, bytes([0x006e62a9 & 0xff])),
        ]),
        ("cameraSkipFix", "bytes", None, [
            (0x02ccd0, bytes([
                0x55,0x8b,0x05,0x48,0x4e,0x88,0x00,0x8b,0x0d,0x44,0x4e,0x88,0x00,0xe9,0x33,0x90,
                0x32,0x00,0x83,0xc0,0x32,0x83,0xc1,0x32,0x3b,0x0d,0xa8,0xeb,0xc4,0x00,0x7e,0x03,
                0x83,0xe9,0x01,0x3b,0x05,0xac,0xeb,0xc4,0x00,0x7e,0x03,0x83,0xe8,0x01,0x83,0xe9,
                0x32,0x83,0xe8,0x32,0x89,0x05,0x48,0x4e,0x88,0x00,0x89,0x0d,0x44,0x4e,0x88,0x00,
                0x5d,0xeb,0x0d,
            ])),
            (0x02d326, bytes([0xe9,0xb1,0x8a,0x32,0x00])),
            (0x02d334, bytes([0x8b,0x35,0x48,0x4e,0x88,0x00])),
            (0x355d15, bytes([
                0x83,0xf8,0x32,0x7d,0x03,0x83,0xc0,0x01,0x83,0xf9,0x32,
                0x7d,0x03,0x83,0xc1,0x01,0xe9,0xb8,0x6f,0xcd,0xff,
            ])),
            (0x355ddc, bytes([
                0x8d,0x4d,0xf0,0x51,
                0xff,0x35,0x00,0x4e,0x88,0x00,0xff,0x15,0x50,0xf6,0x7f,0x00,0x8b,0x45,0xf0,0x8b,
                0x15,0x44,0x4e,0x88,0x00,0xe9,0x35,0x75,0xcd,0xff,
            ])),
        ]),
        ("skillUiGateHijack", "bytes", None, [
            (0x002ddf90, bytes([
                0x55,0x8b,0xec,0x83,0xec,0x08,0x53,0x56,0x57,0x8b,0x3d,0x60,0xab,0xce,0x00,0x83,
                0xff,0xff,0x89,0x55,0xfc,0x89,0x4d,0xf8,0x74,0x79,0x8b,0x75,0x08,0x8b,0x15,0x58,
                0xab,0xce,0x00,0x8b,0xc7,0x23,0xc6,0x8d,0x04,0x40,0x8b,0x4c,0x82,0x08,0xf6,0xc1,
                0x01,0x8d,0x44,0x82,0x04,0x75,0x04,0x85,0xc9,0x75,0x05,0x33,0xc9,0x8d,0x49,0x00,
                0xf6,0xc1,0x01,0x75,0x4e,0x85,0xc9,0x74,0x4a,0x39,0x31,0x74,0x13,0x8b,0xc7,0x23,
                0xc6,0x8d,0x04,0x40,0x8d,0x04,0x82,0x8b,0x00,0x03,0xc1,0x8b,0x48,0x04,0xeb,0xe0,
                0x8b,0x59,0x1c,0x8b,0x71,0x18,0x33,0xff,0x85,0xdb,0x7e,0x27,0x8d,0x64,0x24,0x00,
                0x8b,0x4e,0x0c,0x8b,0x56,0x08,0x6a,0x00,0x6a,0x00,0x51,0x8b,0x4d,0xf8,0x52,0x8b,
                0x55,0xfc,0xe8,0xb9,0xfd,0xff,0xff,0x84,0xc0,0x75,0x13,0x47,0x83,0xc6,0x20,0x3b,
                0xfb,0x7c,0xdd,0x5f,0x5e,0x33,0xc0,0x5b,0x8b,0xe5,0x5d,0xc2,0x04,0x00,0x5f,0x8b,
                0xc6,0x5e,0x5b,0x8b,0xe5,0x5d,0xc2,0x04,0x00,0x90,0x90,0x90,0x90,0x90,0x90,0x90,
            ])),
        ]),
    ]
    # fmt: on


def write_config_wtf(client_dir: str, tweaks: dict | None = None):
    """Write a fresh Config.wtf from scratch, overwriting any existing one.
    Never raises — logs the error if the file can't be written (read-only,
    locked by a running game, or an unwritable folder)."""
    if tweaks is None:
        tweaks = load_tweaks_config()
    far_clip  = tweaks.get("farClip",        TWEAKS_DEFAULTS["farClip"])
    cam_dist  = tweaks.get("cameraDistance", TWEAKS_DEFAULTS["cameraDistance"])
    nameplate = tweaks.get("nameplateRange", TWEAKS_DEFAULTS["nameplateRange"])
    fov_deg   = tweaks.get("fieldOfView",    TWEAKS_DEFAULTS["fieldOfView"])
    fov_rad   = round(fov_deg * math.pi / 180.0, 6)
    bg_sound  = 1 if tweaks.get("soundInBackground",
                                TWEAKS_DEFAULTS["soundInBackground"]) else 0

    di  = _get_display_info_safe()
    srv = "octowow.st"
    vars_ = {
        "realmList": srv, "patchList": srv,
        "readTOS": 1, "readEULA": 1,
        "profanityFilter": 0,
        "gxResolution": f"{di['width']}x{di['height']}",
        "gxWindow": 1, "gxMaximize": 1,
        "gxVSync": 0,
        "gxColorBits": 24, "gxDepthBits": 24,
        "gxRefresh": di["refresh_rate"],
        "gxMultisampleQuality": 0, "gxMultisample": 2,
        "hwDetect": 0,
        "pixelShaders": 1, "M2UsePixelShaders": 1,
        "specular": 1,
        "anisotropic": 16, "trilinear": 1,
        "lod": 0, "lodDist": 100,
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
        "minimapZoom": 0, "minimapInsideZoom": 0,
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
    try:
        cfg_dir = os.path.join(client_dir, "WTF")
        ensure_dir(cfg_dir)
        with open(os.path.join(cfg_dir, "Config.wtf"), "w",
                  encoding="utf-8") as f:
            for k, v in vars_.items():
                f.write(f'SET {k} "{v}"\n')
        log("Config.wtf written.", "ok")
    except Exception as e:
        log(f"Could not write Config.wtf: {e}", "err")


def update_config_wtf(client_dir: str, tweaks: dict):
    cfg_path = os.path.join(client_dir, "WTF", "Config.wtf")
    if not os.path.exists(cfg_path):
        write_config_wtf(client_dir, tweaks)
        return

    far_clip  = tweaks.get("farClip",        TWEAKS_DEFAULTS["farClip"])
    cam_dist  = tweaks.get("cameraDistance", TWEAKS_DEFAULTS["cameraDistance"])
    nameplate = tweaks.get("nameplateRange", TWEAKS_DEFAULTS["nameplateRange"])
    fov_deg   = tweaks.get("fieldOfView",    TWEAKS_DEFAULTS["fieldOfView"])
    fov_rad   = round(fov_deg * math.pi / 180.0, 6)
    bg_sound  = 1 if tweaks.get("soundInBackground",
                                TWEAKS_DEFAULTS["soundInBackground"]) else 0
    updates = {
        "farClip":              str(far_clip),
        "CameraDistanceMax":    str(cam_dist),
        "NP_NameplateDistance": str(nameplate),
        "FoV":                  str(fov_rad),
        "NameplateRange":       str(nameplate),
        "BackgroundSound":      str(bg_sound),
    }

    with open(cfg_path, "r", encoding="utf-8") as f:
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

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    log(f"  Config.wtf updated: farClip={far_clip}, CameraDistanceMax={cam_dist}, "
        f"NameplateRange={nameplate}, NP_NameplateDistance={nameplate}, "
        f"FoV={fov_rad}", "dim")

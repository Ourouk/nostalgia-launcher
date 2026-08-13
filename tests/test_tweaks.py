"""Unit tests for the tweaks module (patch builder, Config.wtf)."""

import struct

import tweaks
import config_store


def test_tweak_limits_cover_all_numeric_items():
    for tid, _label, kind, _rec, _d, _desc, lo, hi, _step in tweaks.TWEAKS_ITEMS:
        if tid is not None and kind == "number":
            assert tweaks.TWEAKS_LIMITS[tid] == (lo, hi)


def test_load_tweaks_config_merges_defaults(tmp_path):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    config_store.save_config({"tweaks": {"farClip": 1000}})
    cfg = tweaks.load_tweaks_config()
    assert cfg["farClip"] == 1000
    assert cfg["nameplateRange"] == tweaks.TWEAKS_DEFAULTS["nameplateRange"]
    assert "fieldOfView" in cfg


def test_save_tweaks_config(tmp_path):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    tweaks.save_tweaks_config({"farClip": 42})
    assert config_store.load_config()["tweaks"] == {"farClip": 42}


def _fake_buffer():
    buf = bytearray(0x1000)
    struct.pack_into("<H", buf, 0x126, 0x0000)
    return buf


def test_build_tweaks_contains_expected_entries():
    ops = tweaks.build_tweaks(_fake_buffer(), tweaks.TWEAKS_DEFAULTS)
    labels = [o[0] for o in ops]
    assert "largeAddress" in labels
    assert "fieldOfView" in labels
    assert "soundInBackground" in labels
    assert "alwaysAutoLoot" in labels
    for label, kind, offset, _value in ops:
        assert kind in ("float", "int8", "uint16", "bytes")
        if kind == "float":
            assert offset is not None


def test_build_tweaks_sound_bg_on_off():
    on = dict(tweaks.TWEAKS_DEFAULTS, soundInBackground=True)
    off = dict(tweaks.TWEAKS_DEFAULTS, soundInBackground=False)
    ops_on = {o[0]: o for o in tweaks.build_tweaks(_fake_buffer(), on)}
    ops_off = {o[0]: o for o in tweaks.build_tweaks(_fake_buffer(), off)}
    assert ops_on["soundInBackground"][3] == 0x27
    assert ops_off["soundInBackground"][3] == 0x14


def test_build_tweaks_always_loot_flips_bytes():
    on = dict(tweaks.TWEAKS_DEFAULTS, alwaysAutoLoot=True)
    off = dict(tweaks.TWEAKS_DEFAULTS, alwaysAutoLoot=False)
    ops_on = {o[0]: o for o in tweaks.build_tweaks(_fake_buffer(), on)}
    ops_off = {o[0]: o for o in tweaks.build_tweaks(_fake_buffer(), off)}
    on_bytes = b"".join(b for _off, b in ops_on["alwaysAutoLoot"][3])
    off_bytes = b"".join(b for _off, b in ops_off["alwaysAutoLoot"][3])
    assert on_bytes == b"\x75\x75"
    assert off_bytes == b"\x74\x74"


def test_build_tweaks_defaults_when_none(tmp_path):
    config_store.configure(str(tmp_path / "config.json"), str(tmp_path / "cache.json"))
    ops = tweaks.build_tweaks(_fake_buffer(), None)
    assert {o[0] for o in ops} == {
        "largeAddress", "fieldOfView", "cameraDistance", "farClip",
        "frillDistance", "nameplateRange", "soundInBackground",
        "alwaysAutoLoot", "crossFactionResurrect", "cameraSkipFix",
        "skillUiGateHijack"}


def test_write_config_wtf_writes_file(tmp_path):
    client = tmp_path / "client"
    tweaks.write_config_wtf(str(client), tweaks.TWEAKS_DEFAULTS)
    cfg = client / "WTF" / "Config.wtf"
    assert cfg.exists()
    content = cfg.read_text(encoding="utf-8")
    assert 'SET realmList "octowow.st"' in content
    assert 'SET farClip "777"' in content


def test_update_config_wtf_creates_when_missing(tmp_path):
    client = tmp_path / "client"
    tweaks.update_config_wtf(str(client), tweaks.TWEAKS_DEFAULTS)
    assert (client / "WTF" / "Config.wtf").exists()


def test_update_config_wtf_updates_existing_values(tmp_path):
    client = tmp_path / "client"
    tweaks.write_config_wtf(str(client), tweaks.TWEAKS_DEFAULTS)
    cfg = client / "WTF" / "Config.wtf"
    cfg.write_text('SET farClip "777"\nSET NameplateRange "41"\n',
                   encoding="utf-8")
    tweaks.update_config_wtf(str(client),
                             dict(tweaks.TWEAKS_DEFAULTS, farClip=1000))
    content = cfg.read_text(encoding="utf-8")
    assert 'SET farClip "1000"' in content
    # Unrelated lines are preserved.
    assert 'SET NameplateRange "41"' in content


def test_fov_default_for_display_matches_display():
    # Falls back to 16:9 defaults when the display can't be queried (or is
    # non-Windows); must still return a valid FOV.
    fov = tweaks.fov_default_for_display()
    assert isinstance(fov, int)
    assert 90 <= fov <= 180

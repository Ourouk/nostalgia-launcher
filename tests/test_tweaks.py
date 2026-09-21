"""Unit tests for the realm/Config.wtf module."""

import nostalgia_launcher.services.tweaks as tweaks


def test_write_config_wtf_writes_file(tmp_path):
    client = tmp_path / "client"
    tweaks.write_config_wtf(str(client))
    cfg = client / "WTF" / "Config.wtf"
    assert cfg.exists()
    content = cfg.read_text(encoding="utf-8")
    assert 'SET realmList "launcher.test"' in content
    assert 'SET farClip "777"' in content


def test_update_config_wtf_creates_when_missing(tmp_path):
    client = tmp_path / "client"
    tweaks.update_config_wtf(str(client))
    assert (client / "WTF" / "Config.wtf").exists()


def test_write_config_wtf_sanitizes_hostile_realm(tmp_path, monkeypatch):
    """Quotes/newlines in a config-supplied realm must not inject extra
    Config.wtf directives."""
    monkeypatch.setattr(
        "nostalgia_launcher.core.launcher.realm",
        lambda: 'evil"\nSET gxApi "opengl',
    )
    client = tmp_path / "client"
    tweaks.write_config_wtf(str(client))
    content = (client / "WTF" / "Config.wtf").read_text(encoding="utf-8")
    assert 'SET realmList "evilSET gxApi opengl"' in content
    # Exactly one SET line per key — the injected newline is gone.
    assert (
        sum(1 for ln in content.splitlines() if ln.startswith("SET realmList"))
        == 1
    )


def test_update_config_wtf_syncs_realm_only(tmp_path):
    client = tmp_path / "client"
    tweaks.write_config_wtf(str(client))
    cfg = client / "WTF" / "Config.wtf"
    cfg.write_text(
        'SET realmList "old.realm"\n'
        'SET patchList "old.realm"\n'
        'SET farClip "777"\n'
        'SET FoV "1.0"\n',
        encoding="utf-8",
    )
    tweaks.update_config_wtf(str(client))
    content = cfg.read_text(encoding="utf-8")
    assert 'SET realmList "launcher.test"' in content
    assert 'SET patchList "launcher.test"' in content
    # In-game graphics choices are left untouched.
    assert 'SET farClip "777"' in content
    assert 'SET FoV "1.0"' in content


def test_fov_default_for_display_matches_display():
    # Falls back to 16:9 defaults when the display can't be queried (or is
    # non-Windows); must still return a valid FOV.
    fov = tweaks.fov_default_for_display()
    assert isinstance(fov, int)
    assert 90 <= fov <= 180


def _write_with_renderer(monkeypatch, tmp_path, renderer):
    monkeypatch.setattr(
        tweaks,
        "load_config",
        lambda: {"launch": {"umu_renderer": renderer}},
    )
    client = tmp_path / "client"
    tweaks.write_config_wtf(str(client))
    return (client / "WTF" / "Config.wtf").read_text(encoding="utf-8")


def test_config_wtf_no_gxapi_when_auto(monkeypatch, tmp_path):
    content = _write_with_renderer(monkeypatch, tmp_path, "auto")
    assert "gxApi" not in content


def test_config_wtf_gxapi_d3d8_for_dxvk(monkeypatch, tmp_path):
    content = _write_with_renderer(monkeypatch, tmp_path, "dxvk-d3d8")
    assert 'SET gxApi "d3d8"' in content


def test_config_wtf_gxapi_opengl_for_wined3d(monkeypatch, tmp_path):
    content = _write_with_renderer(monkeypatch, tmp_path, "wined3d-opengl")
    assert 'SET gxApi "opengl"' in content

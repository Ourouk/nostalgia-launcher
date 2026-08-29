"""Unit tests for first-run launcher config validation, persistence, the
Games/<ServerName> folder suggestion helpers and the strict cli first-launch
flow (the wizard's install folder is the only pre-Settings out_dir writer)."""

import json
import os

import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.core.launcher as launcher
import nostalgia_launcher.core.platform_support as platform_support
from nostalgia_launcher import cli

# ── launcher.validate_dict ──────────────────────────────────────────────


def test_validate_dict_accepts_valid():
    config, err = launcher.validate_dict(
        {"server": {"url": "https://launcher.test"}}
    )
    assert err == ""
    assert config is not None
    assert config.server_url == "https://launcher.test"


def test_validate_dict_accepts_empty_server():
    """A server object with no endpoints is still a valid configuration; the
    endpoints are all optional direct links (no base_url to require)."""
    config, err = launcher.validate_dict({"server": {}})
    assert config is not None
    assert err == ""


# ── launcher.persist_text ───────────────────────────────────────────────


def test_persist_text_writes_valid_config(tmp_path, monkeypatch):
    dest = tmp_path / "nostalgia_launcher.json"
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(dest))
    text = json.dumps({"server": {"url": "https://launcher.test"}})
    out, err = launcher.persist_text(text)
    assert err == ""
    assert out == str(dest)
    assert (
        json.loads(dest.read_text(encoding="utf-8"))["server"]["url"]
        == "https://launcher.test"
    )


def test_persist_text_rejects_invalid(tmp_path, monkeypatch):
    dest = tmp_path / "nostalgia_launcher.json"
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(dest))
    out, err = launcher.persist_text("not json")
    assert out == ""
    assert err


# ── platform_support Games folder ───────────────────────────────────────


def test_games_dir_is_under_home(fake_home):
    assert platform_support.games_dir() == os.path.join(
        str(fake_home), "Games"
    )


def test_server_games_dir_sanitizes(fake_home):
    assert platform_support.server_games_dir("OctoWoW") == os.path.join(
        str(fake_home), "Games", "OctoWoW"
    )
    # illegal path characters are stripped
    assert platform_support.server_games_dir('a/b:c*?"<>|') == os.path.join(
        str(fake_home), "Games", "abc"
    )
    # a blank name falls back to VanillaWoW
    assert platform_support.server_games_dir("   ") == os.path.join(
        str(fake_home), "Games", "VanillaWoW"
    )


# ── cli first-run ────────────────────────────────────────────────────────


def test_first_launch_without_install_dir_touches_no_out_dir(
    tmp_path, monkeypatch
):
    """Strict folder confirmation: a selection WITHOUT an install_dir (a
    pre-wizard-era caller or a skipped flow) persists the launcher config
    but never writes out_dir or creates any game folder."""
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    monkeypatch.setattr(
        launcher, "user_config_path", lambda: str(tmp_path / "cfg.json")
    )
    monkeypatch.setattr(cli, "_run_backend", lambda show_log=False: 0)
    raw = '{"server": {"url": "https://launcher.test"}}'
    monkeypatch.setattr(
        cli,
        "_pick_launcher_config",
        lambda: {
            "kind": "url",
            "config_url": "https://example.invalid/community.json",
            "raw": raw,
        },
    )

    def boom(*a, **kw):
        raise AssertionError("first launch must not create a game folder")

    monkeypatch.setattr(platform_support, "server_games_dir", boom)
    try:
        rc = cli._first_launch()
    finally:
        launcher.reset()
    assert rc == 0
    cfg = config_store.load_config()
    assert "out_dir" not in cfg
    assert (
        json.loads((tmp_path / "cfg.json").read_text(encoding="utf-8"))[
            "server"
        ]["url"]
        == "https://launcher.test"
    )


def test_first_launch_records_wizard_install_dir(
    fake_home, tmp_path, monkeypatch
):
    """The wizard's required install folder becomes the ACTIVE profile's
    confirmed game folder in ITS OWN state store — a non-default profile
    never touches the legacy top-level file. No directory is created and
    no suggestion is applied behind the user's back."""
    from nostalgia_launcher.core import profiles

    prof, err = profiles.create("wizard")
    assert err == ""
    profiles.activate(prof)
    config_store.configure(
        str(tmp_path / "unused-default-state.json"),
        str(tmp_path / "cache.json"),
    )
    monkeypatch.setattr(
        launcher, "user_config_path", lambda: str(tmp_path / "cfg.json")
    )
    monkeypatch.setattr(cli, "_run_backend", lambda show_log=False: 0)
    raw = '{"server": {"url": "https://launcher.test"}}'
    game = tmp_path / "Games" / "WoW"
    monkeypatch.setattr(
        cli,
        "_pick_launcher_config",
        lambda: {
            "kind": "url",
            "config_url": "https://example.invalid/community.json",
            "raw": raw,
            "install_dir": f"{game}/",
        },
    )

    def boom(*a, **kw):
        raise AssertionError("first launch must not create a game folder")

    monkeypatch.setattr(platform_support, "server_games_dir", boom)
    try:
        rc = cli._first_launch()
    finally:
        launcher.reset()
    assert rc == 0

    with open(prof.state_path(), encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["out_dir"] == os.path.normpath(str(game))
    assert cfg["out_dir_user_set"] is True
    # The legacy default-profile store stayed out of it.
    legacy = os.path.join(
        str(fake_home),
        ".nostalgia-launcher",
        "nostalgia_launcher_config.json",
    )
    assert not os.path.exists(legacy)
    assert not game.exists()  # recorded, never created

"""Unit + wiring tests for the profile registry and artifact routing.

The default profile must map byte-identically onto the legacy top-level
paths (back-compat gate); non-default profiles isolate every per-profile
artifact under ``<config_dir>/profiles/<name>/``. CLI tests exercise the
``--profile`` wiring through ``cli.main`` with the backend stubbed.
"""

import json
import os

import pytest

import nostalgia_launcher.cli as cli
import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.core.constants as constants
import nostalgia_launcher.core.launcher as launcher
import nostalgia_launcher.core.platform_support as platform_support
import nostalgia_launcher.core.profiles as profiles
import nostalgia_launcher.services.catalog as catalog
import nostalgia_launcher.services.logo as logo
import nostalgia_launcher.services.update_backend.torrent_update as tu

MINIMAL_CFG = {"server": {"name": "P", "base_url": "https://p.test"}}


def _write_index(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(payload))


@pytest.fixture()
def prof_home(tmp_path, monkeypatch):
    """Redirect profiles.json + profiles/ into tmp_path (config_dir() may
    be import-frozen via constants; the registry must stay test-local)."""
    root = tmp_path / "profiles"
    monkeypatch.setattr(profiles, "profiles_root", lambda: str(root))
    monkeypatch.setattr(
        profiles, "index_path", lambda: str(tmp_path / "profiles.json")
    )
    return root


# ── default-profile back-compat ──────────────────────────────────────────


def test_default_maps_legacy_paths_byte_identically(prof_home):
    prof = profiles.resolve()
    assert prof.name == "default"
    assert prof.root == ""
    assert prof.state_path() == constants.CONFIG_FILE
    assert prof.cache_path() == constants.CACHE_FILE
    assert prof.launcher_path() == launcher.user_config_path()
    assert prof.custom_dir() == platform_support.config_dir()
    assert prof.custom_catalog_path("mods") == os.path.join(
        platform_support.config_dir(), "nostalgia_launcher_mods_custom.json"
    )
    assert prof.torrents_dir() == os.path.join(
        platform_support.cache_dir(), "torrents"
    )
    assert prof.logo_path() == os.path.join(
        platform_support.cache_dir(), "launcher_logo.img"
    )


def test_default_active_services_keep_legacy_paths():
    """With nothing activated, artifact indirection yields exactly
    today's paths."""
    # The autouse _local_repos_env may redirect these wholesale; the
    # contract under a default-active profile is mutual consistency.
    assert catalog.custom_file("addons") == (
        launcher.legacy_custom_path("addons")
    )
    assert logo.logo_cache_path() == os.path.join(
        platform_support.cache_dir(), "launcher_logo.img"
    )
    assert tu.torrent_cache_dir() == os.path.join(
        platform_support.cache_dir(), "torrents"
    )


# ── resolve order ────────────────────────────────────────────────────────


def test_resolve_active_then_override(prof_home):
    profiles.create("alpha")
    profiles.create("beta")
    assert profiles.resolve().name == "default"
    profiles.set_active("alpha")
    assert profiles.resolve().name == "alpha"
    assert profiles.resolve("beta").name == "beta"


def test_unknown_override_raises(prof_home):
    with pytest.raises(profiles.ProfileError, match="nosuch"):
        profiles.resolve("nosuch")


# ── name validation ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "",
        " leading",
        ".dot",
        "/etc/passwd",
        "a\\b",
        "x" * 33,
        "trail.",
        "trail ",
        "default",
    ],
)
def test_invalid_names(name):
    assert profiles.validate_name(name)


@pytest.mark.parametrize("name", ["A", "z9", "My Server", "p.t-1_x", "x" * 32])
def test_valid_names(name, prof_home):
    assert profiles.validate_name(name) == ""


# ── create / duplicate / rename / delete guards ────────────────────────


def test_create_seeds_config_and_order(prof_home):
    prof, err = profiles.create("seeded", json.dumps(MINIMAL_CFG))
    assert err == ""
    assert prof.root == str(prof_home / "seeded")
    assert os.path.isdir(prof.root)
    seeded = json.loads(
        (prof_home / "seeded" / "launcher.json").read_text("utf-8")
    )
    assert seeded["server"]["base_url"] == "https://p.test"
    assert profiles.list_profiles() == ["default", "seeded"]


def test_create_rejects_existing(prof_home):
    profiles.create("dup")
    _prof, err = profiles.create("dup")
    assert err
    _prof, err = profiles.create("bad/")
    assert err


def test_duplicate_copies_server_config_only(prof_home):
    src, _ = profiles.create("src", json.dumps(MINIMAL_CFG))
    (prof_home / "src" / "state.json").write_text("{}", encoding="utf-8")
    err = profiles.duplicate("src", "dst")
    assert err == ""
    dst = profiles.resolve("dst")
    with open(dst.launcher_path(), encoding="utf-8") as f:
        seeded = json.load(f)
    assert seeded["server"]["base_url"] == "https://p.test"
    assert not os.path.exists(os.path.join(dst.root, "state.json"))


def test_rename_fixes_active_pointer(prof_home):
    profiles.create("old")
    profiles.set_active("old")
    assert profiles.rename("old", "new") == ""
    assert not os.path.exists(str(prof_home / "old"))
    assert profiles.load_index()["active"] == "new"
    assert profiles.list_profiles() == ["default", "new"]


def test_delete_refuses_default_and_unknown(prof_home):
    assert profiles.delete("default")
    assert profiles.delete("ghost")


def test_delete_active_resets_pointer(prof_home):
    profiles.create("gone")
    profiles.set_active("gone")
    assert profiles.delete("gone") == ""
    idx = profiles.load_index()
    assert idx["active"] == "default"
    assert not os.path.exists(str(prof_home / "gone"))
    assert profiles.resolve().name == "default"


# ── corrupt / missing index recovery ────────────────────────────────────


def test_corrupt_index_rebuilds_from_disk(prof_home, tmp_path):
    (prof_home / "alpha").mkdir(parents=True)
    index = profiles.index_path()
    os.makedirs(os.path.dirname(index), exist_ok=True)
    _write_index(tmp_path / "profiles.json", "{not json")
    assert profiles.list_profiles() == ["default", "alpha"]
    assert profiles.resolve().name == "default"


def test_ghost_active_falls_back_to_default(prof_home):
    (prof_home / "alpha").mkdir(parents=True)
    _write_index(
        profiles.index_path(), {"active": "ghost", "order": ["alpha"]}
    )
    idx = profiles.load_index()
    assert idx["active"] == "default"
    assert idx["order"] == ["alpha"]
    assert profiles.resolve().name == "default"


# ── artifact indirection with an active non-default profile ───────────


def test_artifacts_route_into_profile(prof_home, real_repo_seams):
    prof, err = profiles.create("iso")
    assert err == ""
    profiles.activate(prof)
    try:
        assert catalog.custom_file("mods") == os.path.join(
            prof.root, "custom", "nostalgia_launcher_mods_custom.json"
        )
        assert logo.logo_cache_path() == os.path.join(
            prof.root, "launcher_logo.img"
        )
        assert tu.torrent_cache_dir() == os.path.join(prof.root, "torrents")
    finally:
        profiles.activate(profiles.DEFAULT)


# ── CLI wiring ───────────────────────────────────────────────────────────


def test_cli_unknown_profile_exits_2(fake_home, capsys):
    assert cli.main(["--profile", "nosuch"]) == 2
    assert "nosuch" in capsys.readouterr().err


def _spy_configure(monkeypatch):
    """Run the REAL _run_backend far enough to observe its
    config_store.configure call: an unusable backend makes it bail out
    with exit 1 right after the routing under test."""
    seen = {}

    def capture(cfg_file, cache_file):
        seen["cfg"] = cfg_file
        seen["cache"] = cache_file

    monkeypatch.setattr(config_store, "configure", capture)
    monkeypatch.setattr(cli, "resolve_backend", lambda name=None: None)
    return seen


def _stub_backend(monkeypatch):
    """Stub cli._run_backend wholesale (for flows whose assertions all
    happen before backend construction)."""
    monkeypatch.setattr(cli, "_run_backend", lambda show_log=False: 0)


def test_cli_good_profile_routes_stores(fake_home, monkeypatch):
    prof, err = profiles.create("good", json.dumps(MINIMAL_CFG))
    assert err == ""
    seen = _spy_configure(monkeypatch)
    assert cli.main(["--profile", "good"]) == 1
    assert seen["cfg"] == os.path.join(prof.root, "state.json")
    assert seen["cache"] == os.path.join(prof.root, "hash_cache.json")
    assert launcher.server_url() == "https://p.test"


def test_cli_default_routes_legacy_stores(fake_home, monkeypatch):
    # Seed the legacy per-user launcher config so main() goes straight
    # to the backend instead of the first-launch wizard.
    os.makedirs(platform_support.config_dir(), exist_ok=True)
    with open(launcher.user_config_path(), "w", encoding="utf-8") as f:
        f.write(json.dumps(MINIMAL_CFG))
    seen = _spy_configure(monkeypatch)
    assert cli.main([]) == 1
    assert seen["cfg"] == constants.CONFIG_FILE
    assert seen["cache"] == constants.CACHE_FILE


def test_cli_first_run_in_profile_persists_wizard_choice(
    fake_home, monkeypatch
):
    prof, err = profiles.create("fresh")
    assert err == ""
    raw = json.dumps(MINIMAL_CFG)
    monkeypatch.setattr(
        cli,
        "_pick_launcher_config",
        lambda: {
            "kind": "url",
            "config_url": "https://example.invalid/c.json",
            "raw": raw,
        },
    )
    _stub_backend(monkeypatch)
    assert cli.main(["--profile", "fresh"]) == 0
    with open(prof.launcher_path(), encoding="utf-8") as f:
        persisted = json.load(f)
    assert persisted["server"]["base_url"] == "https://p.test"


def test_cli_explicit_launcher_config_wins_for_content(
    fake_home, tmp_path, monkeypatch
):
    prof, err = profiles.create("good", json.dumps(MINIMAL_CFG))
    assert err == ""
    other = tmp_path / "other.json"
    other.write_text(
        json.dumps({"server": {"base_url": "https://other.test"}}),
        encoding="utf-8",
    )
    _stub_backend(monkeypatch)
    assert (
        cli.main(["--profile", "good", "--launcher-config", str(other)]) == 0
    )
    assert launcher.server_url() == "https://other.test"
    # The explicit file is never persisted over the profile's own config.
    with open(prof.launcher_path(), encoding="utf-8") as f:
        assert json.load(f)["server"]["base_url"] == "https://p.test"


# ── delete-the-only-server resets to the setup wizard ───────────────────


def _wizard_stub(monkeypatch):
    """Stub the first-launch wizard (URL selection) + backend, mirroring
    the existing wizard-persist test harness."""
    raw = json.dumps(MINIMAL_CFG)
    monkeypatch.setattr(
        cli,
        "_pick_launcher_config",
        lambda: {
            "kind": "url",
            "config_url": "https://example.invalid/c.json",
            "raw": raw,
        },
    )
    _stub_backend(monkeypatch)


def _legacy_config():
    with open(launcher.user_config_path(), encoding="utf-8") as f:
        return json.load(f)


def test_deleting_only_server_restarts_into_setup_wizard(
    fake_home, monkeypatch
):
    """The only configured server is a profile; deleting it while it is
    active and ACCEPTING the restart offer spawns a child running
    ``--profile default`` — which must land on the setup wizard and
    persist into the legacy top-level launcher config."""
    prof, err = profiles.create("only", json.dumps(MINIMAL_CFG))
    assert err == ""
    profiles.set_active("only")
    profiles.activate(profiles.resolve("only"))
    assert profiles.active().name == "only"

    # The UI flow: delete resets the pointer BEFORE removing the dir,
    # then switch_profile("default") detaches the child.
    assert profiles.delete("only") == ""
    assert profiles.load_index()["active"] == "default"

    _wizard_stub(monkeypatch)
    assert cli.main(["--profile", "default"]) == 0

    idx = profiles.load_index()
    assert idx["active"] == "default"
    assert "only" not in idx["order"]
    assert not os.path.exists(str(prof.root))
    assert _legacy_config()["server"]["base_url"] == "https://p.test"


def test_declined_restart_next_launch_lands_on_wizard(fake_home, monkeypatch):
    """Declining the post-delete restart keeps this session alive; the
    NEXT plain launch resolves index-active default (unconfigured) and
    lands on the setup wizard too."""
    prof, err = profiles.create("only", json.dumps(MINIMAL_CFG))
    assert err == ""
    profiles.set_active("only")
    profiles.activate(profiles.resolve("only"))

    assert profiles.delete("only") == ""

    _wizard_stub(monkeypatch)
    assert cli.main([]) == 0

    assert profiles.load_index()["active"] == "default"
    assert not os.path.exists(str(prof.root))
    assert _legacy_config()["server"]["base_url"] == "https://p.test"


# ── local content repos (post main-merge: import-time split) ───────────


def test_local_repos_route_into_profile(prof_home, real_repo_seams):
    """launcher.local_repo_path resolves through the active profile; the
    default profile keeps the byte-compatible top-level file."""
    prof, err = profiles.create("iso")
    assert err == ""
    profiles.activate(prof)
    try:
        for kind in launcher.CONTENT_KINDS:
            assert launcher.local_repo_path(kind) == os.path.join(
                prof.root, f"local_{kind}_repo.json"
            )
    finally:
        profiles.activate(profiles.DEFAULT)
    for kind in launcher.CONTENT_KINDS:
        assert launcher.local_repo_path(kind) == os.path.join(
            platform_support.config_dir(), f"local_{kind}_repo.json"
        )


def test_legacy_custom_path_matches_custom_file(prof_home, real_repo_seams):
    """The migration seed and catalog.custom_file() must resolve to the
    SAME per-profile file (no split brain)."""
    import nostalgia_launcher.services.catalog as catalog

    prof, _ = profiles.create("iso")
    profiles.activate(prof)
    try:
        assert launcher.legacy_custom_path("mods") == (
            catalog.custom_file("mods")
        )
    finally:
        profiles.activate(profiles.DEFAULT)


def test_duplicate_carries_content_repos(prof_home):
    src, _ = profiles.create("src", json.dumps(MINIMAL_CFG))
    payload = json.dumps(
        {"server": [{"name": "S"}], "custom": [{"name": "U"}]}
    )
    for kind in launcher.CONTENT_KINDS:
        with open(src.local_repo_path(kind), "w", encoding="utf-8") as f:
            f.write(payload)

    assert profiles.duplicate("src", "dst") == ""
    dst = profiles.resolve("dst")
    for kind in launcher.CONTENT_KINDS:
        with open(dst.local_repo_path(kind), encoding="utf-8") as f:
            repo = json.load(f)
        assert [e["name"] for e in repo["server"]] == ["S"]
        assert [e["name"] for e in repo["custom"]] == ["U"]


def test_duplicate_without_repos_stays_clean(prof_home):
    profiles.create("bare", json.dumps(MINIMAL_CFG))
    assert profiles.duplicate("bare", "dst") == ""
    dst = profiles.resolve("dst")
    for kind in launcher.CONTENT_KINDS:
        assert not os.path.exists(dst.local_repo_path(kind))


def test_persist_text_splits_content_into_active_profile(
    fake_home, real_repo_seams
):
    """persist_text writes the stripped config into the active profile's
    launcher.json AND lands the local repos inside the profile root."""
    from nostalgia_launcher.core.constants import CACHE_FILE

    prof, err = profiles.create("seeded")
    assert err == ""
    profiles.set_active("seeded")
    profiles.activate(prof)
    launcher.set_profile_launcher_path(prof.launcher_path())
    try:
        doc = {
            "server": {"base_url": "https://p.test"},
            "mods": [],
            "addons": [],
            "assets": [],
        }
        config_store.configure(prof.state_path(), CACHE_FILE)
        dest, err = launcher.persist_text(json.dumps(doc))
        assert err == ""
        assert dest == prof.launcher_path()
        for kind in launcher.CONTENT_KINDS:
            path = os.path.join(prof.root, f"local_{kind}_repo.json")
            with open(path, encoding="utf-8") as f:
                repo = json.load(f)
            assert repo == {"server": [], "custom": []}
    finally:
        launcher.set_profile_launcher_path("")


# ── path-safety: names must never address outside profiles/ ────────────


def test_path_like_names_are_not_addressable(prof_home):
    """--profile overrides and management APIs refuse separator/dot
    names instead of addressing directories outside profiles/."""
    profiles.create("real")
    for bad in ("../evil", "sub/dir", ".hidden", ".."):
        with pytest.raises(profiles.ProfileError, match="Unknown profile"):
            profiles.resolve(bad)
        assert profiles.delete(bad) == f"Unknown profile: {bad}"
        assert profiles.duplicate(bad, "dst") == f"Unknown profile: {bad}"
        assert profiles.rename(bad, "dst") == f"Unknown profile: {bad}"
    assert profiles.resolve("real").name == "real"


def test_duplicate_failure_rolls_back_new_profile(prof_home, monkeypatch):
    """A mid-copy failure must not leave a half-populated profile dir
    behind (the registry would list a broken profile)."""
    src, _ = profiles.create("src", json.dumps(MINIMAL_CFG))
    (prof_home / "src" / "state.json").write_text("{}", encoding="utf-8")
    # A content repo so duplicate() actually reaches its copy step.
    (prof_home / "src" / "local_mods_repo.json").write_text(
        '{"server": [], "custom": []}', encoding="utf-8"
    )

    def boom(src_path, dst_path):
        raise OSError("disk full")

    import nostalgia_launcher.core.profiles as profiles_module

    monkeypatch.setattr(profiles_module.shutil, "copyfile", boom)
    err = profiles.duplicate("src", "dst")
    assert "disk full" in err
    with pytest.raises(profiles.ProfileError):
        profiles.resolve("dst")
    assert not (prof_home / "dst").exists()

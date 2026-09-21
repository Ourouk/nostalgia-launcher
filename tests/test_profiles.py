"""Unit + wiring tests for the profile registry and artifact routing.

Every profile is a real directory under ``<config_dir>/profiles/<name>/``,
named after its server. There is no reserved default: an empty registry
resolves to None and the import wizard creates the first profile. CLI
tests exercise the ``--profile`` wiring through ``cli.main`` with the
backend stubbed.
"""

import json
import os

import pytest

import nostalgia_launcher.cli as cli
import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.core.launcher as launcher
import nostalgia_launcher.core.profiles as profiles
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


# ── empty registry: no implicit default ──────────────────────────────────


def test_empty_registry_resolves_to_none(prof_home):
    assert profiles.list_profiles() == []
    assert profiles.load_index() == {"active": "", "order": []}
    assert profiles.resolve() is None


def test_profile_dir_paths(prof_home):
    prof, err = profiles.create("P")
    assert err == ""
    assert prof.root == profiles.profile_root("P")
    assert prof.launcher_path() == os.path.join(prof.root, "launcher.json")
    assert prof.state_path() == os.path.join(prof.root, "state.json")
    assert prof.cache_path() == os.path.join(prof.root, "hash_cache.json")
    profiles.activate(prof)
    assert prof.launcher_path() == launcher.user_config_path()
    for kind in launcher.CONTENT_KINDS:
        assert prof.local_repo_path(kind) == os.path.join(
            prof.root, f"local_{kind}_repo.json"
        )
    assert prof.torrents_dir() == os.path.join(prof.root, "torrents")
    assert prof.logo_path() == os.path.join(prof.root, "launcher_logo.img")


def test_active_services_follow_activation(prof_home):
    """Artifact indirection yields the activated profile's paths."""
    prof, err = profiles.create("iso")
    assert err == ""
    profiles.activate(prof)
    assert logo.logo_cache_path() == os.path.join(
        prof.root, "launcher_logo.img"
    )
    assert tu.torrent_cache_dir() == os.path.join(prof.root, "torrents")


# ── resolve order ────────────────────────────────────────────────────────


def test_resolve_active_then_override(prof_home):
    assert profiles.resolve() is None
    profiles.create("alpha")
    profiles.create("beta")
    assert profiles.resolve().name == "alpha"
    profiles.set_active("beta")
    assert profiles.resolve().name == "beta"
    assert profiles.resolve("alpha").name == "alpha"


def test_unknown_override_raises(prof_home):
    profiles.create("real")
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
    ],
)
def test_invalid_names(name):
    assert profiles.validate_name(name)


@pytest.mark.parametrize(
    "name", ["A", "z9", "My Server", "p.t-1_x", "x" * 32, "default"]
)
def test_valid_names(name, prof_home):
    assert profiles.validate_name(name) == ""


# ── server-derived names ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("My Server", "My Server"),
        ("  spaced   out  ", "spaced out"),
        ("-dash", "dash"),
        (".dot", "dot"),
        ("trail. ", "trail"),
        ("../evil", "evil"),
        ("a/b\\c", "abc"),
        ("x" * 40, "x" * 32),
        ("", ""),
        ("   ", ""),
        ("...", ""),
        (None, ""),
        (123, ""),
    ],
)
def test_slugify(raw, expected):
    slug = profiles.slugify(raw)
    assert slug == expected
    if slug:
        assert profiles.validate_name(slug) == ""


@pytest.mark.parametrize(
    "server_name, host, expected",
    [
        ("My Server", "h.test", "My Server"),
        ("", "h.test", "h.test"),
        ("   ", "h.test", "h.test"),
        ("", "", "Server"),
        ("../evil", "", "evil"),
    ],
)
def test_profile_name_for(server_name, host, expected):
    assert profiles.profile_name_for(server_name, host) == expected


def test_unique_name_suffixes_on_collision(prof_home):
    assert profiles.unique_name("P") == "P"
    profiles.create("P")
    assert profiles.unique_name("P") == "P 2"
    profiles.create("P 2")
    assert profiles.unique_name("P") == "P 3"


def test_unique_name_truncates_to_cap(prof_home):
    base = "y" * 32
    profiles.create(base)
    got = profiles.unique_name(base)
    assert len(got) <= 32
    assert profiles.validate_name(got) == ""
    assert got.endswith(" 2")


# ── create / delete guards ───────────────────────────────────────────────


def test_create_seeds_config_and_order(prof_home):
    prof, err = profiles.create("seeded", json.dumps(MINIMAL_CFG))
    assert err == ""
    assert prof.root == str(prof_home / "seeded")
    assert os.path.isdir(prof.root)
    seeded = json.loads(
        (prof_home / "seeded" / "launcher.json").read_text("utf-8")
    )
    assert seeded["server"]["base_url"] == "https://p.test"
    assert profiles.list_profiles() == ["seeded"]


def test_create_rejects_existing(prof_home):
    profiles.create("dup")
    _prof, err = profiles.create("dup")
    assert err
    _prof, err = profiles.create("bad/")
    assert err


def test_delete_unknown(prof_home):
    assert profiles.delete("ghost")


def test_delete_active_falls_back_to_first_remaining(prof_home):
    profiles.create("a")
    profiles.create("b")
    profiles.set_active("b")
    assert profiles.delete("b") == ""
    idx = profiles.load_index()
    assert idx["active"] == "a"
    assert profiles.resolve().name == "a"


def test_delete_last_leaves_empty_registry(prof_home):
    profiles.create("only")
    profiles.set_active("only")
    assert profiles.delete("only") == ""
    assert profiles.load_index() == {"active": "", "order": []}
    assert profiles.list_profiles() == []
    assert profiles.resolve() is None


def test_reset_refuses_unknown(prof_home):
    assert profiles.reset("ghost")


def test_reset_wipes_all_local_artifacts_leaves_dir(
    prof_home, real_repo_seams
):
    """Reset removes every per-profile artifact but keeps the empty directory
    and the registry entry, so the profile stays reconfigurable."""
    prof, _ = profiles.create(
        "wipe", launcher_json_text=json.dumps(MINIMAL_CFG)
    )
    # Seed the artifacts the wipe is expected to remove.
    for path in (
        prof.state_path(),
        prof.cache_path(),
        prof.logo_path(),
        prof.torrents_dir(),
    ):
        if path.endswith((".json", ".img")):
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
        else:
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "x.txt"), "w") as f:
                f.write("x")
    for kind in launcher.CONTENT_KINDS:
        with open(prof.local_repo_path(kind), "w", encoding="utf-8") as f:
            f.write(json.dumps({"server": [], "custom": []}))

    assert profiles.reset("wipe") == ""

    # Profile directory survives, but every artifact is gone.
    assert os.path.isdir(prof.root)
    for path in (
        prof.launcher_path(),
        prof.state_path(),
        prof.cache_path(),
        prof.logo_path(),
        prof.torrents_dir(),
    ):
        assert not os.path.exists(path), path
    for kind in launcher.CONTENT_KINDS:
        assert not os.path.exists(prof.local_repo_path(kind))
    # Still registered (reconfigurable), not deleted from the index.
    assert "wipe" in profiles.list_profiles()


def test_reset_active_then_reconfigurable(prof_home, real_repo_seams):
    """Resetting a profile leaves it in the same 'unconfigured' state a fresh
    profile starts in (no launcher.json)."""
    prof, _ = profiles.create(
        "wipe", launcher_json_text=json.dumps(MINIMAL_CFG)
    )
    assert os.path.exists(prof.launcher_path())
    assert profiles.reset("wipe") == ""
    assert not os.path.exists(prof.launcher_path())
    assert "wipe" in profiles.list_profiles()


# ── corrupt / missing index recovery ────────────────────────────────────


def test_corrupt_index_rebuilds_from_disk(prof_home, tmp_path):
    (prof_home / "alpha").mkdir(parents=True)
    index = profiles.index_path()
    os.makedirs(os.path.dirname(index), exist_ok=True)
    _write_index(tmp_path / "profiles.json", "{not json")
    assert profiles.list_profiles() == ["alpha"]
    assert profiles.resolve().name == "alpha"


def test_ghost_active_falls_back_to_first_profile(prof_home):
    (prof_home / "alpha").mkdir(parents=True)
    _write_index(
        profiles.index_path(), {"active": "ghost", "order": ["alpha"]}
    )
    idx = profiles.load_index()
    assert idx["active"] == "alpha"
    assert idx["order"] == ["alpha"]
    assert profiles.resolve().name == "alpha"


def test_ghost_active_with_no_profiles_resolves_to_none(prof_home):
    _write_index(profiles.index_path(), {"active": "ghost", "order": []})
    assert profiles.load_index() == {"active": "", "order": []}
    assert profiles.resolve() is None


# ── artifact indirection with an active profile ──────────────────────────


def test_artifacts_route_into_profile(prof_home, real_repo_seams):
    prof, err = profiles.create("iso")
    assert err == ""
    prev = profiles._ACTIVE
    profiles.activate(prof)
    try:
        assert logo.logo_cache_path() == os.path.join(
            prof.root, "launcher_logo.img"
        )
        assert tu.torrent_cache_dir() == os.path.join(prof.root, "torrents")
    finally:
        profiles._ACTIVE = prev


# ── CLI wiring ───────────────────────────────────────────────────────────


def test_cli_unknown_profile_exits_2(fake_home, capsys):
    profiles.create("real")
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


def _active_config():
    with open(launcher.user_config_path(), encoding="utf-8") as f:
        return json.load(f)


def test_cli_good_profile_routes_stores(fake_home, monkeypatch):
    prof, err = profiles.create("good", json.dumps(MINIMAL_CFG))
    assert err == ""
    seen = _spy_configure(monkeypatch)
    assert cli.main(["--profile", "good"]) == 1
    assert seen["cfg"] == os.path.join(prof.root, "state.json")
    assert seen["cache"] == os.path.join(prof.root, "hash_cache.json")
    assert launcher.server_url() == "https://p.test"


def test_cli_first_run_in_profile_persists_wizard_choice(
    fake_home, monkeypatch
):
    """The wizard creates the server-named profile (MINIMAL_CFG names
    server "P") and persists into it; the unconfigured "fresh" dir is
    left alone for the user to delete."""
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
    named = profiles.resolve("P")
    with open(named.launcher_path(), encoding="utf-8") as f:
        persisted = json.load(f)
    assert persisted["server"]["base_url"] == "https://p.test"
    assert profiles.load_index()["active"] == "P"
    assert profiles.active().name == "P"


def test_cli_no_profiles_wizard_creates_named_profile(fake_home, monkeypatch):
    """Zero profiles, no flags: the import wizard creates the first
    profile under its server's name."""
    _wizard_stub(monkeypatch)
    assert cli.main([]) == 0
    assert profiles.list_profiles() == ["P"]
    assert profiles.load_index()["active"] == "P"
    assert _active_config()["server"]["base_url"] == "https://p.test"


def test_cli_override_with_no_profiles_wizards(fake_home, monkeypatch):
    """--profile with nothing to match against drops into the import
    wizard instead of exiting 2 (the name derives from the server)."""
    _wizard_stub(monkeypatch)
    assert cli.main(["--profile", "whatever"]) == 0
    assert profiles.list_profiles() == ["P"]


def test_cli_explicit_file_with_no_profiles_seeds_named_profile(
    fake_home, tmp_path, monkeypatch
):
    """Zero profiles + --launcher-config: the file seeds the first
    profile (named after its server) with no wizard involved."""
    other = tmp_path / "other.json"
    other.write_text(
        json.dumps({"server": {"name": "Other", "url": "https://o.test"}}),
        encoding="utf-8",
    )
    _stub_backend(monkeypatch)
    assert cli.main(["--launcher-config", str(other)]) == 0
    assert profiles.list_profiles() == ["Other"]
    assert launcher.server_name() == "Other"
    with open(
        profiles.resolve("Other").launcher_path(), encoding="utf-8"
    ) as f:
        assert json.load(f)["server"]["name"] == "Other"


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


# ── delete-the-last-profile resets to the setup wizard ─────────────────


def test_deleting_last_profile_next_launch_wizards(fake_home, monkeypatch):
    """Deleting the only profile and declining any restart: the NEXT
    plain launch finds an empty registry and lands on the setup
    wizard, which creates the server-named profile."""
    prof, err = profiles.create("only", json.dumps(MINIMAL_CFG))
    assert err == ""
    profiles.set_active("only")
    profiles.activate(profiles.resolve("only"))
    assert profiles.active().name == "only"

    assert profiles.delete("only") == ""
    assert profiles.load_index() == {"active": "", "order": []}

    _wizard_stub(monkeypatch)
    assert cli.main([]) == 0

    assert profiles.list_profiles() == ["P"]
    assert profiles.load_index()["active"] == "P"
    assert not os.path.exists(str(prof.root))
    assert _active_config()["server"]["base_url"] == "https://p.test"


# ── local content repos (post main-merge: import-time split) ───────────


def test_local_repos_route_into_profile(prof_home, real_repo_seams):
    """launcher.local_repo_path resolves through the active profile."""
    prof, err = profiles.create("iso")
    assert err == ""
    prev = profiles._ACTIVE
    profiles.activate(prof)
    try:
        for kind in launcher.CONTENT_KINDS:
            assert launcher.local_repo_path(kind) == os.path.join(
                prof.root, f"local_{kind}_repo.json"
            )
    finally:
        profiles._ACTIVE = prev


# ── legacy default adoption ──────────────────────────────────────────────


def test_adopt_legacy_default_renames_after_server(prof_home):
    """A legacy profiles/default/ with a config is renamed after its
    server; the index pointer follows. Nothing is deleted."""
    legacy = prof_home / "default"
    legacy.mkdir(parents=True)
    (legacy / "launcher.json").write_text(
        json.dumps(
            {"server": {"name": "Old Server", "url": "https://o.test"}}
        ),
        encoding="utf-8",
    )
    (legacy / "state.json").write_text("{}", encoding="utf-8")
    _write_index(
        profiles.index_path(), {"active": "default", "order": ["default"]}
    )
    assert profiles.adopt_legacy_default() == "Old Server"
    assert not os.path.exists(str(legacy))
    adopted = profiles.resolve("Old Server")
    assert os.path.exists(adopted.launcher_path())
    assert os.path.exists(adopted.state_path())
    idx = profiles.load_index()
    assert idx["active"] == "Old Server"
    assert idx["order"] == ["Old Server"]


def test_adopt_legacy_default_uses_host_when_unnamed(prof_home):
    legacy = prof_home / "default"
    legacy.mkdir(parents=True)
    (legacy / "launcher.json").write_text(
        json.dumps({"server": {"url": "https://o.test/x"}}),
        encoding="utf-8",
    )
    assert profiles.adopt_legacy_default() == "o.test"


def test_adopt_legacy_default_suffixes_on_collision(prof_home):
    profiles.create("Old Server")
    legacy = prof_home / "default"
    legacy.mkdir(parents=True)
    (legacy / "launcher.json").write_text(
        json.dumps({"server": {"name": "Old Server"}}),
        encoding="utf-8",
    )
    assert profiles.adopt_legacy_default() == "Old Server 2"


def test_adopt_legacy_default_drops_empty_dir(prof_home):
    (prof_home / "default").mkdir(parents=True)
    _write_index(
        profiles.index_path(), {"active": "default", "order": ["default"]}
    )
    assert profiles.adopt_legacy_default() == ""
    assert not os.path.exists(str(prof_home / "default"))
    assert profiles.load_index() == {"active": "", "order": []}


def test_adopt_legacy_default_keeps_nonempty_unconfigured(prof_home):
    """A default dir with user data but no config is NOT adopted or
    deleted — it is an ordinary profile now."""
    legacy = prof_home / "default"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text("{}", encoding="utf-8")
    assert profiles.adopt_legacy_default() == ""
    assert os.path.exists(str(legacy / "state.json"))
    assert profiles.resolve("default").name == "default"


def test_adopt_legacy_default_noop_without_legacy(prof_home):
    assert profiles.adopt_legacy_default() == ""


def test_cli_adopts_legacy_default_on_startup(fake_home, monkeypatch):
    """A real upgrade: legacy default dir + pointer, then plain launch
    with a configured adopted profile skips the wizard."""
    from nostalgia_launcher.core import platform_support

    legacy = os.path.join(platform_support.config_dir(), "profiles", "default")
    os.makedirs(legacy, exist_ok=True)
    with open(
        os.path.join(legacy, "launcher.json"), "w", encoding="utf-8"
    ) as f:
        f.write(json.dumps(MINIMAL_CFG))
    profiles.save_index({"active": "default", "order": ["default"]})
    _stub_backend(monkeypatch)
    assert cli.main([]) == 0
    assert profiles.list_profiles() == ["P"]
    assert profiles.active().name == "P"


def test_persist_text_splits_content_into_active_profile(
    fake_home, real_repo_seams
):
    """persist_text writes the stripped config into the active profile's
    launcher.json AND lands the local repos inside the profile root."""
    prof, err = profiles.create("seeded")
    assert err == ""
    profiles.set_active("seeded")
    profiles.activate(prof)
    doc = {
        "server": {"base_url": "https://p.test"},
        "mods": [],
        "addons": [],
        "assets": [],
    }
    config_store.configure(prof.state_path(), prof.cache_path())
    dest, err = launcher.persist_text(json.dumps(doc))
    assert err == ""
    assert dest == prof.launcher_path()
    for kind in launcher.CONTENT_KINDS:
        path = os.path.join(prof.root, f"local_{kind}_repo.json")
        with open(path, encoding="utf-8") as f:
            repo = json.load(f)
        assert repo == {"server": [], "custom": []}


# ── path-safety: names must never address outside profiles/ ────────────


def test_path_like_names_are_not_addressable(prof_home):
    """--profile overrides and management APIs refuse separator/dot
    names instead of addressing directories outside profiles/."""
    profiles.create("real")
    for bad in ("../evil", "sub/dir", ".hidden", ".."):
        with pytest.raises(profiles.ProfileError, match="Unknown profile"):
            profiles.resolve(bad)
        assert profiles.delete(bad) == f"Unknown profile: {bad}"
    assert profiles.resolve("real").name == "real"

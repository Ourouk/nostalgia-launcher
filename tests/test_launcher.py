"""Unit tests for the launcher configuration (core/launcher)."""

import json

import pytest

from nostalgia_launcher.core import launcher


@pytest.fixture(autouse=True)
def _clean():
    launcher.reset()
    yield
    launcher.reset()


def _config(data):
    return launcher.configure_from_dict(data)


def test_derives_endpoints_from_base_url():
    cfg = _config(
        {"server": {"name": "My", "base_url": "https://srv.example"}}
    )
    assert cfg is not None
    assert cfg.server_url == "https://srv.example"
    assert cfg.manifest_url == (
        "https://srv.example/api/file/latest/manifest.json"
    )
    assert cfg.client_url == "https://srv.example/client/latest"
    assert cfg.news_url == "https://srv.example/news.json"
    assert cfg.featured_news_url == ("https://srv.example/news/featured.json")
    assert cfg.mods_registry_url == "https://srv.example/api/mods.json"
    assert cfg.addons_registry_urls == ["https://srv.example/api/addons.json"]
    assert cfg.realm == "srv.example"
    assert cfg.discord_url is None


@pytest.mark.parametrize("value", [None, "", "   "])
def test_optional_discord_url_can_be_omitted_or_empty(value):
    data = {"server": {"base_url": "https://srv.example"}}
    if value is not None:
        data["discord_url"] = value
    cfg = _config(data)
    assert cfg is not None
    assert cfg.discord_url is None


def test_discord_url_accepts_https():
    cfg = _config(
        {
            "server": {"base_url": "https://srv.example"},
            "discord_url": " https://discord.gg/example/ ",
        }
    )
    assert cfg.discord_url == "https://discord.gg/example"


@pytest.mark.parametrize(
    "value", ["http://discord.gg/example", "discord.gg/example", 42]
)
def test_discord_url_rejects_invalid_nonempty_value(value):
    assert (
        _config(
            {
                "server": {"base_url": "https://srv.example"},
                "discord_url": value,
            }
        )
        is None
    )
    assert "discord_url" in launcher.config_error()


def test_addons_registry_urls_override_order():
    cfg = _config(
        {
            "server": {
                "base_url": "https://srv.example",
                "addons_registry_urls": [
                    "https://a.example/official.json",
                    "https://b.example/overrides.json",
                ],
            }
        }
    )
    assert cfg is not None
    assert cfg.addons_registry_urls == [
        "https://a.example/official.json",
        "https://b.example/overrides.json",
    ]


def test_addons_registry_urls_drop_insecure_entries():
    cfg = _config(
        {
            "server": {
                "base_url": "https://srv.example",
                "addons_registry_urls": [
                    "https://a.example/ok.json",
                    "http://b.example/insecure.json",
                ],
            }
        }
    )
    assert cfg is not None
    assert cfg.addons_registry_urls == ["https://a.example/ok.json"]


def test_server_manifest_and_client_overrides():
    cfg = _config(
        {
            "server": {
                "base_url": "https://srv.example",
                "manifest_url": "https://cdn.example/api/manifest.json",
                "client_url": "https://dl.example/client/latest",
            }
        }
    )
    assert cfg.manifest_url == "https://cdn.example/api/manifest.json"
    assert cfg.client_url == "https://dl.example/client/latest"


# ── embedded mods (top-level "mods") ─────────────────────────────────────────


def test_embedded_mods_kept_raw_dicts_only():
    a = {"id": "A", "name": "A", "source": {"kind": "direct_file"}}
    b = {"id": "B"}
    cfg = _config(
        {
            "server": {"base_url": "https://srv.example"},
            "mods": [a, "junk", 42, b, None],
        }
    )
    assert cfg is not None
    # Kept as-is (sanitizing happens in services/mods); non-dicts dropped.
    assert cfg.embedded_mods == [a, b]


@pytest.mark.parametrize("value", [None, "mods", 42, {"id": "A"}])
def test_embedded_mods_ignores_non_list(value):
    data = {"server": {"base_url": "https://srv.example"}}
    if value is not None:
        data["mods"] = value
    cfg = _config(data)
    assert cfg is not None
    assert cfg.embedded_mods == []


def test_embedded_mods_accessor_and_reset():
    entry = {"id": "A", "name": "A", "source": {"kind": "direct_file"}}
    _config({"server": {"base_url": "https://srv.example"}, "mods": [entry]})
    assert launcher.embedded_mods() == [entry]
    launcher.reset()
    assert launcher.embedded_mods() == []


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_mods_registry_url_not_explicit_when_missing_or_blank(raw):
    data = {"server": {"base_url": "https://srv.example"}}
    if raw is not None:
        data["server"]["mods_registry_url"] = raw
    cfg = _config(data)
    assert cfg is not None
    # The base_url-derived default still applies…
    assert cfg.mods_registry_url == "https://srv.example/api/mods.json"
    # …but it does not count as an explicitly configured catalog.
    assert cfg.mods_registry_url_explicit is False


def test_mods_registry_url_explicit_flag():
    cfg = _config(
        {
            "server": {
                "base_url": "https://srv.example",
                "mods_registry_url": "https://other.example/mods.json",
            }
        }
    )
    assert cfg.mods_registry_url_explicit is True


def test_endpoint_overrides_and_realm():
    cfg = _config(
        {
            "server": {
                "base_url": "https://srv.example",
                "realm": "realms.example",
                "news_url": "https://other.example/news",
                "mods_registry_url": "https://other.example/mods.json",
            }
        }
    )
    assert cfg.news_url == "https://other.example/news"
    assert cfg.mods_registry_url == "https://other.example/mods.json"
    assert cfg.realm == "realms.example"


def test_has_torrent_any_source():
    assert (
        _config(
            {
                "server": {
                    "base_url": "https://srv.example",
                    "torrent_url": "https://srv.example/client.torrent",
                }
            }
        ).has_torrent()
        is True
    )
    # Mirror-only torrent still counts (server fallback when a mirror wins).
    assert (
        _config(
            {
                "server": {"base_url": "https://srv.example"},
                "mirrors": [
                    {
                        "name": "M",
                        "base_url": "https://m1.example",
                        "torrent_url": "https://m1.example/client.torrent",
                    }
                ],
            }
        ).has_torrent()
        is True
    )
    assert (
        _config({"server": {"base_url": "https://srv.example"}}).has_torrent()
        is False
    )


def test_missing_base_url_is_error():
    assert _config({"server": {"realm": "x"}}) is None
    assert "base_url" in launcher.config_error()
    assert _config({}) is None


def test_rejects_non_https_server():
    assert _config({"server": {"base_url": "http://insecure.example"}}) is None


def test_mirrors_parsed_with_default_endpoints():
    cfg = _config(
        {
            "server": {"base_url": "https://srv.example"},
            "mirrors": [
                {"name": "Backup", "base_url": "https://m1.example"},
                {
                    "name": "Second",
                    "base_url": "https://m2.example",
                    "manifest_url": "https://m2.example/custom/manifest.json",
                },
                {"name": "skip-http", "base_url": "http://nope.example"},
            ],
        }
    )
    assert len(cfg.mirrors) == 2
    assert cfg.mirrors[0].name == "Backup"
    assert cfg.mirrors[0].base_url == "https://m1.example"
    assert cfg.mirrors[0].manifest_url == (
        "https://m1.example/api/file/latest/manifest.json"
    )
    assert cfg.mirrors[1].manifest_url == (
        "https://m2.example/custom/manifest.json"
    )


def test_download_hosts_cover_server_and_mirrors():
    cfg = _config(
        {
            "server": {"base_url": "https://srv.example"},
            "mirrors": [{"base_url": "https://m1.example"}],
        }
    )
    assert cfg.download_hosts() == {"srv.example", "m1.example"}


def test_download_hosts_cover_custom_manifest_client_hosts():
    cfg = _config(
        {
            "server": {"base_url": "https://srv.example"},
            "mirrors": [
                {
                    "base_url": "https://m1.example",
                    "manifest_url": "https://api.example/m.json",
                    "client_url": "https://dl.example/client/latest",
                }
            ],
        }
    )
    assert cfg.download_hosts() == {
        "srv.example",
        "m1.example",
        "api.example",
        "dl.example",
    }


def test_configure_from_file(tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    path.write_text(
        json.dumps({"server": {"base_url": "https://file.example"}}),
        encoding="utf-8",
    )
    cfg, err = launcher.configure(str(path))
    assert err == ""
    assert cfg.server_url == "https://file.example"


def test_configure_invalid_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    cfg, err = launcher.configure(str(path))
    assert cfg is None
    assert "Invalid launcher configuration" in err


def test_configure_missing_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        launcher, "user_config_path", lambda: str(tmp_path / "none.json")
    )
    cfg, err = launcher.configure("")
    assert cfg is None
    assert "required" in err


def test_user_config_path_routes_through_active_profile():
    """user_config_path() resolves into the active profile's directory."""
    from nostalgia_launcher.core import profiles

    assert launcher.user_config_path() == profiles.active().launcher_path()


def test_auto_path_prefers_persisted_user_config(monkeypatch, tmp_path):
    user = tmp_path / "nostalgia_launcher.json"
    user.write_text(
        json.dumps({"server": {"base_url": "https://srv.example"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(user))
    monkeypatch.setattr(
        launcher, "discover_path", lambda: "/elsewhere/config.json"
    )
    assert launcher._auto_path() == str(user)


def test_discover_path_macos_frozen_finds_config_next_to_bundle(
    monkeypatch, tmp_path
):
    """A frozen macOS .app must find nostalgia_launcher.json sitting next
    to the bundle (e.g. in the DMG root), not just in Contents/MacOS."""
    bundle = tmp_path / "NostalgiaLauncher.app"
    exe = bundle / "Contents" / "MacOS" / "NostalgiaLauncher"
    exe.parent.mkdir(parents=True)
    cfg = tmp_path / "nostalgia_launcher.json"
    cfg.write_text(
        json.dumps({"server": {"base_url": "https://srv.example"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launcher.sys, "executable", str(exe))
    monkeypatch.setattr(launcher, "is_macos", lambda: True)
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)  # cwd must not be needed

    assert launcher.discover_path() == str(cfg)


def test_auto_path_falls_back_to_discovery(monkeypatch, tmp_path):
    monkeypatch.setattr(
        launcher, "user_config_path", lambda: str(tmp_path / "none.json")
    )
    monkeypatch.setattr(
        launcher, "discover_path", lambda: "/elsewhere/config.json"
    )
    assert launcher._auto_path() == "/elsewhere/config.json"


def test_configure_uses_persisted_user_config(monkeypatch, tmp_path):
    user = tmp_path / "nostalgia_launcher.json"
    user.write_text(
        json.dumps({"server": {"base_url": "https://user.example"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(user))
    monkeypatch.setattr(launcher, "discover_path", lambda: "")
    cfg, err = launcher.configure("")
    assert err == ""
    assert cfg.server_url == "https://user.example"


def test_configure_explicit_overrides_persisted(monkeypatch, tmp_path):
    user = tmp_path / "user.json"
    user.write_text(
        json.dumps({"server": {"base_url": "https://user.example"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(user))
    explicit = tmp_path / "explicit.json"
    explicit.write_text(
        json.dumps({"server": {"base_url": "https://explicit.example"}}),
        encoding="utf-8",
    )
    cfg, err = launcher.configure(str(explicit))
    assert err == ""
    assert cfg.server_url == "https://explicit.example"


def test_persist_copies_config_to_user_path(monkeypatch, tmp_path):
    src = tmp_path / "src.json"
    src.write_text(
        json.dumps({"server": {"base_url": "https://srv.example"}}),
        encoding="utf-8",
    )
    dest = tmp_path / "user" / "nostalgia_launcher.json"
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(dest))
    got, err = launcher.persist(str(src))
    assert err == ""
    assert got == str(dest)
    assert dest.exists()
    assert json.loads(dest.read_text()) == {
        "server": {"base_url": "https://srv.example"}
    }


def test_persist_rejects_invalid_json(monkeypatch, tmp_path):
    src = tmp_path / "bad.json"
    src.write_text("{not json", encoding="utf-8")
    dest = tmp_path / "dest.json"
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(dest))
    got, err = launcher.persist(str(src))
    assert err
    assert got == ""
    assert not dest.exists()


def test_persist_rejects_semantically_invalid_config(monkeypatch, tmp_path):
    """Parseable JSON that isn't a valid launcher config (no server.base_url)
    must not be persisted."""
    src = tmp_path / "bad.json"
    src.write_text(json.dumps({"server": {}}), encoding="utf-8")
    dest = tmp_path / "dest.json"
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(dest))
    got, err = launcher.persist(str(src))
    assert err
    assert got == ""
    assert not dest.exists()


def test_auto_path_prefers_invalid_persisted_file_over_discovery(
    monkeypatch, tmp_path
):
    """An existing persisted file wins over discovery even when invalid, so
    startup surfaces the corruption instead of silently switching servers."""
    user = tmp_path / "nostalgia_launcher.json"
    user.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(user))
    monkeypatch.setattr(
        launcher, "discover_path", lambda: "/elsewhere/config.json"
    )
    assert launcher._auto_path() == str(user)


def test_persist_reports_write_failure(monkeypatch, tmp_path):
    src = tmp_path / "src.json"
    src.write_text(
        json.dumps({"server": {"base_url": "https://srv.example"}}),
        encoding="utf-8",
    )

    def _boom(*a, **k):
        raise OSError("read-only fs")

    monkeypatch.setattr(launcher.os, "makedirs", _boom)
    got, err = launcher.persist(str(src))
    assert err
    assert got == ""


def test_validate_path_valid(tmp_path):
    path = tmp_path / "nostalgia_launcher.json"
    path.write_text(
        json.dumps({"server": {"base_url": "https://launcher.test"}}),
        encoding="utf-8",
    )
    config, err = launcher.validate_path(str(path))
    assert err == ""
    assert config is not None
    assert config.server_url == "https://launcher.test"


def test_validate_path_missing_file(tmp_path):
    config, err = launcher.validate_path(str(tmp_path / "nope.json"))
    assert config is None
    assert err


def test_validate_path_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b"not json")
    config, err = launcher.validate_path(str(path))
    assert config is None
    assert err


def test_validate_path_does_not_touch_active_config(tmp_path):
    launcher.configure_from_dict(
        {"server": {"base_url": "https://launcher.test"}}
    )
    before = launcher.config()
    assert before is not None
    invalid = tmp_path / "bad.json"
    invalid.write_bytes(b"not json")
    config, err = launcher.validate_path(str(invalid))
    assert config is None
    assert err
    assert launcher.config() is before


def test_accessors_empty_when_not_configured():
    launcher.reset()
    assert launcher.server_url() == ""
    assert launcher.news_url() == ""
    assert launcher.mods_registry_url() == ""
    assert launcher.mirrors() == []


def test_theme_dict_parses():
    cfg = _config(
        {
            "server": {"base_url": "https://srv.example"},
            "theme": {
                "C_GOLD": "#d4a02f",
                "logo": "https://srv.example/logo.png",
            },
        }
    )
    assert cfg is not None
    assert cfg.theme == {
        "C_GOLD": "#d4a02f",
        "logo": "https://srv.example/logo.png",
    }


def test_theme_omitted_is_none():
    cfg = _config({"server": {"base_url": "https://srv.example"}})
    assert cfg is not None
    assert cfg.theme is None


def test_non_dict_theme_parses_as_none_and_does_not_fail_config():
    for value in ("bogus", 42, ["C_GOLD"]):
        cfg = _config(
            {
                "server": {"base_url": "https://srv.example"},
                "theme": value,
            }
        )
        assert cfg is not None
        assert cfg.theme is None


# ── import-time content split ────────────────────────────────────────────────


@pytest.fixture
def user_dirs(tmp_path, monkeypatch):
    """Redirect the persisted launcher config and every local repo file
    into tmp_path."""
    dest = tmp_path / "user" / "nostalgia_launcher.json"
    monkeypatch.setattr(launcher, "user_config_path", lambda: str(dest))
    monkeypatch.setattr(
        launcher,
        "local_repo_path",
        lambda kind: str(tmp_path / "user" / f"local_{kind}_repo.json"),
    )
    return dest, tmp_path / "user"


def _import_file(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


_MOD = {
    "id": "M1",
    "name": "M1",
    "source": {
        "kind": "direct_file",
        "url": "https://x.test/m.dll",
        "dest": "m.dll",
    },
}
_ADDON = {"name": "A1", "git": "https://github.com/x/a1"}
_ASSET = {
    "id": "P1",
    "url": "https://x.test/p.mpq",
    "dest": "Data/p.mpq",
}


def test_persist_splits_sections_into_local_repos(tmp_path, user_dirs):
    dest, user = user_dirs
    src = _import_file(
        tmp_path,
        "cfg.json",
        {
            "server": {"base_url": "https://srv.example"},
            "mods": [_MOD],
            "addons": [_ADDON],
            "assets": [_ASSET],
        },
    )
    got, err = launcher.persist(str(src))
    assert err == ""
    assert got == str(dest)
    # The persisted config is stripped of every content section.
    assert json.loads(dest.read_text()) == {
        "server": {"base_url": "https://srv.example"}
    }
    for kind, entries in (
        ("mods", [_MOD]),
        ("addons", [_ADDON]),
        ("assets", [_ASSET]),
    ):
        repo = json.loads((user / f"local_{kind}_repo.json").read_text())
        assert repo == {"server": entries, "custom": []}


def test_persist_writes_empty_repos_when_sections_absent(tmp_path, user_dirs):
    _, user = user_dirs
    src = _import_file(
        tmp_path, "cfg.json", {"server": {"base_url": "https://srv.example"}}
    )
    assert launcher.persist(str(src))[1] == ""
    for kind in ("mods", "addons", "assets"):
        repo = json.loads((user / f"local_{kind}_repo.json").read_text())
        assert repo == {"server": [], "custom": []}


def test_reimport_replaces_server_keeps_custom(tmp_path, user_dirs):
    _, user = user_dirs
    first = _import_file(
        tmp_path,
        "a.json",
        {"server": {"base_url": "https://srv.example"}, "mods": [_MOD]},
    )
    assert launcher.persist(str(first))[1] == ""
    custom = {
        "id": "Mine",
        "name": "Mine",
        "source": {
            "kind": "direct_file",
            "url": "https://x.test/x.dll",
            "dest": "x.dll",
        },
    }
    repo_path = user / "local_mods_repo.json"
    repo = json.loads(repo_path.read_text())
    repo["custom"] = [custom]
    repo_path.write_text(json.dumps(repo), encoding="utf-8")

    mod2 = dict(_MOD, id="M2")
    second = _import_file(
        tmp_path,
        "b.json",
        {"server": {"base_url": "https://srv.example"}, "mods": [mod2]},
    )
    assert launcher.persist(str(second))[1] == ""
    merged = json.loads(repo_path.read_text())
    assert merged["server"] == [mod2]
    assert merged["custom"] == [custom]


def test_validate_helpers_never_write_repo_files(tmp_path, user_dirs):
    dest, user = user_dirs
    src = _import_file(
        tmp_path,
        "cfg.json",
        {"server": {"base_url": "https://srv.example"}, "mods": [_MOD]},
    )
    cfg, err = launcher.validate_path(str(src))
    assert err == "" and cfg is not None
    _data, verr = launcher.validate_dict(json.loads(src.read_text()))
    assert verr == "" and _data is not None
    assert not dest.exists()
    assert not list(user.glob("local_*_repo.json"))


def test_repo_failure_aborts_import_without_persisting(
    tmp_path, user_dirs, monkeypatch
):
    dest, _user = user_dirs

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(launcher, "store_local_repo", boom)
    src = _import_file(
        tmp_path,
        "cfg.json",
        {"server": {"base_url": "https://srv.example"}, "mods": [_MOD]},
    )
    got, err = launcher.persist(str(src))
    assert got == "" and err
    assert not dest.exists()


def test_derive_parses_embedded_addons_raw():
    cfg = _config(
        {
            "server": {"base_url": "https://srv.example"},
            "addons": [_ADDON, "junk", 42],
        }
    )
    assert cfg.embedded_addons == [_ADDON]


def test_repo_failure_rolls_back_earlier_repos(
    tmp_path, user_dirs, monkeypatch
):
    """A mid-split failure must leave every previously written repo at its
    prior content (the documented never-half-applied invariant)."""
    dest, user = user_dirs
    # A pre-existing mods repo with known content.
    user.mkdir(parents=True, exist_ok=True)
    mods_repo = user / "local_mods_repo.json"
    mods_repo.write_text(
        json.dumps({"server": [{"id": "Old"}], "custom": []}),
        encoding="utf-8",
    )

    real_store = launcher.store_local_repo

    def flaky(kind, server, custom):
        if kind == "addons":
            raise OSError("disk full")
        real_store(kind, server, custom)

    monkeypatch.setattr(launcher, "store_local_repo", flaky)
    src = _import_file(
        tmp_path,
        "cfg.json",
        {"server": {"base_url": "https://srv.example"}, "mods": [_MOD]},
    )
    got, err = launcher.persist(str(src))
    assert got == "" and err
    # mods was written before addons failed -> rolled back to prior bytes.
    assert json.loads(mods_repo.read_text()) == {
        "server": [{"id": "Old"}],
        "custom": [],
    }
    assert not (user / "local_addons_repo.json").exists()
    assert not dest.exists()


def test_config_write_failure_rolls_back_repos(
    tmp_path, user_dirs, monkeypatch
):
    """A failing launcher-config write rolls the fresh repos back (a brand-
    new repo disappears; a pre-existing one keeps its old bytes)."""
    dest, user = user_dirs
    user.mkdir(parents=True, exist_ok=True)
    assets_repo = user / "local_assets_repo.json"
    assets_repo.write_text(
        json.dumps({"server": [{"id": "Keep"}], "custom": []}),
        encoding="utf-8",
    )

    def boom():
        raise OSError("config dir unwritable")

    monkeypatch.setattr(
        launcher, "_derive", lambda d: None
    )  # skip semantic validation
    src = _import_file(
        tmp_path,
        "cfg.json",
        {"server": {"base_url": "https://srv.example"}},
    )
    # Inject the failure at the config write itself.
    orig_replace = launcher.os.replace

    def replace_fail(src_, dst, *a, **k):
        if str(dst) == str(dest):
            raise OSError("unwritable")
        return orig_replace(src_, dst, *a, **k)

    monkeypatch.setattr(launcher.os, "replace", replace_fail)
    got, err = launcher.persist(str(src))
    assert got == "" and err
    assert not (user / "local_mods_repo.json").exists()
    assert not (user / "local_addons_repo.json").exists()
    assert json.loads(assets_repo.read_text())["server"] == [{"id": "Keep"}]

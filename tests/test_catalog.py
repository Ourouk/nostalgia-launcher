"""Unit tests for the shared catalog plumbing (services/catalog).

Validation, custom-file loading, URL storage and merge precedence are all
network-free; the fetch entry points live in services/mods.py and
services/addons.py and are covered there.
"""

import nostalgia_launcher.core.config_store as config_store
import nostalgia_launcher.services.catalog as catalog

# ── shared validators ────────────────────────────────────────────────────────


def test_safe_folder_rejects_traversal_and_separators():
    assert catalog.safe_folder("pfUI")
    assert catalog.safe_folder("My_Addon-2")
    assert not catalog.safe_folder("../pfUI")
    assert not catalog.safe_folder("a/b")
    assert not catalog.safe_folder(".")
    assert not catalog.safe_folder("..")
    assert not catalog.safe_folder("")
    assert not catalog.safe_folder("a\\b")


def test_safe_relpath_rejects_absolute_and_traversal():
    assert catalog.safe_relpath("d3d9.dll")
    assert catalog.safe_relpath("sub/file.dll")
    assert not catalog.safe_relpath("/abs/file")
    assert not catalog.safe_relpath("../file")
    assert not catalog.safe_relpath("a/../../b")
    assert not catalog.safe_relpath("")


# ── addon validation ─────────────────────────────────────────────────────────


def test_validate_addon_slims_and_defaults():
    cleaned = catalog.validate_addon(
        {
            "name": "pfUI",
            "git": "https://github.com/a/pfUI",
            "branch": "master",
            "ref": None,
            "description": "d",
            "toc": {"Title": "pfUI", "Notes": "n", "Extra": "skip"},
            "recommended": True,
        }
    )
    assert cleaned["name"] == "pfUI"
    assert cleaned["branch"] == "master"
    assert cleaned["ref"] is None
    assert cleaned["toc"] == {"Title": "pfUI", "Notes": "n"}
    assert cleaned["recommended"] is True
    assert cleaned["blocked"] is False


def test_validate_addon_rejects_bad_folder():
    assert catalog.validate_addon({"name": "../../evil"}) is None
    assert catalog.validate_addon({"name": ""}) is None
    assert catalog.validate_addon({"name": "a/b"}) is None


def test_validate_addon_normalizes_bad_refs():
    cleaned = catalog.validate_addon(
        {"name": "x", "ref": "../bad", "branch": "a b"}
    )
    assert cleaned["ref"] is None
    assert cleaned["branch"] is None


# ── mod validation ───────────────────────────────────────────────────────────


def test_validate_mod_github_release():
    cleaned = catalog.validate_mod(
        {
            "id": "VanillaFixes",
            "name": "VanillaFixes",
            "essential": True,
            "repo_url": "https://github.com/hannesmann/vanillafixes",
            "source": {
                "kind": "github_release",
                "owner": "hannesmann",
                "repo": "vanillafixes",
                "asset_pattern": "vanillafixes-*.zip",
                "prefer_no": "-dxvk",
                "extract_map": {"VfPatcher.dll": "VfPatcher.dll"},
            },
            "register_dll": "VfPatcher.dll",
            "installed_files": ["VfPatcher.dll", "VanillaFixes.exe"],
        }
    )
    assert cleaned["id"] == "VanillaFixes"
    assert cleaned["source"]["kind"] == "github_release"
    assert cleaned["source"]["owner"] == "hannesmann"
    assert cleaned["register_dll"] == "VfPatcher.dll"
    assert cleaned["essential"] is True


def test_validate_mod_rejects_unknown_kind():
    assert (
        catalog.validate_mod({"id": "X", "source": {"kind": "exec_arbitrary"}})
        is None
    )


def test_validate_mod_rejects_unknown_post_install_hook():
    assert (
        catalog.validate_mod(
            {
                "id": "X",
                "source": {
                    "kind": "direct_file",
                    "url": "https://example.com/x.dll",
                    "dest": "x.dll",
                    "post_install": ["rm -rf /"],
                },
            }
        )
        is None
    )


def test_validate_mod_allowlists_dxvk_hook():
    cleaned = catalog.validate_mod(
        {
            "id": "dxvk",
            "source": {
                "kind": "direct_tar",
                "url": "https://example.com/dxvk.tar.gz",
                "extract_map": {"d3d9.dll": "d3d9.dll"},
                "post_install": ["write_dxvk_conf"],
            },
        }
    )
    assert cleaned["source"]["post_install"] == ["write_dxvk_conf"]


def test_validate_mod_direct_tar_keeps_pinned_version():
    cleaned = catalog.validate_mod(
        {
            "id": "dxvk",
            "source": {
                "kind": "direct_tar",
                "url": "https://example.com/dxvk.tar.gz",
                "pinned_version": "v2.7.1-1",
                "extract_map": {"d3d9.dll": "d3d9.dll"},
            },
        }
    )
    assert cleaned is not None
    assert cleaned["source"]["pinned_version"] == "v2.7.1-1"


def test_validate_mod_rejects_path_traversal_dest():
    assert (
        catalog.validate_mod(
            {
                "id": "X",
                "source": {
                    "kind": "direct_file",
                    "url": "https://example.com/x.dll",
                    "dest": "../../x.dll",
                },
            }
        )
        is None
    )
    assert (
        catalog.validate_mod(
            {
                "id": "X",
                "source": {
                    "kind": "github_release",
                    "owner": "a",
                    "repo": "b",
                    "asset_pattern": "*.zip",
                    "extract_map": {"x.dll": "../x.dll"},
                },
            }
        )
        is None
    )


def test_validate_mod_rejects_bad_repo_url():
    assert (
        catalog.validate_mod(
            {
                "id": "X",
                "repo_url": "http://insecure.example",
                "source": {
                    "kind": "direct_file",
                    "url": "https://e/x.dll",
                    "dest": "x.dll",
                },
            }
        )["repo_url"]
        is None
    )


# ── merge precedence ─────────────────────────────────────────────────────────


def test_merge_addons_custom_overrides_and_appends():
    remote = [
        {
            "name": "A",
            "git": "https://github.com/x/A",
            "branch": None,
            "ref": None,
            "description": None,
            "toc": {},
            "recommended": False,
            "blocked": False,
        },
        {
            "name": "B",
            "git": "https://github.com/x/B",
            "branch": None,
            "ref": None,
            "description": None,
            "toc": {},
            "recommended": False,
            "blocked": False,
        },
    ]
    custom = [
        {"name": "A", "git": "https://github.com/fork/A", "recommended": True},
        {"name": "C", "git": "https://github.com/x/C"},
    ]
    merged = {a["name"]: a for a in catalog.merge_addons(remote, custom)}
    assert merged["A"]["git"] == "https://github.com/fork/A"
    assert merged["A"]["recommended"] is True
    assert merged["B"]["git"] == "https://github.com/x/B"
    assert merged["C"]["git"] == "https://github.com/x/C"


def test_merge_mods_custom_overrides_and_appends():
    remote = [{"id": "A", "name": "A", "essential": False}]
    custom = [{"id": "A", "essential": True}, {"id": "B", "name": "B"}]
    merged = {m["id"]: m for m in catalog.merge_mods(remote, custom)}
    assert merged["A"]["essential"] is True
    assert merged["B"]["name"] == "B"


# ── registry URL storage ─────────────────────────────────────────────────────


def test_registry_url_override_and_clear(tmp_path):
    config_store.configure(
        str(tmp_path / "cfg.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    assert catalog.get_registry_url("addons") == ""

    assert (
        catalog.set_registry_url("addons", "https://example.com/catalog.json")
        is None
    )
    assert (
        catalog.get_registry_url("addons")
        == "https://example.com/catalog.json"
    )

    catalog.reset_registry_url("addons")
    assert catalog.get_registry_url("addons") == ""


def test_set_registry_url_clears_override_with_empty(tmp_path):
    config_store.configure(
        str(tmp_path / "cfg.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    catalog.set_registry_url("mods", "https://example.com/mods.json")
    assert catalog.get_registry_url("mods") == "https://example.com/mods.json"
    assert catalog.set_registry_url("mods", "") is None
    assert catalog.get_registry_url("mods") == ""


def test_set_registry_url_rejects_insecure_and_credentials(tmp_path):
    config_store.configure(
        str(tmp_path / "cfg.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    assert catalog.set_registry_url("mods", "http://insecure") is not None
    assert (
        catalog.set_registry_url("mods", "https://user:pass@example.com/x")
        is not None
    )
    assert catalog.get_registry_url("mods") == ""


# ── custom-file loading ──────────────────────────────────────────────────────


def _redirect_custom(tmp_path, monkeypatch):
    monkeypatch.setattr(
        catalog,
        "custom_file",
        lambda kind: str(tmp_path / f"nostalgia_launcher_{kind}_custom.json"),
    )


def test_load_custom_skips_invalid_and_missing(tmp_path, monkeypatch):
    _redirect_custom(tmp_path, monkeypatch)
    assert catalog.load_custom("addons", catalog.validate_addon) == []

    (tmp_path / "nostalgia_launcher_addons_custom.json").write_text(
        '[{"folder": "Good", "git": "https://github.com/a/b"}, '
        '{"folder": "../evil"}, "nope"]',
        encoding="utf-8",
    )
    out = catalog.load_custom("addons", catalog.validate_addon)
    assert [a["name"] for a in out] == ["Good"]


def test_write_custom_template_creates_once(tmp_path, monkeypatch):
    _redirect_custom(tmp_path, monkeypatch)
    assert catalog.write_custom_template("addons", "[]") is True
    assert catalog.write_custom_template("addons", "[]") is False
    assert catalog.clear_custom("addons") is True
    assert catalog.clear_custom("addons") is False

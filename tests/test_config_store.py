"""Unit tests for the atomic JSON config/cache store."""

import json
import threading

import pytest

import nostalgia_launcher.core.config_store as config_store


@pytest.fixture(autouse=True)
def _store_paths(tmp_path):
    """Point the store at a temp config and cache file for every test."""
    cfg = tmp_path / "config.json"
    cache = tmp_path / "hash_cache.json"
    config_store.configure(str(cfg), str(cache))
    yield
    config_store.configure("", "")


def test_load_config_missing_returns_empty():
    assert config_store.load_config() == {}


def test_save_and_load_roundtrip():
    config_store.save_config(
        {"out_dir": "/games/octo", "tweaks": {"farClip": 1}}
    )
    cfg = config_store.load_config()
    assert cfg["out_dir"] == "/games/octo"
    assert cfg["tweaks"] == {"farClip": 1}


def test_save_writes_indented_json(tmp_path):
    cfg = tmp_path / "config.json"
    config_store.save_config({"a": 1})
    raw = cfg.read_text()
    assert json.loads(raw) == {"a": 1}
    assert "\n  " in raw  # indented


def test_load_config_corrupt_returns_empty(tmp_path):
    (tmp_path / "config.json").write_text("{not json")
    assert config_store.load_config() == {}


def test_update_config_applies_mutation_and_preserves_other_keys():
    config_store.save_config({"out_dir": "/x", "keep": 42})
    result = config_store.update_config(
        lambda c: c.__setitem__("mods", {"a": 1})
    )
    assert result["keep"] == 42
    assert result["mods"] == {"a": 1}
    assert config_store.load_config()["keep"] == 42


def test_update_config_saves_to_disk():
    config_store.update_config(
        lambda c: c.setdefault("addons", {}).__setitem__("pfUI", {})
    )
    assert config_store.load_config()["addons"] == {"pfUI": {}}


def test_apply_confirmed_out_dir_writes_flag_and_wipes_folder_scope(
    tmp_path,
):
    """The wizard's recorder mirrors SettingsController.set_path's reset:
    out_dir + confirmation flag in, folder-scoped install records out —
    at an EXPLICIT path (independent of the configured globals)."""
    other = tmp_path / "other" / "state.json"
    other.parent.mkdir()
    other.write_text(
        json.dumps(
            {
                "mods": {"m": 1},
                "addons": {"a": {}},
                "assets": [],
                "asset_probe_cache": {"x": 1},
                "keep": 7,
                "out_dir": "/old",
            }
        ),
        encoding="utf-8",
    )
    config_store.save_config({"untouched": True})
    config_store.apply_confirmed_out_dir(str(other), "/games/wow/")
    cfg = json.loads(other.read_text(encoding="utf-8"))
    assert (
        cfg["out_dir"].replace("\\", "/") == "/games/wow"
    )  # normalized, platform-agnostic
    assert cfg["out_dir_user_set"] is True
    for scoped in ("mods", "addons", "assets", "asset_probe_cache"):
        assert scoped not in cfg
    assert cfg["keep"] == 7
    # The globally-configured store is untouched.
    assert config_store.load_config() == {"untouched": True}


def test_apply_confirmed_out_dir_empty_is_a_noop(tmp_path):
    target = tmp_path / "state.json"
    config_store.apply_confirmed_out_dir(str(target), "   ")
    assert not target.exists()


def test_atomic_write_leaves_no_tmp_behind(tmp_path):
    config_store.save_config({"a": 1})
    assert not (tmp_path / "config.json.tmp").exists()
    assert (tmp_path / "config.json").exists()


def test_load_cache_missing_returns_empty():
    assert config_store.load_cache() == {}


def test_save_and_load_cache_roundtrip():
    config_store.save_cache({"/f": ["A" * 40, 123.0]})
    assert config_store.load_cache() == {"/f": ["A" * 40, 123.0]}


def test_concurrent_update_config_no_key_loss():
    """Concurrent read-modify-write cycles must not clobber each other."""
    config_store.save_config({})
    n_threads = 8
    n_writes = 25

    def worker(worker_id):
        for i in range(n_writes):
            config_store.update_config(
                lambda c, k=f"key_{worker_id}_{i}": c.__setitem__(k, True)
            )

    threads = [
        threading.Thread(target=worker, args=(w,)) for w in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    cfg = config_store.load_config()
    assert len(cfg) == n_threads * n_writes
    assert all(
        cfg[f"key_{w}_{i}"] is True
        for w in range(n_threads)
        for i in range(n_writes)
    )


def test_save_config_creates_parent_dir(tmp_path):
    """Atomic writes must create the per-user data dir on first write."""
    cfg = tmp_path / "deep" / "nested" / "config.json"
    config_store.configure(str(cfg), str(tmp_path / "c.json"))
    config_store.save_config({"a": 1})
    assert cfg.exists()
    assert config_store.load_config() == {"a": 1}
    config_store.configure("", "")

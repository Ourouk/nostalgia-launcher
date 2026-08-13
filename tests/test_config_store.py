"""Unit tests for the atomic JSON config/cache store."""

import json
import threading

import pytest

import config_store


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
    config_store.save_config({"out_dir": "/games/octo", "tweaks": {"farClip": 1}})
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
    result = config_store.update_config(lambda c: c.__setitem__("mods", {"a": 1}))
    assert result["keep"] == 42
    assert result["mods"] == {"a": 1}
    assert config_store.load_config()["keep"] == 42


def test_update_config_saves_to_disk():
    config_store.update_config(lambda c: c.setdefault("addons", {}).__setitem__("pfUI", {}))
    assert config_store.load_config()["addons"] == {"pfUI": {}}


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
                lambda c, k=f"key_{worker_id}_{i}": c.__setitem__(k, True))

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    cfg = config_store.load_config()
    assert len(cfg) == n_threads * n_writes
    assert all(cfg[f"key_{w}_{i}"] is True
               for w in range(n_threads) for i in range(n_writes))

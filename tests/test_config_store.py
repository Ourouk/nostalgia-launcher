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


def test_configure_migrates_legacy_config(tmp_path):
    """A legacy config next to the executable is copied on first use."""
    legacy = tmp_path / "legacy" / "nostalgia_launcher_config.json"
    legacy.parent.mkdir()
    legacy.write_text('{"out_dir": "/games"}')

    new = tmp_path / "new" / "nostalgia_launcher_config.json"
    cache = tmp_path / "new" / "hash_cache.json"

    config_store.configure(str(new), str(cache), str(legacy), "")
    assert config_store.load_config() == {"out_dir": "/games"}

    # Idempotent: a second configure with an existing file is a no-op.
    legacy.write_text('{"out_dir": "/changed"}')
    config_store.configure(str(new), str(cache), str(legacy), "")
    assert config_store.load_config() == {"out_dir": "/games"}
    config_store.configure("", "")


def test_configure_does_not_migrate_when_new_exists(tmp_path):
    new = tmp_path / "new" / "config.json"
    new.parent.mkdir()
    new.write_text('{"keep": true}')
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"keep": false}')

    config_store.configure(
        str(new), str(tmp_path / "cache.json"), str(legacy), ""
    )
    assert config_store.load_config() == {"keep": True}
    config_store.configure("", "")


def test_save_config_creates_parent_dir(tmp_path):
    """Atomic writes must create the per-user data dir on first write."""
    cfg = tmp_path / "deep" / "nested" / "config.json"
    config_store.configure(str(cfg), str(tmp_path / "c.json"))
    config_store.save_config({"a": 1})
    assert cfg.exists()
    assert config_store.load_config() == {"a": 1}
    config_store.configure("", "")


def test_configure_migrates_legacy_cache(tmp_path):
    """A legacy hash cache next to the executable is copied on first use."""
    legacy = tmp_path / "legacy" / "nostalgia_launcher_hash_cache.json"
    legacy.parent.mkdir()
    legacy.write_text('{"/f": ["a"]}')

    new = tmp_path / "new" / "hash_cache.json"
    config_store.configure(
        str(tmp_path / "config.json"), str(new), "", str(legacy)
    )
    assert config_store.load_cache() == {"/f": ["a"]}
    assert new.exists()
    assert new.parent.is_dir()
    config_store.configure("", "")


def test_configure_does_not_migrate_cache_when_new_exists(tmp_path):
    """An existing cache file always wins over a legacy one."""
    new = tmp_path / "cache.json"
    new.write_text('{"new": true}')
    legacy = tmp_path / "legacy_cache.json"
    legacy.write_text('{"old": true}')

    config_store.configure(
        str(tmp_path / "config.json"), str(new), "", str(legacy)
    )
    assert config_store.load_cache() == {"new": True}
    config_store.configure("", "")


def test_configure_migrates_and_creates_destination_parent(tmp_path):
    """Migration creates a missing destination parent directory."""
    legacy = tmp_path / "legacy_config.json"
    legacy.write_text('{"out_dir": "/x"}')

    new = tmp_path / "a" / "b" / "c" / "config.json"
    config_store.configure(
        str(new), str(tmp_path / "cache.json"), str(legacy), ""
    )
    assert new.parent.is_dir()
    assert config_store.load_config() == {"out_dir": "/x"}
    config_store.configure("", "")


def test_configure_migrates_to_bare_filename(monkeypatch, tmp_path):
    """Migration to a destination with no parent dir must not crash (the
    parent of a bare filename is the current directory, not the empty
    string)."""
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / "legacy_config.json"
    legacy.write_text('{"out_dir": "/x"}')

    config_store.configure(
        "octo_config.json", str(tmp_path / "cache.json"), str(legacy), ""
    )
    assert config_store.load_config() == {"out_dir": "/x"}
    config_store.configure("", "")


def test_configure_migrate_copy_failure_warns_and_continues(
    tmp_path, monkeypatch, capsys
):
    """A failed copy (unreadable legacy, etc.) writes a stderr note instead
    of crashing, and the app keeps running with no migrated file."""
    legacy = tmp_path / "legacy_config.json"
    legacy.write_text('{"out_dir": "/x"}')
    new = tmp_path / "new" / "config.json"

    def _broken_copy(src, dst):
        raise OSError("permission denied")

    monkeypatch.setattr(config_store.shutil, "copyfile", _broken_copy)
    config_store.configure(
        str(new), str(tmp_path / "cache.json"), str(legacy), ""
    )
    assert "migration" in capsys.readouterr().err
    assert not new.exists()
    assert config_store.load_config() == {}
    config_store.configure("", "")


def test_configure_legacy_new_same_path_is_noop(tmp_path):
    """When the legacy and new paths coincide (as they do on Windows when
    only one location is used), migration must be a no-op — never a
    copy-onto-itself failure."""
    cfg = tmp_path / "config.json"
    cfg.write_text('{"keep": true}')

    config_store.configure(
        str(cfg), str(tmp_path / "cache.json"), str(cfg), ""
    )
    assert config_store.load_config() == {"keep": True}
    assert cfg.read_text() == '{"keep": true}'
    config_store.configure("", "")


def test_configure_legacy_new_same_path_missing_is_noop(tmp_path):
    """Coinciding paths with no file on disk yet must not attempt to copy a
    file onto itself."""
    cfg = tmp_path / "config.json"

    config_store.configure(
        str(cfg), str(tmp_path / "cache.json"), str(cfg), ""
    )
    assert config_store.load_config() == {}
    assert not cfg.exists()
    config_store.configure("", "")


def test_configure_migrates_legacy_pairs(tmp_path):
    """Extra (legacy, new) pairs migrate files that moved with the config
    dir, e.g. the custom catalog files."""
    legacy = tmp_path / "old" / "nostalgia_launcher_mods_custom.json"
    legacy.parent.mkdir()
    legacy.write_text("[]")
    new = tmp_path / "new" / "nostalgia_launcher_mods_custom.json"

    config_store.configure(
        str(tmp_path / "config.json"),
        str(tmp_path / "cache.json"),
        legacy_pairs=[(str(legacy), str(new))],
    )
    assert new.exists()
    assert new.read_text() == "[]"
    assert not new.parent.joinpath("cache.json").exists()
    config_store.configure("", "")


def test_configure_legacy_pairs_same_path_is_noop(tmp_path):
    """A pair where legacy and new coincide must never copy onto itself."""
    cfg = tmp_path / "config.json"
    cfg.write_text('{"keep": true}')

    config_store.configure(
        str(cfg),
        str(tmp_path / "cache.json"),
        legacy_pairs=[(str(cfg), str(cfg))],
    )
    assert cfg.read_text() == '{"keep": true}'
    config_store.configure("", "")

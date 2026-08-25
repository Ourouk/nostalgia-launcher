"""Unit tests for the assets controller (ASSETS panel lifecycle)."""

import threading

import pytest

import nostalgia_launcher.core.config_store as config_store
from nostalgia_launcher.controllers.assets import AssetsController
from nostalgia_launcher.core import launcher
from nostalgia_launcher.state.events import (
    EventDispatcher,
    LogMessage,
    OperationFinished,
)


def _entry(**over):
    e = {
        "id": "patch3",
        "name": "Patch 3",
        "url": "https://server.test/uploads/patch-3.MPQ",
        "dest": "Data/patch-3.MPQ",
        "version": "v1",
        "essential": True,
    }
    e.update(over)
    return e


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated config store + launcher config with one embedded asset."""
    config_store.configure(
        str(tmp_path / "config.json"), str(tmp_path / "cache.json")
    )
    config_store.save_config({})
    launcher.configure_from_dict(
        {
            "server": {"base_url": "https://server.test"},
            "assets": [_entry()],
        }
    )
    client = tmp_path / "client"
    client.mkdir()
    (client / "WoW.exe").write_bytes(b"MZ")  # asset installs need a client
    return tmp_path, client, monkeypatch


class _R:
    """Minimal streaming-response fake."""

    def __init__(self, data):
        self._data = data
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        out, self._data = self._data[:n], self._data[n:]
        return out


def _serve(monkeypatch, data=b"MPQDATA"):
    import nostalgia_launcher.services.sources.direct_file as df

    monkeypatch.setattr(df, "secure_urlopen", lambda req, **k: _R(data))


def _make(client):
    return AssetsController(EventDispatcher(), get_out_dir=lambda: str(client))


def _drain(dispatcher):
    events = dispatcher.dispatch_all(handler=lambda e: None)
    return events


def test_records_empty_initially(env):
    _, client, _ = env
    ctl = _make(client)
    assert ctl.state.records == {}
    assert ctl.updates_count == 0


def test_apply_essential_installs_missing_asset(env, monkeypatch):
    tmp_path, client, monkeypatch = env
    body = b"MPQDATA"
    _serve(monkeypatch, body)
    ctl = _make(client)
    assert ctl.apply_essential_assets() is True

    deadline = threading.Event()
    for _ in range(200):
        if not ctl.busy:
            break
        deadline.wait(0.05)
    assert not ctl.busy

    installed = client / "Data" / "patch-3.MPQ"
    assert installed.read_bytes() == body
    rec = config_store.load_config()["assets"]["patch3"]
    assert rec["enabled"] is True
    assert rec["installed_version"] == "v1"
    assert rec["installed_files"] == ["Data/patch-3.MPQ"]


def test_verdict_marks_stale_and_action_update(env, monkeypatch):
    tmp_path, client, monkeypatch = env
    data_dir = client / "Data"
    data_dir.mkdir(parents=True)
    (data_dir / "patch-3.MPQ").write_bytes(b"old")
    config_store.update_config(
        lambda c: c.__setitem__(
            "assets",
            {
                "patch3": {
                    "enabled": True,
                    "installed_version": "v1",
                    "installed_files": ["Data/patch-3.MPQ"],
                }
            },
        )
    )
    # Server moved on to v2 (assets.py resolves core.launcher lazily, so
    # patching the module attribute works for both).
    monkeypatch.setattr(
        launcher, "embedded_assets", lambda: [_entry(version="v2")]
    )
    ctl = _make(client)
    assert ctl.action_for("patch3") == "update"
    assert not ctl.action_for("nonexistent")

    ctl.refresh_verdicts()
    for _ in range(100):
        if ctl.state.updates_count:
            break
        _drain(ctl._dispatcher)
        threading.Event().wait(0.02)
    assert ctl.state.updates_count == 1


def test_toggle_pending_then_apply_removes(env, monkeypatch):
    _, client, monkeypatch = env
    data_dir = client / "Data"
    data_dir.mkdir(parents=True)
    (data_dir / "patch-3.MPQ").write_bytes(b"x")
    config_store.update_config(
        lambda c: c.__setitem__(
            "assets",
            {
                "patch3": {
                    "enabled": True,
                    "installed_version": "v1",
                    "installed_files": ["Data/patch-3.MPQ"],
                }
            },
        )
    )
    ctl = _make(client)
    ctl.toggle("patch3", False)
    assert ctl.apply() is True
    for _ in range(200):
        if not ctl.busy:
            break
        threading.Event().wait(0.05)
    assert not (data_dir / "patch-3.MPQ").exists()
    rec = config_store.load_config()["assets"]["patch3"]
    assert rec["enabled"] is False
    assert rec["installed_files"] == []


def test_download_failure_records_error(env, monkeypatch):
    _, client, monkeypatch = env

    def fail(req, **k):
        raise OSError("network down")

    import nostalgia_launcher.services.sources.direct_file as df

    monkeypatch.setattr(df, "secure_urlopen", fail)
    ctl = _make(client)
    ctl.toggle("patch3", True)
    assert ctl.apply() is True
    for _ in range(200):
        if not ctl.busy:
            break
        threading.Event().wait(0.05)
    rec = config_store.load_config()["assets"]["patch3"]
    assert rec["error"]
    assert ctl.state.records["patch3"].has_error
    assert ctl.action_for("patch3") == "retry"


def test_reload_catalog_embedded_only_republishes_silently(env):
    _, client, _ = env
    ctl = _make(client)
    posted = []
    ctl._dispatcher.subscribe(posted.append)
    assert ctl.reload_catalog() is True
    kinds = [
        (
            type(e).__name__
            if not isinstance(e, LogMessage)
            else f"Log:{e.text.strip()[:20]}"
        )
        for e in _drain(ctl._dispatcher)
    ]
    assert any(k.startswith("Log:Using the assets") for k in kinds) or any(
        k == "AssetsLoaded" for k in kinds
    )


def test_reset_clears_pending(env):
    _, client, _ = env
    ctl = _make(client)
    ctl.toggle("patch3", False)
    assert ctl.state.has_pending_changes
    ctl.reset()
    assert not ctl.state.has_pending_changes


def test_operation_finished_kind_is_assets(env, monkeypatch):
    _, client, monkeypatch = env
    import nostalgia_launcher.services.sources.direct_file as df

    monkeypatch.setattr(
        df,
        "secure_urlopen",
        lambda req, **k: (_ for _ in ()).throw(OSError("down")),
    )
    ctl = _make(client)
    ctl.toggle("patch3", True)
    seen = []
    ctl._dispatcher.subscribe(seen.append)
    assert ctl.apply() is True
    for _ in range(200):
        if not ctl.busy:
            break
        ctl._dispatcher.dispatch_all()
        threading.Event().wait(0.05)
    ctl._dispatcher.dispatch_all()
    ops = [e for e in seen if isinstance(e, OperationFinished)]
    assert ops and ops[-1].kind == "assets"

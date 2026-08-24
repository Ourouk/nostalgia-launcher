"""Shared pytest fixtures.

The launcher configuration (`core/launcher`) is process-global, so every test
gets a configured server + mirror to keep the code paths (client updates,
news, settings, registries, tweaks realm) deterministic. It is reset after
each test.
"""

import pytest

from nostalgia_launcher.core import launcher

LAUNCHER_TEST_CONFIG = {
    "server": {
        "name": "Test Server",
        "base_url": "https://launcher.test",
        "realm": "launcher.test",
    },
    "mirrors": [
        {"name": "Backup", "base_url": "https://mirror-a.test"},
    ],
}


@pytest.fixture(autouse=True)
def _log_sink_env(tmp_path_factory, monkeypatch):
    """Keep every test off the real per-user launcher.log.

    Points the log-sink default AND the cli entry-point's by-name binding
    at a throwaway path; the file sink itself stays disabled until
    `log_sink.configure_file()` runs (only the CLI startup calls it), so
    plain unit tests never write any file.
    """
    import nostalgia_launcher.cli as cli_module
    from nostalgia_launcher.core import log_sink

    scratch = str(tmp_path_factory.mktemp("logs") / "launcher.log")
    monkeypatch.setattr(log_sink, "LOG_FILE", scratch)
    monkeypatch.setattr(cli_module, "LOG_FILE", scratch)
    # A previous test's CLI startup may have enabled the sink; keep every
    # test starting from the disabled state.
    monkeypatch.setattr(log_sink, "_sink_path", None)
    yield


@pytest.fixture(autouse=True)
def _local_repos_env(tmp_path, monkeypatch):
    """Keep every test off the real per-user local content repo files
    (`core.launcher.local_repo_path` / `legacy_custom_path`) and off the
    real pre-repo custom files read through `services.catalog.custom_file`
    — the same on-disk names, redirected wholesale."""
    monkeypatch.setattr(
        launcher,
        "local_repo_path",
        lambda kind: str(tmp_path / f"local_{kind}_repo.json"),
    )
    monkeypatch.setattr(
        launcher,
        "legacy_custom_path",
        lambda kind: str(tmp_path / f"nostalgia_launcher_{kind}_custom.json"),
    )
    from nostalgia_launcher.services import catalog as _catalog

    monkeypatch.setattr(
        _catalog,
        "custom_file",
        lambda kind: str(tmp_path / f"nostalgia_launcher_{kind}_custom.json"),
    )


@pytest.fixture(autouse=True)
def _launcher_env():
    launcher.reset()
    launcher.configure_from_dict(LAUNCHER_TEST_CONFIG)
    yield
    launcher.reset()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect the user home to ``tmp_path/home`` for every lookup seam.

    Sets BOTH ``HOME`` and ``USERPROFILE``: ``os.path.expanduser`` prefers
    ``USERPROFILE`` on Windows, so tests that only monkeypatch ``HOME``
    silently keep using the real profile dir there.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home

"""Shared pytest fixtures.

The launcher configuration (`core/launcher`) is process-global, so every test
gets a configured server + mirror to keep the code paths (client updates,
news, settings, registries, tweaks realm) deterministic. It is reset after
each test.
"""

import pytest

from nostalgia_launcher.core import launcher, profiles

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
def _launcher_env():
    launcher.reset()
    launcher.configure_from_dict(LAUNCHER_TEST_CONFIG)
    yield
    launcher.reset()


@pytest.fixture(autouse=True)
def _profiles_env():
    """The active profile is process-global; drop any per-test activation
    so a profile-scoped test can't bleed into later ones."""
    yield
    profiles.activate(profiles.DEFAULT)


@pytest.fixture(autouse=True)
def _single_instance_env():
    """Close any QLocalServer a test's CLI run started, so the next
    cli.main() never sees a stale 'already running' guard for the same
    key (the key derives from constants.CONFIG_FILE in default flows).
    Only touches an ALREADY-imported module: importing here would fight
    the fake import hooks some tests install."""
    import sys

    yield
    mod = sys.modules.get("nostalgia_launcher.ui.qt.app_lock_qt")
    if mod is not None:
        mod.stop_all()


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

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
        "url": "https://launcher.test",
        "realm": "launcher.test",
        "news_url": "https://launcher.test/api/news.json",
        "featured_news_url": "https://launcher.test/api/news/featured.json",
        "mods_registry_url": "https://launcher.test/api/mods.json",
        "addons_registry_url": "https://launcher.test/api/addons.json",
        "assets_registry_url": "https://launcher.test/api/assets.json",
        "download": {
            "update": True,
            "torrent": {
                "torrent_url": "https://launcher.test/client/latest/client.torrent",
                "magnet": "magnet:?xt=urn:btih:" + "ab" * 20,
            },
            "http": {
                "manifest": "https://launcher.test/api/file/latest/manifest.json",
                "client": "https://launcher.test/client/latest",
            },
            "content": {"type": "folder"},
        },
    },
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
    silently keep using the real profile dir there. Also points
    ``APPDATA``/``LOCALAPPDATA`` into the fake home — on Windows those
    take precedence over USERPROFILE (platform_support reads %APPDATA%
    first), so without them every test would share the real per-user
    config dir and leak state across tests/runs.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    return home


@pytest.fixture
def hermetic_cli(fake_home, tmp_path, monkeypatch):
    """Full isolation for tests that drive ``cli.main()``.

    ``fake_home`` alone is not enough: the default profile's state/cache
    paths were resolved from the real HOME at import time and re-imported
    BY NAME into ``core.profiles``, so a dev machine running the launcher
    would otherwise collide with the tests (same single-instance guard
    key → "Already running" short-circuit; real store lock acquired).
    Rebinds the constants on every by-name importer so profile
    resolution, guard keys and store locks all stay inside ``tmp_path``.
    """
    from nostalgia_launcher.core import constants, profiles

    cfg = str(tmp_path / "state.json")
    cache = str(tmp_path / "cache.json")
    for mod in (constants, profiles):
        monkeypatch.setattr(mod, "CONFIG_FILE", cfg)
        monkeypatch.setattr(mod, "CACHE_FILE", cache)
    return fake_home


# Real, profile-aware repo-path implementations (the autouse
# _local_repos_env fixture above replaces these seams with flat tmp
# redirects; individual tests restore them to verify genuine routing).
_REAL_LOCAL_REPO_PATH = launcher.local_repo_path
_REAL_LEGACY_CUSTOM_PATH = launcher.legacy_custom_path


@pytest.fixture
def real_repo_seams(monkeypatch):
    """Restore the real (profile-aware) repo-path resolution for tests
    that exercise it. Shared by test_profiles and the Qt smoke tests."""
    import nostalgia_launcher.services.catalog as catalog_module

    monkeypatch.setattr(launcher, "local_repo_path", _REAL_LOCAL_REPO_PATH)
    monkeypatch.setattr(
        launcher, "legacy_custom_path", _REAL_LEGACY_CUSTOM_PATH
    )
    monkeypatch.setattr(
        catalog_module,
        "custom_file",
        lambda kind: profiles.active().custom_catalog_path(kind),
    )

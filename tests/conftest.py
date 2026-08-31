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
    from nostalgia_launcher.core import constants as constants_module
    from nostalgia_launcher.core import log_sink

    scratch = str(tmp_path_factory.mktemp("logs") / "launcher.log")
    monkeypatch.setattr(log_sink, "LOG_FILE", scratch)
    monkeypatch.setattr(cli_module, "LOG_FILE", scratch)
    # New live helper — keep backward compat for tests that patch it.
    monkeypatch.setattr(constants_module, "log_file", lambda: scratch)
    # log_sink also imports LOG_FILE directly; keep its copy patched.
    # A previous test's CLI startup may have enabled the sink; keep every
    # test starting from the disabled state.
    monkeypatch.setattr(log_sink, "_sink_path", None)
    monkeypatch.setattr(log_sink, "_dispatcher", None)
    yield
    log_sink.set_dispatcher(None)


@pytest.fixture(autouse=True)
def _local_repos_env(tmp_path, monkeypatch):
    """Keep every test off the real per-user local content repo files
    (`core.launcher.local_repo_path`) — redirected wholesale to tmp_path."""
    monkeypatch.setattr(
        launcher,
        "local_repo_path",
        lambda kind: str(tmp_path / f"local_{kind}_repo.json"),
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
    so a profile-scoped test can't bleed into later ones.

    Hardened ``profiles.active()`` now fails loudly when nothing was
    activated — auto-activate the live default so most unit tests keep
    working without explicit ``profiles.activate()``.
    """
    # Use live helper so a HOME redirection is reflected.
    try:
        profiles.activate(profiles.default_profile())
    except Exception:
        profiles._ACTIVE = None
    yield
    profiles._ACTIVE = None


@pytest.fixture(autouse=True)
def _single_instance_env():
    """Close any QLocalServer a test's CLI run started, so the next
    cli.main() never sees a stale 'already running' guard for the same
    key (the key derives from the active profile's state path in default
    flows). Only touches an ALREADY-imported module: importing here would
    fight the fake import hooks some tests install."""
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

    Also isolates XDG vars for Linux: platform_support now honours
    XDG_CONFIG_HOME etc., so they must be pointed into the fake home as
    well, otherwise a real ~/.config/nostalgia-launcher would leak across
    tests.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    # The reserved default profile is a real directory; because its root is
    # resolved at import time it must be rebound to the (now redirected)
    # config dir so profile paths stay test-local.
    # Keep both the deprecated alias and the live helper in sync.
    patched_default = profiles.Profile(
        profiles.DEFAULT_PROFILE,
        profiles.profile_root(profiles.DEFAULT_PROFILE),
    )
    monkeypatch.setattr(profiles, "DEFAULT", patched_default)
    # default_profile() is live via profile_root() -> config_dir() -> HOME,
    # so no need to patch it, but keep _ACTIVE in sync if auto-activated.
    if profiles._ACTIVE is not None:
        try:
            profiles._ACTIVE = profiles.default_profile()
        except Exception:
            pass
    return home


@pytest.fixture
def hermetic_cli(fake_home, tmp_path, monkeypatch):
    """Full isolation for tests that drive ``cli.main()``.

    ``fake_home`` redirects the per-user config/cache dirs (via HOME /
    USERPROFILE / APPDATA / LOCALAPPDATA), so the default profile's
    state/cache paths resolve inside ``tmp_path``. Profile resolution,
    guard keys and store locks all stay off the real per-user directories.
    """
    return fake_home


# Real, profile-aware repo-path implementation (the autouse
# _local_repos_env fixture above replaces this seam with a flat tmp
# redirect; individual tests restore it to verify genuine routing).
_REAL_LOCAL_REPO_PATH = launcher.local_repo_path


@pytest.fixture
def real_repo_seams(monkeypatch):
    """Restore the real (profile-aware) repo-path resolution for tests
    that exercise it. Shared by test_profiles and the Qt smoke tests."""
    monkeypatch.setattr(launcher, "local_repo_path", _REAL_LOCAL_REPO_PATH)

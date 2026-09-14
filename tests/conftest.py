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
                "fallback": "https://launcher.test/client/latest/client.zip",
            },
            "content": {"type": "zip"},
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


@pytest.fixture
def dispatcher():
    """Fresh EventDispatcher for controller tests.

    Replaces the per-module ``dispatcher`` fixtures and inline
    ``EventDispatcher()`` builds in the owned controller tests.
    """
    from nostalgia_launcher.state.events import EventDispatcher

    return EventDispatcher()


@pytest.fixture
def controller_cfg(tmp_path, monkeypatch):
    """Tmp game folder + update-controller config bootstrap.

    Mirrors the ``test_update_events.py`` controller pattern:
    ``load_config`` serves a dict rooted at a real tmp dir,
    ``update_config`` mutates it in place, and client updates stay
    enabled unless a test flips ``client_update_enabled`` off (the
    merge mirrors the controller's effective-switch lookup).
    """
    import nostalgia_launcher.controllers.update as uc

    game = tmp_path / "game"
    game.mkdir(exist_ok=True)
    cfg: dict = {"out_dir": str(game)}
    monkeypatch.setattr(uc, "load_config", lambda: cfg)
    monkeypatch.setattr(
        uc, "update_config", lambda mutator: (mutator(cfg), cfg)[1]
    )
    monkeypatch.setattr(uc, "can_launch_client", lambda: True)
    monkeypatch.setattr(launcher, "download_update_enabled", lambda: True)

    def _effective():
        v = cfg.get("client_update_enabled")
        if v is None:
            return launcher.download_update_enabled()
        return bool(v)

    monkeypatch.setattr(
        launcher, "effective_client_updates_enabled", _effective
    )
    return cfg


@pytest.fixture
def wait_for_event():
    """Polling helper for controller tests.

    Replaces the per-module drain/spin copies (``_drain_for``,
    ``_wait_until_true``, ``_wait_verify_done``, ``_wait_and_poll``,
    ``_drain_n``): call it to drain-until-match, or use its
    ``until_true`` / ``drain_worker`` / ``drain_n`` / ``collect_all``
    methods for condition spins and worker/count/settle waits.
    """
    import time as _time

    class _Waiter:
        def __call__(self, dispatcher, predicate, timeout=2.0):
            """Drain until an event matching `predicate` arrives.

            Returns everything drained along the way (assertion
            failure on timeout).
            """
            deadline = _time.monotonic() + timeout
            collected = []
            while True:
                collected.extend(dispatcher.drain())
                if any(predicate(e) for e in collected):
                    return collected
                if _time.monotonic() > deadline:
                    raise AssertionError("expected event never arrived")
                _time.sleep(0.005)

        def until_true(self, predicate, timeout=2.0):
            """Spin until `predicate` is true (fails on timeout)."""
            deadline = _time.monotonic() + timeout
            while not predicate():
                if _time.monotonic() > deadline:
                    raise AssertionError("condition never became true")
                _time.sleep(0.005)

        def drain_worker(self, controller, done_event, timeout=2.0):
            """Wait for a scripted worker thread, then dispatch events.

            Mirrors the old ``_wait_and_poll``: blocks on `done_event`,
            dispatches the worker's posts to the controller, and retries
            the dispatch a few times to cover thread-scheduling jitter.
            Follow-up events posted by the controller stay queued for
            the test's next drain.
            """
            deadline = _time.monotonic() + timeout
            while not done_event.is_set():
                if _time.monotonic() > deadline:
                    raise AssertionError("scripted worker never finished")
                _time.sleep(0.005)
            _time.sleep(0.01)
            controller._dispatcher.dispatch_all()
            for _ in range(5):
                if (
                    controller.state.running is False
                    or len(controller._dispatcher) > 0
                ):
                    break
                _time.sleep(0.005)
                controller._dispatcher.dispatch_all()

        def drain_n(self, dispatcher, kind, n, timeout=2.0):
            """Drain until `n` events of `kind` arrived; returns all."""
            deadline = _time.monotonic() + timeout
            events = []
            while _time.monotonic() < deadline:
                events.extend(dispatcher.drain())
                if sum(isinstance(e, kind) for e in events) >= n:
                    return events
                _time.sleep(0.01)
            events.extend(dispatcher.drain())
            found = sum(isinstance(e, kind) for e in events)
            assert found >= n, f"only {found} {kind.__name__}"
            return events

        def collect_all(self, dispatcher, is_done, timeout=2.0):
            """Drain until `is_done(collected)` holds; returns all."""
            deadline = _time.monotonic() + timeout
            collected = []
            while True:
                collected.extend(dispatcher.drain())
                if is_done(collected):
                    return collected
                if _time.monotonic() > deadline:
                    raise AssertionError("expected events never arrived")
                _time.sleep(0.005)

    return _Waiter()


@pytest.fixture
def fake_http_response():
    """In-memory HTTP response (context manager) for faking
    ``secure_urlopen`` without touching the network."""

    class _FakeHttpResponse:
        def __init__(self, payload: bytes = b""):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size: int = -1):
            return self._payload

    return _FakeHttpResponse


@pytest.fixture
def fake_secure_urlopen(fake_http_response):
    """Factory for ``secure_urlopen`` replacements by URL.

    ``mapping`` keys on URL (``req.full_url``); values are bytes, an
    exception instance to raise, or a ``(req, timeout)`` callable.
    Unmapped URLs fall back to ``default`` (bytes succeed, exceptions
    raise) — handy for reachability probes where only up/down matters.
    """

    def _factory(mapping=None, default=b""):
        table = dict(mapping or {})

        def _open(req, timeout=6):
            url = getattr(req, "full_url", req)
            value = table.get(url, default)
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(req, timeout)
            return fake_http_response(value)

        return _open

    return _factory

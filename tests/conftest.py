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
def _launcher_env():
    launcher.reset()
    launcher.configure_from_dict(LAUNCHER_TEST_CONFIG)
    yield
    launcher.reset()

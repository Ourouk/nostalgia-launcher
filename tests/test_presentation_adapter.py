"""Headless acceptance test for the Tk presentation adapter (app.py).

Reads the app.py source text and asserts it stays a pure presentation layer:
no raw thread spawning, no direct config-store or mods/addons/news/security
imports (the deferred tweaks panel and pure rendering are the exceptions),
and that the smoke-test members Phase 2's Qt surface must reproduce are
still present. No Tk is instantiated.
"""

import pathlib

_APP_SOURCE = pathlib.Path(__file__).resolve().parent.parent / "app.py"

BANNED = [
    "threading.Thread",
    "config_store.load_config",
    "config_store.update_config",
    "from mods",
    "from addons",
    "from news",
    "import security_http",
]

REQUIRED = [
    "_win_w",
    "_win_h",
    "_ui",
    "_relayout",
    "_on_mousewheel",
    "_on_wheel_button",
    "_scroll_wheel",
]


def _source() -> str:
    return _APP_SOURCE.read_text(encoding="utf-8")


def test_presentation_adapter_has_no_business_logic_imports():
    src = _source()
    for fragment in BANNED:
        assert fragment not in src, f"app.py must not contain {fragment!r}"


def test_presentation_adapter_keeps_smoke_test_members():
    src = _source()
    for member in REQUIRED:
        assert member in src, f"app.py must keep the {member!r} smoke-test member"

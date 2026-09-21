"""Scrubbed environment for spawning system executables.

The AppImage AppRun exports ``LD_LIBRARY_PATH`` at the bundled
Qt/libs (``packaging/linux/AppRun``), and a ``uv run``/venv launch
sets ``VIRTUAL_ENV``/``PYTHON*``. Either makes a system binary load
incompatible libraries — ``/usr/bin/7z`` dying on the bundled
``libstdc++.so.6`` (``CXXABI_1.3.15 not found``) or
``/usr/bin/python3`` dying importing ``umu``.

Every ``subprocess`` spawn of a system binary must go through
`clean_system_env` instead of inheriting ``os.environ`` verbatim.
The game client itself is excluded: it is a Windows exe whose
environment is part of the launch contract.
"""

import os

# Vars that must never leak into a system-binary child.
SCRUBBED_ENV_VARS = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "__PYVENV_LAUNCHER__",
)

# ``QT_*`` only affects Qt plugin/theme lookup; harmless to the
# child but pointless — drop it so nothing picks up bundled Qt.
SCRUBBED_ENV_PREFIXES = ("QT_",)


def clean_system_env(extra: dict | None = None) -> dict:
    """Copy of ``os.environ`` minus bundled-launcher vars.

    ``extra`` entries are layered on top (``None`` values
    delete the key), e.g. ``{"GIT_TERMINAL_PROMPT": "0"}``.
    """
    env = dict(os.environ)
    for key in SCRUBBED_ENV_VARS:
        env.pop(key, None)
    for key in [k for k in env if k.startswith(SCRUBBED_ENV_PREFIXES)]:
        env.pop(key, None)
    if extra:
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    return env

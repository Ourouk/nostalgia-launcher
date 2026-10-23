"""Initial stage: one-off base-client acquisition (Briefing §4.A).

First install only. Decides whether the game folder already holds a
playable client (``has_exe``); when it does not, acquires the base
once — BitTorrent full download first, single-zip HTTP fallback
second. No rechecks, no incremental logic: once a playable client
exists this stage is done.
"""

from __future__ import annotations


def has_playable_client(out_dir: str) -> bool:
    """Whether the folder already holds a launchable client."""
    import os

    from ...core.filesystem import game_executable_exists

    try:
        from ...core import launcher as _launcher

        exe = _launcher.client_executable() or "WoW.exe"
    except Exception:
        exe = "WoW.exe"
    if game_executable_exists(out_dir, exe):
        return True
    try:
        from ...core.filesystem import pick_game_executable
        from ...services import mods as _mods

        exe2, _ = pick_game_executable(
            out_dir,
            _mods.external_launcher_executables(out_dir),
            exe_name=exe,
        )
        return os.path.isfile(exe2)
    except Exception:
        return False


def fallback_available(source) -> bool:
    """Whether the single-zip HTTP fallback is configured."""
    return bool(source is not None and getattr(source, "fallback_url", ""))

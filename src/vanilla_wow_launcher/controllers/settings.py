"""Settings / game-folder controller.

Owns the settings business logic: the game-folder-change reset (hash cache
drop, folder-scoped config wipe, resets of the other controllers), the
first-run flags, the Windows Defender exclusion flow, the download-mirror
check, the verify-game-files shortcut, the settings toggles and the
install-missing mods/addons shortcuts. Publishes LogMessage and
MirrorStatusChanged on the shared EventDispatcher; the Qt Settings dialog
renders them. No GUI toolkit.
"""

import os
import threading
import urllib.request
import webbrowser
from urllib.error import HTTPError

from ..services import addons
from ..core import config_store
from ..core import filesystem
from ..core import launcher
from ..services import mods
from ..core import platform_support
from ..core.constants import (
    CACHE_FILE,
    CONFIG_FILE,
    DEFAULT_OUT_DIR,
    UA,
)
from ..core.security_http import secure_urlopen
from ..core.errors import describe_net_error
from ..state.events import EventDispatcher, LogMessage, MirrorStatusChanged
from ..state.models import SettingsState


class SettingsController:
    """Owns the settings/game-folder lifecycle; speaks to the UI only through
    events.

    The other controllers are injected so a folder change can reset them all
    and the settings shortcuts can delegate to their workers. No widgets:
    the Qt layer keeps the dialogs and mirrors its path field against
    ``state.path``.
    """

    def __init__(self, dispatcher: EventDispatcher, updater, mods, addons,
                 news):
        self._dispatcher = dispatcher
        self._updater = updater
        self._mods = mods
        self._addons = addons
        self._news = news

        cfg = config_store.load_config()
        self.state = SettingsState(
            path=os.path.normpath(cfg.get("out_dir", DEFAULT_OUT_DIR)),
            config=cfg,
        )
        # Detect first run before anything writes the config.
        self.state.first_run = not os.path.exists(CONFIG_FILE)
        # On first run Settings auto-opens with the folder auto-set to the
        # current dir. If the user closes it without changing the folder or
        # adding a Defender exclusion, recommend the exclusion once on close.
        self.state.first_run_av_pending = (
            self.state.first_run and platform_support.can_manage_antivirus())
        # On first run we don't verify (fetch the manifest / touch
        # Config.wtf) until the user closes Settings, so nothing is written to
        # the default folder before they've picked their real game folder. A
        # folder change supersedes this (it verifies the new folder right
        # away).
        self.state.first_run_verify_pending = (
            self.state.first_run and self.client_update_enabled)

        # Download-mirror reachability, as reported by the last check_mirror()
        # ({name: "" | "checking…" | "online" | "offline"}). Not part of
        # SettingsState — it's transient session state the Settings modal
        # renders.
        self.mirror_statuses: dict[str, str] = {}

        # Close-time auto-install flags, armed by the Settings toggles and
        # consumed when the Settings modal closes (a no-op when the option
        # was just toggled back off).
        self._pending_auto_mods = False
        self._pending_auto_addons = False

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def client_update_enabled(self) -> bool:
        """Whether client verification and downloads are enabled."""
        return bool(self.state.config.get("client_update_enabled", True))

    def set_path(self, new_path: str) -> bool:
        """Apply a game-folder change.

        Normalizes the value and — when it actually differs from the current
        folder — deletes the hash cache, wipes folder-scoped config
        (patched-exe hashes + mods/addons install records), resets every
        session controller and re-verifies the new folder (overwriting
        Config.wtf, which also supersedes the first-run settings-close
        verify). Returns True when a change was applied, so the UI can skip
        its own re-renders on a no-op.
        """
        new_val = os.path.normpath((new_path or "").strip() or ".")
        if os.path.normpath(self.state.path.strip() or ".") == new_val:
            return False
        if self._updater.running:
            self._dispatcher.post(LogMessage(
                "Cannot change the game folder while an update is running — "
                "folder change ignored.\n", "err"))
            return False
        self.state.path = new_val
        if not new_val:
            return False

        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
        except Exception:
            pass

        # Only touch WDB when the path is a real client folder — this fires on
        # every keystroke while a path is being typed.
        if os.path.exists(os.path.join(new_val, "WoW.exe")):
            filesystem.remove_wdb(new_val)

        # Wipe folder-scoped config (patched-exe hashes + mods/addons install
        # records) and set the new path — one atomic merge into the live
        # config. This also re-arms the default-mods and recommended-addons
        # auto-install for the new folder.
        def _reset_for_new_folder(c):
            c["out_dir"] = new_val
            for k in ("expected_patched_wow_hash", "original_server_wow_hash",
                      "mods", "addons"):
                c.pop(k, None)
        self.state.config = config_store.update_config(_reset_for_new_folder)

        # Reset every session controller so nothing from the previous folder is
        # served from memory: addons verify + its rendered list, news feed
        # timers, pending mods/addons changes, and the footer readiness.
        self._mods.reset()
        self._addons.reset()
        self._updater.invalidate()
        self._news.invalidate()

        self._dispatcher.post(LogMessage(
            "\nGame folder changed — cache reset, everything will be "
            "re-verified.\n", "acct"))

        # This verify covers the new folder — overwrite its Config.wtf with
        # our defaults + realmList. It also supersedes the first-run
        # settings-close verify.
        self.state.first_run_verify_pending = False
        if self.client_update_enabled:
            self._updater.start_verify(overwrite_config=True)

        # A deliberate folder change already covers the antivirus
        # recommendation, so the first-run settings-close shouldn't ask again.
        self.state.first_run_av_pending = False
        return True

    def should_prompt_av(self) -> bool:
        """Whether the Defender-exclusion prompt should be shown (Windows
        only — the messagebox itself lives in the UI layer)."""
        return platform_support.can_manage_antivirus()

    def av_prompt_dismissed(self):
        """Called when the AV prompt was answered or skipped; clears the
        first-run pending flag so the settings-close doesn't ask again."""
        self.state.first_run_av_pending = False

    def allow_through_antivirus(self):
        """Add a Windows Defender exclusion for the game folder (asks for
        admin elevation via UAC). Windows-only — a no-op elsewhere."""
        if not platform_support.can_manage_antivirus():
            self._dispatcher.post(LogMessage(
                "Windows Defender exclusions are not available on this "
                "platform.\n", "err"))
            return
        # The user handled the exclusion themselves — no need to prompt again
        # when the first-run Settings window closes.
        self.state.first_run_av_pending = False
        client_dir = os.path.normpath(self.state.path.strip())
        if not client_dir or client_dir == ".":
            return
        import ctypes
        cmd = f"Add-MpPreference -ExclusionPath '{client_dir}'"
        r = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe",
            f'-NoProfile -WindowStyle Hidden -Command "{cmd}"', None, 0)
        if r > 32:
            self._dispatcher.post(LogMessage(
                f"Requested Defender exclusion for: {client_dir}\n", "ok"))
        else:
            self._dispatcher.post(LogMessage(
                "Antivirus exclusion cancelled.\n", "err"))

    def check_mirror(self):
        """Background reachability check of every configured server and
        mirror. The UI shows "checking…" itself and re-renders on the
        MirrorStatusChanged event."""
        names = self.mirror_names()
        if not names:
            self._dispatcher.post(MirrorStatusChanged(False, "Not configured"))
            return
        threading.Thread(target=self._mirror_worker, args=(names,),
                         daemon=True).start()

    def mirror_names(self) -> list:
        """Every download source name (the server followed by its mirrors),
        in the order the launcher configuration declares them."""
        cfg = launcher.config()
        if cfg is None:
            return []
        return [cfg.server_name] + [m.name for m in cfg.mirrors]

    def verify_files(self):
        """Full re-verification: drop the hash cache and the patched-exe
        bookkeeping so every file is re-hashed against the manifest and
        WoW.exe gets re-downloaded and re-patched (tweaks reapplied). Unlike
        a game-folder change, installed mods are left alone."""
        if self._updater.running or not self.client_update_enabled:
            return
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
        except Exception:
            pass

        def _drop_hashes(c):
            c.pop("expected_patched_wow_hash", None)
            c.pop("original_server_wow_hash", None)
        self.state.config = config_store.update_config(_drop_hashes)
        self._updater.invalidate()
        self._dispatcher.post(LogMessage(
            "\nVerify game files — cache dropped, re-checking everything.\n",
            "acct"))
        self._updater.start_verify(overwrite_config=False)

    def set_clear_wdb(self, enabled: bool) -> dict:
        self.state.config = config_store.update_config(
            lambda c: c.__setitem__("clear_wdb_on_launch", enabled))
        return self.state.config

    def set_close_on_launch(self, enabled: bool) -> dict:
        self.state.config = config_store.update_config(
            lambda c: c.__setitem__("close_on_launch", enabled))
        return self.state.config

    def set_client_update_enabled(self, enabled: bool) -> dict:
        """Persist the client-update switch and update first-run scheduling."""
        enabled = bool(enabled)
        self.state.config = config_store.update_config(
            lambda c: c.__setitem__("client_update_enabled", enabled))
        if self.state.first_run:
            self.state.first_run_verify_pending = enabled
        return self.state.config

    def set_auto_mods(self, enabled: bool) -> dict:
        self._pending_auto_mods = enabled
        self.state.config = config_store.update_config(
            lambda c: c.__setitem__("auto_install_mods", enabled))
        return self.state.config

    def set_auto_addons(self, enabled: bool) -> dict:
        self._pending_auto_addons = enabled
        self.state.config = config_store.update_config(
            lambda c: c.__setitem__("auto_install_addons", enabled))
        return self.state.config

    def take_pending_auto_mods(self) -> bool:
        """Whether a close-time essential-mods install was armed (consume it)."""
        pending = self._pending_auto_mods
        self._pending_auto_mods = False
        return pending

    def take_pending_auto_addons(self) -> bool:
        """Whether a close-time recommended-addons install was armed (consume)."""
        pending = self._pending_auto_addons
        self._pending_auto_addons = False
        return pending

    def prune_folder_records(self) -> dict:
        """Drop stale mods/addons install records when the configured game
        folder no longer exists (a folder that was deleted or never created)."""
        def _wipe(c):
            c.pop("mods", None)
            c.pop("addons", None)
        self.state.config = config_store.update_config(_wipe)
        return self.state.config

    def mods_initialized(self) -> bool:
        """Whether any mod install record exists for this folder — gates the
        post-update recommended-addons chain."""
        return bool(config_store.load_config().get("mods"))

    def install_missing_essential_mods(self) -> bool:
        """Install every essential mod not already present. Used when the user
        turns 'Install essential mods' on after the fact. Returns True when an
        install actually started."""
        if self._updater.running:
            return False
        out = self.state.path.strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return False  # no client yet — the fresh-folder auto-install
        mods_cfg = config_store.load_config().get("mods", {})
        pending = False
        for mod in mods.mods_registry():
            if not mod.get("essential", False):
                continue
            state = mods_cfg.get(mod["id"], {})
            if (state.get("installed_version")
                    and mods.mod_installed_files_present(mod, out)):
                continue  # already installed
            self._mods.toggle(mod["id"], True)
            pending = True
        if not pending:
            return False
        self._dispatcher.post(LogMessage(
            "\nInstalling essential mods...\n", "acct"))
        self._mods.apply()
        return True

    def install_missing_recommended_addons(self) -> bool:
        """Install every recommended addon not already present. Used when the
        user turns 'Install recommended addons' on afterwards. Returns True
        when an install actually started."""
        if self._addons.state.busy:
            return False
        out = self.state.path.strip()
        if not out or not os.path.exists(os.path.join(out, "WoW.exe")):
            return False
        ap = addons.addons_path(out)
        recs = [{"folder": name, "status": "available", "git": url,
                 "branch": None, "ref": None, "toc": {},
                 "description": None, "error": None}
                for name, url in addons.RECOMMENDED_ADDONS.items()
                if not os.path.isdir(os.path.join(ap, name))]
        if not recs:
            return False
        self._dispatcher.post(LogMessage(
            "\nInstalling recommended addons...\n", "acct"))
        self._addons.apply(recs)
        return True

    def open_client_folder(self):
        path = os.path.normpath(self.state.path.strip())
        if os.path.isdir(path):
            try:
                platform_support.open_folder(path)
                self._dispatcher.post(
                    LogMessage(f"Opened folder: {path}\n", "dim"))
            except OSError as e:
                self._dispatcher.post(LogMessage(
                    f"Could not open folder: {e}\n", "err"))
        else:
            self._dispatcher.post(LogMessage(f"Folder not found: {path}\n",
                                             "err"))

    def open_url(self, url: str):
        webbrowser.open(url)

    # ── catalog registries ──────────────────────────────────────────────────

    def addons_registry_url(self) -> str:
        """The active addon catalog URL (configured or server default)."""
        return addons.registry_url()

    def mods_registry_url(self) -> str:
        """The active mod catalog URL (configured or server default)."""
        return mods.registry_url()

    def set_addons_registry_url(self, url: str) -> str | None:
        """Validate and store the addon catalog URL. Returns an error string
        or None on success."""
        err = addons.set_registry_url(url)
        if err is None:
            self._dispatcher.post(
                LogMessage("Addon catalog URL updated.\n", "ok"))
        return err

    def set_mods_registry_url(self, url: str) -> str | None:
        """Validate and store the mod catalog URL. Returns an error string or
        None on success."""
        err = mods.set_registry_url(url)
        if err is None:
            self._dispatcher.post(
                LogMessage("Mod catalog URL updated.\n", "ok"))
        return err

    def reset_addons_registry_url(self):
        addons.reset_registry_url()
        self._dispatcher.post(
            LogMessage("Addon catalog URL reset to default.\n", "dim"))

    def reset_mods_registry_url(self):
        mods.reset_registry_url()
        self._dispatcher.post(
            LogMessage("Mod catalog URL reset to default.\n", "dim"))

    def open_addons_custom_file(self):
        """Create the custom addon file (with a template) when missing and
        open it in the default editor."""
        if addons.open_custom_file():
            self._dispatcher.post(LogMessage(
                "Created the custom addon file.\n", "dim"))
        try:
            platform_support.open_folder(addons.custom_file())
        except OSError as e:
            self._dispatcher.post(LogMessage(
                f"Could not open custom addon file: {e}\n", "err"))

    def open_mods_custom_file(self):
        """Create the custom mod file (with a template) when missing and open
        it in the default editor."""
        if mods.open_custom_file():
            self._dispatcher.post(LogMessage(
                "Created the custom mod file.\n", "dim"))
        try:
            platform_support.open_folder(mods.custom_file())
        except OSError as e:
            self._dispatcher.post(LogMessage(
                f"Could not open custom mod file: {e}\n", "err"))

    def clear_addons_custom(self):
        if addons.clear_custom_file():
            self._dispatcher.post(LogMessage(
                "Custom addon entries cleared.\n", "ok"))
        else:
            self._dispatcher.post(LogMessage(
                "No custom addon file to clear.\n", "dim"))

    def clear_mods_custom(self):
        if mods.clear_custom_file():
            self._dispatcher.post(LogMessage(
                "Custom mod entries cleared.\n", "ok"))
        else:
            self._dispatcher.post(LogMessage(
                "No custom mod file to clear.\n", "dim"))

    def reload_addons_registry(self):
        """Force-fetch the addon catalog and rescan the ADDONS tab."""
        if self._addons.state.busy:
            self._dispatcher.post(LogMessage(
                "Wait for the current addon install to finish first.\n",
                "err"))
            return
        self._dispatcher.post(LogMessage(
            "Reloading the addon catalog...\n", "dim"))
        threading.Thread(target=self._reload_addons_worker,
                         daemon=True).start()

    def reload_mods_registry(self):
        """Force-fetch the mod catalog and re-render the MODS tab."""
        self._dispatcher.post(LogMessage(
            "Reloading the mod catalog...\n", "dim"))
        threading.Thread(target=self._reload_mods_worker,
                         daemon=True).start()

    def _reload_addons_worker(self):
        try:
            addons.fetch_addons_catalog(force=True)
        except Exception as e:
            self._dispatcher.post(LogMessage(
                f"✗ Addon catalog reload failed: "
                f"{describe_net_error(e)}\n", "err"))
            return
        self._addons.invalidate()
        self._addons.verify()
        self._dispatcher.post(LogMessage(
            "✓ Addon catalog reloaded.\n", "ok"))

    def _reload_mods_worker(self):
        try:
            mods.fetch_mods_catalog(force=True)
        except Exception as e:
            self._dispatcher.post(LogMessage(
                f"✗ Mod catalog reload failed: "
                f"{describe_net_error(e)}\n", "err"))
            return
        self._mods.invalidate()
        self._mods.load_latest_versions()
        self._dispatcher.post(LogMessage(
            "✓ Mod catalog reloaded.\n", "ok"))

    # ── internals ───────────────────────────────────────────────────────────

    def _mirror_worker(self, names: list):
        ok_any = False
        for name in names:
            online = self._probe_mirror(name)
            self.mirror_statuses[name] = "online" if online else "offline"
            ok_any = ok_any or online
        self._dispatcher.post(MirrorStatusChanged(
            ok=ok_any, text="online" if ok_any else "offline"))

    def _probe_mirror(self, name: str) -> bool:
        """Whether a named download source can serve client files. Any HTTP
        response (even an error status) proves it is reachable; only transport
        failures count as down."""
        url = self._mirror_probe_url(name)
        if not url:
            return False
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with secure_urlopen(req, timeout=6):
                return True
        except HTTPError:
            return True
        except Exception:
            return False

    def _mirror_probe_url(self, name: str) -> str:
        """The client-files endpoint of a named download source (the server
        or a mirror)."""
        cfg = launcher.config()
        if cfg is None:
            return ""
        if cfg.server_name == name:
            return cfg.client_url
        for m in cfg.mirrors:
            if m.name == name:
                return m.client_url
        return ""

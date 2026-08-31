"""Update/verify orchestration controller.

Owns the verify/update lifecycle: starting VerifyWorker/UpdateWorker,
computing the footer READY/PLAY/UPDATE button state and publishing
everything as events on the shared EventDispatcher. Workers post typed
lifecycle events directly to the dispatcher; the controller subscribes and
mutates UpdateState. No GUI toolkit: the controller never touches widgets,
it only posts events and mutates its own UpdateState.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

from ..core.config_store import load_config, update_config
from ..core.filesystem import (
    get_client_version,
    pick_game_executable,
    remove_wdb,
)
from ..core.log_sink import log
from ..core.platform_support import can_launch_client, is_linux
from ..services import mods
from ..services.self_update import (
    fetch_updater_latest_tag,
    updater_update_available,
)
from ..services.update_backend.http_update import (
    UpdateWorker,
    VerifyWorker,
    torrent_recovery_available,
)
from ..state.events import (
    ClientVersionReady,
    Event,
    EventDispatcher,
    GameExited,
    GameLaunched,
    LogMessage,
    OperationFailed,
    OperationFinished,
    ProgressChanged,
    StatusChanged,
    TorrentCorrupt,
    TorrentDiffReady,
    TorrentDiskError,
    TorrentReachable,
    TorrentRecoveryDone,
    TorrentSessionError,
    TorrentStalled,
    TorrentUnavailable,
    TorrentUpToDate,
    TorrentVerifyFailed,
    UpdateCompleted,
    UpdateFailed,
    UpdateFilesList,
    UpdateRequired,
    VerificationUpToDate,
)
from ..state.models import UpdateState


@dataclass
class Readiness:
    """Footer button decision produced by UpdateController.compute_readiness.

    mode is "play", "update", "busy", "disabled" or "terminate"; label is the
    button text used in busy mode; status is the footer status line the UI
    should show.
    """

    mode: str
    label: str
    status: str


# Cap on captured child-process (umu/Wine, WoW.exe) lines per run — a chatty
# Wine session must not flood the session buffer, the log file and the UI.
_CHILD_OUTPUT_MAX_LINES = 800


class UpdateController:
    """Owns the verify/update flow; speaks to the UI only through events.

    `get_out_dir` is an optional zero-arg callable returning the current game
    folder (the Qt UI supplies its path field's getter). When omitted the
    controller reads ``out_dir`` from the on-disk config, mirroring the
    UI's default.
    """

    def __init__(
        self,
        dispatcher: EventDispatcher,
        get_out_dir: Callable[[], str] | None = None,
    ) -> None:
        import threading

        self._dispatcher = dispatcher
        self.state = UpdateState()
        self._lock = threading.Lock()
        self._worker: UpdateWorker | None = None
        self._verify_worker: VerifyWorker | None = None
        self._op: str | None = None
        # Set by check_updater_update(): a newer updater release exists and
        # the header "Update available!" label should be shown.
        self.updater_update_available: bool = False
        if get_out_dir is None:

            def _default_get_out_dir() -> str:
                val = load_config().get("out_dir", "")
                return val if isinstance(val, str) else ""

            get_out_dir = _default_get_out_dir

        self._get_out_dir: Callable[[], str] = get_out_dir
        self._dispatcher.subscribe(self._on_event)

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.state.running

    @property
    def client_ready(self) -> bool:
        return self.state.client_ready

    @property
    def client_update_enabled(self) -> bool:
        return self._client_updates_enabled()

    def start_verify(self, overwrite_config: bool = False):
        if not self._client_updates_enabled():
            return
        out = (self._get_out_dir() or "").strip()
        if not out:
            return
        self.state.verify_out_dir = out
        # Cancel any verify already in flight.
        if self._verify_worker is not None:
            self._verify_worker.cancel()
        # Clear torrent state from previous attempts
        self.state.torrent_reachable = None
        self.state.torrent_error = None
        self.state.torrent_stale = None
        self.state.running = True
        self._op = "verify"
        self.state.status = "Verifying…"
        from ..services.update_backend.sources import _download_source

        worker = VerifyWorker(
            out,
            self._dispatcher,
            overwrite_config=overwrite_config,
            source=_download_source(),
        )
        self._verify_worker = worker
        threading.Thread(target=worker.run, daemon=True).start()
        self._dispatcher.post(StatusChanged("Verifying…"))
        self._dispatcher.post(ProgressChanged(0.0, ""))

    def start_update(self):
        if self.state.running:
            self._dispatcher.post(
                LogMessage("An update is already in progress.\n", "dim")
            )
            return False
        if not self._client_updates_enabled():
            return False
        out = (self._get_out_dir() or "").strip()
        if not out:
            self._dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return False
        if (
            self.state.torrent_stale is not None
            and self.state.verify_out_dir != out
        ):
            # The cached stale set belongs to another folder — re-verify.
            self._dispatcher.post(
                LogMessage(
                    "Game folder changed since the last verify — "
                    "verifying again…\n",
                    "err",
                )
            )
            self.start_verify()
            return False
        self._dispatcher.post(LogMessage(f"\nGame folder: {out}\n", "dim"))
        torrent_wanted = (
            set(self.state.torrent_stale)
            if self.state.torrent_stale is not None
            else None
        )
        if torrent_wanted == set():
            self.state.torrent_stale = None
            self.state.client_ready = True
            self.state.status = "Up to date"
            self._dispatcher.post(
                LogMessage("[torrent] No stale files; update skipped.\n", "ok")
            )
            self._dispatcher.post(ProgressChanged(1.0, ""))
            self._dispatcher.post(OperationFinished("update", True))
            return True
        # Clear torrent state from previous attempts
        self.state.torrent_reachable = None
        self.state.torrent_error = None
        # Note: torrent_stale is cleared AFTER capturing it for the worker
        self.state.running = True
        self._op = "update"
        self.state.status = "Updating…"
        from ..services.update_backend.sources import _download_source

        worker = UpdateWorker(
            out,
            self._dispatcher,
            source=_download_source(),
        )
        self._worker = worker
        self.state.torrent_stale = None
        threading.Thread(
            target=worker.run,
            args=(None, torrent_wanted),
            daemon=True,
        ).start()
        self._dispatcher.post(StatusChanged("Updating…"))
        self._dispatcher.post(ProgressChanged(0.0, ""))
        return True

    def start_client_download(self) -> bool:
        """First-time client acquisition via BitTorrent (``torrent_url`` or
        ``magnet``), available even when client updates are disabled. Downloads
        the whole client manifest-less; the torrent's piece hashes are the
        integrity guarantee. The payload is then extracted per
        ``server.download.content.type`` (folder/zip/rar)."""
        if self.state.running:
            self._dispatcher.post(
                LogMessage("An operation is already in progress.\n", "dim")
            )
            return False
        out = (self._get_out_dir() or "").strip()
        if not out:
            self._dispatcher.post(
                LogMessage("✗  Please set the game folder first.\n", "err")
            )
            return False
        if not torrent_recovery_available():
            self._dispatcher.post(
                LogMessage(
                    "✗  This server has no BitTorrent source configured — "
                    "enable client updates to download via HTTP.\n",
                    "err",
                )
            )
            return False
        self.state.running = True
        self._op = "download"
        self.state.status = "Downloading client…"
        from ..services.update_backend.sources import _download_source

        worker = UpdateWorker(out, self._dispatcher, source=_download_source())
        self._worker = worker
        threading.Thread(
            target=worker.run, kwargs={"recovery_full": True}, daemon=True
        ).start()
        self._dispatcher.post(StatusChanged("Downloading client…"))
        self._dispatcher.post(ProgressChanged(0.0, ""))
        return True

    def cancel(self):
        """Ask every live worker to stop."""
        for worker in (self._verify_worker, self._worker):
            if worker is not None:
                worker.cancel()

    def invalidate(self):
        """Drop readiness and the cached torrent state (game folder changed
        or a verify-game-files recheck)."""
        self.cancel()
        self._worker = None
        self._verify_worker = None
        self._op = None
        self.state.running = False
        self.state.client_ready = False
        self.state.verify_out_dir = ""
        self.state.torrent_reachable = None
        self.state.torrent_error = None
        self.state.torrent_stale = None

    def close(self):
        """Unsubscribe from dispatcher (call on shutdown)."""
        try:
            self._dispatcher.unsubscribe(self._on_event)
        except Exception:
            pass

    def read_client_version(self) -> str:
        """The client version straight from disk, cached on state (footer
        label at startup, before any worker has run)."""
        self.state.client_version = get_client_version(
            (self._get_out_dir() or "").strip()
        )
        return self.state.client_version

    def check_updater_update(self):
        """Background daily check of the updater's own GitHub releases; sets
        `updater_update_available` when a newer version exists. The UI polls
        that flag from its event loop and draws the header label."""

        def worker():
            try:
                tag = fetch_updater_latest_tag()
            except Exception:
                tag = None
            self.updater_update_available = bool(updater_update_available(tag))

        threading.Thread(target=worker, daemon=True).start()

    def launch_game(self) -> tuple:
        """Launch the client detached.

        Returns ``(ok, dxvk_notice)``: ``ok`` is False when the client can't
        be launched (a LogMessage explains why) and ``dxvk_notice`` is True
        when the one-time DXVK first-launch notice should be shown. Consumes
        the notice flag, the clear-wdb and the launch itself. Windows runs
        the binary directly; Linux runs it through umu-launcher (Proton/Wine)
        when umu-run is available. Only one game process is allowed at a
        time — a second launch is refused while one is running.
        """
        if not can_launch_client():
            self._dispatcher.post(
                LogMessage(
                    "Game launch is not available on this platform — on Linux, "
                    "umu-run must be installed (the client is a Windows "
                    "binary).\n",
                    "err",
                )
            )
            return False, False
        if self.state.game_running:
            self._dispatcher.post(
                LogMessage(
                    "A game is already running — use TERMINATE to end it "
                    "first.\n",
                    "err",
                )
            )
            return False, False
        client_dir = (self._get_out_dir() or "").strip()
        cfg = load_config()
        if is_linux():
            return self._launch_game_via_umu(client_dir, cfg)
        return self._launch_game_windows(client_dir, cfg)

    def _launch_game_windows(self, client_dir: str, cfg: dict) -> tuple:
        """Windows direct launch: prefer an installed external-launcher mod's
        executable, then WoW.exe, spawned detached from the caller's job
        object. Its merged output is drained into the session log by a
        watcher thread that also records the exit (same bookkeeping as the
        umu path, so the one-game-at-a-time guard holds on Windows too)."""
        import subprocess

        exe, exe_lbl = pick_game_executable(
            client_dir,
            mods.external_launcher_executables(client_dir),
        )
        if not os.path.exists(exe):
            self._dispatcher.post(
                LogMessage(f"{exe_lbl} not found at: {exe}\n", "err")
            )
            return False, False

        dxvk_notice = False
        if cfg.get("dxvk_notice_pending"):
            update_config(lambda c: c.pop("dxvk_notice_pending", None))
            dxvk_notice = True
        if cfg.get("clear_wdb_on_launch", False):
            remove_wdb(client_dir)

        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0
            )
            try:
                proc = subprocess.Popen(
                    [exe],
                    cwd=client_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                    close_fds=True,
                )
            except OSError:
                # The job object doesn't permit breakaway — retry without it.
                flags &= ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
                proc = subprocess.Popen(
                    [exe],
                    cwd=client_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                    close_fds=True,
                )
            pid = proc.pid
            pgid = getattr(proc, "pgid", None) or pid
            self.state.game_running = True
            self.state.game_pid = pid
            self.state.game_pgid = pgid
            self._dispatcher.post(GameLaunched(pid, pgid))
            threading.Thread(
                target=self._watch_game,
                args=(proc, pid, "game"),
                daemon=True,
            ).start()
            self._dispatcher.post(LogMessage(f"Launched {exe_lbl}!\n", "ok"))
            return True, dxvk_notice
        except Exception as e:
            self.state.game_running = False
            self.state.game_pid = None
            self.state.game_pgid = None
            self._dispatcher.post(
                LogMessage(f"Failed to launch {exe_lbl}: {e}\n", "err")
            )
            return False, dxvk_notice

    def _launch_game_via_umu(self, client_dir: str, cfg: dict) -> tuple:
        """Linux launch through umu-launcher: the client run under Proton in
        a launcher-wide WINEPREFIX. Prefers an installed external-launcher
        mod's executable like Windows. No DXVK notice (shader-cache stutter
        is a Windows/DXVK-mod concern). Records the running game and watches
        its exit — unless close_on_launch fires, in which case the child's
        output goes to a sidecar file and no watcher runs (the launcher exits
        right after spawning, so nothing may depend on our pipes staying
        open)."""
        from ..services import umu

        exe, exe_lbl = pick_game_executable(
            client_dir,
            mods.external_launcher_executables(client_dir),
        )
        if not os.path.exists(exe):
            self._dispatcher.post(
                LogMessage(f"{exe_lbl} not found at: {exe}\n", "err")
            )
            return False, False
        if cfg.get("clear_wdb_on_launch", False):
            remove_wdb(client_dir)

        launch_cfg = cfg.get("launch") or {}
        # Once this process exits, any later write by umu/Proton into a pipe
        # we used to read dies of EPIPE/SIGPIPE and can kill the game
        # mid-startup (worst on SteamOS: first-run Proton download). With
        # close-on-launch there is no reader left, so redirect to a file.
        output_file = (
            umu.game_output_log_path()
            if cfg.get("close_on_launch", False)
            else ""
        )
        try:
            pid, pgid, proc = umu.launch(
                client_dir,
                exe,
                proton=launch_cfg.get("umu_proton") or umu.default_proton(),
                game_id=launch_cfg.get(
                    "umu_game_id", "umu-nostalgia-launcher"
                ),
                umu_binary=launch_cfg.get("umu_binary_path", ""),
                renderer=launch_cfg.get("umu_renderer", "auto"),
                gamemode=launch_cfg.get("umu_gamemode", True),
                wayland=launch_cfg.get("umu_wayland", True),
                skip_builtin_dxvk=launch_cfg.get(
                    "umu_skip_builtin_dxvk", False
                ),
                output_file=output_file,
            )
            self.state.game_running = True
            self.state.game_pid = pid
            self.state.game_pgid = pgid
            self._dispatcher.post(GameLaunched(pid, pgid))
            if not output_file:
                threading.Thread(
                    target=self._watch_game, args=(proc, pid), daemon=True
                ).start()
            prefix = umu.compute_wine_prefix()
            self._dispatcher.post(
                LogMessage(
                    f"Launched {exe_lbl} via umu (PID {pid}, WINEPREFIX "
                    f"{prefix}).\n",
                    "ok",
                )
            )
            if output_file:
                self._dispatcher.post(
                    LogMessage(
                        "Launcher will close on launch — game output "
                        f"goes to {output_file}\n",
                        "dim",
                    )
                )
            self._dispatcher.post(
                StatusChanged("Running WoW.exe — click TERMINATE to quit")
            )
            return True, False
        except Exception as e:
            self.state.game_running = False
            self.state.game_pid = None
            self.state.game_pgid = None
            self._dispatcher.post(
                LogMessage(f"Failed to launch {exe_lbl} via umu: {e}\n", "err")
            )
            return False, False

    def _watch_game(self, proc, pid: int, source: str = "umu"):
        """Background watcher: drains the game's output into the session log
        until EOF (the process exited), then clears the running state and
        publishes GameExited."""
        self._drain_child_output(proc, source)
        try:
            code = proc.wait()
        except Exception:
            code = None
        self.state.game_running = False
        self.state.game_pid = None
        self.state.game_pgid = None
        self._dispatcher.post(GameExited(pid, code))
        if code in (0, None):
            self._dispatcher.post(StatusChanged("Game exited."))
        else:
            self._dispatcher.post(StatusChanged(f"Game exited (code {code})."))

    def _drain_child_output(self, proc, source: str = "umu"):
        """Log a child process's merged stdout/stderr into the session log.

        Each non-blank line is prefixed ``[source]``; consecutive duplicates
        collapse into one line with a ×N suffix and a hard cap keeps a chatty
        Wine session from flooding the buffer/file/UI (a suppression notice
        is logged once). Blocks until EOF — run it on a worker thread."""
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        prefix = f"[{source}] "
        last = None
        repeats = 0
        shown = 0
        dropped = 0

        def flush():
            nonlocal last, repeats
            if last is not None:
                suffix = f"  ×{repeats + 1}" if repeats else ""
                log(f"{prefix}{last}{suffix}", "dim")
            last = None
            repeats = 0

        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if shown >= _CHILD_OUTPUT_MAX_LINES:
                    dropped += 1
                    continue
                if line == last:
                    repeats += 1
                    continue
                flush()
                last = line
                shown += 1
            flush()
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass
            if dropped:
                log(f"{prefix}… {dropped} more lines suppressed", "dim")

    def terminate_game(self) -> bool:
        """Request termination of the running game (umu + WoW.exe process
        group). Returns True when a game was running; the actual exit is
        reported asynchronously via GameExited from the watcher. The kill
        itself runs off-thread: its grace wait (up to ~2 s) must not freeze
        the GUI."""
        if not self.state.game_running:
            return False
        pid = self.state.game_pid
        pgid = self.state.game_pgid
        self._dispatcher.post(
            LogMessage(f"Terminating game (PID {pid})…\n", "acct")
        )

        def _killer():
            from ..services import umu

            try:
                assert pid is not None and pgid is not None
                umu.kill_game(pid, pgid)
            except Exception as e:
                self._dispatcher.post(
                    LogMessage(f"Failed to terminate game: {e}\n", "err")
                )

        threading.Thread(target=_killer, daemon=True).start()
        return True

    def _on_event(self, event: Event) -> None:
        """Subscribed handler for typed worker lifecycle events (UI thread)."""
        # State is mutated only on the dispatcher thread (bridge 50ms or test
        # direct dispatch_all). Guard with lock for test parallelism.
        with self._lock:
            if isinstance(event, ProgressChanged):
                self.state.progress = max(0.0, min(1.0, event.value))
                self.state.progress_label = event.label
                self.state.progress_file = event.current_file
                self.state.progress_downloaded = event.downloaded
                self.state.progress_total = event.total
                self.state.progress_speed = event.speed
                self.state.progress_peers = event.peers
                self.state.progress_verified_pieces = event.verified_pieces
                self.state.progress_total_pieces = event.total_pieces
                return
            if isinstance(event, ClientVersionReady):
                self.state.client_version = event.version
                return
            if isinstance(event, VerificationUpToDate):
                self._on_up_to_date(event)
                return
            if isinstance(event, UpdateRequired):
                self._on_update_needed(event)
                return
            if isinstance(event, TorrentReachable):
                self._on_torrent_reachable(event)
                return
            if isinstance(event, TorrentUnavailable):
                self._on_torrent_unreachable(event)
                return
            if isinstance(event, TorrentCorrupt):
                self._on_torrent_corrupt(event)
                return
            if isinstance(event, TorrentStalled):
                self._on_torrent_stalled(event)
                return
            if isinstance(event, TorrentSessionError):
                self._on_torrent_session_error(event)
                return
            if isinstance(event, TorrentDiskError):
                self._on_torrent_disk_error(event)
                return
            if isinstance(event, TorrentVerifyFailed):
                self._on_torrent_verify_failed(event)
                return
            if isinstance(event, TorrentDiffReady):
                self._on_torrent_diff(event)
                return
            if isinstance(event, TorrentUpToDate):
                self._on_torrent_up_to_date(event)
                return
            if isinstance(event, TorrentRecoveryDone):
                self._on_torrent_recovery_done(event)
                return
            if isinstance(event, UpdateCompleted):
                self._on_done(event)
                return
            if isinstance(event, UpdateFailed):
                self._on_error(event)
                return

    def compute_readiness(self, addons_installing: bool = False) -> Readiness:
        """Footer button/status decision for the current state.

        `addons_installing` is owned by the addons controller; the UI passes
        its own flag so the button stays disabled while addons download,
        exactly like the mods flow.
        """
        if self.state.game_running:
            return Readiness(
                "terminate",
                "TERMINATE",
                "Running WoW.exe — click TERMINATE to quit",
            )
        if addons_installing:
            return Readiness("busy", "Installing…", "Downloading addons…")
        if self.state.running:
            label = {
                "update": "Updating…",
                "download": "Downloading…",
            }.get(self._op or "", "Checking…")
            return Readiness("busy", label, self.state.status)
        if not self._client_updates_enabled():
            if not self._playable_client_present():
                # Updates are off but the client folder holds nothing
                # playable yet — offer a first-time BitTorrent acquisition.
                out = (self._get_out_dir() or "").strip()
                if not out:
                    return Readiness(
                        "disabled", "DOWNLOAD", "Set the game folder first"
                    )
                if torrent_recovery_available():
                    return Readiness(
                        "download",
                        "DOWNLOAD",
                        "Download client via BitTorrent",
                    )
                if not can_launch_client():
                    return Readiness(
                        "disabled",
                        "DOWNLOAD",
                        "No BitTorrent source — enable client updates",
                    )
                return Readiness(
                    "busy",
                    "DOWNLOAD",
                    "No BitTorrent source — enable client updates",
                )
            if self._mods_have_errors():
                return Readiness("busy", "PLAY", "Mod errors — check MODS tab")
            if not can_launch_client():
                return Readiness("busy", "READY", "Client updates disabled")
            return Readiness("play", "PLAY", "Client updates disabled")
        # Torrent-only readiness (no manifest/diff).
        # Disabled till verification (cache makes this instant via
        # TORRENT_VALIDATION_CACHE_KEY). Magnet-only with
        # torrent.update=false skips incremental verify → Download.
        if (
            self.state.torrent_stale is None
            and self.state.torrent_reachable is None
            and self.state.torrent_error is None
            and not self.state.client_ready
        ):
            from ..core import launcher as _launcher_v

            _vcfg = _launcher_v.config()
            _torrent_only = bool(_vcfg and _vcfg.has_torrent())
            if _torrent_only:
                # Respect torrent.update flag; magnet-only first-time-only
                # still shows Download via BitTorrent, not Verifying.
                if _vcfg is not None and _vcfg.torrent_update_allowed():
                    if not self._playable_client_present():
                        return Readiness("disabled", "UPDATE", "Verifying…")
                    return Readiness("disabled", "PLAY", "Verifying…")
        if self.state.torrent_stale:
            # Magnet-only first-time-only: don't offer incremental
            # torrent updates (server.download.torrent.update).
            from ..core import launcher as _launcher_cfg

            _cfg = _launcher_cfg.config()
            if _cfg is not None and not _cfg.torrent_update_allowed():
                pass  # fall through — no torrent update for magnet-only
            else:
                n = len(self.state.torrent_stale)
                error_suffix = ""
                if self.state.torrent_error:
                    error_suffix = f" ({self.state.torrent_error})"
                return Readiness(
                    "update",
                    "UPDATE",
                    f"{n} file(s) to update via BitTorrent{error_suffix}",
                )
        if self.state.torrent_reachable is False:
            error_detail = (
                f": {self.state.torrent_error}"
                if self.state.torrent_error
                else ""
            )
            if not can_launch_client():
                return Readiness(
                    "disabled",
                    "UPDATE",
                    f"Torrent unavailable{error_detail}",
                )
            return Readiness(
                "play", "PLAY", f"Torrent unavailable{error_detail}"
            )
        if self.state.torrent_error and self.state.torrent_stale is None:
            # Torrent reachable but had an error (stalled, session, disk,
            # verify failed). Offer recovery so the user can retry — but
            # never at the cost of stranding an installed client: with a
            # game executable on disk, PLAY wins (Force recheck remains
            # the repair path).
            if self._playable_client_present():
                return Readiness(
                    "play",
                    "PLAY",
                    "Verification failed — playing installed client",
                )
            return Readiness(
                "update",
                "UPDATE",
                f"Download via BitTorrent ({self.state.torrent_error})",
            )
        if not self.state.client_ready and torrent_recovery_available():
            if self._playable_client_present():
                return Readiness(
                    "play",
                    "PLAY",
                    "Torrent unavailable — playing unverified client",
                )
            return Readiness("update", "UPDATE", "Download via BitTorrent")
        # Fallback zip for first-install when torrent unavailable
        if not self.state.client_ready:
            from ..core import launcher as _launcher_f

            _cfg_f = _launcher_f.config()
            _fallback = _cfg_f.download_fallback_url if _cfg_f else None
            if _fallback and not self._playable_client_present():
                return Readiness(
                    "update", "UPDATE", "Download via HTTP fallback"
                )
            if not can_launch_client():
                reason = (
                    "umu-run not found"
                    if is_linux()
                    else "launching unsupported on this platform"
                )
                return Readiness(
                    "disabled", "UPDATE", f"Torrent unavailable — {reason}"
                )
            if self._playable_client_present():
                return Readiness(
                    "play", "PLAY", "Torrent unavailable — playing client"
                )
            return Readiness("play", "PLAY", "Torrent unavailable")
        if self._mods_have_errors():
            return Readiness("busy", "PLAY", "Mod errors — check MODS tab")
        if not can_launch_client():
            return Readiness("busy", "READY", "Everything up to date!")
        return Readiness("play", "PLAY", "Everything up to date!")

    # ── internals ───────────────────────────────────────────────────────────

    def _playable_client_present(self) -> bool:
        """Whether the configured game folder holds a launchable executable —
        the same pick ``launch_game`` makes, so a PLAY readiness can trust
        the click to actually start something."""
        client_dir = (self._get_out_dir() or "").strip()
        if not client_dir:
            return False
        exe, _ = pick_game_executable(
            client_dir,
            mods.external_launcher_executables(client_dir),
        )
        return os.path.isfile(exe)

    def _client_updates_enabled(self) -> bool:
        from ..core import launcher

        return launcher.effective_client_updates_enabled()

    def _on_done(self, event: UpdateCompleted):
        if event.version:
            self.state.client_version = event.version
        self.state.running = False
        self.state.client_ready = True
        self._op = None
        self._dispatcher.post(ProgressChanged(1.0, ""))
        self._dispatcher.post(OperationFinished("update", True))

    def _on_error(self, event: UpdateFailed):
        op = event.op or self._op or "update"
        self.state.running = False
        self.state.client_ready = False
        self._op = None
        self._dispatcher.post(ProgressChanged(0.0, ""))
        self._dispatcher.post(OperationFailed(op, event.message))

    def _on_up_to_date(self, event: VerificationUpToDate):
        self.state.running = False
        self.state.client_ready = True
        self._op = None
        self._dispatcher.post(ProgressChanged(1.0, ""))
        self._dispatcher.post(OperationFinished("verify", True))

    def _on_update_needed(self, event: UpdateRequired):
        self.state.running = False
        self.state.client_ready = False
        self._op = None
        self._dispatcher.post(ProgressChanged(0.0, ""))
        self._dispatcher.post(OperationFinished("verify", False))

    def _on_torrent_reachable(self, event: TorrentReachable):
        self.state.torrent_reachable = True

    def _on_torrent_unreachable(self, event: TorrentUnavailable):
        self._torrent_failure(event.message, reachable=False)

    def _on_torrent_corrupt(self, event: TorrentCorrupt):
        self._torrent_failure(event.message, reachable=False)

    def _on_torrent_stalled(self, event: TorrentStalled):
        self._torrent_failure(event.message, reachable=True)

    def _on_torrent_session_error(self, event: TorrentSessionError):
        self._torrent_failure(event.message, reachable=True)

    def _on_torrent_disk_error(self, event: TorrentDiskError):
        self._torrent_failure(event.message, reachable=True)

    def _on_torrent_verify_failed(self, event: TorrentVerifyFailed):
        self._torrent_failure(event.message, reachable=True)

    def _torrent_failure(self, message: str, *, reachable: bool):
        """Common landing for failed torrent verifications."""

        self.state.running = False
        self.state.client_ready = False
        self.state.torrent_reachable = reachable
        self.state.torrent_error = message or None
        self.state.torrent_stale = None
        op = self._op or "verify"
        self._op = None
        self._dispatcher.post(ProgressChanged(0.0, ""))
        self._dispatcher.post(OperationFinished(op, False))

    def _on_torrent_diff(self, event: TorrentDiffReady):
        self.state.running = False
        self.state.client_ready = False
        self.state.torrent_stale = list(event.stale) if event.stale else []
        self._op = None
        self._dispatcher.post(ProgressChanged(0.0, ""))
        self._dispatcher.post(
            UpdateFilesList(sorted(self.state.torrent_stale))
        )
        self._dispatcher.post(OperationFinished("verify", False))

    def _on_torrent_up_to_date(self, event: TorrentUpToDate):
        self.state.running = False
        self.state.client_ready = True
        self.state.torrent_stale = None
        self._op = None
        self._dispatcher.post(ProgressChanged(1.0, ""))
        self._dispatcher.post(OperationFinished("verify", True))

    def _on_torrent_recovery_done(self, event: TorrentRecoveryDone):
        self.state.running = False
        self.state.client_ready = True
        self.state.torrent_stale = None
        self._op = None
        self._dispatcher.post(ProgressChanged(1.0, ""))
        self._dispatcher.post(OperationFinished("update", True))

    def _mods_have_errors(self) -> bool:
        return any(
            bool(s.get("error"))
            for s in load_config().get("mods", {}).values()
        )

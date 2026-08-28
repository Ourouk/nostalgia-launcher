"""Toolkit-agnostic shared application-state models.

Pure dataclasses that capture the runtime state of every panel — the
controllers mutate them and the Qt layer renders them. No GUI toolkit, no
threading. Field names match the on-disk config keys and the session records
the controllers keep.
"""

from dataclasses import dataclass, field

# ── update / verification flow ────────────────────────────────────────────────


@dataclass
class UpdateState:
    """Footer state: status text, progress, and the verify/update lifecycle
    flags (_status_var / _pb_val / _prog_label_var / _running /
    _client_ready / _diff_nodes / _client_ver_var)."""

    status: str = "Ready to update"
    progress: float = 0.0
    progress_label: str = ""
    progress_file: str = ""
    progress_downloaded: int = 0
    progress_total: int = 0
    progress_speed: float = 0.0
    progress_peers: int = 0
    progress_verified_pieces: int = 0
    progress_total_pieces: int = 0
    running: bool = False
    client_ready: bool = False
    manifest_available: bool = False
    diff_nodes: list | None = None
    torrent_stale: list | None = None
    torrent_reachable: bool | None = None
    torrent_error: str | None = None
    # Folder the last verify ran against — start_update refuses to apply a
    # cached diff tree to a different path.
    verify_out_dir: str = ""
    client_version: str = ""
    game_running: bool = False
    game_pid: int | None = None
    game_pgid: int | None = None


# ── news ──────────────────────────────────────────────────────────────────────


@dataclass
class NewsState:
    """Cached news feed (_featured, _news_items, _feat_ts, _news_ts)."""

    featured: dict | None = None
    items: list | None = None
    feat_ts: float = 0.0
    news_ts: float = 0.0


# ── mods ──────────────────────────────────────────────────────────────────────


@dataclass
class ModState:
    """One per-mod config record ("mods" key): enabled, installed_version,
    installed_files, error.

    ``present`` is the session-computed filesystem-truth flag (files on disk +
    dlls.txt registration); it is not a config key.
    """

    enabled: bool = False
    installed_version: str | None = None
    installed_files: list = field(default_factory=list)
    error: str | None = None
    present: bool = False

    @property
    def has_error(self) -> bool:
        return bool(self.error)


@dataclass
class ModPending:
    """One not-yet-applied checkbox change from _mod_pending_state."""

    enabled: bool | None = None


@dataclass
class ModsState:
    """MODS panel state: config records, fetched latest versions
    (_mods_state), pending checkbox changes, the nav-badge count, and
    filesystem-detected mods no catalog claims (``unknown``)."""

    records: dict[str, ModState] = field(default_factory=dict)
    latest_versions: dict[str, str] = field(default_factory=dict)
    pending: dict[str, ModPending] = field(default_factory=dict)
    updates_count: int = 0
    unknown: list = field(default_factory=list)

    def latest_version(self, mod_id: str) -> str | None:
        return self.latest_versions.get(mod_id)

    @property
    def has_errors(self) -> bool:
        return any(rec.has_error for rec in self.records.values())

    @property
    def has_pending_changes(self) -> bool:
        return bool(self.pending)


# ── assets ───────────────────────────────────────────────────────────────────


@dataclass
class AssetPending:
    """One not-yet-applied checkbox change from the ASSETS panel."""

    enabled: bool | None = None


@dataclass
class AssetState:
    """One per-asset config record ("assets" key): enabled, installed
    version, installed files, install-time probe state, error.

    ``present`` is the session-computed filesystem-truth flag (recorded
    files exist on disk); it is not a config key.
    """

    enabled: bool = False
    installed_version: str | None = None
    installed_files: list = field(default_factory=list)
    probe_state: dict = field(default_factory=dict)
    error: str | None = None
    present: bool = False

    @property
    def has_error(self) -> bool:
        return bool(self.error)


@dataclass
class AssetsState:
    """ASSETS panel state: config records and pending checkbox changes,
    plus the nav-badge updates count."""

    records: dict[str, AssetState] = field(default_factory=dict)
    pending: dict[str, AssetPending] = field(default_factory=dict)
    updates_count: int = 0

    @property
    def has_errors(self) -> bool:
        return any(rec.has_error for rec in self.records.values())

    @property
    def has_pending_changes(self) -> bool:
        return bool(self.pending)


# ── addons ────────────────────────────────────────────────────────────────────


@dataclass
class AddonState:
    """One addon record — the shape stored in the config's "addons" key
    ({"folder", "status", "git", "branch", "ref", "toc", "description",
    "error"})."""

    folder: str
    status: str = "available"
    git: str | None = None
    branch: str | None = None
    ref: str | None = None
    toc: dict = field(default_factory=dict)
    description: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, rec: dict) -> "AddonState":
        if not isinstance(rec, dict):
            rec = {}
        values = {name: rec.get(name) for name in cls.__dataclass_fields__}
        # `folder` is the identity key every consumer indexes by — never
        # let it be None despite its str annotation.
        if not isinstance(values["folder"], str):
            values["folder"] = ""
        return cls(**values)

    def to_dict(self) -> dict:
        return {
            name: getattr(self, name) for name in self.__dataclass_fields__
        }


@dataclass
class AddonError:
    """Session install/update failure carried across rescans
    (_addon_errors: {folder: {"error", "git"}})."""

    error: str
    git: str | None = None


@dataclass
class AddonsState:
    """ADDONS panel state, mirroring _addons_status, _addons_busy,
    _addons_installing, _addons_verified_ts, _addon_errors,
    _addon_sections_open and _addon_updates_count."""

    state: str = "idle"
    addons: dict[str, AddonState] = field(default_factory=dict)
    available: list[AddonState] = field(default_factory=list)
    busy: bool = False
    installing: bool = False
    verified_ts: float = 0.0
    errors: dict[str, AddonError] = field(default_factory=dict)
    pending: dict[str, bool] = field(default_factory=dict)
    sections_open: dict[str, bool] = field(
        default_factory=lambda: {"INSTALLED": True, "AVAILABLE": True}
    )
    updates_count: int = 0


# ── settings / path ───────────────────────────────────────────────────────────


@dataclass
class SettingsState:
    """Game-folder path (_path_var), the loaded config dict (_cfg) and the
    first-run flags. ``path`` is the stored folder ("" when unconfirmed);
    ``suggestion`` is the Games/<ServerName> placeholder the Settings dialog
    shows until the user confirms a folder."""

    path: str = ""
    suggestion: str = ""
    config: dict = field(default_factory=dict)
    first_run: bool = False
    first_run_av_pending: bool = False
    first_run_verify_pending: bool = False


@dataclass
class LaunchSettings:
    """Linux umu-launcher launch settings (the "launch" config key)."""

    umu_proton: str = "UMU-Proton"  # PROTONPATH value (codename or path)
    umu_binary_path: str = ""  # "" = auto-detect umu-run on PATH
    umu_game_id: str = "umu-nostalgia-launcher"
    umu_renderer: str = "auto"  # one of RENDERER_* in services/umu.py
    umu_gamemode: bool = True  # wrap the launch in Feral GameMode if installed
    umu_wayland: bool = True  # enable the Proton/Wine Wayland backend
    # Skip Proton's built-in DXVK so client-folder DLLs (e.g. a dxvk-gplasync
    # build installed via the MODS panel) are the ones Wine loads.
    umu_skip_builtin_dxvk: bool = False

    @classmethod
    def from_config(cls, cfg: dict) -> "LaunchSettings":
        data = cfg.get("launch") or {}
        if not isinstance(data, dict):
            # A corrupted state file must not crash SettingsController
            # construction at startup.
            data = {}
        return cls(
            umu_proton=data.get("umu_proton", "UMU-Proton"),
            umu_binary_path=data.get("umu_binary_path", ""),
            umu_game_id=data.get("umu_game_id", "umu-nostalgia-launcher"),
            umu_renderer=data.get("umu_renderer", "auto"),
            umu_gamemode=bool(data.get("umu_gamemode", True)),
            umu_wayland=bool(data.get("umu_wayland", True)),
            umu_skip_builtin_dxvk=bool(
                data.get("umu_skip_builtin_dxvk", False)
            ),
        )

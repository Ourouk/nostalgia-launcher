"""Source-backend protocol and registry.

A *source backend* is one way to obtain a payload for a content entry —
release archives, pinned URLs, git-host snapshots. Every backend speaks the
same tiny interface so the content verticals (mods / assets / addons) can
share the whole pool; only each vertical's deployment choice and allowed
install hooks vary (see `deploy.py` / `hooks.py`).

Interface contract:

* ``validate(source) -> dict | None``
  Kind-specific sanitization of one catalog ``source`` object. Returns the
  cleaned source dict, or None when unusable (the whole entry is refused).
* ``resolve_version(entry, *, force=False) -> str | None``
  The latest version string for the entry (cached when it costs network);
  None when undeterminable. Direct kinds answer offline from their pin.
* ``fetch(entry, *, client_dir=None, release=None) -> FetchResult``
  Download the payload. ``client_dir`` is an optional staging hint: backends
  that stream a single file stage it beside its final destination there so
  the deployer's rename stays atomic on one volume. ``release`` optionally
  supplies a pre-fetched API release object to release-kind backends;
  other backends ignore it.

Wire compatibility: kind strings ("github_release", "codeberg_release",
"direct_file", "direct_tar", "git_archive") and every persisted cache key
are unchanged.
"""

from dataclasses import dataclass, field


@dataclass
class StreamedFile:
    """A single-file payload already staged on disk."""

    path: str
    size: int = 0
    sha1_hex: str | None = None  # computed digest of the staged bytes
    probe: dict = field(default_factory=dict)  # response header capture


@dataclass
class FetchResult:
    """What a backend hands to the deployer."""

    data: bytes | None = None  # in-memory payload (archives / single files)
    file: StreamedFile | None = None  # streamed single-file payloads
    version: str | None = None
    name: str | None = None  # artifact name (e.g. the release asset name)


class SourceBackend:
    """Base class every backend extends (duck-typed protocol)."""

    KIND: str = ""

    def validate(self, source: dict) -> dict | None:
        raise NotImplementedError

    def resolve_version(
        self, entry: dict, *, force: bool = False
    ) -> str | None:
        return None

    def fetch(
        self,
        entry: dict,
        *,
        client_dir: str | None = None,
        release: dict | None = None,
    ) -> FetchResult:
        raise NotImplementedError


_REGISTRY: dict[str, SourceBackend] = {}


def register(backend: SourceBackend) -> None:
    """Add a backend to the pool (later registration wins)."""
    _REGISTRY[backend.KIND] = backend


def get(kind: str) -> SourceBackend:
    """The backend registered for ``kind``; unknown kinds are a hard error
    (catalog validation refuses them long before fetch time)."""
    backend = _REGISTRY.get(kind)
    if backend is None:
        raise KeyError(f"No source backend registered for {kind!r}")
    return backend


def kinds() -> tuple[str, ...]:
    """Every registered kind string."""
    return tuple(sorted(_REGISTRY))

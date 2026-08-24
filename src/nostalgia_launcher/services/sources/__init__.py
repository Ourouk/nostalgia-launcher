"""Download-source backends shared by every content vertical.

One module per way to obtain a payload; each registers itself into the
registry on import (`base.get(kind)`). The verticals differ only in how
they *deploy* a fetched payload (`deploy.py`) and which post-install
*hooks* they allow (`hooks.py`) — every backend is callable from every
vertical.
"""

from . import (  # noqa: F401  (backend imports fill the registry)
    codeberg_release,
    direct_file,
    git_archive,
    github_release,
    hooks,  # noqa: F401  (hook registry side effects)
)
from .base import (
    FetchResult,
    SourceBackend,
    StreamedFile,
    get,
    kinds,
    register,
)

# Which registered hook names each content type's catalogs may reference.
# mods may use any hook; assets/addons currently allow none (their extra
# behaviour — integrity pins, pfUI fixups — is intrinsic to their flows).
TYPE_HOOK_POLICY = {
    "mods": frozenset(hooks.names()),
    "assets": frozenset(),
    "addons": frozenset(),
}

__all__ = [
    "FetchResult",
    "SourceBackend",
    "StreamedFile",
    "TYPE_HOOK_POLICY",
    "get",
    "kinds",
    "register",
]

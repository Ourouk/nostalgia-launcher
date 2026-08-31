"""Post-install hook registry.

Hooks are named, allowlisted side-effects a catalog entry may request after
its payload is deployed (e.g. writing a dxvk.conf next to the client). A
catalog can only reference names registered here — never arbitrary code.
Which hook names a content *type* accepts is that type's policy (mods
allow the registry; assets/addons allow none today).
"""

from ...core.log_sink import log

# ── built-in hooks ───────────────────────────────────────────────────────────

DXVK_CONF_CONTENT = """# Low latency - limit queued frames - helps input lag
d3d9.maxFrameLatency = 1
# Forces clamp for AF through DXVK - if you see grass textures shimmering or shaking try false
d3d9.clampNegativeLodBias = True
# Disable logging for performance
dxvk.logLevel = none
# Triple buffering (needed for smooth G-SYNC + RTSS capping) can try lowering backbuffers if want
dxvk.presentInterval = 0
# Use hardware mouse for responsiveness
d3d9.cursor = 1
# VanillaFix handles DPI awareness; avoid double-scaling
d3d9.dpiAware = False
# Enable GPL if supported to reduce stuttering (NVIDIA 473.33+, AMD 24.6.1+)
dxvk.enableGraphicsPipelineLibrary = Auto
# Track pipeline lifetimes to reduce memory usage
dxvk.trackPipelineLifetime = True
# Limit compiler threads to reduce memory usage
dxvk.numCompilerThreads = 2
"""


def _write_dxvk_conf(client_dir: str) -> list[str]:
    path = client_dir + "/dxvk.conf"
    with open(path, "w") as f:
        f.write(DXVK_CONF_CONTENT)
    log("  Wrote dxvk.conf")
    return ["dxvk.conf"]


# ── registry ─────────────────────────────────────────────────────────────────

_HOOKS: dict[str, object] = {}


def register(name: str, fn):
    """Register a hook callable ``fn(client_dir) -> list[str]`` (the files it
    wrote, relative to the client dir). Later registration wins."""
    _HOOKS[name] = fn


def names() -> tuple[str, ...]:
    """Every registered hook name (the allowlist catalogs may reference)."""
    return tuple(sorted(_HOOKS))


def run(name: str, client_dir: str) -> list[str]:
    """Execute one registered hook; unknown names are refused."""
    fn = _HOOKS.get(name)
    if fn is None:
        raise RuntimeError(f"Unknown post-install hook: {name!r}")
    assert callable(fn)
    result = fn(client_dir)
    return result or []  # type: ignore[no-untyped-call]


register("write_dxvk_conf", _write_dxvk_conf)

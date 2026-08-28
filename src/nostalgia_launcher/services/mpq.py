"""MPQ scanner: classify a client ``Data/`` tree's archives as stock or custom.

Given the client folder and the game version (provided by the caller — no
auto-detection here), walks ``Data/`` including its per-locale subfolders
and sorts every ``*.MPQ`` into three buckets:

- **stock** — the basename matches the known Blizzard archive set for that
  version (patch-chain tables: warcraft-rs "WoW Patch Chain Summary" plus
  the archive lists TrinityCore/maNGOS extractors load);
- **custom_managed** — not stock, but its path matches an entry of the
  launcher's assets registry (a server content patch the launcher
  installed or tracks);
- **custom_foreign** — not stock and untracked: a stray/custom MPQ the
  launcher knows nothing about.

The scan is read-only; deletion goes through `remove_custom_mpq`, which
refuses anything outside the client's ``Data/`` folder. ``Data/Cache``
(the Blizzard updater's download cache) is skipped entirely.
"""

import os

from . import catalog

# Locale subfolder codes shipped by the installers ({L} placeholder).
LOCALES = (
    "enUS",
    "enGB",
    "koKR",
    "frFR",
    "deDE",
    "zhCN",
    "zhTW",
    "esES",
    "esMX",
    "ruRU",
)

# ── stock archive tables ─────────────────────────────────────────────────────
#
# Spec language: literal name parts with ``{L}`` (any known locale) and
# ``{N}`` (digits) placeholders; the ``.mpq`` extension is implicit.
# Deliberately inclusive: a genuine Blizzard archive must never be flagged
# as custom, so borderline installer-era names (wmo/terrain/speech/misc)
# count as stock.


def _classic() -> list:
    return [
        "dbc",
        "interface",
        "model",
        "sound",
        "texture",
        # Installer-era names still present on some 1.x installs.
        "base",
        "misc",
        "speech",
        "terrain",
        "wmo",
        # Patch chain.
        "patch",
        "patch-2",
    ]


def _expansion_locale_specs(prefixes: list) -> list:
    out = []
    for prefix in prefixes:
        out.append(f"{prefix}-locale-{{L}}" if prefix else "locale-{L}")
        out.append(f"{prefix}-speech-{{L}}" if prefix else "speech-{L}")
    return out


def _update_specs(locale_patches: bool) -> list:
    """The Cata/MoP update system: numbered builds in Data/, base-scoped
    variants and per-locale copies in the locale subfolders."""
    specs = ["wow-update-{N}", "wow-update-base-{N}", "base-{N}", "base-{L}"]
    if locale_patches:
        specs.append("wow-update-{L}-{N}")
    return specs


STOCK_SPECS = {
    "1.12.1": _classic(),
    "2.4.3": [
        "common",
        "expansion",
        *_expansion_locale_specs(["", "expansion"]),
        "patch",
        "patch-2",
        "patch-{L}",
        "patch-{L}-2",
    ],
    "3.3.5a": [
        "common",
        "common-2",
        "expansion",
        "lichking",
        *_expansion_locale_specs(["", "expansion", "lichking"]),
        "base-{L}",
        "patch",
        "patch-2",
        "patch-3",
        "patch-{L}",
        "patch-{L}-2",
        "patch-{L}-3",
    ],
    "4.3.4": [
        "art",
        "model",
        "sound",
        "world",
        "world2",
        *[f"expansion{n}" for n in range(1, 4)],
        *_expansion_locale_specs(
            ["", *[f"expansion{n}" for n in range(1, 4)]]
        ),
        *_update_specs(locale_patches=True),
    ],
    "5.4.8": [
        "art",
        "misc",
        "model",
        "sound",
        "texture",
        "world",
        "world2",
        *[f"expansion{n}" for n in range(1, 5)],
        *_expansion_locale_specs(
            ["", *[f"expansion{n}" for n in range(1, 5)]]
        ),
        *_update_specs(locale_patches=True),
    ],
}

SUPPORTED_VERSIONS = tuple(STOCK_SPECS)


def compile_stock_patterns(specs: list) -> list:
    """Compile spec strings into case-insensitive full-match regexes over
    an MPQ stem (filename without extension)."""
    import re

    locale_alt = "|".join(LOCALES)
    patterns = []
    for spec in specs:
        rx = ""
        for part in re.split(r"(\{L\}|\{N\})", spec):
            if part == "{L}":
                rx += f"(?:{locale_alt})"
            elif part == "{N}":
                rx += r"\d+"
            else:
                rx += re.escape(part)
        patterns.append(re.compile(rf"^{rx}$", re.IGNORECASE))
    return patterns


STOCK_PATTERNS = {
    version: compile_stock_patterns(specs)
    for version, specs in STOCK_SPECS.items()
}


def is_stock_mpq(filename: str, version: str) -> bool:
    """Whether an MPQ filename is a known stock archive for `version`."""
    stem = os.path.splitext(filename)[0]
    return any(p.match(stem) for p in STOCK_PATTERNS[version])


def human_size(num) -> str:
    """A compact human-readable byte size ('1.2 MB'); '' when unknown."""
    if isinstance(num, bool) or not isinstance(num, (int, float)) or num < 0:
        return ""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


# ── scanning ─────────────────────────────────────────────────────────────────


def managed_dests(registry: list) -> list:
    """The install targets the assets registry tracks (`dest` values), used
    to tell launcher-managed customs from foreign ones."""
    return [
        e["dest"]
        for e in registry or []
        if isinstance(e, dict) and e.get("dest")
    ]


def scan_custom_mpqs(client_dir: str, version: str, managed=None) -> dict:
    """Classify every MPQ under ``<client_dir>/Data``.

    `managed` is the list of registry dest paths (client-relative, e.g.
    ``Data/patch-9.MPQ``). Returns {"version", "data_dir", "stock": [rel…],
    "custom_managed": [{"path", "size"}…], "custom_foreign": […]} sorted by
    path; a missing ``Data/`` yields empty buckets. Managed matching is done
    on both the full relative path and the bare basename so entries parked
    inside a locale subfolder still resolve.
    """
    if version not in STOCK_PATTERNS:
        raise ValueError(f"Unsupported game version: {version!r}")
    result = {
        "version": version,
        "data_dir": "",
        "stock": [],
        "custom_managed": [],
        "custom_foreign": [],
    }
    data_dir = os.path.join(client_dir, "Data")
    result["data_dir"] = data_dir
    if not os.path.isdir(data_dir):
        return result
    managed_paths = set()
    managed_names = set()
    for dest in managed or []:
        rel = str(dest).replace("\\", "/").lower()
        managed_paths.add(rel)
        managed_names.add(rel.rsplit("/", 1)[-1])
    seen_lower: set[str] = set()
    for root, dirs, files in os.walk(data_dir):
        dirs[:] = [d for d in dirs if d.lower() != "cache"]
        for name in sorted(files):
            if not name.lower().endswith(".mpq"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, client_dir).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = None
            rel_lower = rel.lower()
            if rel_lower in seen_lower:
                # Case-insensitive duplicate on a case-sensitive FS
                # (e.g. Data/patch-3.MPQ vs Data/patch-3.mpq) — the
                # first hit keeps its managed/foreign verdict; the
                # extra inode is surfaced as foreign so the user
                # can remove it.
                if not is_stock_mpq(name, version):
                    result["custom_foreign"].append(
                        {"path": rel, "size": size}
                    )
                else:
                    result["stock"].append(rel)
                continue
            seen_lower.add(rel_lower)
            if is_stock_mpq(name, version):
                result["stock"].append(rel)
                continue
            entry = {"path": rel, "size": size}
            if rel.lower() in managed_paths or name.lower() in managed_names:
                result["custom_managed"].append(entry)
            else:
                result["custom_foreign"].append(entry)
    return result


def remove_custom_mpq(client_dir: str, rel_path: str) -> str:
    """Delete one scanned MPQ from the client's Data tree. Refuses paths
    outside Data/ (or anything unsafe) so a tampered UI value cannot delete
    arbitrary files. Returns '' on success, else an error message."""
    if not catalog.safe_relpath(rel_path):
        return f"Refusing unsafe path: {rel_path}"
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) < 2 or parts[0].lower() != "data":
        return "Only files inside Data/ can be removed."
    full = os.path.join(client_dir, *parts)
    if not os.path.isfile(full):
        return "File no longer exists."
    try:
        os.remove(full)
    except OSError as e:
        return f"Could not remove {rel_path}: {e}"
    return ""

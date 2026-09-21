"""Shared payload deployment — how fetched bytes land in the client folder.

Deployment is chosen by the *entry shape*, not by the content type, which is
what lets every download backend serve every vertical:

* plain ``dest``            → `install_plain` (single file)
* ``extract_map`` + zip     → `extract_zip_map`
* ``extract_map`` + tar.gz  → `extract_tar_map`
* ``extract_map`` + 7z      → `extract_7z_map` (system 7z binary)
* addon folder target       → `unpack_folder` (strip the archive's top-level
                              dir into Interface/AddOns/<folder>)

All paths are validated against `core.safety.safe_relative_path`; a compromised
upstream must not be able to write outside the client folder. Extracted
members are also size-capped so a small compressed bomb cannot fill the
disk (the archives themselves are capped by the fetch layer).
"""

import io
import os
import shutil
import sys
import zipfile

from ...core.filesystem import rmtree_force
from ...core.log_sink import log
from ...core.process_env import clean_system_env
from ...core.safety import safe_relative_path

# Per-member uncompressed ceiling: far above any legitimate game file,
# far below disk-filling territory.
_MAX_MEMBER_BYTES = 1 * 1024 * 1024 * 1024

# System 7z binary candidates: upstream 7-Zip ships `7zz`, p7zip and most
# distro/brew packages ship `7z`, minimal builds ship `7za`. Probed in
# order via `find_seven_z` (mirrors services/umu.find_umu).
SEVEN_Z_CANDIDATES = ("7zz", "7z", "7za")

SEVEN_Z_MISSING_MSG = (
    "7z backend missing — Linux: Debian/Ubuntu "
    "'sudo apt install p7zip-full'; Fedora "
    "'sudo dnf install p7zip'; Arch 'sudo pacman -S p7zip'; "
    "then retry (Windows/macOS releases bundle their own 7z; "
    "dev runs: 7-Zip with 7z.exe on PATH / "
    "'brew install sevenzip')"
)


def _bundled_seven_z_names() -> tuple:
    """Bundled 7z binary names for this platform (shipped inside the
    frozen app on Windows/macOS — see packaging/fetch-7z-*)."""
    if os.name == "nt":
        return ("7zr.exe", "7za.exe", "7z.exe")
    return ("7zz", "7z", "7za")


def _bundled_seven_z() -> str:
    """7z binary shipped inside the frozen app, or '' when absent.

    Only the Windows (onefile) and macOS (.app) bundles ship one;
    dev runs and the Linux AppImage fall through to the PATH probe
    in `find_seven_z`.
    """
    if not getattr(sys, "frozen", False):
        return ""
    meipass = getattr(sys, "_MEIPASS", "")
    exe_dir = os.path.dirname(sys.executable or "")
    bases = (meipass, exe_dir, os.path.join(exe_dir, "_internal"))
    for base in bases:
        if not base:
            continue
        for name in _bundled_seven_z_names():
            candidate = os.path.join(base, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return ""


def find_seven_z() -> str:
    """Locate a 7z binary: bundled first, then system PATH.

    Returns '' when none is installed (callers raise
    SEVEN_Z_MISSING_MSG).
    """
    import shutil

    bundled = _bundled_seven_z()
    if bundled:
        return bundled
    for name in SEVEN_Z_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return ""


def checked_rel(dest_rel) -> str:
    """Validate a client-dir-relative install target before it is joined
    onto `client_dir`."""
    if not safe_relative_path(dest_rel):
        raise RuntimeError(f"Refusing unsafe install path: {dest_rel!r}")
    return dest_rel


def install_plain(client_dir: str, data: bytes, dest_rel: str) -> str:
    """Write one file into the client dir. The destination is re-validated
    here (defence in depth) before it is ever joined onto client_dir.
    Returns the written relative path."""
    dest_rel = checked_rel(dest_rel)
    dest = os.path.join(client_dir, dest_rel)
    os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    log(f"  Installed {dest_rel}")
    return dest_rel


def move_into_place(staged_path: str, client_dir: str, dest_rel: str) -> str:
    """Move an already-staged temp file to its final client-dir location.
    The stage file is expected beside the destination when the caller could
    arrange it (same volume → atomic rename); shutil.move falls back to a
    copy otherwise. Returns the written relative path."""
    dest_rel = checked_rel(dest_rel)
    dest = os.path.join(client_dir, dest_rel)
    os.makedirs(os.path.dirname(dest) or client_dir, exist_ok=True)
    shutil.move(staged_path, dest)
    return dest_rel


def extract_zip_map(
    client_dir: str, data: bytes, label: str, extract_map: dict
) -> list[str]:
    """Write every extract_map {zip entry: dest} found in a zip archive."""
    tmp_path = os.path.join(client_dir, f"_src_tmp_{label}.zip")
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        with zipfile.ZipFile(tmp_path) as zf:
            written = []
            for zip_path, dest_rel in extract_map.items():
                try:
                    info = zf.getinfo(zip_path)
                except KeyError:
                    log(f"  Warning: {zip_path} not in zip, skipping")
                    continue
                if info.file_size > _MAX_MEMBER_BYTES:
                    log(
                        f"  Warning: {zip_path} exceeds the extraction "
                        "size cap, skipping"
                    )
                    continue
                written.append(
                    install_plain(client_dir, zf.read(zip_path), dest_rel)
                )
            return written
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def extract_tar_map(
    client_dir: str, data: bytes, extract_map: dict
) -> list[str]:
    """Write every extract_map {tar entry pattern: dest} found in a
    .tar.gz."""
    import fnmatch
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        all_names = tf.getnames()
        written = []
        for pattern, dest_rel in extract_map.items():
            matched = (
                pattern
                if pattern in all_names
                else next(
                    (n for n in all_names if fnmatch.fnmatch(n, pattern)),
                    None,
                )
            )
            if matched is None:
                log(
                    f"  Warning: no file matching '{pattern}' in tar, skipping"
                )
                continue
            member = tf.getmember(matched)
            fh = tf.extractfile(member)
            if fh is None:
                # Directories / special members have no payload — a pattern
                # that matches one is unsatisfiable, not fatal.
                log(f"  Warning: '{matched}' has no file content, skipping")
                continue
            if member.size > _MAX_MEMBER_BYTES:
                log(
                    f"  Warning: {matched} exceeds the extraction size "
                    "cap, skipping"
                )
                continue
            written.append(install_plain(client_dir, fh.read(), dest_rel))
        return written


def extract_7z_map(
    client_dir: str, data: bytes, extract_map: dict
) -> list[str]:
    """Write every extract_map {7z entry pattern: dest} found in a .7z.

    Extracts via the system 7z binary into an isolated temp dir (never
    directly into the client dir, so hostile member names stay inside
    the throwaway dir) and installs only the mapped members through
    `install_plain`, which re-validates every destination. Matching is
    exact-first with an `fnmatch` fallback, like `extract_tar_map`.
    Raises RuntimeError with install instructions when no 7z binary is
    on PATH.
    """
    import fnmatch
    import subprocess
    import tempfile

    seven_z = find_seven_z()
    if not seven_z:
        raise RuntimeError(SEVEN_Z_MISSING_MSG)
    with tempfile.TemporaryDirectory(prefix="nl7z-") as tmp:
        archive = os.path.join(tmp, "payload.7z")
        outdir = os.path.join(tmp, "out")
        os.makedirs(outdir, exist_ok=True)
        with open(archive, "wb") as f:
            f.write(data)
        try:
            # Scrubbed env: AppImage AppRun exports LD_LIBRARY_PATH
            # at the bundled libs, which makes the system 7z load
            # the bundled libstdc++ and die on CXXABI_1.3.15.
            proc = subprocess.run(
                [seven_z, "x", archive, f"-o{outdir}", "-y", "-bd"],
                capture_output=True,
                text=True,
                timeout=120,
                env=clean_system_env(),
            )
        except FileNotFoundError:
            raise RuntimeError(SEVEN_Z_MISSING_MSG) from None
        except subprocess.SubprocessError as e:
            raise RuntimeError(f"7z extraction failed: {e}") from e
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            detail = detail[-500:] if len(detail) > 500 else detail
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"7z extraction failed{suffix}")
        members: dict[str, str] = {}
        for root, _dirs, files in os.walk(outdir):
            for name in files:
                full = os.path.join(root, name)
                if os.path.islink(full):
                    continue
                rel = os.path.relpath(full, outdir).replace("\\", "/")
                members.setdefault(rel, full)
        all_names = list(members)
        written = []
        total = 0
        for pattern, dest_rel in extract_map.items():
            matched = (
                pattern
                if pattern in members
                else next(
                    (n for n in all_names if fnmatch.fnmatch(n, pattern)),
                    None,
                )
            )
            if matched is None:
                log(f"  Warning: no file matching '{pattern}' in 7z, skipping")
                continue
            with open(members[matched], "rb") as f:
                payload = f.read()
            if len(payload) > _MAX_MEMBER_BYTES:
                log(
                    f"  Warning: {matched} exceeds the extraction size "
                    "cap, skipping"
                )
                continue
            total += len(payload)
            if total > _MAX_MEMBER_BYTES * 4:
                raise RuntimeError(
                    "archive exceeds the total extraction budget"
                )
            written.append(install_plain(client_dir, payload, dest_rel))
        return written


def _stripped_parts(filename: str) -> list[str]:
    """A zip member path with the archive's top-level "<repo>-<sha>/"
    component removed and separators normalised ("" / "." dropped)."""
    return [
        p
        for p in filename.replace("\\", "/").split("/")[1:]
        if p not in ("", ".")
    ]


def unpack_folder(data: bytes, dest_root: str) -> None:
    """Atomically unpack a repo-style zip (entries under one top-level
    directory) into ``dest_root``, replacing any existing copy.

    The archive's top-level "<repo>-<sha>/" component is stripped and
    separators normalised; traversal members are skipped and a defence-
    in-depth absolute-path check runs before every write. A failure never
    leaves a half-written ".tmp_install" behind.
    """
    unpack_prefix(data, "", dest_root)


def unpack_prefix(data: bytes, prefix: str, dest_root: str) -> None:
    """Like unpack_folder but installs only the members under one
    stripped-root-relative subdirectory (``prefix`` with "/" separators;
    "" installs everything, i.e. unpack_folder).

    Lets one repo archive install a single addon folder (a Git repo often
    ships several addons — e.g. AtlasLoot's seven modules) into its own
    Interface/AddOns/<folder> without dragging the whole tree along. Same
    traversal guards + atomic replace as unpack_folder.
    """
    wanted = [
        p for p in prefix.replace("\\", "/").split("/") if p not in ("", ".")
    ]
    if ".." in wanted:
        raise RuntimeError(f"Refusing unsafe unpack prefix: {prefix!r}")
    tmp_root = dest_root + ".tmp_install"
    tmp_abs = os.path.abspath(tmp_root)
    if os.path.isdir(tmp_root):
        rmtree_force(tmp_root)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            total_written = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.file_size > _MAX_MEMBER_BYTES:
                    log(
                        f"  Warning: {info.filename} exceeds the "
                        "extraction size cap, skipping"
                    )
                    continue
                total_written += info.file_size
                if total_written > _MAX_MEMBER_BYTES * 4:
                    raise RuntimeError(
                        "archive exceeds the total extraction budget"
                    )
                parts = _stripped_parts(info.filename)
                if not parts or ".." in parts:
                    continue
                if wanted and (
                    len(parts) <= len(wanted) or parts[: len(wanted)] != wanted
                ):
                    continue
                target = os.path.join(tmp_root, *parts[len(wanted) :])
                if not os.path.abspath(target).startswith(tmp_abs + os.sep):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        if os.path.isdir(dest_root):
            rmtree_force(dest_root)
        os.replace(tmp_root, dest_root)
    except BaseException:
        if os.path.isdir(tmp_root):
            try:
                rmtree_force(tmp_root)
            except Exception:
                pass
        raise

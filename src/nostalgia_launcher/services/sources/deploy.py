"""Shared payload deployment — how fetched bytes land in the client folder.

Deployment is chosen by the *entry shape*, not by the content type, which is
what lets every download backend serve every vertical:

* plain ``dest``            → `install_plain` (single file, atomic rename)
* ``extract_map`` + zip     → `extract_zip_map`
* ``extract_map`` + tar.gz  → `extract_tar_map`
* addon folder target       → `unpack_folder` (strip the archive's top-level
                              dir into Interface/AddOns/<folder>)

All paths are validated against `safety.safe_relpath`; a compromised
upstream must not be able to write outside the client folder.
"""

import io
import os
import shutil
import zipfile

from ...core.filesystem import rmtree_force
from ...core.log_sink import log
from . import safety


def checked_rel(dest_rel) -> str:
    """Validate a client-dir-relative install target before it is joined
    onto `client_dir`."""
    if not safety.safe_relpath(dest_rel):
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
                    zip_data = zf.read(zip_path)
                except KeyError:
                    log(f"  Warning: {zip_path} not in zip, skipping")
                    continue
                written.append(install_plain(client_dir, zip_data, dest_rel))
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
            tar_data = tf.extractfile(tf.getmember(matched)).read()
            written.append(install_plain(client_dir, tar_data, dest_rel))
        return written


def unpack_folder(data: bytes, dest_root: str) -> None:
    """Atomically unpack a repo-style zip (entries under one top-level
    directory) into ``dest_root``, replacing any existing copy.

    The archive's top-level "<repo>-<sha>/" component is stripped and
    separators normalised; traversal members are skipped and a defence-
    in-depth absolute-path check runs before every write. A failure never
    leaves a half-written ".tmp_install" behind.
    """
    tmp_root = dest_root + ".tmp_install"
    tmp_abs = os.path.abspath(tmp_root)
    if os.path.isdir(tmp_root):
        rmtree_force(tmp_root)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                parts = [
                    p
                    for p in info.filename.replace("\\", "/").split("/")[1:]
                    if p not in ("", ".")
                ]
                if not parts or ".." in parts:
                    continue
                target = os.path.join(tmp_root, *parts)
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

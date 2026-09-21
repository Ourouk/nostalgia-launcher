#!/usr/bin/env python3
"""Stage the pinned upstream 7-Zip macOS binary for the .app bundle.

Downloads ``7z2603-mac.tar.xz`` from github.com/ip7z/7zip, verifies
its sha256, extracts just the console ``7zz`` binary plus the
license text, and stages them under ``packaging/vendor/macos/``.

Idempotent: when the staged ``7zz`` already matches the pinned
binary hash (and the license text is present) the download is
skipped. Pass ``--force`` to re-fetch regardless.

The macOS PyInstaller spec (``NostalgiaLauncher-macos.spec``)
bundles the staged ``7zz`` next to the executable, where
``services/sources/deploy.py::_bundled_seven_z`` finds it.
Stdlib only (usable on a bare macOS CI runner before ``uv sync``).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tarfile
import tempfile
import urllib.request

SEVEN_Z_URL = (
    "https://github.com/ip7z/7zip/releases/download/26.03/7z2603-mac.tar.xz"
)
TARBALL_SHA256 = (
    "5ca87677072c59f5602e5c49baa27d4694bacd2259b4e507f0094249d4281480"
)
SEVEN_ZZ_SHA256 = (
    "74b0910e50ea44d9760a57fada2192cfd530ba8bffbe7b47c412a464b796cabf"
)
BIN_MEMBER = "7zz"
LICENSE_MEMBER = "License.txt"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
VENDOR_DIR = os.path.join(REPO_ROOT, "packaging", "vendor", "macos")
STAGED_BIN = os.path.join(VENDOR_DIR, "7zz")
STAGED_LICENSE = os.path.join(VENDOR_DIR, "7-Zip-License.txt")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(dest: str) -> None:
    print(f"downloading {SEVEN_Z_URL}")
    with urllib.request.urlopen(SEVEN_Z_URL, timeout=120) as resp:
        with open(dest, "wb") as handle:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                handle.write(chunk)


def _extract_member(tar_path: str, member: str, dest: str) -> None:
    # Member names are pinned constants above, never user input,
    # so no traversal filtering is needed here.
    with tarfile.open(tar_path, "r:xz") as tar:
        info = tar.getmember(member)
        if not info.isfile():
            raise RuntimeError(f"tar member not a file: {member}")
        reader = tar.extractfile(info)
        if reader is None:
            raise RuntimeError(f"cannot read tar member: {member}")
        data = reader.read()
    with open(dest, "wb") as handle:
        handle.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch and stage the macOS 7zz binary."
    )
    parser.add_argument(
        "--force", action="store_true", help="re-fetch even if staged"
    )
    args = parser.parse_args(argv)

    os.makedirs(VENDOR_DIR, exist_ok=True)
    if not args.force and os.path.isfile(STAGED_BIN):
        if _sha256(STAGED_BIN) != SEVEN_ZZ_SHA256:
            print("staged 7zz hash mismatch; re-fetching")
        elif not os.path.isfile(STAGED_LICENSE):
            print("staged license text missing; re-fetching")
        else:
            print(f"already staged: {STAGED_BIN} (hash matches)")
            return 0

    fd, tmp_path = tempfile.mkstemp(
        dir=VENDOR_DIR, prefix="7z2603-mac.", suffix=".tar.xz"
    )
    os.close(fd)
    try:
        _download(tmp_path)
        actual = _sha256(tmp_path)
        if actual != TARBALL_SHA256:
            raise RuntimeError(f"tarball sha256 mismatch: {actual}")
        print(f"verified sha256: {actual}")
        _extract_member(tmp_path, BIN_MEMBER, STAGED_BIN)
        if _sha256(STAGED_BIN) != SEVEN_ZZ_SHA256:
            raise RuntimeError("staged 7zz hash mismatch")
        os.chmod(STAGED_BIN, 0o755)
        _extract_member(tmp_path, LICENSE_MEMBER, STAGED_LICENSE)
        print(f"staged {STAGED_BIN} (universal arm64+x86_64)")
        print(f"staged {STAGED_LICENSE}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

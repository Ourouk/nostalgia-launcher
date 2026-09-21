"""Fetch the 7-Zip console binary for the Windows onefile build.

Downloads pinned upstream artifacts, verifies their SHA-256, and
stages them under ``packaging/vendor/windows/`` for
``NostalgiaLauncher.spec`` to bundle:

* ``7zr.exe`` -- standalone 7z console binary (single-file
  download, no extraction step needed)
* ``License.txt`` -- upstream license text (LGPL attribution)

Idempotent: files already staged with a matching hash are left
untouched. Stdlib only, so it runs on stock GitHub
``windows-latest`` runners with no extra tooling.

Usage (from the repo root)::

    python packaging/fetch-7z-windows.py
"""

import hashlib
import os
import sys
import urllib.request

VERSION = "26.03"
BASE_URL = "https://github.com/ip7z/7zip/releases/download/" + VERSION
LICENSE_URL = (
    "https://raw.githubusercontent.com/ip7z/7zip/"
    + VERSION
    + "/DOC/License.txt"
)
# (staged name, download URL, pinned SHA-256).
ARTIFACTS = (
    (
        "7zr.exe",
        BASE_URL + "/7zr.exe",
        "ad4c82fadcbdf93c03b4fc440f300509c7d60c5c2f4d183e35d9d70d6957037d",
    ),
    (
        "License.txt",
        LICENSE_URL,
        "dac8389b6bc39339537bc351772106afe7951cb242cdf03e855b67c3a683deb1",
    ),
)
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
VENDOR_DIR = os.path.join(REPO_ROOT, "packaging", "vendor", "windows")
README_NAME = "README.md"
UPSTREAM_URL = "https://github.com/ip7z/7zip"


def _sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch(name, url, sha256, dest):
    if os.path.isfile(dest) and _sha256_of(dest) == sha256:
        print("up to date: " + dest)
        return
    print("downloading " + url + " ...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except Exception as e:
        raise SystemExit(f"error: download failed: {url} ({e})") from e
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise SystemExit(
            f"error: SHA-256 mismatch for {url}:\n"
            f"  expected {sha256}\n"
            f"  got      {actual}"
        )
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    print(f"staged {dest} ({len(data)} bytes, sha256 ok)")


def _write_readme():
    lines = [
        "# Bundled 7-Zip binary (Windows)",
        "",
        "Staged by `packaging/fetch-7z-windows.py` (not committed;"
        " see `.gitignore`).",
        "",
        f"Upstream: 7-Zip {VERSION} ({UPSTREAM_URL}).",
        "Binary: standalone `7zr.exe` console build, bundled into"
        " the PyInstaller onefile root so `.7z` mods extract with"
        " no system dependency.",
        "",
        "License: 7-Zip is under the GNU LGPL (plus the unRAR"
        " license restriction for the Rar codec); see the bundled"
        " `License.txt` staged next to the binary.",
        "",
    ]
    dest = os.path.join(VENDOR_DIR, README_NAME)
    with open(dest, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote " + dest)


def main():
    os.makedirs(VENDOR_DIR, exist_ok=True)
    for name, url, sha256 in ARTIFACTS:
        _fetch(name, url, sha256, os.path.join(VENDOR_DIR, name))
    _write_readme()
    return 0


if __name__ == "__main__":
    sys.exit(main())

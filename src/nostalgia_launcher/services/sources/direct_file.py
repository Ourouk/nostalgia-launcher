"""Pinned-URL backends — ``direct_file`` and ``direct_tar``.

``direct_file`` serves both deployment modes:

* plain ``dest`` (no ``extract_map``): the payload streams to a temp file
  staged *beside its destination* (the caller passes ``client_dir``) with
  the SHA-1 computed while writing and the declared size enforced, then the
  deployer renames it into place. Response headers are captured for the
  opt-in drift probe (assets). This is what makes it suitable for
  multi-megabyte MPQ patches.
* ``extract_map``: the whole archive is fetched into memory and the mapped
  members extracted (zip or tar.gz, chosen by URL extension).

``direct_tar`` is the archive-only variant registered under its historical
wire kind (its catalog entries always carry an ``extract_map``).
"""

import hashlib
import os
import tempfile
import urllib.request

from ...core.constants import UA
from ...core.security_http import allowed_download_hosts, secure_urlopen
from .base import FetchResult, SourceBackend, StreamedFile, register
from .safety import https_url, safe_relpath, valid_extract_map


def _valid_sha1(value) -> str | None:
    """A lowercase 40-hex SHA-1 digest, or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if len(v) != 40 or any(c not in "0123456789abcdef" for c in v):
        return None
    return v


def _fetch_headers(r) -> dict:
    """Lower-cased response header dict (best-effort)."""
    try:
        return {k.lower(): v for k, v in r.headers.items()}
    except Exception:
        return {}


class DirectFileBackend(SourceBackend):
    KIND = "direct_file"
    REQUIRES_MAP = False

    def validate(self, source: dict) -> dict | None:
        url = https_url(source.get("url"))
        dest = source.get("dest")
        emap = valid_extract_map(source.get("extract_map"))
        has_dest = isinstance(dest, str) and safe_relpath(dest)
        if not url or (not has_dest and not emap):
            return None
        if self.REQUIRES_MAP and not emap:
            return None
        cleaned: dict = {"kind": self.KIND, "url": url}
        if has_dest:
            cleaned["dest"] = dest
        if emap:
            cleaned["extract_map"] = emap
        pinned = source.get("pinned_version")
        if pinned is not None:
            cleaned["pinned_version"] = str(pinned)
        # Optional integrity pins (used by the assets vertical; ignored by
        # mod catalogs that never set them). A malformed pin refuses the
        # whole entry rather than being silently dropped.
        sha1 = source.get("sha1")
        if sha1 is not None:
            normalized = _valid_sha1(sha1)
            if normalized is None:
                return None
            cleaned["sha1"] = normalized
        size = source.get("size")
        if size is not None:
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size <= 0
            ):
                return None
            cleaned["size"] = size
        return cleaned

    def resolve_version(
        self, entry: dict, *, force: bool = False
    ) -> str | None:
        return entry["source"].get("pinned_version")

    # ── fetching ──────────────────────────────────────────────────────────

    def fetch(
        self,
        entry: dict,
        *,
        client_dir: str | None = None,
        release: dict | None = None,
    ):
        src = entry["source"]
        if src.get("extract_map") is None and not self.REQUIRES_MAP:
            return self._fetch_streaming(src, client_dir)
        return self._fetch_archive(src)

    def _fetch_streaming(
        self, src: dict, client_dir: str | None
    ) -> FetchResult:
        """Stream the payload straight to a temp file beside its final
        destination, hashing and enforcing the declared size on the way."""
        dest_rel = src["dest"]
        expected_sha1 = src.get("sha1")
        declared_size = src.get("size")
        if client_dir:
            final = os.path.join(client_dir, dest_rel)
            stage = final + ".tmp"
            os.makedirs(os.path.dirname(final) or client_dir, exist_ok=True)
        else:
            fd, stage = tempfile.mkstemp(prefix="nlsrc-")
            os.close(fd)

        hasher = hashlib.sha1()
        got = 0
        header_map: dict = {}
        req = urllib.request.Request(src["url"], headers={"User-Agent": UA})
        try:
            with secure_urlopen(
                req,
                timeout=120,
                allowed_hosts=allowed_download_hosts(),
            ) as r:
                header_map = _fetch_headers(r)
                with open(stage, "wb") as f:
                    while True:
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        hasher.update(chunk)
                        got += len(chunk)
        except Exception:
            self._discard(stage)
            raise
        if declared_size and got != declared_size:
            self._discard(stage)
            raise RuntimeError(
                f"{dest_rel}: downloaded {got} bytes, expected {declared_size}"
            )
        if expected_sha1 and hasher.hexdigest() != expected_sha1:
            self._discard(stage)
            raise RuntimeError(f"{dest_rel}: SHA-1 mismatch after download")

        probe: dict = {}
        cl = header_map.get("content-length")
        if cl:
            try:
                probe["size"] = int(cl)
            except ValueError:
                pass
        if header_map.get("last-modified"):
            probe["last_modified"] = header_map["last-modified"]
        if header_map.get("etag"):
            probe["etag"] = header_map["etag"]

        return FetchResult(
            file=StreamedFile(
                path=stage,
                size=got,
                sha1_hex=hasher.hexdigest(),
                probe=probe,
            ),
            version=src.get("pinned_version"),
            name=src["url"].rsplit("/", 1)[-1],
        )

    def _fetch_archive(self, src: dict) -> FetchResult:
        from .github_release import fetch_bytes

        data = fetch_bytes(src["url"])
        return FetchResult(
            data=data,
            version=src.get("pinned_version"),
            name=src["url"].rsplit("/", 1)[-1],
        )

    @staticmethod
    def _discard(stage: str):
        try:
            if os.path.exists(stage):
                os.remove(stage)
        except OSError:
            pass


class DirectTarBackend(DirectFileBackend):
    KIND = "direct_tar"
    REQUIRES_MAP = True


register(DirectFileBackend())
register(DirectTarBackend())

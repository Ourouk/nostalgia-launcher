"""Human-readable descriptions for install/update failures."""

import zipfile
import urllib.error


def describe_net_error(e: Exception) -> str:
    """Human-readable cause for a failed API request."""
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 403:
            return "API rate limit exceeded — try again in 1 hour"
        if e.code == 404:
            return "repository or branch not found"
        host = (e.url or "").split("/")[2] if (e.url or "").count("/") >= 2 \
            else "server"
        return f"HTTP {e.code} from {host}"
    if isinstance(e, urllib.error.URLError):
        return f"network error ({e.reason})"
    return str(e)


def describe_install_error(e: Exception) -> str:
    """Map an install/update failure to a message the user can act on."""
    if isinstance(e, (urllib.error.HTTPError, urllib.error.URLError)):
        return describe_net_error(e)
    if isinstance(e, OSError) and getattr(e, "errno", None) in (2, 13, 22):
        # The archive/file vanished or got locked mid-operation — on Windows
        # that's almost always the antivirus quarantining the download.
        return ("Blocked by antivirus — open Settings (⚙) → "
                "'Add game folder to Defender exclusions', then retry")
    if isinstance(e, zipfile.BadZipFile):
        return ("Downloaded archive is corrupted (possibly blocked by "
                "antivirus) — retry, or use Settings (⚙) → "
                "'Add game folder to Defender exclusions'")
    return str(e)

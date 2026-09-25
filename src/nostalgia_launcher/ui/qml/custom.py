"""Custom-entry dialogs for QML (Phase 6b).

Implements the custom-entry dialogs: form models assemble a
catalog entry, validate it (`catalog.validate_mod/asset`,
`addons.is_allowed_git_url`), and emit `entryReady` on success — the tab
glue in `ui.qml.app` persists/applies exactly like the widget shell.
"""

from urllib.parse import urlsplit

from PySide6.QtCore import Property, QObject, Signal, Slot

from ...core.launcher import ADDON_GIT_HOSTS
from ...services import addons, catalog

_MOD_KINDS = (
    "github_release",
    "codeberg_release",
    "direct_file",
    "direct_tar",
)


class _FormModel(QObject):
    """Shared error plumbing for the custom-entry forms."""

    entryReady = Signal(object)
    errorChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._error = ""

    def _get_error(self) -> str:
        return self._error

    errorText = Property(str, _get_error, notify=errorChanged)

    def _fail(self, message: str):
        self._error = message
        self.errorChanged.emit()
        return False


class CustomModModel(_FormModel):
    """ADD CUSTOM MOD form (kind/type-conditional fields)."""

    def _get_mod_types(self) -> list:
        return list(catalog.MOD_TYPES)

    modTypes = Property("QVariantList", _get_mod_types, constant=True)

    def _get_installations(self) -> list:
        return list(catalog.MOD_INSTALLATIONS)

    installations = Property("QVariantList", _get_installations, constant=True)

    def _get_source_kinds(self) -> list:
        return list(_MOD_KINDS)

    sourceKinds = Property("QVariantList", _get_source_kinds, constant=True)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields = {
            "modId": "",
            "name": "",
            "repoUrl": "",
            "description": "",
            "modType": catalog.MOD_TYPES[0],
            "installation": catalog.MOD_INSTALLATIONS[0],
            "executable": "",
            "clientVersions": "",
            "kind": _MOD_KINDS[0],
            "owner": "",
            "repo": "",
            "pattern": "",
            "preferNo": "",
            "versionFromAsset": False,
            "fileUrl": "",
            "dest": "",
            "pinnedVersion": "",
            "extractMap": "",
        }

    @Slot(str, str)
    def setField(self, name: str, value: str):
        if name in self._fields and self._fields[name] != value:
            self._fields[name] = value

    @Slot(str, bool)
    def setFlag(self, name: str, value: bool):
        if name in self._fields and self._fields[name] != bool(value):
            self._fields[name] = bool(value)

    def _parse_extract_map(self):
        emap = {}
        for line in self._fields["extractMap"].splitlines():
            line = line.strip()
            if not line:
                continue
            pattern, sep, dest = line.partition("=")
            if not sep or not pattern.strip() or not dest.strip():
                return None
            emap[pattern.strip()] = dest.strip()
        return emap

    @Slot(result=bool)
    def submit(self):
        f = self._fields
        mid = f["modId"].strip()
        if not mid:
            return self._fail("A mod id is required.")
        kind = f["kind"]
        mod_type = f["modType"]
        entry = {
            "id": mid,
            "name": f["name"].strip() or mid,
            "type": mod_type,
            "installation": f["installation"],
            "description": f["description"].strip(),
            "source": {"kind": kind},
        }
        if mod_type == "external-launcher" and f["executable"].strip():
            entry["executable"] = f["executable"].strip()
        versions = [
            v.strip() for v in f["clientVersions"].split(",") if v.strip()
        ]
        if versions:
            entry["client_versions"] = versions
        if f["repoUrl"].strip():
            entry["repo_url"] = f["repoUrl"].strip()
        emap = self._parse_extract_map()
        if emap is None:
            return self._fail(
                "Extract map lines must look like 'pattern = dest/file'."
            )
        if kind in ("github_release", "codeberg_release"):
            entry["source"].update(
                {
                    "owner": f["owner"].strip(),
                    "repo": f["repo"].strip(),
                    "asset_pattern": f["pattern"].strip(),
                }
            )
            if f["preferNo"].strip():
                entry["source"]["prefer_no"] = f["preferNo"].strip()
            if f["versionFromAsset"]:
                entry["source"]["version_from"] = "asset"
            if emap:
                entry["source"]["extract_map"] = emap
        else:
            entry["source"].update({"url": f["fileUrl"].strip()})
            if f["dest"].strip():
                entry["source"]["dest"] = f["dest"].strip()
            if f["pinnedVersion"].strip():
                entry["source"]["pinned_version"] = f["pinnedVersion"].strip()
            if emap:
                entry["source"]["extract_map"] = emap
        if catalog.validate_mod(entry) is None:
            return self._fail(
                "This entry is not usable — check the highlighted fields "
                "(https URLs, safe relative paths, allowed source kinds)."
            )
        self.entryReady.emit(entry)
        return True


class CustomAddonModel(_FormModel):
    """ADD CUSTOM GIT ADDON form (single URL field)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._url = ""
        self._hosts_hint = "Allowed hosts: " + ", ".join(ADDON_GIT_HOSTS)

    def _get_hosts_hint(self) -> str:
        return self._hosts_hint

    hostsHint = Property(str, _get_hosts_hint, constant=True)

    @Slot(str)
    def setUrl(self, url: str):
        self._url = url
        self._error = ""
        self.errorChanged.emit()

    @Slot(result=bool)
    def submit(self):
        url = self._url.strip().rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        if not addons.is_allowed_git_url(url):
            return self._fail("URL must be https from an allowed host.")
        folder = url.rsplit("/", 1)[-1]
        if (
            not folder
            or folder in (".", "..")
            or "\\" in folder
            or not urlsplit(url).path
        ):
            return self._fail("Could not derive addon folder name.")
        self.entryReady.emit(
            {
                "folder": folder,
                "status": "available",
                "git": url,
                "branch": None,
                "ref": None,
                "toc": {},
                "description": None,
                "error": None,
            }
        )
        return True


class CustomAssetModel(_FormModel):
    """ADD CUSTOM ASSET form (single-file server content)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fields = {
            "assetId": "",
            "name": "",
            "url": "",
            "dest": "",
            "sha1": "",
            "size": "",
            "version": "",
            "repoUrl": "",
        }
        self._essential = False
        self._probe = False

    @Slot(str, str)
    def setField(self, name: str, value: str):
        if name in self._fields and self._fields[name] != value:
            self._fields[name] = value

    @Slot(str, bool)
    def setFlag(self, name: str, value: bool):
        if name == "essential":
            self._essential = bool(value)
        elif name == "probe":
            self._probe = bool(value)

    @Slot(result=bool)
    def submit(self):
        f = self._fields
        aid = f["assetId"].strip()
        if not aid:
            return self._fail("An asset id is required.")
        entry = {
            "id": aid,
            "name": f["name"].strip() or aid,
            "url": f["url"].strip(),
            "dest": f["dest"].strip(),
            "description": "",
            "essential": self._essential,
            "probe": self._probe,
        }
        for key in ("sha1", "version", "repoUrl"):
            value = f[key].strip()
            if value:
                entry[key if key != "repoUrl" else "repo_url"] = value
        if f["size"].strip():
            try:
                entry["size"] = int(f["size"].strip())
            except ValueError:
                return self._fail("Size must be a whole number of bytes.")
        if catalog.validate_asset(entry) is None:
            return self._fail(
                "This entry is not usable — the URL must be https and the "
                "destination a safe path relative to the game folder."
            )
        self.entryReady.emit(entry)
        return True

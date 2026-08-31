"""Pydantic v2 models for catalog entries — thin wrappers around
domain-specific safety checks.

The safety primitives (safe_relpath, safe_folder, valid_sha1, etc.) remain
canonical in ``core.safety``; these models compose them via
``AfterValidator`` so validation is declarative and errors are aggregated.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    field_validator,
)

from ..core.safety import safe_folder, safe_relpath, valid_sha1


def _check_folder(v: str) -> str:
    if not safe_folder(v):
        raise ValueError("invalid folder")
    return v


def _check_rel(v: str) -> str:
    if not safe_relpath(v):
        raise ValueError("invalid relative path")
    return v


def _check_sha1(v: str | None) -> str | None:
    if v is None:
        return None
    normalized = valid_sha1(v)
    if normalized is None and v is not None:
        raise ValueError("invalid sha1")
    return normalized


SafeFolderStr = Annotated[str, AfterValidator(_check_folder)]
SafeRelStr = Annotated[str, AfterValidator(_check_rel)]


class AddonModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: SafeFolderStr
    git: str | None = None
    branch: str | None = None
    ref: str | None = None
    description: str | None = None
    toc: dict[str, Any] | None = None
    recommended: bool = False
    blocked: bool = False

    @field_validator("git", mode="before")
    @classmethod
    def _coerce_git(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            return v.strip()
        return None

    @field_validator("branch", "ref", mode="before")
    @classmethod
    def _coerce_ref(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        if not isinstance(v, str):
            return None
        v = v.strip()
        if not v or any(ch.isspace() for ch in v) or ".." in v:
            return None
        return v

    @field_validator("toc", mode="before")
    @classmethod
    def _filter_toc(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return {}
        return {k: v[k] for k in ("Title", "Notes", "Interface") if k in v}


class AssetModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: SafeFolderStr
    name: str
    essential: bool = False
    description: str = ""
    repo_url: str | None = None
    url: str
    dest: SafeRelStr
    version: str | None = None
    sha1: str | None = None
    size: int | None = None
    probe: bool = False

    @field_validator("url", mode="before")
    @classmethod
    def _validate_url(cls, v: Any) -> Any:
        from ..core.safety import validate_download_url

        if not isinstance(v, str):
            raise ValueError("invalid url")
        if validate_download_url(v.strip()) is None:
            raise ValueError("invalid url")
        return v.strip()

    @field_validator("repo_url", mode="before")
    @classmethod
    def _validate_repo(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        from ..core.safety import validate_download_url

        if not isinstance(v, str):
            return None
        if validate_download_url(v.strip()) is None:
            raise ValueError("invalid repo_url")
        return v.strip()

    @field_validator("sha1", mode="before")
    @classmethod
    def _validate_sha(cls, v: Any) -> Any:
        if v is None:
            return None
        n = valid_sha1(v)
        if n is None:
            raise ValueError("invalid sha1")
        return n

    @field_validator("size", mode="before")
    @classmethod
    def _validate_size(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError("invalid size")
        return v

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v if v else None
        return None

    @field_validator("description", mode="before")
    @classmethod
    def _coerce_desc(cls, v: Any) -> Any:
        return v if isinstance(v, str) else ""

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            return v.strip()
        raise ValueError("invalid name")

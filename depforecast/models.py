"""Pydantic types shared by every module (dependencies, features, predictions, reports)."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DepKind(StrEnum):
    """Which section of package.json a dependency came from."""

    PROD = "dependencies"
    DEV = "devDependencies"
    OPTIONAL = "optionalDependencies"
    PEER = "peerDependencies"


class Status(StrEnum):
    """Per-package result status. One package failing never fails the whole scan."""

    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


class Dependency(BaseModel):
    """One npm dependency of the scanned project."""

    model_config = ConfigDict(frozen=True)

    name: str
    spec: str
    """Version range as written in package.json, or the resolved version from the lockfile."""
    kind: DepKind = DepKind.PROD
    direct: bool = True


class Release(BaseModel):
    """One published npm version."""

    model_config = ConfigDict(frozen=True)

    version: str
    published: datetime
    has_funding: bool = False


class NpmPackage(BaseModel):
    """What the npm registry says about a package. Releases cover all time; callers
    must filter by date (see `releases_before`) to avoid leaking the future."""

    name: str
    releases: list[Release]
    """Sorted oldest first. Includes versions that were later unpublished."""
    latest_version: str | None = None
    deprecated: str | None = None
    """Deprecation message of the latest version. Has no timestamp: never use as a label."""
    repo: str | None = None
    """GitHub `owner/repo` (lowercase), or None if the package is not on GitHub."""
    repo_url: str | None = None
    """Raw repository URL as published, kept for logging non-GitHub packages."""
    repo_directory: str | None = None
    """Folder inside a monorepo, e.g. `packages/babel-core`."""
    funding_url: str | None = None

    def releases_before(self, as_of: datetime) -> list[Release]:
        return [r for r in self.releases if r.published < as_of]

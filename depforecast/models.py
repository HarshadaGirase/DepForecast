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


class RepoActivity(BaseModel):
    """GitHub facts about a repo strictly before `as_of`. Used for features."""

    repo: str
    as_of: datetime
    found: bool = True
    created_at: datetime | None = None
    archived_by_as_of: bool = False
    """Archived on or before as_of. The archive date itself is never exposed here."""
    commits_3m: int = 0
    commits_6m: int = 0
    commits_12m: int = 0
    commits_prev_6m: int = 0
    """Commits in the 6 months before the last 6 months (12 to 6 months before as_of)."""
    last_commit_at: datetime | None = None
    issues_opened_6m: int = 0
    issues_closed_6m: int = 0
    stale_prs: int = 0
    """PRs open at as_of that were created more than 90 days before it."""
    author_commits: dict[str, int] = {}
    """Commits per author among the latest (up to 100) commits in the 12 months before as_of.
    Keys are GitHub logins or hashed emails, never raw emails."""


class RepoOutcome(BaseModel):
    """What happened to a repo during an outcome window [start, end). Used for labels only."""

    repo: str
    start: datetime
    end: datetime
    found: bool = True
    archived_in_window: bool = False
    commits_in_window: int = 0

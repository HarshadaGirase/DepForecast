"""Pydantic types shared by every module (dependencies, features, predictions, reports)."""

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

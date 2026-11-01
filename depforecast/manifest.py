"""Manifest reader: direct deps from package.json, transitive deps from package-lock.json."""

import json
from pathlib import Path
from typing import Any

from depforecast.models import Dependency, DepKind

# Earlier sections win when a package appears in more than one.
_SECTIONS = (DepKind.PROD, DepKind.OPTIONAL, DepKind.PEER, DepKind.DEV)

# Specs that point somewhere other than the npm registry.
_LOCAL_PREFIXES = ("file:", "link:", "workspace:", "portal:")


class ManifestError(Exception):
    """package.json or package-lock.json is missing or unreadable."""


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ManifestError(f"{path} not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{path} is not a JSON object")
    return data


def _resolve(name: str, spec: str) -> tuple[str, str] | None:
    """Return the registry package name and spec, or None for non-registry deps."""
    if spec.startswith(_LOCAL_PREFIXES):
        return None
    if spec.startswith("npm:"):
        # Alias: "foo": "npm:real-pkg@^1.0.0" (the real name may itself be scoped)
        target = spec[4:]
        at = target.rfind("@")
        if at > 0:
            return target[:at], target[at + 1 :]
        return target, "*"
    return name, spec


def read_package_json(path: Path) -> list[Dependency]:
    """Direct dependencies from package.json, de-duplicated by name."""
    data = _load_json(path)
    deps: dict[str, Dependency] = {}
    for kind in _SECTIONS:
        section = data.get(kind.value) or {}
        if not isinstance(section, dict):
            continue
        for raw_name, raw_spec in section.items():
            resolved = _resolve(raw_name, str(raw_spec))
            if resolved is None:
                continue
            name, spec = resolved
            deps.setdefault(name, Dependency(name=name, spec=spec, kind=kind, direct=True))
    return sorted(deps.values(), key=lambda d: d.name)


def _lock_v1(tree: dict[str, Any], out: dict[str, str]) -> None:
    for name, info in tree.items():
        if not isinstance(info, dict) or info.get("bundled"):
            continue
        version = str(info.get("version", ""))
        if version and not version.startswith(_LOCAL_PREFIXES):
            out.setdefault(name, version)
        _lock_v1(info.get("dependencies") or {}, out)


def read_lockfile(path: Path) -> dict[str, str]:
    """All installed packages from package-lock.json (v1, v2 or v3): name -> version."""
    data = _load_json(path)
    out: dict[str, str] = {}
    packages = data.get("packages")
    if isinstance(packages, dict):  # lockfileVersion 2 and 3
        for key, info in packages.items():
            if not key or "node_modules/" not in key or not isinstance(info, dict):
                continue
            if info.get("link") or info.get("bundled"):
                continue
            name = info.get("name") or key.rsplit("node_modules/", 1)[1]
            out.setdefault(str(name), str(info.get("version", "*")))
    else:  # lockfileVersion 1
        _lock_v1(data.get("dependencies") or {}, out)
    return out


def load_dependencies(project_dir: Path, transitive: bool = False) -> list[Dependency]:
    """Dependencies of a project: direct by default, plus lockfile packages with transitive."""
    direct = read_package_json(project_dir / "package.json")
    if not transitive:
        return direct
    known = {d.name for d in direct}
    extra = [
        Dependency(name=name, spec=version, direct=False)
        for name, version in read_lockfile(project_dir / "package-lock.json").items()
        if name not in known
    ]
    return direct + sorted(extra, key=lambda d: d.name)

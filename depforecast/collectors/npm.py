"""npm registry collector: versions, publish dates, deprecated flag, repo URL, funding."""

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from depforecast.collectors.http import HttpClient
from depforecast.models import NpmPackage, Release

REGISTRY_URL = "https://registry.npmjs.org"

# `time` holds one entry per version plus these two bookkeeping keys.
_NOT_RELEASES = {"created", "modified", "unpublished"}

_GITHUB_URL = re.compile(
    r"""^(?:git\+)?(?:https?|git|ssh)://(?:[^@/]+@)?(?:www\.)?github\.com[:/]
        (?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?
        (?:/tree/[^/]+/(?P<dir>.+?))?/?(?:[#?].*)?$""",
    re.VERBOSE | re.IGNORECASE,
)
_GITHUB_SCP = re.compile(
    r"^(?:git\+)?git@github\.com:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
# npm shorthands: "github:owner/repo" or a bare "owner/repo"
_GITHUB_SHORT = re.compile(r"^(?:github:)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:#.*)?$")


def parse_github_repo(url: str) -> tuple[str, str | None] | None:
    """Normalize any GitHub repository URL to (`owner/repo`, directory or None)."""
    url = url.strip()
    for pattern in (_GITHUB_URL, _GITHUB_SCP, _GITHUB_SHORT):
        m = pattern.match(url)
        if m:
            directory = m.groupdict().get("dir")
            return f"{m['owner']}/{m['repo']}".lower(), directory
    return None


def _repository(meta: Any) -> tuple[str | None, str | None]:
    """(url, directory) from a package.json `repository` field (string or object)."""
    if isinstance(meta, str):
        return meta, None
    if isinstance(meta, dict) and isinstance(meta.get("url"), str):
        directory = meta.get("directory")
        return meta["url"], directory if isinstance(directory, str) else None
    return None, None


def _funding_url(funding: Any) -> str | None:
    """First URL from a `funding` field: a string, an object, or a list of either."""
    if isinstance(funding, list):
        funding = funding[0] if funding else None
    if isinstance(funding, str):
        return funding
    if isinstance(funding, dict) and isinstance(funding.get("url"), str):
        return str(funding["url"])
    return None


def package_url(name: str) -> str:
    # Scoped names keep the "@" but encode the slash: @babel/core -> @babel%2fcore
    return f"{REGISTRY_URL}/{quote(name, safe='@')}"


def parse_packument(doc: dict[str, Any]) -> NpmPackage:
    versions: dict[str, Any] = doc.get("versions") or {}
    releases = []
    for version, stamp in (doc.get("time") or {}).items():
        if version in _NOT_RELEASES or not isinstance(stamp, str):
            continue
        info = versions.get(version) or {}
        releases.append(
            Release(
                version=version,
                published=datetime.fromisoformat(stamp),
                has_funding=_funding_url(info.get("funding")) is not None,
            )
        )
    releases.sort(key=lambda r: r.published)

    latest = (doc.get("dist-tags") or {}).get("latest")
    latest_info: dict[str, Any] = (versions.get(latest) if latest else None) or {}
    repo_url, directory = _repository(latest_info.get("repository") or doc.get("repository"))
    parsed = parse_github_repo(repo_url) if repo_url else None
    if parsed and parsed[1] and not directory:
        directory = parsed[1]
    deprecated = latest_info.get("deprecated")

    return NpmPackage(
        name=str(doc.get("name", "")),
        releases=releases,
        latest_version=latest,
        deprecated=deprecated if isinstance(deprecated, str) and deprecated else None,
        repo=parsed[0] if parsed else None,
        repo_url=repo_url,
        repo_directory=directory,
        funding_url=_funding_url(latest_info.get("funding")),
    )


_VERSION_FIELDS = ("repository", "funding", "deprecated")


def trim_packument(doc: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields parse_packument reads. Full packuments can be many MB."""
    versions = doc.get("versions") or {}
    return {
        "name": doc.get("name"),
        "dist-tags": doc.get("dist-tags"),
        "time": doc.get("time"),
        "repository": doc.get("repository"),
        "versions": {
            version: {k: info[k] for k in _VERSION_FIELDS if k in info}
            for version, info in versions.items()
            if isinstance(info, dict)
        },
    }


def _trim_text(text: str) -> str:
    return json.dumps(trim_packument(json.loads(text)), separators=(",", ":"))


def fetch_package(http: HttpClient, name: str) -> NpmPackage | None:
    """Registry metadata for one package, or None if it does not exist on npm."""
    resp = http.get(package_url(name), transform=_trim_text)
    if resp.status == 404:
        return None
    if resp.status != 200:
        raise RuntimeError(f"npm registry returned HTTP {resp.status} for {name}")
    return parse_packument(resp.json())

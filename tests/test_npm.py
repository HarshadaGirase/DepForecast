import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import respx

from depforecast.collectors.cache import HttpCache
from depforecast.collectors.http import HttpClient
from depforecast.collectors.npm import (
    fetch_package,
    package_url,
    parse_github_repo,
    parse_packument,
)

FIXTURES = Path(__file__).parent / "fixtures" / "npm"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git+https://github.com/expressjs/express.git", ("expressjs/express", None)),
        ("https://github.com/babel/babel.git", ("babel/babel", None)),
        ("git://github.com/request/request.git", ("request/request", None)),
        ("git+ssh://git@github.com/Owner/Repo.git", ("owner/repo", None)),
        ("ssh://git@github.com/owner/repo", ("owner/repo", None)),
        ("git@github.com:owner/repo.git", ("owner/repo", None)),
        ("github:owner/repo", ("owner/repo", None)),
        ("owner/repo", ("owner/repo", None)),
        ("https://www.github.com/owner/repo#readme", ("owner/repo", None)),
        ("https://github.com/owner/repo/", ("owner/repo", None)),
        ("https://github.com/owner/repo.js", ("owner/repo.js", None)),
        (
            "https://github.com/owner/mono/tree/main/packages/x",
            ("owner/mono", "packages/x"),
        ),
    ],
)
def test_parse_github_repo(url: str, expected: tuple[str, str | None]) -> None:
    assert parse_github_repo(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/owner/repo.git",
        "gitlab:owner/repo",
        "bitbucket:owner/repo",
        "https://example.com/owner/repo",
        "",
    ],
)
def test_non_github_repos(url: str) -> None:
    assert parse_github_repo(url) is None


def test_express_packument() -> None:
    pkg = parse_packument(_load("express"))
    assert pkg.name == "express"
    assert pkg.repo == "expressjs/express"
    assert pkg.funding_url == "https://opencollective.com/express"
    assert pkg.deprecated is None
    assert pkg.releases[0].version in {"0.14.0", "0.14.1"}  # oldest first (same timestamp)
    dates = [r.published for r in pkg.releases]
    assert dates == sorted(dates)


def test_bookkeeping_time_keys_are_not_releases() -> None:
    versions = {r.version for r in parse_packument(_load("express")).releases}
    assert "created" not in versions
    assert "modified" not in versions


def test_deprecated_package() -> None:
    pkg = parse_packument(_load("request"))
    assert pkg.deprecated is not None and "deprecated" in pkg.deprecated
    assert pkg.repo == "request/request"
    assert pkg.funding_url is None


def test_scoped_monorepo_package() -> None:
    pkg = parse_packument(_load("babel-core"))
    assert pkg.name == "@babel/core"
    assert pkg.repo == "babel/babel"
    assert pkg.repo_directory == "packages/babel-core"


def test_releases_before_excludes_the_future() -> None:
    pkg = parse_packument(_load("express"))
    t0 = datetime(2024, 10, 1, tzinfo=UTC)
    before = pkg.releases_before(t0)
    assert before and all(r.published < t0 for r in before)
    assert len(before) < len(pkg.releases)


def test_funding_is_tracked_per_version() -> None:
    releases = parse_packument(_load("express")).releases
    assert not releases[0].has_funding  # 2010: no funding field yet
    assert releases[-1].has_funding


def test_funding_field_variants() -> None:
    doc = {
        "name": "x",
        "dist-tags": {"latest": "2.0.0"},
        "time": {"1.0.0": "2020-01-01T00:00:00Z", "2.0.0": "2021-01-01T00:00:00Z"},
        "versions": {
            "1.0.0": {"funding": "https://github.com/sponsors/x"},
            "2.0.0": {"funding": [{"type": "github", "url": "https://github.com/sponsors/y"}]},
        },
        "repository": "github:x/x",
    }
    pkg = parse_packument(doc)
    assert [r.has_funding for r in pkg.releases] == [True, True]
    assert pkg.funding_url == "https://github.com/sponsors/y"
    assert pkg.repo == "x/x"


def test_unpublished_package_has_no_releases() -> None:
    doc = {"name": "gone", "time": {"modified": "2022-01-01T00:00:00Z", "unpublished": {}}}
    pkg = parse_packument(doc)
    assert pkg.releases == [] and pkg.repo is None


def test_scoped_url_encoding() -> None:
    assert package_url("@babel/core") == "https://registry.npmjs.org/@babel%2Fcore"
    assert package_url("express") == "https://registry.npmjs.org/express"


@respx.mock
def test_fetch_package_and_404(tmp_path: Path) -> None:
    respx.get(package_url("express")).respond(json=_load("express"))
    respx.get(package_url("no-such-pkg-xyz")).respond(status_code=404)
    http = HttpClient(HttpCache(tmp_path))
    pkg = fetch_package(http, "express")
    assert pkg is not None and pkg.repo == "expressjs/express"
    assert fetch_package(http, "no-such-pkg-xyz") is None

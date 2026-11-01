import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from depforecast.collectors.cache import HttpCache
from depforecast.collectors.github import (
    GRAPHQL_URL,
    GitHubClient,
    GitHubError,
    parse_activity,
    parse_outcome,
    shift_months,
)
from depforecast.collectors.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "github"
T0 = datetime(2024, 10, 1, tzinfo=UTC)
T1 = datetime(2025, 10, 1, tzinfo=UTC)
REPOS = ["expressjs/express", "request/request", "nobody-xyz/does-not-exist-xyz"]


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return data


def _client(tmp_path: Path, sleeps: list[float] | None = None) -> GitHubClient:
    sink = sleeps if sleeps is not None else []
    return GitHubClient(HttpClient(HttpCache(tmp_path)), token="t", sleep=sink.append)


@pytest.mark.parametrize(
    ("dt", "months", "expected"),
    [
        (datetime(2024, 10, 1), -3, datetime(2024, 7, 1)),
        (datetime(2024, 10, 1), -12, datetime(2023, 10, 1)),
        (datetime(2024, 1, 15), -1, datetime(2023, 12, 15)),
        (datetime(2024, 3, 31), -1, datetime(2024, 2, 29)),  # day clamped (leap year)
        (datetime(2024, 10, 1), 12, datetime(2025, 10, 1)),
    ],
)
def test_shift_months(dt: datetime, months: int, expected: datetime) -> None:
    assert shift_months(dt, months) == expected


def test_parse_real_activity_express() -> None:
    a = parse_activity(_load("activity")["data"], "r0", "expressjs/express", T0)
    assert a.found and not a.archived_by_as_of
    assert a.commits_3m <= a.commits_6m <= a.commits_12m
    assert a.commits_12m >= a.commits_6m + a.commits_prev_6m - 1  # windows add up
    assert a.last_commit_at is not None and a.last_commit_at < T0
    assert a.created_at is not None and a.created_at.year == 2009
    assert sum(a.author_commits.values()) <= 100
    assert not any("@" in k for k in a.author_commits)  # no raw emails


def test_parse_real_activity_dormant_repo() -> None:
    a = parse_activity(_load("activity")["data"], "r1", "request/request", T0)
    assert a.found
    assert a.commits_12m == 0 and a.author_commits == {}
    assert a.last_commit_at is not None and a.last_commit_at.year == 2020


def test_parse_missing_repo() -> None:
    assert not parse_activity(_load("activity")["data"], "r2", REPOS[2], T0).found
    assert not parse_outcome(_load("outcome")["data"], "r2", REPOS[2], T0, T1).found


def test_parse_real_outcome() -> None:
    data = _load("outcome")["data"]
    alive = parse_outcome(data, "r0", REPOS[0], T0, T1)
    dead = parse_outcome(data, "r1", REPOS[1], T0, T1)
    assert alive.commits_in_window > 0 and not alive.archived_in_window
    assert dead.commits_in_window == 0 and not dead.archived_in_window


@pytest.mark.parametrize(
    ("archived_at", "in_window", "by_t0"),
    [
        (None, False, False),
        ("2024-09-30T00:00:00Z", False, True),  # archived before the window
        ("2024-10-01T00:00:00Z", True, True),  # start is inclusive
        ("2025-03-01T00:00:00Z", True, False),
        ("2025-10-01T00:00:00Z", False, False),  # end is exclusive
    ],
)
def test_archive_date_boundaries(archived_at: str | None, in_window: bool, by_t0: bool) -> None:
    data = {"r0": {"archivedAt": archived_at, "createdAt": "2015-01-01T00:00:00Z"}}
    assert parse_outcome(data, "r0", "a/b", T0, T1).archived_in_window is in_window
    assert parse_activity(data, "r0", "a/b", T0).archived_by_as_of is by_t0


def test_empty_repo_without_default_branch() -> None:
    node = {"archivedAt": None, "createdAt": "2020-01-01T00:00:00Z", "defaultBranchRef": None}
    data = {"r0": node}
    a = parse_activity(data, "r0", "a/b", T0)
    assert a.found and a.commits_12m == 0 and a.last_commit_at is None


def test_stale_prs_never_negative() -> None:
    data = {
        "r0": {"archivedAt": None},
        "r0_oldprs": {"issueCount": 3},
        "r0_oldprs_closed": {"issueCount": 5},
    }
    assert parse_activity(data, "r0", "a/b", T0).stale_prs == 0


def test_authors_use_login_or_hashed_email() -> None:
    nodes = [
        {"author": {"email": "a@x.com", "user": {"login": "Alice"}}},
        {"author": {"email": "a@x.com", "user": {"login": "alice"}}},
        {"author": {"email": "bob@x.com", "user": None}},
        {"author": None},
    ]
    data = {"r0": {"defaultBranchRef": {"target": {"authors": {"nodes": nodes}}}}}
    authors = parse_activity(data, "r0", "a/b", T0).author_commits
    assert authors["alice"] == 2
    assert len(authors) == 2
    assert not any("bob" in k for k in authors)


@respx.mock
def test_activity_end_to_end_with_not_found(tmp_path: Path) -> None:
    respx.post(GRAPHQL_URL).respond(json=_load("activity"))
    result = _client(tmp_path).activity(REPOS, T0)
    assert result["expressjs/express"].commits_12m > 0
    assert not result[REPOS[2]].found


@respx.mock
def test_outcome_end_to_end(tmp_path: Path) -> None:
    respx.post(GRAPHQL_URL).respond(json=_load("outcome"))
    result = _client(tmp_path).outcome(REPOS, T0, T1)
    assert result["expressjs/express"].commits_in_window > 0


@respx.mock
def test_batches_repos(tmp_path: Path) -> None:
    route = respx.post(GRAPHQL_URL).respond(json={"data": {}})
    repos = [f"o/r{i}" for i in range(25)]
    result = _client(tmp_path).outcome(repos, T0, T1)
    assert route.call_count == 3  # batches of 10
    assert len(result) == 25 and not any(o.found for o in result.values())


@respx.mock
def test_rate_limited_error_waits_and_retries(tmp_path: Path) -> None:
    respx.post(GRAPHQL_URL).mock(
        side_effect=[
            httpx.Response(200, json={"errors": [{"type": "RATE_LIMITED", "message": "x"}]}),
            httpx.Response(200, json={"data": {"ok": 1}}),
        ]
    )
    sleeps: list[float] = []
    assert _client(tmp_path, sleeps).graphql("query { ok }") == {"ok": 1}
    assert sleeps == [60]


@respx.mock
def test_other_errors_raise_and_are_not_cached(tmp_path: Path) -> None:
    route = respx.post(GRAPHQL_URL).respond(
        json={"errors": [{"type": "INTERNAL", "message": "boom"}]}
    )
    client = _client(tmp_path)
    for _ in range(2):
        with pytest.raises(GitHubError, match="boom"):
            client.graphql("query { x }")
    assert route.call_count == 2


def test_token_required(tmp_path: Path) -> None:
    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        GitHubClient(HttpClient(HttpCache(tmp_path)), token=None)

"""SPEC §5.5: no collector call made for a window may use data after its T0."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx

from depforecast.collectors.cache import HttpCache
from depforecast.collectors.github import (
    GRAPHQL_URL,
    GitHubClient,
    _wrap,
    activity_query_part,
    outcome_query_part,
)
from depforecast.collectors.http import HttpClient
from depforecast.collectors.leakage import LeakageError, assert_no_leakage

WINDOW_T0S = [
    datetime(2024, 10, 1, tzinfo=UTC),  # Window A
    datetime(2025, 4, 1, tzinfo=UTC),  # Window B
    datetime(2024, 3, 31, tzinfo=UTC),  # month-end edge case
    datetime(2024, 1, 1, tzinfo=UTC),  # year boundary
]
T0 = WINDOW_T0S[0]
_ANY_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@pytest.mark.parametrize("as_of", WINDOW_T0S)
def test_real_feature_queries_pass_the_guard(as_of: datetime) -> None:
    query = _wrap([activity_query_part("r0", "expressjs/express", as_of)])
    assert_no_leakage(query, as_of)  # must not raise


@pytest.mark.parametrize("as_of", WINDOW_T0S)
def test_feature_query_dates_never_exceed_as_of(as_of: datetime) -> None:
    """Independent of the guard: every date written in the query is on or before T0."""
    query = activity_query_part("r0", "o/r", as_of)
    dates = {datetime.fromisoformat(d).replace(tzinfo=UTC) for d in _ANY_DATE.findall(query)}
    assert dates and max(dates) <= as_of


def test_outcome_query_would_be_blocked() -> None:
    """The label query looks into the future by design, so it must never be a feature."""
    query = outcome_query_part("r0", "o/r", T0, datetime(2025, 10, 1, tzinfo=UTC))
    with pytest.raises(LeakageError):
        assert_no_leakage(query, T0)


@pytest.mark.parametrize(
    "poison",
    [
        'history(since: "2024-01-01T00:00:00Z", until: "2024-10-02T00:00:00Z")',
        'search(query: "repo:o/r is:issue created:2024-04-01..2024-10-01")',
        'search(query: "repo:o/r is:issue closed:>=2024-11-01")',
        'search(query: "repo:o/r is:pr created:<2024-10-02")',
    ],
)
def test_future_dates_are_blocked(poison: str) -> None:
    with pytest.raises(LeakageError):
        assert_no_leakage(f"query {{ {poison} }}", T0)


@pytest.mark.parametrize(
    "ok",
    [
        'history(since: "2023-10-01T00:00:00Z", until: "2024-10-01T00:00:00Z")',
        'search(query: "repo:o/r is:issue created:2024-04-01..2024-09-30")',
        'search(query: "repo:o/r is:pr created:<2024-07-03 closed:<2024-10-01")',
    ],
)
def test_boundaries_up_to_t0_are_allowed(ok: str) -> None:
    assert_no_leakage(f"query {{ {ok} }}", T0)


@pytest.mark.parametrize(
    "present_only",
    [
        "repository(owner: \"o\", name: \"r\") { stargazerCount }",
        "repository(owner: \"o\", name: \"r\") { forkCount }",
        "repository(owner: \"o\", name: \"r\") { watchers { totalCount } }",
        "repository(owner: \"o\", name: \"r\") { pushedAt }",
        "repository(owner: \"o\", name: \"r\") { issues(states: OPEN) { totalCount } }",
        'search(query: "repo:o/r is:issue is:open", type: ISSUE) { issueCount }',
    ],
)
def test_present_only_fields_are_blocked(present_only: str) -> None:
    with pytest.raises(LeakageError):
        assert_no_leakage(f"query {{ {present_only} }}", T0)


@respx.mock
@pytest.mark.parametrize("as_of", WINDOW_T0S[:2])
def test_no_request_sent_for_a_window_reads_after_t0(tmp_path: Path, as_of: datetime) -> None:
    """Spy on every request the collector actually sends during a feature run."""
    route = respx.post(GRAPHQL_URL).respond(json={"data": {}})
    client = GitHubClient(HttpClient(HttpCache(tmp_path)), token="t", sleep=lambda _: None)
    client.activity([f"o/r{i}" for i in range(15)], as_of)
    assert route.call_count == 2
    for call in route.calls:
        query = json.loads(call.request.content)["query"]
        dates = [datetime.fromisoformat(d).replace(tzinfo=UTC) for d in _ANY_DATE.findall(query)]
        assert dates and max(dates) <= as_of
        assert "stargazerCount" not in query

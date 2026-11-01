"""GitHub GraphQL collector: date-bounded commit, author, issue and archive data."""

import calendar
import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Any

from depforecast.collectors.http import HttpClient, Response
from depforecast.collectors.leakage import assert_no_leakage
from depforecast.models import RepoActivity, RepoOutcome

GRAPHQL_URL = "https://api.github.com/graphql"
BATCH_SIZE = 10
AUTHOR_SAMPLE = 100
STALE_PR_DAYS = 90
MAX_RATE_LIMIT_RETRIES = 3


class GitHubError(Exception):
    """GitHub returned errors other than a missing repository."""


def shift_months(dt: datetime, months: int) -> datetime:
    """Move a date by whole calendar months (negative = back), clamping the day."""
    index = dt.year * 12 + dt.month - 1 + months
    year, month = divmod(index, 12)
    day = min(dt.day, calendar.monthrange(year, month + 1)[1])
    return dt.replace(year=year, month=month + 1, day=day)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _split(repo: str) -> tuple[str, str]:
    owner, name = repo.split("/", 1)
    return owner, name


def _author_key(author: dict[str, Any] | None) -> str | None:
    """GitHub login if linked, else a hash of the email. Raw emails never leave here."""
    if not author:
        return None
    user = author.get("user") or {}
    if user.get("login"):
        return str(user["login"]).lower()
    email = (author.get("email") or "").strip().lower()
    if email:
        return "email:" + hashlib.sha256(email.encode()).hexdigest()[:12]
    return None


def _count(node: Any) -> int:
    return int(node["totalCount"]) if isinstance(node, dict) else 0


def _issue_count(node: Any) -> int:
    return int(node["issueCount"]) if isinstance(node, dict) else 0


# --- query builders -------------------------------------------------------------


def activity_query_part(alias: str, repo: str, as_of: datetime) -> str:
    """GraphQL fragment for one repo's feature data. Every date bound is <= as_of."""
    owner, name = _split(repo)
    m3, m6, m12 = (shift_months(as_of, -n) for n in (3, 6, 12))
    stale_cutoff = as_of - timedelta(days=STALE_PR_DAYS)
    last_day = as_of - timedelta(days=1)  # search date ranges are inclusive
    q = f"repo:{owner}/{name}"
    until = _ts(as_of)

    def search(extra: str) -> str:
        query = json.dumps(f"{q} {extra}")
        return f"search(query: {query}, type: ISSUE, first: 0) {{ issueCount }}"

    return f"""
  {alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{
    createdAt
    archivedAt
    defaultBranchRef {{ target {{ ... on Commit {{
      c3: history(since: "{_ts(m3)}", until: "{until}") {{ totalCount }}
      c6: history(since: "{_ts(m6)}", until: "{until}") {{ totalCount }}
      c12: history(since: "{_ts(m12)}", until: "{until}") {{ totalCount }}
      cprev6: history(since: "{_ts(m12)}", until: "{_ts(m6)}") {{ totalCount }}
      last: history(first: 1, until: "{until}") {{ nodes {{ committedDate }} }}
      authors: history(first: {AUTHOR_SAMPLE}, since: "{_ts(m12)}", until: "{until}") {{
        nodes {{ author {{ email user {{ login }} }} }}
      }}
    }} }} }}
  }}
  {alias}_opened: {search(f"is:issue created:{_day(m6)}..{_day(last_day)}")}
  {alias}_closed: {search(f"is:issue closed:{_day(m6)}..{_day(last_day)}")}
  {alias}_oldprs: {search(f"is:pr created:<{_day(stale_cutoff)}")}
  {alias}_oldprs_closed: {search(f"is:pr created:<{_day(stale_cutoff)} closed:<{_day(as_of)}")}"""


def outcome_query_part(alias: str, repo: str, start: datetime, end: datetime) -> str:
    """GraphQL fragment for one repo's label data in the outcome window [start, end)."""
    owner, name = _split(repo)
    return f"""
  {alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{
    archivedAt
    defaultBranchRef {{ target {{ ... on Commit {{
      window: history(since: "{_ts(start)}", until: "{_ts(end)}") {{ totalCount }}
    }} }} }}
  }}"""


def _wrap(parts: Iterable[str]) -> str:
    return "query {" + "".join(parts) + "\n  rateLimit { cost remaining resetAt }\n}"


# --- parsers ----------------------------------------------------------------------


def _parse_dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def parse_activity(data: dict[str, Any], alias: str, repo: str, as_of: datetime) -> RepoActivity:
    node = data.get(alias)
    if node is None:
        return RepoActivity(repo=repo, as_of=as_of, found=False)
    target = ((node.get("defaultBranchRef") or {}).get("target")) or {}
    archived_at = _parse_dt(node.get("archivedAt"))
    created_at = _parse_dt(node.get("createdAt"))
    last_nodes = (target.get("last") or {}).get("nodes") or []
    authors = Counter(
        key
        for n in (target.get("authors") or {}).get("nodes") or []
        if (key := _author_key(n.get("author")))
    )
    old_prs = _issue_count(data.get(f"{alias}_oldprs"))
    old_prs_closed = _issue_count(data.get(f"{alias}_oldprs_closed"))
    return RepoActivity(
        repo=repo,
        as_of=as_of,
        created_at=created_at,
        archived_by_as_of=archived_at is not None and archived_at <= as_of,
        commits_3m=_count(target.get("c3")),
        commits_6m=_count(target.get("c6")),
        commits_12m=_count(target.get("c12")),
        commits_prev_6m=_count(target.get("cprev6")),
        last_commit_at=_parse_dt(last_nodes[0]["committedDate"]) if last_nodes else None,
        issues_opened_6m=_issue_count(data.get(f"{alias}_opened")),
        issues_closed_6m=_issue_count(data.get(f"{alias}_closed")),
        stale_prs=max(0, old_prs - old_prs_closed),
        author_commits=dict(authors),
    )


def parse_outcome(
    data: dict[str, Any], alias: str, repo: str, start: datetime, end: datetime
) -> RepoOutcome:
    node = data.get(alias)
    if node is None:
        return RepoOutcome(repo=repo, start=start, end=end, found=False)
    archived_at = _parse_dt(node.get("archivedAt"))
    target = ((node.get("defaultBranchRef") or {}).get("target")) or {}
    return RepoOutcome(
        repo=repo,
        start=start,
        end=end,
        archived_in_window=archived_at is not None and start <= archived_at < end,
        commits_in_window=_count(target.get("window")),
    )


# --- client ------------------------------------------------------------------------


def _only_not_found(errors: list[dict[str, Any]]) -> bool:
    return all(e.get("type") == "NOT_FOUND" for e in errors)


class GitHubClient:
    def __init__(
        self,
        http: HttpClient,
        token: str | None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not token:
            raise GitHubError("GITHUB_TOKEN is required for the GitHub GraphQL API")
        self.http = http
        self._headers = {"Authorization": f"bearer {token}"}
        self._sleep = sleep

    def graphql(self, query: str) -> dict[str, Any]:
        """Run a query. Missing repos come back as null; other errors raise."""

        def cacheable(resp: Response) -> bool:
            return _only_not_found(resp.json().get("errors") or [])

        for _ in range(MAX_RATE_LIMIT_RETRIES + 1):
            resp = self.http.post(
                GRAPHQL_URL, {"query": query}, headers=self._headers, cache_if=cacheable
            )
            if resp.status != 200:
                raise GitHubError(f"GitHub GraphQL returned HTTP {resp.status}")
            body = resp.json()
            errors = body.get("errors") or []
            if any(e.get("type") == "RATE_LIMITED" for e in errors):
                self._sleep(60)
                continue
            if not _only_not_found(errors):
                messages = "; ".join(str(e.get("message")) for e in errors[:3])
                raise GitHubError(f"GitHub GraphQL errors: {messages}")
            data: dict[str, Any] = body.get("data") or {}
            return data
        raise GitHubError("GitHub GraphQL rate limit did not reset")

    def activity(self, repos: list[str], as_of: datetime) -> dict[str, RepoActivity]:
        """Feature data for each repo, strictly before as_of. Batched."""
        out: dict[str, RepoActivity] = {}
        for i in range(0, len(repos), BATCH_SIZE):
            batch = repos[i : i + BATCH_SIZE]
            aliases = [f"r{j}" for j in range(len(batch))]
            query = _wrap(
                activity_query_part(a, r, as_of) for a, r in zip(aliases, batch, strict=True)
            )
            assert_no_leakage(query, as_of)
            data = self.graphql(query)
            for alias, repo in zip(aliases, batch, strict=True):
                out[repo] = parse_activity(data, alias, repo, as_of)
        return out

    def outcome(self, repos: list[str], start: datetime, end: datetime) -> dict[str, RepoOutcome]:
        """Label data for each repo in [start, end). Never use as features."""
        out: dict[str, RepoOutcome] = {}
        for i in range(0, len(repos), BATCH_SIZE):
            batch = repos[i : i + BATCH_SIZE]
            aliases = [f"r{j}" for j in range(len(batch))]
            data = self.graphql(
                _wrap(
                    outcome_query_part(a, r, start, end)
                    for a, r in zip(aliases, batch, strict=True)
                )
            )
            for alias, repo in zip(aliases, batch, strict=True):
                out[repo] = parse_outcome(data, alias, repo, start, end)
        return out

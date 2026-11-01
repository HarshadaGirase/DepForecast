"""Developer-only dataset builder: python -m depforecast.dataset."""

import json
from collections import Counter
from datetime import UTC, date, datetime
from itertools import zip_longest
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from depforecast.collectors.cache import HttpCache
from depforecast.collectors.github import BATCH_SIZE, GitHubClient
from depforecast.collectors.http import HttpClient
from depforecast.collectors.leakage import assert_no_leakage
from depforecast.config import load_settings
from depforecast.manifest import parse_package_json
from depforecast.models import Dependency

# SPEC §5.2. Outcome windows are the 12 months after each T0.
WINDOW_A_T0 = datetime(2024, 10, 1, tzinfo=UTC)
WINDOW_B_T0 = datetime(2025, 4, 1, tzinfo=UTC)

SEED_LANGUAGES = ("JavaScript", "TypeScript")
SEARCH_PAGE = 100
DATA_DIR = Path("data")

app = typer.Typer(help="Build the DepForecast dataset (developer only).", no_args_is_help=True)
console = Console()


@app.callback()
def main() -> None:
    """Build the DepForecast dataset (developer only)."""


def _client() -> GitHubClient:
    settings = load_settings()
    return GitHubClient(HttpClient(HttpCache(settings.cache_dir)), settings.github_token)


def search_top_repos(
    gh: GitHubClient, language: str, limit: int, created_before: date
) -> list[str]:
    """Most-starred non-fork repos in a language, created before a date. Stars are used
    only to choose seeds, never as a feature."""
    query = f"language:{language} created:<{created_before:%Y-%m-%d} sort:stars-desc"
    repos: list[str] = []
    cursor: str | None = None
    while len(repos) < limit:
        after = f", after: {json.dumps(cursor)}" if cursor else ""
        data = gh.graphql(
            f"""query {{
  search(query: {json.dumps(query)}, type: REPOSITORY, first: {SEARCH_PAGE}{after}) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ ... on Repository {{ nameWithOwner isFork }} }}
  }}
  rateLimit {{ cost remaining resetAt }}
}}"""
        )
        search = data["search"]
        repos += [n["nameWithOwner"] for n in search["nodes"] if n and not n.get("isFork")]
        if not search["pageInfo"]["hasNextPage"]:
            break
        cursor = search["pageInfo"]["endCursor"]
    return repos[:limit]


def _manifest_query_part(alias: str, repo: str, as_of: datetime) -> str:
    owner, name = repo.split("/", 1)
    until = as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""
  {alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{
    defaultBranchRef {{ target {{ ... on Commit {{
      history(first: 1, until: "{until}") {{ nodes {{
        oid
        file(path: "package.json") {{ object {{ ... on Blob {{ text }} }} }}
      }} }}
    }} }} }}
  }}"""


def _parse_manifest(node: Any) -> list[Dependency] | None:
    """Dependencies from a repo node, or None if there was no readable package.json."""
    try:
        commit = node["defaultBranchRef"]["target"]["history"]["nodes"][0]
        text = commit["file"]["object"]["text"]
        data = json.loads(text)
    except (TypeError, KeyError, IndexError, ValueError):
        return None
    return parse_package_json(data) if isinstance(data, dict) else None


def manifests_at(
    gh: GitHubClient, repos: list[str], as_of: datetime
) -> dict[str, list[Dependency] | None]:
    """Each repo's root package.json as of the last commit before as_of."""
    out: dict[str, list[Dependency] | None] = {}
    for i in range(0, len(repos), BATCH_SIZE):
        batch = repos[i : i + BATCH_SIZE]
        parts = "".join(_manifest_query_part(f"r{j}", r, as_of) for j, r in enumerate(batch))
        query = "query {" + parts + "\n  rateLimit { cost remaining resetAt }\n}"
        assert_no_leakage(query, as_of)
        data = gh.graphql(query)
        for j, repo in enumerate(batch):
            out[repo] = _parse_manifest(data.get(f"r{j}"))
    return out


def interleave(*lists: list[str]) -> list[str]:
    """Merge ranked lists by rank (1st of each, 2nd of each, ...), dropping duplicates."""
    seen: dict[str, None] = {}
    for group in zip_longest(*lists):
        for repo in group:
            if repo is not None:
                seen.setdefault(repo.lower(), None)
    return list(seen)


def read_seeds(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


@app.command()
def seeds(
    n: int = typer.Option(250, help="Number of seed repos to keep."),
    candidates: int = typer.Option(300, help="Candidates to fetch per language."),
    out: Path = typer.Option(DATA_DIR / "seed_repos.txt", help="Where to write the list."),
) -> None:
    """Pick seed repos (SPEC §5.1) and write data/seed_repos.txt."""
    gh = _client()
    ranked = [
        search_top_repos(gh, lang, candidates, WINDOW_A_T0.date()) for lang in SEED_LANGUAGES
    ]
    pool = interleave(*ranked)
    counts = zip(SEED_LANGUAGES, map(len, ranked), strict=True)
    per_language = ", ".join(f"{lang}: {count}" for lang, count in counts)
    console.print(f"{len(pool)} candidate repos ({per_language})")

    manifests = manifests_at(gh, pool, WINDOW_A_T0)
    skipped: Counter[str] = Counter()
    chosen: list[str] = []
    for repo in pool:
        deps = manifests[repo]
        if deps is None:
            skipped["no readable package.json at T0"] += 1
        elif not deps:
            skipped["package.json lists no dependencies"] += 1
        elif len(chosen) < n:
            chosen.append(repo)

    header = [
        "# DepForecast seed repositories (SPEC §5.1).",
        f"# Rule: top {'/'.join(SEED_LANGUAGES)} repos by stars from GitHub search (forks",
        f"#   excluded, created before {WINDOW_A_T0:%Y-%m-%d}), interleaved by rank. A repo",
        "#   is kept if its root package.json at the last commit before that date lists",
        "#   at least one dependency.",
        f"# Generated {datetime.now(UTC):%Y-%m-%d} by: python -m depforecast.dataset seeds"
        f" --n {n} --candidates {candidates}",
        "# Stars were used only to pick seeds; they are never a model feature.",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header + chosen) + "\n", encoding="utf-8")

    console.print(f"kept {len(chosen)} seeds -> {out}")
    for reason, count in skipped.most_common():
        console.print(f"  skipped {count}: {reason}")
    console.print("  first 10: " + ", ".join(chosen[:10]))


if __name__ == "__main__":
    app()

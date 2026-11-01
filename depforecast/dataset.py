"""Developer-only dataset builder: python -m depforecast.dataset."""

import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
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
from depforecast.collectors.npm import fetch_package
from depforecast.config import load_settings
from depforecast.manifest import parse_package_json
from depforecast.models import Dependency, NpmPackage

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


def _clients() -> tuple[HttpClient, GitHubClient]:
    settings = load_settings()
    http = HttpClient(HttpCache(settings.cache_dir))
    return http, GitHubClient(http, settings.github_token)


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
    _, gh = _clients()
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


# --- universe ----------------------------------------------------------------------

EXCLUDED_SCOPES = ("@types/",)  # DefinitelyTyped: thousands of packages, one repo (D2)


@dataclass(frozen=True)
class UniverseRow:
    repo: str
    package: str
    """Representative package: the one used by the most seeds (D2)."""
    n_seeds: int
    """Seeds whose package.json at Window A's T0 lists the representative package."""
    repo_packages: int
    """How many universe packages map to this repo (monorepos > 1)."""
    other_packages: str


def seed_usage(manifests: dict[str, list[Dependency] | None]) -> Counter[str]:
    """How many seeds depend on each package (each seed counts once per package)."""
    usage: Counter[str] = Counter()
    for deps in manifests.values():
        usage.update({d.name for d in deps or []})
    return usage


def build_universe(
    usage: Counter[str], packages: dict[str, NpmPackage | None | Exception]
) -> tuple[list[UniverseRow], Counter[str]]:
    """Resolve packages to GitHub repos and collapse monorepos to one row per repo."""
    skipped: Counter[str] = Counter()
    by_repo: dict[str, list[str]] = defaultdict(list)
    for name in usage:
        if name.startswith(EXCLUDED_SCOPES):
            skipped["excluded scope (@types/*)"] += 1
            continue
        pkg = packages.get(name)
        if isinstance(pkg, Exception):
            skipped["npm request failed"] += 1
        elif pkg is None:
            skipped["not found on npm"] += 1
        elif not pkg.repo_url:
            skipped["no repository field"] += 1
        elif not pkg.repo:
            skipped["repository not on GitHub"] += 1
        else:
            by_repo[pkg.repo].append(name)

    rows = []
    for repo, names in by_repo.items():
        names.sort(key=lambda n: (-usage[n], n))
        rows.append(
            UniverseRow(
                repo=repo,
                package=names[0],
                n_seeds=usage[names[0]],
                repo_packages=len(names),
                other_packages=";".join(names[1:]),
            )
        )
    rows.sort(key=lambda r: (-r.n_seeds, r.repo))
    return rows, skipped


def write_universe(path: Path, rows: list[UniverseRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(UniverseRow.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(r) for r in rows)


def read_universe(path: Path) -> list[UniverseRow]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            UniverseRow(
                repo=r["repo"],
                package=r["package"],
                n_seeds=int(r["n_seeds"]),
                repo_packages=int(r["repo_packages"]),
                other_packages=r["other_packages"],
            )
            for r in csv.DictReader(fh)
        ]


@app.command()
def universe(
    seeds_file: Path = typer.Option(DATA_DIR / "seed_repos.txt", help="Seed list to read."),
    out: Path = typer.Option(DATA_DIR / "universe.csv", help="Where to write the universe."),
) -> None:
    """Union of seed dependencies, resolved to GitHub, one row per repo (SPEC §5.1, D2)."""
    http, gh = _clients()
    seeds_list = read_seeds(seeds_file)
    usage = seed_usage(manifests_at(gh, seeds_list, WINDOW_A_T0))
    console.print(f"{len(seeds_list)} seeds -> {len(usage)} distinct direct dependencies")

    names = [n for n, _ in usage.most_common() if not n.startswith(EXCLUDED_SCOPES)]
    packages: dict[str, NpmPackage | None | Exception] = {}
    for i, name in enumerate(names, 1):
        try:
            packages[name] = fetch_package(http, name)
        except Exception as exc:  # one bad package never stops the build
            packages[name] = exc
        if i % 250 == 0 or i == len(names):
            console.print(f"  npm: {i}/{len(names)}")

    rows, skipped = build_universe(usage, packages)
    write_universe(out, rows)
    summary = {
        "generated": f"{datetime.now(UTC):%Y-%m-%d}",
        "seeds": len(seeds_list),
        "distinct_dependencies": len(usage),
        "github_packages": sum(r.repo_packages for r in rows),
        "repos": len(rows),
        "monorepos": sum(1 for r in rows if r.repo_packages > 1),
        "skipped": dict(skipped.most_common()),
    }
    out.with_name("universe_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    console.print(f"kept {len(rows)} repos -> {out}")
    console.print(json.dumps(summary, indent=2))
    top = ", ".join(f"{r.package} ({r.n_seeds})" for r in rows[:10])
    console.print(f"  top 10 by seed usage: {top}")


if __name__ == "__main__":
    app()

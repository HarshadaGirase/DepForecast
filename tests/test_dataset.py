import json
from collections import Counter
from pathlib import Path

from depforecast.dataset import (
    UniverseRow,
    _parse_manifest,
    build_universe,
    interleave,
    read_seeds,
    read_universe,
    seed_usage,
    write_universe,
)
from depforecast.models import Dependency, NpmPackage


def test_interleave_by_rank_and_dedupe() -> None:
    js = ["a/one", "b/two", "c/three"]
    ts = ["x/one", "B/Two", "y/two"]
    assert interleave(js, ts) == ["a/one", "x/one", "b/two", "c/three", "y/two"]


def test_read_seeds_skips_comments(tmp_path: Path) -> None:
    path = tmp_path / "seeds.txt"
    path.write_text("# header\n# more\nreact/react\n\nvuejs/vue\n")
    assert read_seeds(path) == ["react/react", "vuejs/vue"]


def _node(text: str | None) -> dict[str, object]:
    blob = {"text": text} if text is not None else None
    commit = {"oid": "abc", "file": {"object": blob} if blob else None}
    return {"defaultBranchRef": {"target": {"history": {"nodes": [commit]}}}}


def test_parse_manifest_from_github_blob() -> None:
    deps = _parse_manifest(_node(json.dumps({"dependencies": {"express": "^4"}})))
    assert deps is not None and [d.name for d in deps] == ["express"]


def test_parse_manifest_missing_or_broken() -> None:
    assert _parse_manifest(None) is None  # repo not found
    assert _parse_manifest(_node(None)) is None  # no package.json at that commit
    assert _parse_manifest(_node("{ not json")) is None
    assert _parse_manifest(_node("[1, 2]")) is None
    assert _parse_manifest({"defaultBranchRef": None}) is None  # empty repo


def test_parse_manifest_without_dependencies() -> None:
    assert _parse_manifest(_node(json.dumps({"name": "docs-only"}))) == []


def _pkg(name: str, repo: str | None, repo_url: str | None = "x") -> "NpmPackage":
    return NpmPackage(name=name, releases=[], repo=repo, repo_url=repo_url)


def test_seed_usage_counts_each_seed_once() -> None:
    d = lambda n: Dependency(name=n, spec="*")  # noqa: E731
    usage = seed_usage({"s1": [d("a"), d("b")], "s2": [d("a")], "s3": None})
    assert usage == {"a": 2, "b": 1}


def test_build_universe_skips_with_reasons_and_collapses_monorepos() -> None:
    usage = Counter(
        {
            "@babel/core": 50,
            "@babel/parser": 10,
            "express": 40,
            "@types/node": 90,
            "gone": 3,
            "norepo": 2,
            "gitlabby": 2,
            "flaky": 1,
        }
    )
    packages: dict[str, NpmPackage | None | Exception] = {
        "@babel/core": _pkg("@babel/core", "babel/babel"),
        "@babel/parser": _pkg("@babel/parser", "babel/babel"),
        "express": _pkg("express", "expressjs/express"),
        "gone": None,
        "norepo": _pkg("norepo", None, repo_url=None),
        "gitlabby": _pkg("gitlabby", None, repo_url="https://gitlab.com/a/b"),
        "flaky": RuntimeError("boom"),
    }
    rows, skipped = build_universe(usage, packages)
    assert [(r.repo, r.package, r.n_seeds, r.repo_packages) for r in rows] == [
        ("babel/babel", "@babel/core", 50, 2),
        ("expressjs/express", "express", 40, 1),
    ]
    assert rows[0].other_packages == "@babel/parser"
    assert skipped == {
        "excluded scope (@types/*)": 1,
        "not found on npm": 1,
        "no repository field": 1,
        "repository not on GitHub": 1,
        "npm request failed": 1,
    }


def test_universe_csv_round_trip(tmp_path: Path) -> None:
    rows = [UniverseRow("a/b", "pkg", 3, 2, "pkg2"), UniverseRow("c/d", "x", 1, 1, "")]
    write_universe(tmp_path / "u.csv", rows)
    assert read_universe(tmp_path / "u.csv") == rows

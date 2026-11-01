import json
from pathlib import Path

from depforecast.dataset import _parse_manifest, interleave, read_seeds


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

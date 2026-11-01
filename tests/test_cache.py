from datetime import timedelta
from pathlib import Path

from depforecast.collectors.cache import HttpCache, cache_key

URL = "https://registry.npmjs.org/express"


def test_miss_then_hit(tmp_path: Path) -> None:
    cache = HttpCache(tmp_path)
    assert cache.get("GET", URL) is None
    cache.put("GET", URL, None, 200, '{"name": "express"}')
    hit = cache.get("GET", URL)
    assert hit is not None
    assert hit.status == 200
    assert hit.json() == {"name": "express"}


def test_key_depends_on_body_not_key_order() -> None:
    a = cache_key("POST", URL, {"query": "q", "variables": {"x": 1, "y": 2}})
    b = cache_key("POST", URL, {"variables": {"y": 2, "x": 1}, "query": "q"})
    c = cache_key("POST", URL, {"query": "other"})
    assert a == b
    assert a != c


def test_method_is_case_insensitive() -> None:
    assert cache_key("get", URL) == cache_key("GET", URL)


def test_max_age_expires_entries(tmp_path: Path) -> None:
    cache = HttpCache(tmp_path)
    cache.put("GET", URL, None, 200, "{}")
    assert cache.get("GET", URL, max_age=timedelta(hours=1)) is not None
    assert cache.get("GET", URL, max_age=timedelta(seconds=-1)) is None
    assert cache.get("GET", URL) is not None  # no max_age: kept forever


def test_corrupt_entry_is_a_miss(tmp_path: Path) -> None:
    cache = HttpCache(tmp_path)
    cache.put("GET", URL, None, 200, "{}")
    for f in (tmp_path / "http").rglob("*.json"):
        f.write_text("not json")
    assert cache.get("GET", URL) is None


def test_token_never_written_to_disk(tmp_path: Path) -> None:
    cache = HttpCache(tmp_path)
    body = {"query": "{ viewer { login } }"}
    cache.put("POST", "https://api.github.com/graphql", body, 200, "{}")
    for f in (tmp_path / "http").rglob("*"):
        if f.is_file():
            assert "bearer" not in f.read_text().lower()


def test_no_temp_files_left_behind(tmp_path: Path) -> None:
    cache = HttpCache(tmp_path)
    cache.put("GET", URL, None, 200, "{}")
    assert not list((tmp_path / "http").rglob("*.tmp"))

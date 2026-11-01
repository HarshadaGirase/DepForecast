from pathlib import Path

import httpx
import pytest
import respx

from depforecast.collectors.cache import HttpCache
from depforecast.collectors.http import HttpClient, HttpError, OfflineCacheMiss

URL = "https://registry.npmjs.org/express"
GQL = "https://api.github.com/graphql"
NOW = 1_000_000.0


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _client(tmp_path: Path, sleep: FakeSleep, offline: bool = False) -> HttpClient:
    return HttpClient(
        HttpCache(tmp_path), offline=offline, sleep=sleep, clock=lambda: NOW, max_retries=3
    )


@respx.mock
def test_success_is_cached(tmp_path: Path) -> None:
    route = respx.get(URL).respond(json={"name": "express"})
    client = _client(tmp_path, FakeSleep())
    first = client.get(URL)
    second = client.get(URL)
    assert first.json() == {"name": "express"} and not first.from_cache
    assert second.from_cache
    assert route.call_count == 1


@respx.mock
def test_retries_on_server_error_then_succeeds(tmp_path: Path) -> None:
    respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(500), httpx.Response(200, json={})]
    )
    sleep = FakeSleep()
    assert _client(tmp_path, sleep).get(URL).status == 200
    assert len(sleep.calls) == 2
    assert 1 <= sleep.calls[0] < 2 and 2 <= sleep.calls[1] < 3  # exponential backoff


@respx.mock
def test_retries_on_connection_error(tmp_path: Path) -> None:
    respx.get(URL).mock(side_effect=[httpx.ConnectError("down"), httpx.Response(200, json={})])
    assert _client(tmp_path, FakeSleep()).get(URL).status == 200


@respx.mock
def test_gives_up_after_max_retries(tmp_path: Path) -> None:
    route = respx.get(URL).respond(status_code=503)
    with pytest.raises(HttpError, match="4 attempts"):
        _client(tmp_path, FakeSleep()).get(URL)
    assert route.call_count == 4


@respx.mock
def test_errors_are_not_cached(tmp_path: Path) -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(404), httpx.Response(200, json={})])
    client = _client(tmp_path, FakeSleep())
    assert client.get(URL).status == 404  # returned immediately, no retry
    assert client.get(URL).status == 200


@respx.mock
def test_retry_after_header_is_respected(tmp_path: Path) -> None:
    respx.post(GQL).mock(
        side_effect=[httpx.Response(403, headers={"retry-after": "42"}), httpx.Response(200)]
    )
    sleep = FakeSleep()
    _client(tmp_path, sleep).post(GQL, {"query": "q"})
    assert sleep.calls == [42.0]


@respx.mock
def test_waits_until_rate_limit_reset(tmp_path: Path) -> None:
    limited = httpx.Response(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW + 120))}
    )
    respx.post(GQL).mock(side_effect=[limited, httpx.Response(200)])
    sleep = FakeSleep()
    _client(tmp_path, sleep).post(GQL, {"query": "q"})
    assert sleep.calls == [121.0]


@respx.mock
def test_secondary_rate_limit_waits_at_least_a_minute(tmp_path: Path) -> None:
    respx.post(GQL).mock(
        side_effect=[
            httpx.Response(403, text="You have exceeded a secondary rate limit"),
            httpx.Response(200),
        ]
    )
    sleep = FakeSleep()
    _client(tmp_path, sleep).post(GQL, {"query": "q"})
    assert sleep.calls[0] >= 60


@respx.mock
def test_pauses_before_next_request_when_points_run_out(tmp_path: Path) -> None:
    last_point = httpx.Response(
        200, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(NOW + 30))}
    )
    respx.post(GQL).mock(side_effect=[last_point, httpx.Response(200)])
    sleep = FakeSleep()
    client = _client(tmp_path, sleep)
    client.post(GQL, {"query": "a"})
    assert sleep.calls == []
    client.post(GQL, {"query": "b"})
    assert sleep.calls == [31.0]


@respx.mock
def test_cache_if_can_veto_caching(tmp_path: Path) -> None:
    route = respx.post(GQL).respond(json={"errors": [{"type": "RATE_LIMITED"}]})
    client = _client(tmp_path, FakeSleep())
    no_errors = lambda r: "errors" not in r.json()  # noqa: E731
    client.post(GQL, {"query": "q"}, cache_if=no_errors)
    client.post(GQL, {"query": "q"}, cache_if=no_errors)
    assert route.call_count == 2


def test_offline_miss_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(OfflineCacheMiss, match="without --offline"):
        _client(tmp_path, FakeSleep(), offline=True).get(URL)


@respx.mock
def test_offline_hit_never_touches_network(tmp_path: Path) -> None:
    HttpCache(tmp_path).put("GET", URL, None, 200, '{"ok": true}')
    route = respx.get(URL).respond(status_code=500)
    assert _client(tmp_path, FakeSleep(), offline=True).get(URL).json() == {"ok": True}
    assert route.call_count == 0


@respx.mock
def test_token_header_sent_but_not_cached(tmp_path: Path) -> None:
    route = respx.post(GQL).respond(json={"data": {}})
    _client(tmp_path, FakeSleep()).post(
        GQL, {"query": "q"}, headers={"Authorization": "bearer SECRET123"}
    )
    assert route.calls[0].request.headers["Authorization"] == "bearer SECRET123"
    for f in (tmp_path / "http").rglob("*.json"):
        assert "SECRET123" not in f.read_text()


@respx.mock
def test_transform_applies_before_caching(tmp_path: Path) -> None:
    route = respx.get(URL).respond(text="RAW BODY")
    client = _client(tmp_path, FakeSleep())
    first = client.get(URL, transform=str.lower)
    second = client.get(URL, transform=str.lower)
    assert first.text == second.text == "raw body"
    assert second.from_cache and route.call_count == 1

"""Shared HTTP client: disk cache, offline mode, timeouts, retry with backoff, rate limits."""

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx

from depforecast.collectors.cache import HttpCache

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_BACKOFF = 60.0
MAX_RATE_LIMIT_WAIT = 3700.0  # GitHub's primary limit resets hourly


class OfflineCacheMiss(Exception):
    """--offline was set and the response is not in the cache."""


class HttpError(Exception):
    """The request still failed after all retries."""


@dataclass(frozen=True)
class Response:
    status: int
    text: str
    from_cache: bool = False

    def json(self) -> Any:
        return json.loads(self.text)


class HttpClient:
    def __init__(
        self,
        cache: HttpCache,
        offline: bool = False,
        client: httpx.Client | None = None,
        max_retries: int = 5,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache = cache
        self.offline = offline
        self.max_retries = max_retries
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._sleep = sleep
        self._clock = clock
        self._pause_until = 0.0

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        max_age: timedelta | None = None,
        transform: Callable[[str], str] | None = None,
    ) -> Response:
        return self.request("GET", url, None, headers, max_age, transform=transform)

    def post(
        self,
        url: str,
        body: Any,
        headers: dict[str, str] | None = None,
        max_age: timedelta | None = None,
        cache_if: Callable[[Response], bool] | None = None,
    ) -> Response:
        return self.request("POST", url, body, headers, max_age, cache_if)

    def request(
        self,
        method: str,
        url: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        max_age: timedelta | None = None,
        cache_if: Callable[[Response], bool] | None = None,
        transform: Callable[[str], str] | None = None,
    ) -> Response:
        """Serve from cache, or fetch with retries. Only 200 responses are cached.

        `cache_if` can veto caching a 200 response (e.g. a GraphQL body with errors).
        `transform` rewrites a 200 body before it is cached and returned (e.g. to keep
        only the fields we use). Headers are never part of the cache key, so tokens
        never reach the disk.
        """
        cached = self.cache.get(method, url, body, max_age)
        if cached is not None:
            return Response(cached.status, cached.text, from_cache=True)
        if self.offline:
            raise OfflineCacheMiss(f"{method} {url} is not cached; run once without --offline")

        last_error = ""
        for attempt in range(self.max_retries + 1):
            self._wait_for_rate_limit()
            try:
                resp = self._client.request(method, url, json=body, headers=headers)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}"
                self._sleep(self._backoff(attempt))
                continue

            self._note_rate_limit(resp)
            if resp.status_code == 200:
                result = Response(200, transform(resp.text) if transform else resp.text)
                if cache_if is None or cache_if(result):
                    self.cache.put(method, url, body, 200, result.text)
                return result

            wait = self._retry_wait(resp, attempt)
            if wait is None:  # not retryable (404, 401, 422, ...): let the caller decide
                return Response(resp.status_code, resp.text)
            last_error = f"HTTP {resp.status_code}"
            self._sleep(wait)

        attempts = self.max_retries + 1
        raise HttpError(f"{method} {url} failed after {attempts} attempts: {last_error}")

    def _backoff(self, attempt: int) -> float:
        return float(min(MAX_BACKOFF, 2.0**attempt + random.uniform(0, 1)))

    def _retry_wait(self, resp: httpx.Response, attempt: int) -> float | None:
        """Seconds to wait before retrying, or None if the response is final."""
        retry_after = resp.headers.get("retry-after")
        if retry_after is not None:
            try:
                return min(MAX_RATE_LIMIT_WAIT, float(retry_after))
            except ValueError:
                pass
        if resp.headers.get("x-ratelimit-remaining") == "0":
            return self._seconds_until_reset(resp)
        if resp.status_code in RETRY_STATUSES:
            return self._backoff(attempt)
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            # GitHub secondary rate limit without headers: wait at least a minute.
            return max(60.0, self._backoff(attempt))
        return None

    def _seconds_until_reset(self, resp: httpx.Response) -> float:
        try:
            reset = float(resp.headers.get("x-ratelimit-reset", ""))
        except ValueError:
            return MAX_BACKOFF
        return min(MAX_RATE_LIMIT_WAIT, max(1.0, reset - self._clock() + 1))

    def _note_rate_limit(self, resp: httpx.Response) -> None:
        """If this response used the last point, pause before the next request."""
        if resp.status_code == 200 and resp.headers.get("x-ratelimit-remaining") == "0":
            self._pause_until = self._clock() + self._seconds_until_reset(resp)

    def _wait_for_rate_limit(self) -> None:
        remaining = self._pause_until - self._clock()
        if remaining > 0:
            self._sleep(remaining)

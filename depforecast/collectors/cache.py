"""On-disk HTTP response cache in ~/.cache/depforecast/, keyed by URL + query."""

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CachedResponse:
    status: int
    text: str
    stored_at: datetime

    def json(self) -> Any:
        return json.loads(self.text)


def cache_key(method: str, url: str, body: Any = None) -> str:
    """Stable fingerprint of a request. Headers (and therefore tokens) are not part of it."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")) if body is not None else ""
    return hashlib.sha256(f"{method.upper()} {url}\n{canonical}".encode()).hexdigest()


class HttpCache:
    def __init__(self, root: Path) -> None:
        self.root = root / "http"

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(
        self, method: str, url: str, body: Any = None, max_age: timedelta | None = None
    ) -> CachedResponse | None:
        """Cached response, or None if missing, unreadable or older than max_age."""
        path = self._path(cache_key(method, url, body))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cached = CachedResponse(
                status=int(data["status"]),
                text=str(data["text"]),
                stored_at=datetime.fromisoformat(data["stored_at"]),
            )
        except (OSError, ValueError, KeyError):
            return None
        if max_age is not None and datetime.now(UTC) - cached.stored_at > max_age:
            return None
        return cached

    def put(self, method: str, url: str, body: Any, status: int, text: str) -> None:
        path = self._path(cache_key(method, url, body))
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "method": method.upper(),
            "url": url,
            "status": status,
            "text": text,
            "stored_at": datetime.now(UTC).isoformat(),
        }
        # Write to a temp file and rename, so a crash never leaves a half-written entry.
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(record, fh)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

"""`depforecast doctor`: checks GitHub token, cache, TabPFN token/weights and Ollama."""

import importlib.util
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from depforecast.config import Settings, redact

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
TIMEOUT = httpx.Timeout(10.0)


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str


def check_github(settings: Settings, client: httpx.Client) -> Check:
    if not settings.github_token:
        return Check(
            "GitHub token",
            Level.WARN,
            "GITHUB_TOKEN not set: GitHub features are skipped and scans use npm data only. "
            "Create a token at https://github.com/settings/tokens (no scopes needed).",
        )
    query = "query { viewer { login } rateLimit { limit remaining resetAt } }"
    try:
        resp = client.post(
            GITHUB_GRAPHQL_URL,
            json={"query": query},
            headers={"Authorization": f"bearer {settings.github_token}"},
        )
    except httpx.HTTPError as exc:
        return Check("GitHub token", Level.FAIL, redact(f"cannot reach GitHub: {exc}", settings))
    if resp.status_code == 401:
        return Check("GitHub token", Level.FAIL, "GITHUB_TOKEN is invalid or expired")
    if resp.status_code != 200:
        return Check("GitHub token", Level.FAIL, f"GitHub returned HTTP {resp.status_code}")
    data = resp.json().get("data") or {}
    login = (data.get("viewer") or {}).get("login", "?")
    rl = data.get("rateLimit") or {}
    return Check(
        "GitHub token",
        Level.OK,
        f"authenticated as {login}; {rl.get('remaining')}/{rl.get('limit')} points left, "
        f"resets {rl.get('resetAt')}",
    )


def check_cache(settings: Settings) -> Check:
    try:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=settings.cache_dir):
            pass
    except OSError as exc:
        return Check("HTTP cache", Level.FAIL, f"{settings.cache_dir} is not writable: {exc}")
    entries = sum(1 for p in settings.cache_dir.rglob("*") if p.is_file())
    return Check("HTTP cache", Level.OK, f"{settings.cache_dir} ({entries} cached files)")


def _tabpfn_cache_dir() -> Path:
    env = os.environ.get("TABPFN_MODEL_CACHE_DIR")
    return Path(env) if env else Path.home() / ".cache" / "tabpfn"


def check_tabpfn(settings: Settings) -> list[Check]:
    checks = [
        Check("TabPFN token", Level.OK, "TABPFN_TOKEN is set")
        if settings.tabpfn_token
        else Check(
            "TabPFN token",
            Level.WARN,
            "TABPFN_TOKEN not set: accept the license at "
            "https://platform.priorlabs.ai/account/licenses and create a key at "
            "https://platform.priorlabs.ai/account/api-keys. Until then: model = fallback.",
        )
    ]
    if importlib.util.find_spec("tabpfn") is None:
        checks.append(
            Check("TabPFN package", Level.WARN, "tabpfn not installed: model = fallback")
        )
        return checks
    checks.append(Check("TabPFN package", Level.OK, "tabpfn installed"))
    cache = _tabpfn_cache_dir()
    weights = list(cache.rglob("*.ckpt")) + list(cache.rglob("*.safetensors"))
    if weights:
        checks.append(Check("TabPFN weights", Level.OK, f"{len(weights)} file(s) in {cache}"))
    else:
        checks.append(
            Check("TabPFN weights", Level.WARN, f"none in {cache}: downloaded on first use")
        )
    return checks


def check_ollama(settings: Settings, client: httpx.Client) -> Check:
    try:
        resp = client.get(f"{settings.ollama_host}/api/tags")
        resp.raise_for_status()
    except httpx.HTTPError:
        return Check(
            "Ollama",
            Level.WARN,
            f"not reachable at {settings.ollama_host}: explanations use rules instead of Gemma",
        )
    names = {m.get("name") for m in resp.json().get("models", [])}
    if settings.ollama_model in names:
        return Check("Ollama", Level.OK, f"running; model {settings.ollama_model} available")
    return Check(
        "Ollama",
        Level.WARN,
        f"running, but {settings.ollama_model} is missing: run `ollama pull "
        f"{settings.ollama_model}` (or set DEPFORECAST_OLLAMA_MODEL)",
    )


def run_checks(settings: Settings, client: httpx.Client | None = None) -> list[Check]:
    own_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        return [
            check_github(settings, http),
            check_cache(settings),
            *check_tabpfn(settings),
            check_ollama(settings, http),
        ]
    finally:
        if own_client:
            http.close()

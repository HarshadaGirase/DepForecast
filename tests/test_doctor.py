from pathlib import Path

import httpx
import respx

from depforecast.config import Settings, redact
from depforecast.doctor import (
    GITHUB_GRAPHQL_URL,
    Level,
    check_cache,
    check_github,
    check_ollama,
)

FAKE_TOKEN = "ghp_FAKE0123456789"


def _settings(tmp_path: Path, github_token: str | None = FAKE_TOKEN) -> Settings:
    return Settings(
        github_token=github_token,
        tabpfn_token=None,
        cache_dir=tmp_path / "cache",
        ollama_host="http://ollama.test",
        ollama_model="gemma3:4b",
    )


@respx.mock
def test_github_ok(tmp_path: Path) -> None:
    respx.post(GITHUB_GRAPHQL_URL).respond(
        json={
            "data": {
                "viewer": {"login": "octo"},
                "rateLimit": {"limit": 5000, "remaining": 4990, "resetAt": "2026-01-01T00:00:00Z"},
            }
        }
    )
    with httpx.Client() as client:
        check = check_github(_settings(tmp_path), client)
    assert check.level is Level.OK
    assert "octo" in check.detail and "4990/5000" in check.detail
    assert FAKE_TOKEN not in check.detail


@respx.mock
def test_github_bad_token(tmp_path: Path) -> None:
    respx.post(GITHUB_GRAPHQL_URL).respond(status_code=401)
    with httpx.Client() as client:
        check = check_github(_settings(tmp_path), client)
    assert check.level is Level.FAIL


def test_github_missing_token_is_warning(tmp_path: Path) -> None:
    with httpx.Client() as client:
        check = check_github(_settings(tmp_path, github_token=None), client)
    assert check.level is Level.WARN


@respx.mock
def test_network_error_never_leaks_token(tmp_path: Path) -> None:
    respx.post(GITHUB_GRAPHQL_URL).mock(side_effect=httpx.ConnectError(f"boom {FAKE_TOKEN}"))
    with httpx.Client() as client:
        check = check_github(_settings(tmp_path), client)
    assert check.level is Level.FAIL
    assert FAKE_TOKEN not in check.detail


def test_cache_created_and_writable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert check_cache(settings).level is Level.OK
    assert settings.cache_dir.is_dir()


@respx.mock
def test_ollama_model_present(tmp_path: Path) -> None:
    respx.get("http://ollama.test/api/tags").respond(json={"models": [{"name": "gemma3:4b"}]})
    with httpx.Client() as client:
        assert check_ollama(_settings(tmp_path), client).level is Level.OK


@respx.mock
def test_ollama_model_missing(tmp_path: Path) -> None:
    respx.get("http://ollama.test/api/tags").respond(json={"models": [{"name": "llama3:8b"}]})
    with httpx.Client() as client:
        check = check_ollama(_settings(tmp_path), client)
    assert check.level is Level.WARN and "ollama pull" in check.detail


@respx.mock
def test_ollama_not_running(tmp_path: Path) -> None:
    respx.get("http://ollama.test/api/tags").mock(side_effect=httpx.ConnectError("refused"))
    with httpx.Client() as client:
        assert check_ollama(_settings(tmp_path), client).level is Level.WARN


def test_redact(tmp_path: Path) -> None:
    assert redact(f"token={FAKE_TOKEN}", _settings(tmp_path)) == "token=***"

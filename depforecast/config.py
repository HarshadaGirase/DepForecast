"""Settings from environment variables (and a local .env file). Tokens are never printed."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "gemma3:4b"


@dataclass(frozen=True)
class Settings:
    github_token: str | None
    tabpfn_token: str | None
    cache_dir: Path
    ollama_host: str
    ollama_model: str


def load_settings() -> Settings:
    """Read settings; values already in the environment win over .env."""
    load_dotenv(override=False)
    cache = os.environ.get("DEPFORECAST_CACHE_DIR") or Path.home() / ".cache" / "depforecast"
    return Settings(
        github_token=os.environ.get("GITHUB_TOKEN") or None,
        tabpfn_token=os.environ.get("TABPFN_TOKEN") or None,
        cache_dir=Path(cache),
        ollama_host=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/"),
        ollama_model=os.environ.get("DEPFORECAST_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
    )


def redact(text: str, settings: Settings) -> str:
    """Remove any configured token from text before it is shown or logged."""
    for token in (settings.github_token, settings.tabpfn_token):
        if token:
            text = text.replace(token, "***")
    return text

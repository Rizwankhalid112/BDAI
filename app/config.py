"""Loads all settings from the .env file (or real environment variables).

Every other module imports `settings` from here instead of reading os.environ
itself, so there is exactly one place where configuration lives.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import quote, urlparse

from dotenv import load_dotenv

ALLOWED_OLLAMA_HOSTS = {"localhost", "127.0.0.1", "::1", "ollama"}


class ConfigError(Exception):
    """Raised when a setting is missing or not allowed."""


def _get(name: str, default: str | None = None) -> str:
    """Read one setting; fail loudly if it is required but missing."""
    value = os.getenv(name, default)
    if value is None or value == "":
        raise ConfigError(f"Missing setting {name}. Copy .env.example to .env and fill it in.")
    return value


@dataclass(frozen=True)
class Settings:
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str
    postgres_port: int

    ollama_url: str
    llm_model: str
    embed_model: str
    embed_dim: int
    llm_timeout_seconds: float
    llm_num_ctx: int
    temperature_parse: float
    temperature_write: float

    match_weight_semantic: float
    match_weight_skills: float
    match_weight_experience: float
    match_min_total: float
    match_min_margin: float

    @property
    def database_url(self) -> str:
        """Connection string for psycopg, e.g. postgresql://user:pw@localhost:5434/db."""
        return (
            f"postgresql://{quote(self.postgres_user)}:{quote(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def _check_ollama_is_local(url: str) -> None:
    """Refuse to run if OLLAMA_URL points anywhere other than a local host."""
    host = urlparse(url).hostname
    if host not in ALLOWED_OLLAMA_HOSTS:
        raise ConfigError(
            f"OLLAMA_URL host '{host}' is not allowed. Only local Ollama is permitted "
            f"(allowed: {sorted(ALLOWED_OLLAMA_HOSTS)})."
        )


@lru_cache
def get_settings() -> Settings:
    """Build the Settings object once and reuse it."""
    load_dotenv()

    ollama_url = _get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    _check_ollama_is_local(ollama_url)

    return Settings(
        postgres_user=_get("POSTGRES_USER"),
        postgres_password=_get("POSTGRES_PASSWORD"),
        postgres_db=_get("POSTGRES_DB"),
        postgres_host=_get("POSTGRES_HOST", "localhost"),
        postgres_port=int(_get("POSTGRES_PORT", "5434")),
        ollama_url=ollama_url,
        llm_model=_get("LLM_MODEL", "qwen3:8b"),
        embed_model=_get("EMBED_MODEL", "bge-m3"),
        embed_dim=int(_get("EMBED_DIM", "1024")),
        llm_timeout_seconds=float(_get("LLM_TIMEOUT_SECONDS", "300")),
        llm_num_ctx=int(_get("LLM_NUM_CTX", "8192")),
        temperature_parse=float(_get("TEMPERATURE_PARSE", "0.1")),
        temperature_write=float(_get("TEMPERATURE_WRITE", "0.4")),
        match_weight_semantic=float(_get("MATCH_WEIGHT_SEMANTIC", "0.50")),
        match_weight_skills=float(_get("MATCH_WEIGHT_SKILLS", "0.35")),
        match_weight_experience=float(_get("MATCH_WEIGHT_EXPERIENCE", "0.15")),
        match_min_total=float(_get("MATCH_MIN_TOTAL", "0.50")),
        match_min_margin=float(_get("MATCH_MIN_MARGIN", "0.05")),
    )

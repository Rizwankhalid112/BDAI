"""Tests for app/config.py."""

import pytest

from app import config


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    """Provide the minimum required settings and clear the cached Settings."""
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("POSTGRES_USER", "user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss word")
    monkeypatch.setenv("POSTGRES_DB", "db")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_defaults_are_loaded():
    s = config.get_settings()
    assert s.llm_model == "qwen3:8b"
    assert s.embed_dim == 1024
    assert s.postgres_port == 5434


def test_database_url_escapes_password():
    url = config.get_settings().database_url
    assert url == "postgresql://user:p%40ss%20word@localhost:5434/db"


def test_remote_ollama_is_rejected(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "https://api.example.com")
    with pytest.raises(config.ConfigError, match="not allowed"):
        config.get_settings()


def test_missing_required_setting(monkeypatch):
    monkeypatch.delenv("POSTGRES_PASSWORD")
    with pytest.raises(config.ConfigError, match="POSTGRES_PASSWORD"):
        config.get_settings()

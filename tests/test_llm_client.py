"""Tests for app/llm/client.py. Ollama is mocked — no real AI calls."""

import json

import httpx
import pytest
from pydantic import BaseModel

from app import config
from app.llm import client


class Answer(BaseModel):
    title: str
    years: int


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for key, value in {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p", "POSTGRES_DB": "d",
                       "OLLAMA_URL": "http://localhost:11434", "EMBED_DIM": "4"}.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@pytest.fixture
def fake_ollama(monkeypatch):
    """Replace httpx.post with a fake that returns queued replies in order."""
    calls: list[dict] = []
    replies: list[dict] = []

    def fake_post(url, json, timeout):
        calls.append({"url": url, "body": json, "timeout": timeout})
        return httpx.Response(200, json=replies.pop(0), request=httpx.Request("POST", url))

    monkeypatch.setattr(client.httpx, "post", fake_post)
    return calls, replies


def chat_reply(content: str) -> dict:
    return {"message": {"role": "assistant", "content": content}}


def test_chat_text_sends_expected_request(fake_ollama):
    calls, replies = fake_ollama
    replies.append(chat_reply("  Hello there.  "))

    assert client.chat_text([{"role": "user", "content": "hi"}], temperature=0.4) == "Hello there."
    body = calls[0]["body"]
    assert calls[0]["url"] == "http://localhost:11434/api/chat"
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.4, "num_ctx": 8192}
    assert calls[0]["timeout"] == 300


def test_chat_json_valid_first_try(fake_ollama):
    calls, replies = fake_ollama
    replies.append(chat_reply(json.dumps({"title": "Engineer", "years": 5})))

    result = client.chat_json([{"role": "user", "content": "x"}], Answer, temperature=0.1)
    assert result == Answer(title="Engineer", years=5)
    assert calls[0]["body"]["format"] == Answer.model_json_schema()


def test_chat_json_retries_once_then_succeeds(fake_ollama):
    calls, replies = fake_ollama
    replies.append(chat_reply("not json"))
    replies.append(chat_reply(json.dumps({"title": "Engineer", "years": 5})))

    result = client.chat_json([{"role": "user", "content": "x"}], Answer, temperature=0.1)
    assert result.years == 5
    assert len(calls) == 2
    assert "invalid" in calls[1]["body"]["messages"][-1]["content"]


def test_chat_json_fails_after_two_invalid_replies(fake_ollama):
    _, replies = fake_ollama
    replies.append(chat_reply(json.dumps({"title": "Engineer"})))
    replies.append(chat_reply(json.dumps({"years": "many"})))

    with pytest.raises(client.LLMError, match="invalid JSON twice"):
        client.chat_json([{"role": "user", "content": "x"}], Answer, temperature=0.1)


def test_embed_returns_vector(fake_ollama):
    calls, replies = fake_ollama
    replies.append({"embeddings": [[0.1, 0.2, 0.3, 0.4]]})

    assert client.embed("hello") == [0.1, 0.2, 0.3, 0.4]
    assert calls[0]["body"] == {"model": "bge-m3", "input": "hello"}


def test_embed_wrong_dimension_raises(fake_ollama):
    _, replies = fake_ollama
    replies.append({"embeddings": [[0.1, 0.2]]})

    with pytest.raises(client.LLMError, match="expected 4"):
        client.embed("hello")


def test_unreachable_ollama_raises_llm_error(monkeypatch):
    def broken_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(client.httpx, "post", broken_post)
    with pytest.raises(client.LLMError, match="Could not reach Ollama"):
        client.embed("hello")

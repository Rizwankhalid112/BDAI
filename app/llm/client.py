"""Small wrapper around the LOCAL Ollama HTTP API.

Three functions are all the rest of the app needs:

    chat_text(messages, temperature)             -> str
    chat_json(messages, ResponseModel, temperature) -> ResponseModel instance
    embed(text)                                   -> list[float]  (1024 numbers for bge-m3)

Hard rule: this is the only place that makes AI calls, and it only talks to
the Ollama URL from config, which config.py guarantees is a local host.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import get_settings

logger = logging.getLogger(__name__)

Message = dict[str, str]
ModelT = TypeVar("ModelT", bound=BaseModel)


class LLMError(Exception):
    """Raised when Ollama is unreachable, times out, or returns unusable output.

    Callers (the worker) must mark the job as failed with this message —
    never replace the output with defaults.
    """


def _post(path: str, body: dict) -> dict:
    """Send one POST request to Ollama and return the JSON response."""
    settings = get_settings()
    url = f"{settings.ollama_url}{path}"
    try:
        response = httpx.post(url, json=body, timeout=settings.llm_timeout_seconds)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise LLMError(f"Ollama timed out after {settings.llm_timeout_seconds}s ({path})") from exc
    except httpx.HTTPStatusError as exc:
        raise LLMError(f"Ollama returned HTTP {exc.response.status_code}: {exc.response.text[:300]}") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"Could not reach Ollama at {settings.ollama_url}: {exc}") from exc
    return response.json()


def _chat(messages: list[Message], temperature: float, json_schema: dict | None = None) -> str:
    """Call /api/chat once and return the assistant's text."""
    settings = get_settings()
    body: dict = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature, "num_ctx": settings.llm_num_ctx},
    }
    if json_schema is not None:
        body["format"] = json_schema

    started = time.monotonic()
    data = _post("/api/chat", body)
    logger.info("chat model=%s took %.1fs", settings.llm_model, time.monotonic() - started)

    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise LLMError(f"Unexpected response shape from Ollama: keys={list(data)}") from exc


def chat_text(messages: list[Message], temperature: float) -> str:
    """Ask the model for a plain-text reply (e.g. a cover letter)."""
    reply = _chat(messages, temperature).strip()
    if not reply:
        raise LLMError("Model returned an empty reply.")
    return reply


def chat_json(messages: list[Message], response_model: type[ModelT], temperature: float) -> ModelT:
    """Ask the model for JSON matching `response_model`, validated by Pydantic.

    If the first reply is not valid, we retry once and tell the model what was
    wrong. If the second reply is still invalid, we raise LLMError.
    """
    schema = response_model.model_json_schema()
    attempt_messages = list(messages)
    last_error = ""

    for attempt in (1, 2):
        reply = _chat(attempt_messages, temperature, json_schema=schema)
        try:
            return response_model.model_validate(json.loads(reply))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)[:500]
            logger.warning("chat_json attempt %d produced invalid output: %s", attempt, type(exc).__name__)
            attempt_messages = list(messages) + [
                {"role": "assistant", "content": reply},
                {"role": "user", "content": f"That output was invalid: {last_error}\n"
                                            "Reply again with ONLY valid JSON matching the schema."},
            ]

    raise LLMError(f"Model returned invalid JSON twice. Last error: {last_error}")


def embed(text: str) -> list[float]:
    """Turn text into a vector (list of numbers) using the embedding model."""
    settings = get_settings()
    if not text.strip():
        raise LLMError("Cannot embed empty text.")
    data = _post("/api/embed", {"model": settings.embed_model, "input": text})
    try:
        vector = data["embeddings"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected embed response from Ollama: keys={list(data)}") from exc
    if len(vector) != settings.embed_dim:
        raise LLMError(f"Embedding has {len(vector)} dimensions, expected {settings.embed_dim}.")
    return vector

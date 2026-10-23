"""OpenAI-compatible clients for VT ARC generation and configured embeddings."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

import config

T = TypeVar("T", bound=BaseModel)


def _is_retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    markers = (
        "rate limit",
        "429",
        "timeout",
        "timed out",
        "connection",
        "temporarily unavailable",
        "503",
        "502",
        "500",
    )
    return any(marker in message for marker in markers)


def _sleep_for_attempt(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 8))


class GenerationClient:
    """Small wrapper around the VT ARC OpenAI-compatible chat endpoint."""

    def __init__(self) -> None:
        self.base_url = config.env("OPEN_AI_ENDPOINT")
        self.api_key = config.env("OPEN_AI_API_KEY")
        self.model = config.env("OPEN_AI_MODEL")
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=config.LLM_TIMEOUT_SECONDS,
        )
        self.calls = 0

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = config.LLM_MAX_TOKENS,
    ) -> T:
        """Return a validated Pydantic object or raise after bounded retries."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: Exception | None = None

        for attempt in range(config.LLM_MAX_RETRIES):
            try:
                self.calls += 1
                completion = self.client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=schema,
                    temperature=0,
                    max_tokens=max_tokens,
                )
                message = completion.choices[0].message
                refusal = getattr(message, "refusal", None)
                if refusal:
                    raise ValueError(f"model refused: {refusal}")
                parsed = message.parsed
                if parsed is not None:
                    return parsed
                return _lenient_validate(schema, message.content or "")
            except ValidationError as exc:
                last_error = exc
                try:
                    return self._json_schema_fallback(messages, schema, max_tokens)
                except Exception as fallback_exc:
                    last_error = fallback_exc
            except Exception as exc:
                last_error = exc
                if not _is_retryable(exc):
                    break
            if attempt + 1 < config.LLM_MAX_RETRIES:
                _sleep_for_attempt(attempt)

        try:
            return self._json_schema_fallback(messages, schema, max_tokens, json_object=True)
        except Exception as exc:
            raise RuntimeError(f"structured generation failed: {type(exc).__name__}") from (
                last_error or exc
            )

    def _json_schema_fallback(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        max_tokens: int,
        *,
        json_object: bool = False,
    ) -> T:
        response_format: dict[str, object]
        if json_object:
            response_format = {"type": "json_object"}
            schema_json = json.dumps(schema.model_json_schema())
            messages = [
                messages[0],
                {
                    "role": "user",
                    "content": (
                        f"{messages[1]['content']}\n\n"
                        "Return ONLY a JSON object matching this schema:\n"
                        f"{schema_json}"
                    ),
                },
            ]
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                },
            }
        self.calls += 1
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_format=response_format,
            temperature=0,
            max_tokens=max_tokens,
        )
        content = completion.choices[0].message.content or ""
        return _lenient_validate(schema, content)


def _lenient_validate[T: BaseModel](schema: type[T], text: str) -> T:
    """Validate JSON, tolerating the VT ARC strict-mode stray-`{` quirk."""
    text = text.strip()
    try:
        return schema.model_validate_json(text)
    except ValidationError:
        first = text.find("{")
        second = text.find("{", first + 1) if first >= 0 else -1
        if second == -1:
            raise
        return schema.model_validate_json(text[second:])


class EmbeddingClient:
    """OpenAI-compatible embeddings with a native Ollama fallback."""

    def __init__(self) -> None:
        self.base_url = config.env("OLLAMA_ENDPOINT").rstrip("/")
        self.model = config.env("EMBED_MODEL")
        self.client = OpenAI(
            base_url=self.base_url,
            api_key="ollama",
            timeout=config.EMBED_TIMEOUT_SECONDS,
        )
        self._dimension: int | None = None

    @property
    def dimension(self) -> int | None:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._embed_openai(texts)
        if vectors:
            if self._dimension is None:
                self._dimension = len(vectors[0])
            return vectors
        vectors = self._embed_native(texts)
        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
        return vectors

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self.client.embeddings.create(model=self.model, input=texts)
            return [item.embedding for item in response.data]
        except Exception:
            return []

    def _embed_native(self, texts: list[str]) -> list[list[float]]:
        base = self.base_url
        if base.endswith("/v1"):
            base = base[:-3]
        url = base.rstrip("/") + "/api/embed"
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=config.EMBED_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError("embedding endpoint unavailable") from exc
        if "embeddings" in data:
            return data["embeddings"]
        if "embedding" in data:
            return [data["embedding"]]
        raise RuntimeError("embedding endpoint returned an unexpected payload")

"""OpenAI-compatible clients with bounded retries, budgets, and safe failures."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

import config
from src.redaction import get_logger, redact_secrets, safe_exception_message

T = TypeVar("T", bound=BaseModel)
logger = get_logger("vt_team.llm")


class LLMError(RuntimeError):
    """Base class for bounded LLM failures."""


class LLMBudgetExceeded(LLMError):
    """Raised when a run exhausts its call, token, or wall-clock budget."""


class LLMCircuitOpen(LLMError):
    """Raised while the process-wide circuit breaker is open."""


class LLMRefusalError(LLMError):
    """Raised when the model explicitly refuses the request."""


@dataclass
class LLMCallBudget:
    """Mutable per-run budget shared by every LLM stage in one team build."""

    max_calls: int = config.LLM_MAX_CALLS_PER_RUN
    max_tokens: int = config.LLM_MAX_TOKENS_PER_RUN
    max_wall_seconds: float = config.LLM_MAX_WALL_SECONDS
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    started_at: float = field(default_factory=time.monotonic)
    exceeded_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def ensure_available(self) -> None:
        if self.max_calls > 0 and self.calls >= self.max_calls:
            self.exceeded_reason = f"call budget {self.max_calls} exhausted"
            raise LLMBudgetExceeded(self.exceeded_reason)
        if self.max_tokens > 0 and self.total_tokens >= self.max_tokens:
            self.exceeded_reason = f"token budget {self.max_tokens} exhausted"
            raise LLMBudgetExceeded(self.exceeded_reason)
        if self.max_wall_seconds > 0 and self.elapsed_seconds >= self.max_wall_seconds:
            self.exceeded_reason = (
                f"wall-clock budget {self.max_wall_seconds:.0f}s exhausted"
            )
            raise LLMBudgetExceeded(self.exceeded_reason)

    def record_usage(self, *, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += max(0, int(prompt_tokens))
        self.completion_tokens += max(0, int(completion_tokens))

    def snapshot(self) -> dict[str, int | float | str | bool | None]:
        return {
            "max_calls": self.max_calls,
            "calls": self.calls,
            "max_tokens": self.max_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "max_wall_seconds": self.max_wall_seconds,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "exceeded": self.exceeded_reason is not None,
            "exceeded_reason": self.exceeded_reason,
        }


@dataclass
class CircuitBreaker:
    """Small failure-count breaker to stop hammering an unavailable service."""

    failure_threshold: int = config.LLM_CIRCUIT_FAILURE_THRESHOLD
    cooldown_seconds: float = config.LLM_CIRCUIT_COOLDOWN_SECONDS
    failures: int = 0
    opened_at: float | None = None
    now_fn: Callable[[], float] = time.monotonic

    def _reset_if_cooled(self) -> None:
        if (
            self.opened_at is not None
            and self.now_fn() - self.opened_at >= self.cooldown_seconds
        ):
            self.failures = 0
            self.opened_at = None

    def ensure_closed(self) -> None:
        self._reset_if_cooled()
        if self.opened_at is not None:
            remaining = max(
                0.0, self.cooldown_seconds - (self.now_fn() - self.opened_at)
            )
            raise LLMCircuitOpen(
                f"LLM circuit open after {self.failures} failures; "
                f"cooldown {remaining:.0f}s remaining"
            )

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self._reset_if_cooled()
        self.failures += 1
        if self.failure_threshold > 0 and self.failures >= self.failure_threshold:
            self.opened_at = self.now_fn()

    def snapshot(self) -> dict[str, int | float | str | None]:
        self._reset_if_cooled()
        return {
            "failures": self.failures,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "state": "open" if self.opened_at is not None else "closed",
        }


def _status_code(exc: BaseException) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(exc, "code", None),
    ):
        try:
            if candidate is not None:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (LLMBudgetExceeded, LLMCircuitOpen, LLMRefusalError)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = _status_code(exc)
    if status is not None:
        return status in {408, 409, 425, 429, 500, 502, 503, 504}
    message = f"{type(exc).__name__} {exc}".lower()
    markers = (
        "rate limit",
        "429",
        "timeout",
        "timed out",
        "connection",
        "temporarily unavailable",
        "overloaded",
        "503",
        "502",
        "500",
    )
    return any(marker in message for marker in markers)


def _retry_after_seconds(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")
    except (AttributeError, TypeError):
        return None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _usage_tokens(completion: object) -> tuple[int, int]:
    usage = getattr(completion, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    return int(prompt_tokens), int(completion_tokens)


class GenerationClient:
    """Bounded wrapper around the VT ARC OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        *,
        client: object | None = None,
        model: str | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        circuit: CircuitBreaker | None = None,
    ) -> None:
        if client is not None:
            self.base_url = config.optional_env("OPEN_AI_ENDPOINT") or "injected"
            self.model = model or config.optional_env("OPEN_AI_MODEL") or "test-model"
            self.client = client
        else:
            self.base_url = config.env("OPEN_AI_ENDPOINT")
            self.model = config.env("OPEN_AI_MODEL")
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=config.env("OPEN_AI_API_KEY"),
                timeout=config.LLM_TIMEOUT_SECONDS,
                max_retries=0,
            )
        self._sleep = sleep_fn or time.sleep
        self.circuit = circuit or CircuitBreaker()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @property
    def token_usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
        }

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = config.LLM_MAX_TOKENS,
        budget: LLMCallBudget | None = None,
    ) -> T:
        """Return a validated Pydantic object or raise after bounded attempts."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: BaseException | None = None
        fallback_allowed = True
        attempts = max(1, config.LLM_MAX_RETRIES)

        for attempt in range(attempts):
            retry_after: float | None = None
            try:
                completion = self._request(
                    lambda: self.client.chat.completions.parse(
                        model=self.model,
                        messages=messages,
                        response_format=schema,
                        temperature=0,
                        max_tokens=max_tokens,
                        timeout=self._timeout_for(budget),
                    ),
                    budget=budget,
                )
                message = completion.choices[0].message
                refusal = getattr(message, "refusal", None)
                if refusal:
                    raise LLMRefusalError(
                        f"model refused: {redact_secrets(refusal)}"
                    )
                parsed = getattr(message, "parsed", None)
                if parsed is not None:
                    self.circuit.record_success()
                    return parsed
                result = _lenient_validate(schema, getattr(message, "content", "") or "")
                self.circuit.record_success()
                return result
            except LLMRefusalError:
                self.circuit.record_failure()
                raise
            except (LLMBudgetExceeded, LLMCircuitOpen):
                raise
            except ValidationError as exc:
                last_error = exc
                try:
                    result = self._json_schema_fallback(
                        messages,
                        schema,
                        max_tokens,
                        budget=budget,
                    )
                    self.circuit.record_success()
                    return result
                except (LLMBudgetExceeded, LLMCircuitOpen, LLMRefusalError):
                    raise
                except Exception as fallback_exc:
                    last_error = fallback_exc
                    if not (
                        _is_retryable(fallback_exc)
                        or isinstance(fallback_exc, ValidationError)
                    ):
                        fallback_allowed = False
                        break
            except Exception as exc:
                last_error = exc
                if not _is_retryable(exc):
                    fallback_allowed = False
                    break
                retry_after = _retry_after_seconds(exc)
            if attempt + 1 < attempts:
                self._sleep_for_attempt(attempt, retry_after)

        if not fallback_allowed:
            self.circuit.record_failure()
            detail = safe_exception_message(last_error or RuntimeError("LLM failure"))
            logger.error("structured generation failed: %s", detail)
            raise RuntimeError(
                f"structured generation failed: {type(last_error).__name__}"
            ) from last_error

        try:
            result = self._json_schema_fallback(
                messages,
                schema,
                max_tokens,
                json_object=True,
                budget=budget,
            )
            self.circuit.record_success()
            return result
        except (LLMBudgetExceeded, LLMCircuitOpen, LLMRefusalError):
            raise
        except Exception as exc:
            self.circuit.record_failure()
            detail = safe_exception_message(last_error or exc)
            logger.error("structured generation failed: %s", detail)
            raise RuntimeError(
                f"structured generation failed: {type(exc).__name__}"
            ) from (last_error or exc)

    @staticmethod
    def _timeout_for(budget: LLMCallBudget | None) -> float:
        if budget is None or budget.max_wall_seconds <= 0:
            return float(config.LLM_TIMEOUT_SECONDS)
        remaining = budget.max_wall_seconds - budget.elapsed_seconds
        return max(1.0, min(float(config.LLM_TIMEOUT_SECONDS), remaining))

    def _sleep_for_attempt(
        self, attempt: int, retry_after: float | None = None
    ) -> None:
        if retry_after is not None:
            delay = min(max(0.0, retry_after), config.LLM_RETRY_MAX_SECONDS)
        else:
            delay = min(
                config.LLM_RETRY_BASE_SECONDS * (2**attempt)
                + random.uniform(0, config.LLM_RETRY_JITTER_SECONDS),
                config.LLM_RETRY_MAX_SECONDS,
            )
        self._sleep(delay)

    def _request(
        self,
        callable_fn: Callable[[], object],
        *,
        budget: LLMCallBudget | None,
    ) -> object:
        self.circuit.ensure_closed()
        if budget is not None:
            budget.ensure_available()
        self.calls += 1
        if budget is not None:
            budget.calls += 1
        try:
            completion = callable_fn()
        except Exception as exc:
            logger.warning("LLM request failed: %s", safe_exception_message(exc))
            raise
        prompt_tokens, completion_tokens = _usage_tokens(completion)
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        if budget is not None:
            budget.record_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        return completion

    def _json_schema_fallback(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        max_tokens: int,
        *,
        json_object: bool = False,
        budget: LLMCallBudget | None = None,
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
        completion = self._request(
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format=response_format,
                temperature=0,
                max_tokens=max_tokens,
                timeout=self._timeout_for(budget),
            ),
            budget=budget,
        )
        content = completion.choices[0].message.content or ""
        return _lenient_validate(schema, content)


class BudgetedLLMClient:
    """Per-run facade that enforces one shared call/token/wall budget."""

    def __init__(
        self,
        inner: object,
        budget: LLMCallBudget | None = None,
    ) -> None:
        self.inner = inner
        self.budget = budget or LLMCallBudget()
        self.model = getattr(inner, "model", "unknown")

    @property
    def calls(self) -> int:
        return self.budget.calls

    @property
    def token_usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.budget.prompt_tokens,
            "completion_tokens": self.budget.completion_tokens,
            "total_tokens": self.budget.total_tokens,
        }

    @property
    def budget_snapshot(self) -> dict[str, int | float | str | bool | None]:
        return self.budget.snapshot()

    def complete_structured(self, **kwargs: object) -> object:
        return self.inner.complete_structured(**kwargs, budget=self.budget)


def ensure_budgeted(client: object | None) -> object | None:
    """Wrap a raw client once; leave an existing per-run wrapper untouched."""
    if client is None or isinstance(client, BudgetedLLMClient):
        return client
    return BudgetedLLMClient(client)


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

    def __init__(
        self,
        *,
        client: object | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or config.env("OLLAMA_ENDPOINT")).rstrip("/")
        self.model = model or config.env("EMBED_MODEL")
        self.client = client or OpenAI(
            base_url=self.base_url,
            api_key="ollama",
            timeout=config.EMBED_TIMEOUT_SECONDS,
            max_retries=0,
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
        except Exception as exc:
            logger.warning("Embedding API failed: %s", safe_exception_message(exc))
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

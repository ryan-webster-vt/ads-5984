# Offline test module: Failure injection for Grants.gov, Elsevier, LLM, embedding, and budget paths.
from __future__ import annotations

import urllib.error
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import config
from src.grants.client import GrantsError, _post_json
from src.llm import (
    CircuitBreaker,
    EmbeddingClient,
    GenerationClient,
    LLMBudgetExceeded,
    LLMCallBudget,
    LLMCircuitOpen,
    LLMRefusalError,
)


class Payload(BaseModel):
    value: int


class FakeStatusError(Exception):
    def __init__(self, status_code: int, message: str = "request failed") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(headers={})


class FakeRateLimitError(FakeStatusError):
    def __init__(self, retry_after: str = "0.25") -> None:
        super().__init__(429, "rate limit exceeded")
        self.response = SimpleNamespace(headers={"Retry-After": retry_after})


def _response(
    *,
    parsed: BaseModel | None = None,
    content: str | None = None,
    refusal: str | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> SimpleNamespace:
    message = SimpleNamespace(parsed=parsed, content=content, refusal=refusal)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )


class ScriptedCompletions:
    def __init__(self, parse_steps, create_steps=None) -> None:
        self.parse_steps = list(parse_steps)
        self.create_steps = list(create_steps or [])
        self.parse_calls = 0
        self.create_calls = 0

    def _next(self, steps, counter_name: str):
        setattr(self, counter_name, getattr(self, counter_name) + 1)
        step = steps[0] if len(steps) == 1 else steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def parse(self, **kwargs):
        return self._next(self.parse_steps, "parse_calls")

    def create(self, **kwargs):
        return self._next(self.create_steps, "create_calls")


class FakeOpenAI:
    def __init__(self, completions: ScriptedCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _client(completions, *, sleep=None, circuit=None) -> GenerationClient:
    return GenerationClient(
        client=FakeOpenAI(completions),
        model="fake-model",
        sleep_fn=sleep or (lambda _seconds: None),
        circuit=circuit,
    )


def _complete(client, *, budget=None) -> Payload:
    return client.complete_structured(
        system="system",
        user="user",
        schema=Payload,
        budget=budget,
    )


# Verifies: 429 retry uses retry after and records usage.
def test_429_retry_uses_retry_after_and_records_usage():
    sleeps: list[float] = []
    completions = ScriptedCompletions(
        [FakeRateLimitError("0.25"), _response(parsed=Payload(value=7))]
    )
    client = _client(completions, sleep=sleeps.append)
    assert _complete(client).value == 7
    assert sleeps == [0.25]
    assert client.calls == 2
    assert client.token_usage["total_tokens"] == 15


# Verifies: timeout is retryable.
def test_timeout_is_retryable():
    completions = ScriptedCompletions(
        [TimeoutError("timed out"), _response(parsed=Payload(value=3))]
    )
    client = _client(completions)
    assert _complete(client).value == 3
    assert client.calls == 2


# Verifies: 500 exhausts bounded attempts and opens circuit.
def test_500_exhausts_bounded_attempts_and_opens_circuit():
    completions = ScriptedCompletions(
        [FakeStatusError(500, "server error")],
        [FakeStatusError(500, "server error")],
    )
    circuit = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
    client = _client(completions, circuit=circuit)
    with pytest.raises(RuntimeError):
        _complete(client)
    assert client.calls == config.LLM_MAX_RETRIES + 1
    assert circuit.failures == 1
    assert circuit.opened_at is not None


# Verifies: 401 is not retried or fallback called.
def test_401_is_not_retried_or_fallback_called():
    completions = ScriptedCompletions([FakeStatusError(401, "unauthorized")])
    client = _client(completions)
    with pytest.raises(RuntimeError):
        _complete(client)
    assert client.calls == 1
    assert completions.create_calls == 0


# Verifies: model refusal is not retried.
def test_model_refusal_is_not_retried():
    completions = ScriptedCompletions(
        [_response(parsed=None, content="", refusal="cannot help")]
    )
    client = _client(completions)
    with pytest.raises(LLMRefusalError):
        _complete(client)
    assert client.calls == 1
    assert completions.create_calls == 0


# Verifies: malformed JSON stray brace and schema fallback.
def test_malformed_json_stray_brace_and_schema_fallback():
    brace = ScriptedCompletions(
        [_response(parsed=None, content='{\n{"value": 7}')]
    )
    assert _complete(_client(brace)).value == 7
    assert brace.create_calls == 0

    schema = ScriptedCompletions(
        [_response(parsed=None, content='{"wrong": 1}')],
        [_response(parsed=None, content='{"value": 4}')],
    )
    assert _complete(_client(schema)).value == 4
    assert schema.create_calls == 1


# Verifies: invalid schema raises clean runtime error.
def test_invalid_schema_raises_clean_runtime_error():
    completions = ScriptedCompletions(
        [_response(parsed=None, content='{"wrong": 1}')],
        [_response(parsed=None, content='{"wrong": 1}')],
    )
    client = _client(completions)
    with pytest.raises(RuntimeError, match="structured generation failed"):
        _complete(client)
    assert client.calls == config.LLM_MAX_RETRIES * 2 + 1


# Verifies: call budget stops before second request.
def test_call_budget_stops_before_second_request():
    completions = ScriptedCompletions(
        [FakeStatusError(500, "server error")]
    )
    budget = LLMCallBudget(max_calls=1, max_tokens=0, max_wall_seconds=0)
    client = _client(completions)
    with pytest.raises(LLMBudgetExceeded):
        _complete(client, budget=budget)
    assert client.calls == 1
    assert budget.calls == 1
    assert budget.snapshot()["exceeded"] is True


# Verifies: circuit breaker cooldown recovers.
def test_circuit_breaker_cooldown_recovers():
    clock = [1000.0]
    circuit = CircuitBreaker(
        failure_threshold=1,
        cooldown_seconds=10,
        now_fn=lambda: clock[0],
    )
    failing = _client(
        ScriptedCompletions([FakeStatusError(401, "unauthorized")]),
        circuit=circuit,
    )
    with pytest.raises(RuntimeError):
        _complete(failing)

    recovered = _client(
        ScriptedCompletions([_response(parsed=Payload(value=9))]),
        circuit=circuit,
    )
    with pytest.raises(LLMCircuitOpen):
        _complete(recovered)

    clock[0] += 11
    assert _complete(recovered).value == 9
    assert circuit.opened_at is None


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self.body


# Verifies: grants 429 retry honors retry after.
def test_grants_429_retry_honors_retry_after(monkeypatch):
    attempts: list[int] = []
    sleeps: list[float] = []

    def fake_urlopen(request, timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limit",
                {"Retry-After": "0.5"},
                None,
            )
        return _FakeHTTPResponse(b'{"errorcode": 0, "data": {"ok": true}}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = _post_json(
        "https://example.test/api",
        {},
        timeout=1,
        retries=1,
        sleep=sleeps.append,
    )
    assert result["data"]["ok"] is True
    assert sleeps == [0.5]
    assert len(attempts) == 2


# Verifies: grants 500 and timeout fail cleanly.
def test_grants_500_and_timeout_fail_cleanly(monkeypatch):
    def always_500(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            500,
            "server error",
            {},
            None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", always_500)
    with pytest.raises(GrantsError):
        _post_json("https://example.test/api", {}, timeout=1, retries=1)

    def always_timeout(request, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", always_timeout)
    with pytest.raises(GrantsError):
        _post_json("https://example.test/api", {}, timeout=1, retries=1)


# Verifies: embedding failure raises clear runtime error.
def test_embedding_failure_raises_clear_runtime_error(monkeypatch):
    class FailingEmbeddings:
        def create(self, **kwargs):
            raise TimeoutError("timed out")

    fake_client = SimpleNamespace(embeddings=FailingEmbeddings())
    client = EmbeddingClient(
        client=fake_client,
        base_url="http://127.0.0.1:1/v1",
        model="fake-embed",
    )

    def unavailable(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    with pytest.raises(RuntimeError, match="embedding endpoint unavailable"):
        client.embed(["text"])


# Verifies: per request timeout respects remaining wall budget.
def test_per_request_timeout_respects_remaining_wall_budget():
    import time

    budget = LLMCallBudget(max_calls=0, max_tokens=0, max_wall_seconds=30)
    budget.started_at = time.monotonic() - 26
    assert 1.0 <= GenerationClient._timeout_for(budget) <= 5.0
    assert GenerationClient._timeout_for(None) == config.LLM_TIMEOUT_SECONDS


# Verifies: token and wall budgets stop before next request.
def test_token_and_wall_budgets_stop_before_next_request():
    import time

    token_budget = LLMCallBudget(max_calls=0, max_tokens=10, max_wall_seconds=0)
    token_budget.record_usage(prompt_tokens=6, completion_tokens=4)
    with pytest.raises(LLMBudgetExceeded, match="token budget"):
        token_budget.ensure_available()

    wall_budget = LLMCallBudget(max_calls=0, max_tokens=0, max_wall_seconds=5)
    wall_budget.started_at = time.monotonic() - 6
    with pytest.raises(LLMBudgetExceeded, match="wall-clock budget"):
        wall_budget.ensure_available()
    assert wall_budget.snapshot()["exceeded"] is True


class FakeLengthFinishError(Exception):
    # Mirrors the OpenAI SDK error raised when a reasoning model exhausts the
    # completion budget on thinking before emitting parsable JSON.
    def __init__(self) -> None:
        super().__init__(
            "Could not parse response content as the length limit was reached "
            "- completion_tokens=8000"
        )


class FakeStream:
    # Minimal stand-in for an SDK streamed completion iterator.
    def __init__(self, chunks) -> None:
        self._chunks = list(chunks)

    def __iter__(self):
        return iter(self._chunks)


def _stream_chunk(content: str | None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


# Verifies: length exhaustion routes to the streaming fallback.
def test_length_finish_error_uses_streaming_fallback():
    completions = ScriptedCompletions(
        [FakeLengthFinishError(), _response(parsed=Payload(value=11))],
        [FakeStream([_stream_chunk('{"value":'), _stream_chunk(" 11}")])],
    )
    client = _client(completions)
    assert _complete(client).value == 11
    # The buffered attempt failed once, then one streamed request succeeded.
    assert completions.parse_calls == 1
    assert completions.create_calls == 1


# Verifies: persistent length exhaustion fails cleanly after bounded attempts.
def test_persistent_length_exhaustion_falls_back_then_fails_cleanly():
    completions = ScriptedCompletions(
        [FakeLengthFinishError()],
        # Truncated stream content cannot validate, so every streamed attempt
        # fails as well; the single scripted step repeats for each attempt.
        [FakeStream([_stream_chunk('{"trunca')])],
    )
    client = _client(completions)
    with pytest.raises(RuntimeError, match="structured generation failed"):
        _complete(client)
    # Every buffered retry triggers one streamed attempt, plus the final
    # JSON-object fallback request.
    assert completions.parse_calls == config.LLM_MAX_RETRIES
    assert completions.create_calls == config.LLM_MAX_RETRIES + 1


# Verifies: buffered requests are clamped to the proxy's non-streaming cap.
def test_buffered_requests_are_clamped_to_proxy_cap():
    recorded: list[dict] = []

    class RecordingCompletions:
        def parse(self, **kwargs):
            recorded.append(kwargs)
            return _response(parsed=Payload(value=3))

        def create(self, **kwargs):
            recorded.append(kwargs)
            return _response(parsed=Payload(value=3))

    client = GenerationClient(
        client=FakeOpenAI(RecordingCompletions()),
        model="fake-model",
        sleep_fn=lambda _seconds: None,
    )
    client.complete_structured(
        system="s", user="u", schema=Payload, max_tokens=config.LLM_MAX_TOKENS
    )
    assert all(
        kwargs["max_tokens"] <= config.LLM_BUFFERED_MAX_TOKENS for kwargs in recorded
    )

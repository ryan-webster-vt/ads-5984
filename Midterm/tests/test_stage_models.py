# Offline test module: Per-stage generation model resolution and plumbing.
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import config
from models import (
    LLMRerankBatch,
    LLMRerankItem,
    LLMResearchQuery,
    Opportunity,
    ResearchQuery,
    RetrievedEvidence,
)
from src.llm import GenerationClient
from src.retrieval.optimize import optimize_query
from src.retrieval.rerank import rerank_evidence


class Payload(BaseModel):
    value: int


class RecordingCompletions:
    def __init__(self, response) -> None:
        self.response = response
        self.parse_kwargs: list[dict] = []
        self.create_kwargs: list[dict] = []

    def parse(self, **kwargs):
        self.parse_kwargs.append(kwargs)
        return self.response

    def create(self, **kwargs):
        self.create_kwargs.append(kwargs)
        return self.response


def _sdk_response(parsed: BaseModel) -> SimpleNamespace:
    message = SimpleNamespace(parsed=parsed, content=None, refusal=None)
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


# Verifies: stage model falls back to None (client default) when unset.
def test_stage_model_defaults_to_none(monkeypatch):
    for stage in config.LLM_STAGES:
        monkeypatch.delenv(f"OPEN_AI_{stage.upper()}_MODEL", raising=False)
        assert config.stage_model(stage) is None


# Verifies: per-stage overrides are read from the environment.
def test_stage_model_reads_override(monkeypatch):
    monkeypatch.setenv("OPEN_AI_RERANK_MODEL", "gpt-oss-120b")
    assert config.stage_model("rerank") == "gpt-oss-120b"
    with pytest.raises(ValueError):
        config.stage_model("not-a-stage")


# Verifies: stage_models resolves unset stages to the client default.
def test_stage_models_resolve_default(monkeypatch):
    for stage in config.LLM_STAGES:
        monkeypatch.delenv(f"OPEN_AI_{stage.upper()}_MODEL", raising=False)
    resolved = config.stage_models("GLM-5.3")
    assert set(resolved) == set(config.LLM_STAGES)
    assert all(model == "GLM-5.3" for model in resolved.values())


# Verifies: the model override reaches the SDK call, and None keeps the default.
def test_client_passes_model_override_to_sdk():
    completions = RecordingCompletions(_sdk_response(Payload(value=1)))
    client = GenerationClient(
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        model="default-model",
    )
    client.complete_structured(
        system="s", user="u", schema=Payload, model="stage-model"
    )
    client.complete_structured(system="s", user="u", schema=Payload)
    assert [kwargs["model"] for kwargs in completions.parse_kwargs] == [
        "stage-model",
        "default-model",
    ]


class RecordingStageClient:
    def __init__(self, output) -> None:
        self.output = output
        self.kwargs: list[dict] = []

    def complete_structured(self, **kwargs):
        self.kwargs.append(kwargs)
        return self.output


# Verifies: query optimization pins its configured stage model.
def test_optimize_query_uses_stage_model(monkeypatch):
    monkeypatch.setenv("OPEN_AI_OPTIMIZE_MODEL", "gpt-oss-120b")
    client = RecordingStageClient(
        LLMResearchQuery(
            problem_statement="Lake carbon cycling.",
            search_queries=["lake carbon"],
        )
    )
    opportunity = Opportunity(
        opportunity_id="opp", title="LTREB", agency="NSF", description="Freshwater."
    )
    optimize_query(opportunity, "", llm_client=client)
    assert client.kwargs[0]["model"] == "gpt-oss-120b"


# Verifies: rerank batches pin the configured stage model.
def test_rerank_uses_stage_model(monkeypatch):
    monkeypatch.setenv("OPEN_AI_RERANK_MODEL", "gpt-oss-120b")
    client = RecordingStageClient(
        LLMRerankBatch(items=[LLMRerankItem(evidence_id="e1", score=5)])
    )
    evidence = [
        RetrievedEvidence(
            evidence_id="e1",
            text="ecology climate",
            metadata={"faculty_ids_csv": "1", "eid": "P1", "title": "T"},
            similarity=0.8,
        )
    ]
    query = ResearchQuery(
        problem_statement="Ecology.",
        search_queries=["ecology"],
        required_expertise=["ecology"],
    )
    rerank_evidence(evidence, query, llm_client=client, k_out=1)
    assert client.kwargs and client.kwargs[0]["model"] == "gpt-oss-120b"

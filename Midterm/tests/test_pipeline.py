from __future__ import annotations

import json
from types import SimpleNamespace

import config
from models import (
    Faculty,
    LLMTeamMember,
    LLMTeamProposal,
    LLMVerification,
    Opportunity,
    ResearchQuery,
    RetrievedEvidence,
    UserConstraints,
)
from src.llm import BudgetedLLMClient
from src.team.pipeline import build_team_run
from src.team.scoring import score_faculty_candidates


def _faculty() -> dict[str, Faculty]:
    names = [
        ("1", "Carey, Cayelan C.", "Biological Sciences", "Academic Tenure"),
        ("2", "Smith, John", "Chemistry", "Academic Tenure"),
        ("3", "Jones, Amy", "Physics", "Academic Tenure"),
    ]
    return {
        scopus_id: Faculty(
            scopus_id=scopus_id,
            author_name=name,
            department=department,
            tenure=tenure,
            h_index=20,
            h5_index=10,
            citation_count=1000,
        )
        for scopus_id, name, department, tenure in names
    }


def _evidence() -> list[RetrievedEvidence]:
    return [
        RetrievedEvidence(
            evidence_id=f"e{faculty_id}",
            text=f"Ecology climate evidence for faculty {faculty_id}.",
            metadata={
                "chunk_type": "publication",
                "faculty_ids_csv": faculty_id,
                "eid": f"E{faculty_id}",
                "year": 2024,
                "title": f"Ecology study {faculty_id}",
                "final_score": 0.9 - int(faculty_id) * 0.05,
            },
            similarity=0.9 - int(faculty_id) * 0.05,
        )
        for faculty_id in ("1", "2", "3")
    ]


def _query() -> ResearchQuery:
    return ResearchQuery(
        problem_statement="Ecology and climate.",
        required_expertise=["ecology", "climate"],
        search_queries=["ecology climate"],
        prompt_version="optimize_query-v1",
    )


def _manifest() -> SimpleNamespace:
    return SimpleNamespace(
        index_version="full-v1",
        collection_name="research_full_v1",
        embedding_model="qwen3-embedding:8b",
        corpus_sha256="abc123",
        chunker_version="1",
        schema_version="2",
    )


class ValidBudgetedFake:
    def __init__(self) -> None:
        self.model = "fake-model"

    def complete_structured(self, **kwargs):
        budget = kwargs.get("budget")
        if budget is not None:
            budget.ensure_available()
            budget.calls += 1
            budget.record_usage(prompt_tokens=100, completion_tokens=20)
        schema = kwargs["schema"]
        if schema is LLMTeamProposal:
            return LLMTeamProposal(
                members=[
                    LLMTeamMember(
                        scopus_id=str(index),
                        role="Proposed PI" if index == 1 else "Domain Expert",
                        rationale=f"Ecology climate evidence for faculty {index}.",
                        evidence_ids=[f"e{index}"],
                    )
                    for index in (1, 2, 3)
                ],
                proposed_pi_id="1",
                team_rationale="Covers ecology and climate.",
            )
        if schema is LLMVerification:
            return LLMVerification(approved=True, issues=[])
        raise AssertionError("unexpected schema")


def _run(tmp_path, monkeypatch, llm_client):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    faculty = _faculty()
    evidence = _evidence()
    candidates = score_faculty_candidates(
        evidence,
        faculty,
        research_query=_query(),
        current_year=2026,
    )
    return build_team_run(
        run_id="pipeline-test",
        opportunity=Opportunity(opportunity_id="opp", title="Ecology grant"),
        focus="",
        research_query=_query(),
        constraints=UserConstraints(team_size=3),
        faculty_by_id=faculty,
        collection=object(),
        manifest=_manifest(),
        llm_client=llm_client,
        evidence=evidence,
        candidates=candidates,
        use_cache=False,
    )


def test_build_team_run_persists_metadata_for_baseline(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, None)
    path = tmp_path / "pipeline-test.json"
    assert path.exists()
    payload = json.loads(path.read_text())
    assert payload["metadata"]["run_id"] == "pipeline-test"
    assert payload["metadata"]["cache_enabled"] is False
    assert payload["metadata"]["llm_budget"] == {}
    assert payload["cache"] == {"enabled": False, "hit": False}
    assert result.metadata.final_team == [
        member.scopus_id for member in result.proposal.members
    ]


def test_build_team_run_records_budget_calls_and_tokens(tmp_path, monkeypatch):
    client = BudgetedLLMClient(ValidBudgetedFake())
    result = _run(tmp_path, monkeypatch, client)
    assert result.proposal.used_llm is True
    assert result.metadata.llm_calls == 2
    assert result.metadata.token_usage == {
        "prompt_tokens": 200,
        "completion_tokens": 40,
        "total_tokens": 240,
    }
    assert result.metadata.llm_budget["calls"] == 2
    assert result.metadata.llm_budget["total_tokens"] == 240
    payload = json.loads((tmp_path / "pipeline-test.json").read_text())
    assert payload["metadata"]["llm_calls"] == 2
    assert payload["metadata"]["runtime_budget_ms"] == int(
        config.RUNTIME_BUDGETS["team_build_p95_ms"]
    )


def test_build_team_run_records_validation_issues_from_fallback(tmp_path, monkeypatch):
    class InvalidFake:
        model = "fake-model"

        def complete_structured(self, **kwargs):
            if kwargs["schema"] is LLMTeamProposal:
                return LLMTeamProposal(
                    members=[
                        LLMTeamMember(
                            scopus_id="99999999",
                            role="Proposed PI",
                            rationale="Injected member.",
                            evidence_ids=["e1"],
                        )
                    ],
                    proposed_pi_id="99999999",
                )
            return LLMVerification(approved=True, issues=[])

    result = _run(tmp_path, monkeypatch, InvalidFake())
    assert result.proposal.used_llm is False
    assert result.metadata.validation_issues
    assert result.metadata.errors
    assert result.metadata.fallback_used is True

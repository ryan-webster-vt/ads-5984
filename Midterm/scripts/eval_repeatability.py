#!/usr/bin/env python3
"""Run uncached repeatability and runtime-budget evaluation on a fixed fixture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from models import UserConstraints  # noqa: E402
from src.evaluation import RunObservation, evaluate_observations  # noqa: E402
from src.grants.client import load_opportunity_fixture  # noqa: E402
from src.grants.store import load_database  # noqa: E402
from src.llm import BudgetedLLMClient, GenerationClient, ensure_budgeted  # noqa: E402
from src.research.identity import build_eligible_faculty_index  # noqa: E402
from src.research.index import get_collection, load_manifest  # noqa: E402
from src.research.loaders import load_faculty  # noqa: E402
from src.retrieval.cache import (  # noqa: E402
    RetrievalCache,
    cache_context_from_manifest,
)
from src.retrieval.optimize import optimize_query  # noqa: E402
from src.team.pipeline import build_team_run, new_run_id  # noqa: E402
from src.team.verify import validate_proposal  # noqa: E402


def _opportunity(opportunity_id: str | None):
    if opportunity_id and config.OPPORTUNITIES_PATH.exists():
        database = load_database()
        for record in database.opportunities:
            if record.opportunity_id == opportunity_id:
                return record
        raise SystemExit(f"opportunity not found in local store: {opportunity_id}")
    return load_opportunity_fixture()


def _build_client(offline: bool):
    if offline:
        return None
    try:
        return GenerationClient()
    except Exception as exc:
        print(
            f"warning: live LLM unavailable ({type(exc).__name__}); "
            "continuing with the deterministic baseline",
            file=sys.stderr,
        )
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--opportunity-id",
        default=None,
        help="Use an opportunity from data/opportunities.json instead of the fixture.",
    )
    parser.add_argument(
        "--focus",
        default="",
        help="Optional focus text supplied to the query optimizer.",
    )
    parser.add_argument("--team-size", type=int, default=3)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip the LLM entirely; measure the deterministic pipeline.",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Allow retrieval-cache reuse. Evaluation defaults to uncached runs.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Explicit uncached evaluation (the default; accepted for clarity).",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete retrieval-cache entries before running.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write per-run metadata under data/runs/.",
    )
    parser.add_argument("--min-jaccard", type=float, default=0.8)
    parser.add_argument("--min-pi-agreement", type=float, default=0.8)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    manifest = load_manifest()
    if manifest is None:
        raise SystemExit("research index is missing; run scripts/build_index.py --rebuild")
    collection = get_collection()
    faculty_by_id = build_eligible_faculty_index(load_faculty())
    opportunity = _opportunity(args.opportunity_id)
    cache_context = cache_context_from_manifest(manifest)
    if args.clear_cache:
        removed = RetrievalCache(config.RETRIEVAL_CACHE_DIR).clear()
        print(f"cleared retrieval cache entries: {removed}")

    raw_client = _build_client(args.offline)
    observations: list[RunObservation] = []
    for index in range(1, args.runs + 1):
        run_id = f"eval-{new_run_id()}"
        budgeted = (
            BudgetedLLMClient(raw_client) if raw_client is not None else None
        )
        budgeted = ensure_budgeted(budgeted)
        research_query = optimize_query(
            opportunity,
            args.focus,
            llm_client=budgeted,
        )
        result = build_team_run(
            run_id=run_id,
            opportunity=opportunity,
            focus=args.focus,
            research_query=research_query,
            constraints=UserConstraints(team_size=args.team_size),
            faculty_by_id=faculty_by_id,
            collection=collection,
            manifest=manifest,
            llm_client=budgeted,
            use_cache=args.use_cache,
            cache_context=cache_context,
            persist=not args.no_persist,
        )
        issues = validate_proposal(
            result.proposal,
            candidates={
                candidate.scopus_id: candidate for candidate in result.candidates
            },
            evidence_by_id={
                item.evidence_id: item for item in result.evidence
            },
            faculty_by_id=faculty_by_id,
            expected_size=args.team_size,
            constraints=UserConstraints.model_validate(result.metadata.constraints),
        )
        observation = RunObservation(
            run_id=run_id,
            member_ids=[member.scopus_id for member in result.proposal.members],
            pi_id=result.proposal.proposed_pi_id,
            evidence_ids=[
                evidence_id
                for member in result.proposal.members
                for evidence_id in member.evidence_ids
            ],
            validator_status=result.proposal.validator_status,
            validation_issues=issues,
            latency_ms=result.metadata.latency_ms,
            llm_calls=result.metadata.llm_calls,
            total_tokens=result.metadata.token_usage.get("total_tokens", 0),
            fallback_used=result.metadata.fallback_used,
            cache_hit=bool(result.payload.get("cache", {}).get("hit")),
        )
        observations.append(observation)
        print(
            f"run {index}/{args.runs}: members={observation.member_ids} "
            f"pi={observation.pi_id} status={observation.validator_status} "
            f"latency_ms={observation.latency_ms} "
            f"llm_calls={observation.llm_calls} "
            f"cache_hit={observation.cache_hit} "
            f"validation_issues={len(observation.validation_issues)}"
        )

    summary = evaluate_observations(
        observations,
        min_jaccard=args.min_jaccard,
        min_pi_agreement=args.min_pi_agreement,
    )
    rendered = json.dumps(summary, indent=2)
    print(rendered)
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    return 0 if summary["targets"]["targets_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

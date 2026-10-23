"""Repeatability and runtime-budget metrics for team-build evaluations."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import config


@dataclass
class RunObservation:
    run_id: str
    member_ids: list[str]
    pi_id: str
    evidence_ids: list[str]
    validator_status: str
    validation_issues: list[str] = field(default_factory=list)
    latency_ms: int = 0
    llm_calls: int = 0
    total_tokens: int = 0
    fallback_used: bool = False
    cache_hit: bool = False


def jaccard(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _pairwise(values: list[list[str]]) -> list[float]:
    scores: list[float] = []
    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            scores.append(jaccard(left, right))
    return scores


def _percentile(values: list[int | float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[min(position, len(ordered) - 1)]


def evaluate_observations(
    observations: list[RunObservation],
    *,
    min_jaccard: float = 0.8,
    min_pi_agreement: float = 0.8,
) -> dict:
    """Summarize member/PI/evidence stability, validation, and runtime budget."""
    if not observations:
        raise ValueError("at least one observation is required")

    member_scores = _pairwise([item.member_ids for item in observations])
    evidence_scores = _pairwise([item.evidence_ids for item in observations])
    mean_jaccard = (
        sum(member_scores) / len(member_scores) if member_scores else 1.0
    )
    mean_evidence_jaccard = (
        sum(evidence_scores) / len(evidence_scores) if evidence_scores else 1.0
    )

    pi_counts = Counter(item.pi_id for item in observations)
    modal_pi, modal_count = pi_counts.most_common(1)[0]
    pi_agreement = modal_count / len(observations)
    validator_pass_rate = (
        sum(1 for item in observations if not item.validation_issues)
        / len(observations)
    )

    latencies = [item.latency_ms for item in observations]
    llm_calls = [item.llm_calls for item in observations]
    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    max_llm_calls = max(llm_calls) if llm_calls else 0
    within_budget = (
        p95 <= config.RUNTIME_BUDGETS["team_build_p95_ms"]
        and max_llm_calls <= config.RUNTIME_BUDGETS["max_llm_calls"]
    )

    targets_met = (
        mean_jaccard >= min_jaccard
        and pi_agreement >= min_pi_agreement
        and validator_pass_rate >= 1.0
    )
    return {
        "runs": len(observations),
        "member_jaccard_mean": round(mean_jaccard, 4),
        "member_jaccard_min": round(min(member_scores), 4) if member_scores else 1.0,
        "evidence_jaccard_mean": round(mean_evidence_jaccard, 4),
        "pi_agreement": round(pi_agreement, 4),
        "modal_pi_id": modal_pi,
        "validator_pass_rate": round(validator_pass_rate, 4),
        "fallback_runs": sum(1 for item in observations if item.fallback_used),
        "cache_hits": sum(1 for item in observations if item.cache_hit),
        "runtime": {
            "latency_ms_p50": round(p50, 1),
            "latency_ms_p95": round(p95, 1),
            "max_llm_calls": max_llm_calls,
            "mean_tokens": round(
                sum(item.total_tokens for item in observations) / len(observations), 1
            ),
            "targets": dict(config.RUNTIME_BUDGETS),
            "within_budget": within_budget,
        },
        "targets": {
            "min_member_jaccard": min_jaccard,
            "min_pi_agreement": min_pi_agreement,
            "validator_pass_rate": 1.0,
            "targets_met": targets_met,
        },
        "per_run": [
            {
                "run_id": item.run_id,
                "member_ids": item.member_ids,
                "pi_id": item.pi_id,
                "validator_status": item.validator_status,
                "validation_issue_count": len(item.validation_issues),
                "latency_ms": item.latency_ms,
                "llm_calls": item.llm_calls,
                "total_tokens": item.total_tokens,
                "fallback_used": item.fallback_used,
                "cache_hit": item.cache_hit,
            }
            for item in observations
        ],
    }

"""Typed data contracts shared by ingestion, retrieval, generation, and the UI."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Opportunity(BaseModel):
    opportunity_id: str
    opportunity_number: str = ""
    title: str
    agency: str = ""
    agency_code: str = ""
    status: str = "unknown"
    doc_type: str = ""
    open_date: str | None = None
    close_date: str | None = None
    revision: int | None = None
    description: str = ""
    description_html: str | None = None
    eligibility: str = ""
    award_ceiling: float | None = None
    award_floor: float | None = None
    estimated_funding: float | None = None
    cost_sharing: bool | None = None
    cfda_numbers: list[str] = Field(default_factory=list)
    synopsis_url: str | None = None
    raw_ref: str | None = None
    detail_status: str = "missing"
    detail_error: str | None = None
    detail_fetched_at: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    source_hash: str | None = None
    archive_reason: str | None = None
    archived_at: str | None = None


class RefreshSummary(BaseModel):
    started_at: str
    completed_at: str
    status: Literal["success", "partial", "failed"]
    complete_feed: bool
    search_hit_count: int = 0
    open_count: int = 0
    active_open_count: int = 0
    archived_count: int = 0
    detail_fetched: int = 0
    detail_cached: int = 0
    detail_failed: int = 0
    duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)


class OpportunityDatabase(BaseModel):
    schema_version: str = "1"
    last_refresh: RefreshSummary | None = None
    opportunities: list[Opportunity] = Field(default_factory=list)


class Faculty(BaseModel):
    scopus_id: str
    author_name: str
    department: str
    college: str = ""
    rank: str = ""
    tenure: str = ""
    appointment_type: str = ""
    functional_title: str = ""
    hire_date: str = ""
    h_index: int | None = None
    h5_index: int | None = None
    scholarly_output: int | None = None
    citation_count: int | None = None
    corporate_collaboration_pct: float | None = None
    international_collaboration_pct: float | None = None
    top_journal_quartile_pct: float | None = None
    identity_conflict: bool = False
    eligible_for_selection: bool = True
    data_quality_flags: list[str] = Field(default_factory=list)


class Publication(BaseModel):
    eid: str
    doi: str | None = None
    title: str
    year: int
    source_title: str = ""
    document_type: str = ""
    publication_stage: str = ""
    cited_by: int | None = None
    open_access: int | None = None
    abstract: str | None = None
    author_keywords: str | None = None
    index_keywords: str | None = None
    author_ids: list[str] = Field(default_factory=list)
    author_names: str = ""
    faculty_ids: list[str] = Field(default_factory=list)
    is_attributed: bool = False
    provenance_url: str | None = None
    dedupe_key: str = ""
    canonical_eid: str = ""
    alias_eids: list[str] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)


class EvidenceChunk(BaseModel):
    evidence_id: str
    chunk_type: Literal["publication", "faculty_profile"]
    parent_id: str
    chunk_index: int = 0
    chunk_count: int = 1
    text: str
    faculty_ids: list[str] = Field(default_factory=list)
    title: str = ""
    year: int | None = None
    source_title: str = ""
    eid: str | None = None
    doi: str | None = None
    has_abstract: bool = False
    index_version: str = ""
    content_hash: str = ""
    provenance_url: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "chunk_type": self.chunk_type,
            "parent_id": self.parent_id,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "faculty_ids_csv": ",".join(self.faculty_ids),
            "title": self.title[:500],
            "year": self.year if self.year is not None else 0,
            "source_title": self.source_title[:500],
            "eid": self.eid or "",
            "doi": self.doi or "",
            "has_abstract": self.has_abstract,
            "index_version": self.index_version,
            "content_hash": self.content_hash,
            "provenance_url": self.provenance_url or "",
        }


class RetrievedEvidence(BaseModel):
    evidence_id: str
    text: str
    metadata: dict[str, Any]
    distance: float | None = None
    similarity: float = 0.0


class FacultyCandidate(BaseModel):
    scopus_id: str
    author_name: str
    department: str
    score: float
    relevance: float
    metrics: float | None = None
    recency: float = 0.0
    coverage: float | None = None
    collaboration: float = 0.0
    diversity: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_titles: list[str] = Field(default_factory=list)
    matched_concepts: list[str] = Field(default_factory=list)
    metric_missing: list[str] = Field(default_factory=list)
    score_components: dict[str, float] = Field(default_factory=dict)
    tenure: str = ""
    rank: str = ""


class LLMResearchQuery(BaseModel):
    problem_statement: str
    required_expertise: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    disciplines: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


class ResearchQuery(BaseModel):
    problem_statement: str
    required_expertise: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    disciplines: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    prompt_version: str = ""
    fallback_used: bool = False
    optimization_error: str | None = None


class UserConstraints(BaseModel):
    team_size: int = 4
    required_members: list[str] = Field(default_factory=list)
    excluded_members: list[str] = Field(default_factory=list)
    add_expertise: list[str] = Field(default_factory=list)
    remove_expertise: list[str] = Field(default_factory=list)
    department_preferences: list[str] = Field(default_factory=list)
    pi_preference: str | None = None
    recency_preference: str = "balanced"
    tenure_track_only: bool = False
    weights_override: dict[str, float] | None = None
    notes: str = ""
    version: int = 1
    conflicts: list[str] = Field(default_factory=list)


class ConstraintPatch(BaseModel):
    team_size: int | None = None
    add_required_members: list[str] = Field(default_factory=list)
    add_excluded_members: list[str] = Field(default_factory=list)
    remove_required_members: list[str] = Field(default_factory=list)
    remove_excluded_members: list[str] = Field(default_factory=list)
    add_expertise: list[str] = Field(default_factory=list)
    remove_expertise: list[str] = Field(default_factory=list)
    department_preferences: list[str] = Field(default_factory=list)
    pi_preference: str | None = None
    tenure_track_only: bool | None = None
    recency_preference: str | None = None
    notes: str | None = None
    clarification_needed: str | None = None


class FollowupResult(BaseModel):
    patch: ConstraintPatch
    state: UserConstraints
    conflicts: list[str] = Field(default_factory=list)
    clarification_needed: str | None = None
    applied: bool = False
    redo_retrieval: bool = False
    assistant_message: str = ""


class LLMRerankItem(BaseModel):
    evidence_id: str
    score: int = 0
    matched_concepts: list[str] = Field(default_factory=list)
    rationale: str = ""


class LLMRerankBatch(BaseModel):
    items: list[LLMRerankItem] = Field(default_factory=list)


class VerificationIssue(BaseModel):
    issue: str
    member_id: str | None = None
    evidence_id: str | None = None


class LLMVerification(BaseModel):
    approved: bool = True
    issues: list[VerificationIssue] = Field(default_factory=list)


class LLMTeamMember(BaseModel):
    scopus_id: str
    role: str
    rationale: str
    evidence_ids: list[str]


class LLMTeamProposal(BaseModel):
    members: list[LLMTeamMember]
    proposed_pi_id: str
    team_rationale: str = ""
    limitations: list[str] = Field(default_factory=list)


class TeamMember(BaseModel):
    scopus_id: str
    name: str
    department: str
    role: str
    rationale: str
    evidence_ids: list[str]
    evidence_titles: list[str] = Field(default_factory=list)
    factual_claims: list[dict[str, Any]] = Field(default_factory=list)
    score: float = 0.0
    score_components: dict[str, float] = Field(default_factory=dict)
    metric_missing: list[str] = Field(default_factory=list)
    inferred_suitability: bool = True


class TeamProposal(BaseModel):
    run_id: str
    opportunity_id: str
    members: list[TeamMember]
    proposed_pi_id: str
    team_rationale: str = ""
    limitations: list[str] = Field(default_factory=list)
    coverage: dict[str, list[str]] = Field(default_factory=dict)
    constraints_satisfied: bool = True
    validator_status: str = "not_run"
    validation_issues: list[str] = Field(default_factory=list)
    baseline_team_ids: list[str] = Field(default_factory=list)
    llm_team_ids: list[str] = Field(default_factory=list)
    used_llm: bool = False
    llm_call_count: int = 0
    repair_attempts: int = 0
    verifier_calls: int = 0


class RunMetadata(BaseModel):
    run_id: str
    timestamp: str
    opportunity_id: str
    index_version: str
    corpus_hash: str = ""
    embedding_model: str
    chat_model: str
    query: str
    search_queries: list[str] = Field(default_factory=list)
    research_query: dict[str, Any] = Field(default_factory=dict)
    k_in: int
    k_out: int
    team_size: int
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    cache_hits: int = 0
    llm_calls: int = 0
    rerank_calls: int = 0
    generator_calls: int = 0
    verifier_calls: int = 0
    repair_attempts: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int = 0
    validation_issues: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    weights: dict[str, float] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    ranked_evidence_ids: list[str] = Field(default_factory=list)
    baseline_team: list[str] = Field(default_factory=list)
    final_team: list[str] = Field(default_factory=list)
    coverage: dict[str, list[str]] = Field(default_factory=dict)


class ConversationTurn(BaseModel):
    turn_index: int
    user_message: str = ""
    assistant_message: str = ""
    constraint_state: UserConstraints | None = None
    run_id: str | None = None
    clarification_needed: str | None = None


class TeamRun(BaseModel):
    run_id: str
    opportunity_id: str
    conversation_turns: list[ConversationTurn] = Field(default_factory=list)
    constraint_state_history: list[UserConstraints] = Field(default_factory=list)
    draft_proposals: list[TeamProposal] = Field(default_factory=list)
    final_proposal: TeamProposal | None = None
    evidence_packet_snapshot: list[RetrievedEvidence] = Field(default_factory=list)


class IndexManifest(BaseModel):
    index_version: str
    collection_name: str
    source: str = "csv"
    index_scope: str = "full"
    is_full_corpus: bool = False
    schema_version: str
    chunker_version: str
    embedding_model: str
    embedding_dimension: int | None = None
    corpus_sha256: str
    authors_sha256: str
    publications_sha256: str
    publication_rows: int
    canonical_publications: int
    attributed_publications: int
    unattributed_publication_rows: int = 0
    unattributed_canonical_publications: int = 0
    multi_faculty_publications: int = 0
    selected_publications: int
    faculty_rows: int
    unique_faculty: int
    conflict_ids: int
    publication_chunks: int
    faculty_chunks: int
    total_chunks: int = 0
    git_commit: str | None = None
    app_version: str = ""
    selection_scope: dict[str, Any] = Field(default_factory=dict)
    created_at: str

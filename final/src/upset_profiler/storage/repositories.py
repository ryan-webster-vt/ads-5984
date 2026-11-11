"""Typed repositories for SQLite-backed persistence.

All SQL lives in this module (and ``sqlite.py``). Repositories accept and return
Pydantic contracts so no ad-hoc dictionaries cross agent boundaries, while
storage stays free of pipeline logic.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from upset_profiler.context import utc_now
from upset_profiler.errors import StorageError
from upset_profiler.models.common import ValidationIssue
from upset_profiler.models.games import DailyGame, HistoricalGame
from upset_profiler.models.monitoring import GameFlag, MonitorAggregate
from upset_profiler.models.preconditions import CandidatePrecondition, ValidatedPrecondition
from upset_profiler.models.research import (
    CachedSearchResponse,
    SearchHitRecord,
    SourceDocument,
)
from upset_profiler.models.scoring import UpsetRanking, UpsetScore
from upset_profiler.models.validation import BacktestPrediction
from upset_profiler.storage.sqlite import database_lock, initialize_database, transaction

ModelT = TypeVar("ModelT", bound=BaseModel)


def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _dump_payload(payload: BaseModel | dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    data = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _load(model: type[ModelT], payload: str) -> ModelT:
    return model.model_validate_json(payload)


def _load_object(payload: str | None) -> Any:
    return None if payload is None else json.loads(payload)


def _now_iso() -> str:
    return utc_now().isoformat()


class _Repository:
    """Shared connection and error-handling helpers."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def _execute(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> sqlite3.Cursor:
        try:
            with database_lock():
                return self._connection.execute(sql, params)
        except sqlite3.Error as exc:
            operation = sql.split(maxsplit=1)[0].upper() if sql.strip() else "UNKNOWN"
            raise StorageError(
                "sqlite operation failed",
                context={"operation": operation},
            ) from exc

    def _fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with database_lock():
            cursor = self._execute(sql, params)
            row = cursor.fetchone()
        return row if isinstance(row, sqlite3.Row) else None

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with database_lock():
            cursor = self._execute(sql, params)
            return list(cursor.fetchall())


class GameRepository(_Repository):
    """Persistence for historical and daily game records."""

    _UPSERT = """
        INSERT INTO games (
            game_id, record_type, season, slate_date, game_date, tipoff_at,
            home_team_id, away_team_id, completed, winner_team_id, payload, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id) DO UPDATE SET
            record_type = excluded.record_type,
            season = excluded.season,
            slate_date = excluded.slate_date,
            game_date = excluded.game_date,
            tipoff_at = excluded.tipoff_at,
            home_team_id = excluded.home_team_id,
            away_team_id = excluded.away_team_id,
            completed = excluded.completed,
            winner_team_id = excluded.winner_team_id,
            payload = excluded.payload,
            updated_at = excluded.updated_at
    """

    def save_historical(self, game: HistoricalGame) -> None:
        """Insert or update a completed historical game."""
        self._execute(
            self._UPSERT,
            (
                game.game_id,
                "historical",
                game.season,
                None,
                game.game_date.isoformat(),
                game.tipoff_at.isoformat(),
                game.home_team.team_id,
                game.away_team.team_id,
                int(game.completed),
                game.winner_team_id,
                _dump(game),
                _now_iso(),
            ),
        )

    def save_daily(self, game: DailyGame) -> None:
        """Insert or update a daily slate game."""
        self._execute(
            self._UPSERT,
            (
                game.game_id,
                "daily",
                None,
                game.slate_date.isoformat(),
                game.slate_date.isoformat(),
                game.tipoff_at.isoformat(),
                game.home_team.team_id,
                game.away_team.team_id,
                int(game.status == "final"),
                None,
                _dump(game),
                _now_iso(),
            ),
        )

    def get_historical(self, game_id: str) -> HistoricalGame | None:
        """Return a historical game by id, if present."""
        row = self._fetch_one(
            "SELECT payload FROM games WHERE game_id = ? AND record_type = 'historical'",
            (game_id,),
        )
        return None if row is None else _load(HistoricalGame, str(row["payload"]))

    def get_daily(self, game_id: str) -> DailyGame | None:
        """Return a daily game by id, if present."""
        row = self._fetch_one(
            "SELECT payload FROM games WHERE game_id = ? AND record_type = 'daily'",
            (game_id,),
        )
        return None if row is None else _load(DailyGame, str(row["payload"]))

    def list_historical(self, season: str | None = None) -> list[HistoricalGame]:
        """List historical games, optionally filtered to one season."""
        if season is None:
            rows = self._fetch_all(
                "SELECT payload FROM games WHERE record_type = 'historical' "
                "ORDER BY game_date, game_id"
            )
        else:
            rows = self._fetch_all(
                "SELECT payload FROM games WHERE record_type = 'historical' AND season = ? "
                "ORDER BY game_date, game_id",
                (season,),
            )
        return [_load(HistoricalGame, str(row["payload"])) for row in rows]

    def list_daily(self, slate_date: str) -> list[DailyGame]:
        """List daily games for a calendar date, ordered by tip-off."""
        rows = self._fetch_all(
            "SELECT payload FROM games WHERE record_type = 'daily' AND slate_date = ? "
            "ORDER BY tipoff_at, game_id",
            (slate_date,),
        )
        return [_load(DailyGame, str(row["payload"])) for row in rows]


class SourceDocumentRepository(_Repository):
    """Persistence for cited contextual documents."""

    def upsert(self, document: SourceDocument) -> None:
        """Insert or update a source document."""
        self._execute(
            """
            INSERT INTO source_documents (
                document_id, game_id, source_url, source_domain, published_at,
                retrieved_at, content_hash, payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                game_id = excluded.game_id,
                source_url = excluded.source_url,
                source_domain = excluded.source_domain,
                published_at = excluded.published_at,
                retrieved_at = excluded.retrieved_at,
                content_hash = excluded.content_hash,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                document.document_id,
                document.game_id,
                str(document.url),
                document.source_domain,
                None if document.published_at is None else document.published_at.isoformat(),
                document.retrieved_at.isoformat(),
                document.content_hash,
                _dump(document),
                _now_iso(),
            ),
        )

    def get(self, document_id: str) -> SourceDocument | None:
        """Return a document by id, if present."""
        row = self._fetch_one(
            "SELECT payload FROM source_documents WHERE document_id = ?",
            (document_id,),
        )
        return None if row is None else _load(SourceDocument, str(row["payload"]))

    def list_for_game(self, game_id: str) -> list[SourceDocument]:
        """List documents attached to a game, ordered by publication time."""
        rows = self._fetch_all(
            "SELECT payload FROM source_documents WHERE game_id = ? "
            "ORDER BY published_at, document_id",
            (game_id,),
        )
        return [_load(SourceDocument, str(row["payload"])) for row in rows]


class ResearchRunRepository(_Repository):
    """Persistence for historical research run metadata."""

    def create(
        self,
        run_id: str,
        *,
        status: str = "running",
        payload: BaseModel | dict[str, Any] | None = None,
    ) -> None:
        """Create a research run record."""
        now = _now_iso()
        self._execute(
            """
            INSERT INTO research_runs (run_id, status, started_at, payload, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, status, now, _dump_payload(payload), now),
        )

    def update_status(
        self,
        run_id: str,
        status: str,
        *,
        completed: bool = False,
        summary: str | None = None,
        payload: BaseModel | dict[str, Any] | None = None,
    ) -> None:
        """Update a research run's status and optional summary."""
        now = _now_iso()
        self._execute(
            """
            UPDATE research_runs SET
                status = ?,
                completed_at = CASE WHEN ? THEN ? ELSE completed_at END,
                summary = COALESCE(?, summary),
                payload = COALESCE(?, payload),
                updated_at = ?
            WHERE run_id = ?
            """,
            (status, int(completed), now, summary, _dump_payload(payload), now, run_id),
        )

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return a research run record as a plain mapping."""
        row = self._fetch_one("SELECT * FROM research_runs WHERE run_id = ?", (run_id,))
        if row is None:
            return None
        record = dict(row)
        record["payload"] = _load_object(record.get("payload"))
        return record


class QuarantineRepository(_Repository):
    """Persistence for invalid records and their validation errors."""

    def add(
        self,
        *,
        stage: str,
        errors: Sequence[BaseModel | str],
        run_id: str | None = None,
        game_id: str | None = None,
        payload: BaseModel | dict[str, Any] | None = None,
    ) -> int:
        """Insert a quarantine entry and return its row id."""
        serialized = [
            error.model_dump(mode="json") if isinstance(error, BaseModel) else error
            for error in errors
        ]
        cursor = self._execute(
            """
            INSERT INTO quarantine (run_id, game_id, stage, errors, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                game_id,
                stage,
                json.dumps(serialized, sort_keys=True),
                _dump_payload(payload),
                _now_iso(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def add_issues(
        self,
        *,
        stage: str,
        issues: Sequence[ValidationIssue],
        run_id: str | None = None,
        game_id: str | None = None,
        payload: BaseModel | dict[str, Any] | None = None,
    ) -> int:
        """Insert validation issues as a quarantine entry."""
        return self.add(
            stage=stage,
            errors=issues,
            run_id=run_id,
            game_id=game_id,
            payload=payload,
        )

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        """List quarantine entries for a run."""
        rows = self._fetch_all(
            "SELECT * FROM quarantine WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        entries: list[dict[str, Any]] = []
        for row in rows:
            entry = dict(row)
            entry["errors"] = _load_object(entry.get("errors"))
            entry["payload"] = _load_object(entry.get("payload"))
            entries.append(entry)
        return entries


class CandidatePreconditionRepository(_Repository):
    """Persistence for candidate precondition hypotheses."""

    def upsert(self, candidate: CandidatePrecondition) -> None:
        """Insert or update a candidate precondition."""
        now = _now_iso()
        self._execute(
            """
            INSERT INTO candidate_preconditions (
                precondition_id, name, feature, source, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(precondition_id) DO UPDATE SET
                name = excluded.name,
                feature = excluded.feature,
                source = excluded.source,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                candidate.precondition_id,
                candidate.name,
                candidate.feature,
                candidate.source,
                _dump(candidate),
                now,
                now,
            ),
        )

    def get(self, precondition_id: str) -> CandidatePrecondition | None:
        """Return a candidate precondition by id."""
        row = self._fetch_one(
            "SELECT payload FROM candidate_preconditions WHERE precondition_id = ?",
            (precondition_id,),
        )
        return None if row is None else _load(CandidatePrecondition, str(row["payload"]))

    def list_all(self) -> list[CandidatePrecondition]:
        """List candidate preconditions ordered by id."""
        rows = self._fetch_all(
            "SELECT payload FROM candidate_preconditions ORDER BY precondition_id"
        )
        return [_load(CandidatePrecondition, str(row["payload"])) for row in rows]


class ValidatedPreconditionRepository(_Repository):
    """Persistence for preconditions accepted by the Extractor."""

    def upsert(self, precondition: ValidatedPrecondition) -> None:
        """Insert or update a validated precondition."""
        self._execute(
            """
            INSERT INTO validated_preconditions (
                precondition_id, validation_version, feature, comparison, threshold,
                validated_at, citations, payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(precondition_id) DO UPDATE SET
                validation_version = excluded.validation_version,
                feature = excluded.feature,
                comparison = excluded.comparison,
                threshold = excluded.threshold,
                validated_at = excluded.validated_at,
                citations = excluded.citations,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                precondition.precondition_id,
                precondition.validation_version,
                precondition.candidate.feature,
                precondition.candidate.comparison.value,
                json.dumps(precondition.candidate.threshold, sort_keys=True),
                precondition.validated_at.isoformat(),
                json.dumps(
                    [citation.model_dump(mode="json") for citation in precondition.citations],
                    sort_keys=True,
                ),
                _dump(precondition),
                _now_iso(),
            ),
        )

    def get(self, precondition_id: str) -> ValidatedPrecondition | None:
        """Return a validated precondition by id."""
        row = self._fetch_one(
            "SELECT payload FROM validated_preconditions WHERE precondition_id = ?",
            (precondition_id,),
        )
        return None if row is None else _load(ValidatedPrecondition, str(row["payload"]))

    def list_all(self) -> list[ValidatedPrecondition]:
        """List all validated preconditions ordered by id."""
        rows = self._fetch_all(
            "SELECT payload FROM validated_preconditions ORDER BY precondition_id"
        )
        return [_load(ValidatedPrecondition, str(row["payload"])) for row in rows]


class AnalysisRunRepository(_Repository):
    """Persistence for daily analysis run metadata."""

    def create(
        self,
        run_id: str,
        *,
        slate_date: str | None = None,
        status: str = "running",
        payload: BaseModel | dict[str, Any] | None = None,
    ) -> None:
        """Create an analysis run record."""
        now = _now_iso()
        self._execute(
            """
            INSERT INTO analysis_runs (
                run_id, slate_date, status, started_at, payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, slate_date, status, now, _dump_payload(payload), now),
        )

    def update_status(
        self,
        run_id: str,
        status: str,
        *,
        completed: bool = False,
        payload: BaseModel | dict[str, Any] | None = None,
    ) -> None:
        """Update an analysis run's status."""
        now = _now_iso()
        self._execute(
            """
            UPDATE analysis_runs SET
                status = ?,
                completed_at = CASE WHEN ? THEN ? ELSE completed_at END,
                payload = COALESCE(?, payload),
                updated_at = ?
            WHERE run_id = ?
            """,
            (status, int(completed), now, _dump_payload(payload), now, run_id),
        )

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return an analysis run record as a plain mapping."""
        row = self._fetch_one("SELECT * FROM analysis_runs WHERE run_id = ?", (run_id,))
        if row is None:
            return None
        record = dict(row)
        record["payload"] = _load_object(record.get("payload"))
        return record


class MonitorResultRepository(_Repository):
    """Persistence for flagged games from monitoring runs."""

    def save_flag(self, run_id: str, flag: GameFlag) -> None:
        """Insert or update one game flag for a run."""
        self._execute(
            """
            INSERT INTO monitor_results (
                run_id, game_id, worker_id, flagged, payload, created_at
            ) VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(run_id, game_id) DO UPDATE SET
                worker_id = excluded.worker_id,
                flagged = excluded.flagged,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (run_id, flag.game_id, flag.worker_id, _dump(flag), _now_iso()),
        )

    def save_aggregate(self, aggregate: MonitorAggregate) -> None:
        """Persist every flag contained in a monitor aggregate."""
        for flag in aggregate.flags:
            self.save_flag(str(aggregate.run_id), flag)

    def get_flag(self, run_id: str, game_id: str) -> GameFlag | None:
        """Return a stored flag for one game in a run."""
        row = self._fetch_one(
            "SELECT payload FROM monitor_results WHERE run_id = ? AND game_id = ?",
            (run_id, game_id),
        )
        return None if row is None else _load(GameFlag, str(row["payload"]))

    def list_flags(self, run_id: str) -> list[GameFlag]:
        """List stored flags for a run ordered by creation."""
        rows = self._fetch_all(
            "SELECT payload FROM monitor_results WHERE run_id = ? ORDER BY created_at, game_id",
            (run_id,),
        )
        return [_load(GameFlag, str(row["payload"])) for row in rows]


class UpsetScoreRepository(_Repository):
    """Persistence for upset-risk scores."""

    def upsert(self, run_id: str, score: UpsetScore) -> None:
        """Insert or update one upset score for a run."""
        self._execute(
            """
            INSERT INTO upset_scores (
                run_id, game_id, score, score_rank, model_name, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, game_id) DO UPDATE SET
                score = excluded.score,
                score_rank = excluded.score_rank,
                model_name = excluded.model_name,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (
                run_id,
                score.game_id,
                score.score,
                score.rank,
                score.model_name,
                _dump(score),
                _now_iso(),
            ),
        )

    def save_ranking(self, ranking: UpsetRanking) -> None:
        """Persist all scores in a ranking."""
        for score in ranking.scores:
            self.upsert(str(ranking.run_id), score)

    def list_for_run(self, run_id: str) -> list[UpsetScore]:
        """List scores for a run ordered by rank."""
        rows = self._fetch_all(
            "SELECT payload FROM upset_scores WHERE run_id = ? ORDER BY score_rank",
            (run_id,),
        )
        return [_load(UpsetScore, str(row["payload"])) for row in rows]


class CacheRepository(_Repository):
    """Persistence for analysis cache entries with TTL expiry."""

    def set(
        self,
        cache_key: str,
        value: str,
        *,
        ttl_seconds: int,
        metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Insert or replace a cache entry."""
        current = now or utc_now()
        expires_at = current + timedelta(seconds=ttl_seconds)
        self._execute(
            """
            INSERT INTO cache_entries (cache_key, value, metadata, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                value = excluded.value,
                metadata = excluded.metadata,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (
                cache_key,
                value,
                None if metadata is None else json.dumps(metadata, sort_keys=True),
                current.isoformat(),
                expires_at.isoformat(),
            ),
        )

    def get(self, cache_key: str, *, now: datetime | None = None) -> str | None:
        """Return a cache value, deleting it when expired."""
        row = self._fetch_one(
            "SELECT value, expires_at FROM cache_entries WHERE cache_key = ?",
            (cache_key,),
        )
        if row is None:
            return None
        current = now or utc_now()
        expires_at = datetime.fromisoformat(str(row["expires_at"]))
        if expires_at <= current:
            self.delete(cache_key)
            return None
        return str(row["value"])

    def delete(self, cache_key: str) -> None:
        """Delete a cache entry."""
        self._execute("DELETE FROM cache_entries WHERE cache_key = ?", (cache_key,))

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete expired entries and return the number removed."""
        current = now or utc_now()
        cursor = self._execute(
            "DELETE FROM cache_entries WHERE expires_at <= ?",
            (current.isoformat(),),
        )
        return int(cursor.rowcount)

    def count(self) -> int:
        """Return the number of cache entries, including expired ones."""
        row = self._fetch_one("SELECT COUNT(*) AS count FROM cache_entries")
        return int(row["count"]) if row is not None else 0


class SearchCacheRepository(_Repository):
    """Persistence for normalized search queries and their returned hits.

    The cache is keyed by a deterministic hash of the normalized query, the
    provider, and a parameters fingerprint, so identical requests replay without
    a network call. Historical rows have ``expires_at = NULL``; live rows expire
    after the configured TTL (ADR-006 Decision 2).
    """

    _UPSERT_QUERY = """
        INSERT INTO search_queries (
            query_id, query_hash, normalized_query, provider, params_hash,
            game_id, season, created_at, last_used_at, expires_at,
            result_count, credits_used, payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(query_hash) DO UPDATE SET
            normalized_query = excluded.normalized_query,
            provider = excluded.provider,
            params_hash = excluded.params_hash,
            game_id = excluded.game_id,
            season = excluded.season,
            last_used_at = excluded.last_used_at,
            expires_at = excluded.expires_at,
            result_count = excluded.result_count,
            credits_used = excluded.credits_used,
            payload = excluded.payload
    """

    _INSERT_HIT = """
        INSERT INTO search_hits (
            hit_id, query_hash, url, canonical_url, source_domain, title, score,
            published_at, retrieved_at, content_hash, content, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def get(
        self,
        query_hash: str,
        *,
        now: datetime | None = None,
    ) -> CachedSearchResponse | None:
        """Return a cached response, deleting and ignoring it when expired."""
        row = self._fetch_one(
            "SELECT * FROM search_queries WHERE query_hash = ?",
            (query_hash,),
        )
        if row is None:
            return None
        current = now or utc_now()
        expires_at = _optional_datetime(row["expires_at"])
        if expires_at is not None and expires_at <= current:
            self.delete(query_hash)
            return None
        return self._response(row, self._hits(query_hash))

    def upsert(self, response: CachedSearchResponse) -> None:
        """Insert or replace a cached response and all of its hits."""
        with transaction(self._connection):
            self._execute(
                self._UPSERT_QUERY,
                (
                    f"q-{response.query_hash[:24]}",
                    response.query_hash,
                    response.normalized_query,
                    response.provider,
                    response.params_hash,
                    response.game_id,
                    response.season,
                    response.created_at.isoformat(),
                    response.last_used_at.isoformat(),
                    None if response.expires_at is None else response.expires_at.isoformat(),
                    response.result_count,
                    response.credits_used,
                    json.dumps(response.payload, sort_keys=True),
                ),
            )
            self._execute(
                "DELETE FROM search_hits WHERE query_hash = ?",
                (response.query_hash,),
            )
            for hit in response.hits:
                self._execute(
                    self._INSERT_HIT,
                    (
                        _hit_id(response.query_hash, hit.canonical_url),
                        response.query_hash,
                        str(hit.url),
                        hit.canonical_url,
                        hit.source_domain,
                        hit.title,
                        hit.score,
                        None if hit.published_at is None else hit.published_at.isoformat(),
                        hit.retrieved_at.isoformat(),
                        hit.content_hash,
                        hit.content,
                        json.dumps(hit.metadata, sort_keys=True),
                    ),
                )

    def touch(self, query_hash: str, *, now: datetime | None = None) -> None:
        """Record that a cached query was reused."""
        self._execute(
            "UPDATE search_queries SET last_used_at = ? WHERE query_hash = ?",
            ((now or utc_now()).isoformat(), query_hash),
        )

    def delete(self, query_hash: str) -> None:
        """Delete one cached query; its hits cascade."""
        self._execute("DELETE FROM search_queries WHERE query_hash = ?", (query_hash,))

    def purge_expired(self, *, now: datetime | None = None) -> int:
        """Delete expired live queries and return how many were removed."""
        cursor = self._execute(
            "DELETE FROM search_queries "
            "WHERE expires_at IS NOT NULL AND expires_at <= ?",
            ((now or utc_now()).isoformat(),),
        )
        return int(cursor.rowcount)

    def count_queries(self) -> int:
        """Return the number of cached queries, including expired ones."""
        row = self._fetch_one("SELECT COUNT(*) AS count FROM search_queries")
        return int(row["count"]) if row is not None else 0

    def total_credits(self) -> float:
        """Return the total credits recorded across cached queries."""
        row = self._fetch_one("SELECT COALESCE(SUM(credits_used), 0) AS total FROM search_queries")
        return float(row["total"]) if row is not None else 0.0

    def list_queries(self, *, game_id: str | None = None) -> list[CachedSearchResponse]:
        """List cached responses for auditing, optionally for one game."""
        if game_id is None:
            rows = self._fetch_all(
                "SELECT * FROM search_queries ORDER BY created_at, query_hash"
            )
        else:
            rows = self._fetch_all(
                "SELECT * FROM search_queries WHERE game_id = ? "
                "ORDER BY created_at, query_hash",
                (game_id,),
            )
        return [
            self._response(row, self._hits(str(row["query_hash"])))
            for row in rows
        ]

    def _hits(self, query_hash: str) -> list[SearchHitRecord]:
        rows = self._fetch_all(
            "SELECT * FROM search_hits WHERE query_hash = ? ORDER BY rowid",
            (query_hash,),
        )
        return [
            SearchHitRecord(
                url=str(row["url"]),
                canonical_url=str(row["canonical_url"]),
                source_domain=str(row["source_domain"]),
                title=None if row["title"] is None else str(row["title"]),
                score=None if row["score"] is None else float(row["score"]),
                published_at=_optional_datetime(row["published_at"]),
                retrieved_at=_required_datetime(row["retrieved_at"]),
                content=None if row["content"] is None else str(row["content"]),
                content_hash=None if row["content_hash"] is None else str(row["content_hash"]),
                metadata=_metadata(row["metadata"]),
            )
            for row in rows
        ]

    def _response(
        self,
        row: sqlite3.Row,
        hits: Sequence[SearchHitRecord],
    ) -> CachedSearchResponse:
        return CachedSearchResponse(
            query_hash=str(row["query_hash"]),
            normalized_query=str(row["normalized_query"]),
            provider=str(row["provider"]),
            params_hash=str(row["params_hash"]),
            game_id=None if row["game_id"] is None else str(row["game_id"]),
            season=None if row["season"] is None else str(row["season"]),
            created_at=_required_datetime(row["created_at"]),
            last_used_at=_required_datetime(row["last_used_at"]),
            expires_at=_optional_datetime(row["expires_at"]),
            result_count=int(row["result_count"]),
            credits_used=float(row["credits_used"]),
            payload=_metadata(row["payload"]),
            hits=list(hits),
        )


def _hit_id(query_hash: str, canonical_url: str) -> str:
    digest = hashlib.sha256(f"{query_hash}|{canonical_url}".encode()).hexdigest()
    return f"hit-{digest[:32]}"


def _required_datetime(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_datetime(value: Any) -> datetime | None:
    return None if value is None else _required_datetime(value)


def _metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    loaded = json.loads(str(value))
    return loaded if isinstance(loaded, dict) else {}


class BacktestPredictionRepository(_Repository):
    """Persistence for leakage-safe backtest predictions."""

    def upsert(self, prediction: BacktestPrediction) -> None:
        """Insert or update a backtest prediction."""
        self._execute(
            """
            INSERT INTO backtest_predictions (
                run_id, game_id, season, predicted_probability, actual_upset,
                model_name, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, game_id) DO UPDATE SET
                season = excluded.season,
                predicted_probability = excluded.predicted_probability,
                actual_upset = excluded.actual_upset,
                model_name = excluded.model_name,
                payload = excluded.payload,
                created_at = excluded.created_at
            """,
            (
                str(prediction.run_id),
                prediction.game_id,
                prediction.season,
                prediction.predicted_probability,
                int(prediction.actual_upset),
                prediction.model_name,
                _dump(prediction),
                _now_iso(),
            ),
        )

    def list_for_run(self, run_id: str) -> list[BacktestPrediction]:
        """List predictions for a run ordered by game id."""
        rows = self._fetch_all(
            "SELECT payload FROM backtest_predictions WHERE run_id = ? ORDER BY game_id",
            (run_id,),
        )
        return [_load(BacktestPrediction, str(row["payload"])) for row in rows]


class Repositories:
    """Container that exposes every repository over one connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.games = GameRepository(connection)
        self.source_documents = SourceDocumentRepository(connection)
        self.research_runs = ResearchRunRepository(connection)
        self.quarantine = QuarantineRepository(connection)
        self.candidate_preconditions = CandidatePreconditionRepository(connection)
        self.validated_preconditions = ValidatedPreconditionRepository(connection)
        self.analysis_runs = AnalysisRunRepository(connection)
        self.monitor_results = MonitorResultRepository(connection)
        self.upset_scores = UpsetScoreRepository(connection)
        self.cache = CacheRepository(connection)
        self.search_cache = SearchCacheRepository(connection)
        self.backtest_predictions = BacktestPredictionRepository(connection)

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()


def bootstrap(database: str | Path) -> Repositories:
    """Open a database, apply migrations, and return repositories."""
    return Repositories(initialize_database(database))

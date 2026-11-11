"""Typed repositories for SQLite-backed persistence.

All SQL lives in this module (and ``sqlite.py``). Repositories accept and return
Pydantic contracts so no ad-hoc dictionaries cross agent boundaries, while
storage stays free of pipeline logic.
"""

from __future__ import annotations

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
from upset_profiler.models.research import SourceDocument
from upset_profiler.models.scoring import UpsetRanking, UpsetScore
from upset_profiler.models.validation import BacktestPrediction
from upset_profiler.storage.sqlite import initialize_database

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
            return self._connection.execute(sql, params)
        except sqlite3.Error as exc:
            operation = sql.split(maxsplit=1)[0].upper() if sql.strip() else "UNKNOWN"
            raise StorageError(
                "sqlite operation failed",
                context={"operation": operation},
            ) from exc

    def _fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        cursor = self._execute(sql, params)
        row = cursor.fetchone()
        return row if isinstance(row, sqlite3.Row) else None

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
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
        self.backtest_predictions = BacktestPredictionRepository(connection)

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()


def bootstrap(database: str | Path) -> Repositories:
    """Open a database, apply migrations, and return repositories."""
    return Repositories(initialize_database(database))

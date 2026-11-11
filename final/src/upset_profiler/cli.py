"""Command-line entry point for administrative pipelines and the offline demo.

Phase 2 implements ``validate-config`` and the fixture-backed ``demo`` command.
The remaining commands are registered so the CLI surface is stable, but they
exit with a clear message until their corresponding implementation phase lands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from upset_profiler import __version__
from upset_profiler.agents.research_orchestrator import ResearchOrchestrator
from upset_profiler.agents.upset_scorer import UpsetScorer
from upset_profiler.config import DEFAULT_ENV_FILE, Settings, reset_settings_cache
from upset_profiler.context import RunContext
from upset_profiler.errors import ConfigurationError, UpsetProfilerError
from upset_profiler.fixtures import FIXTURE_ROOT_ENV, load_json
from upset_profiler.integrations.base import HttpClientConfig, create_http_client
from upset_profiler.integrations.college_basketball import (
    FixtureHistoricalStatsProvider,
    build_espn_historical_provider,
)
from upset_profiler.integrations.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from upset_profiler.integrations.fakes import (
    FixtureContextProvider,
    FixtureContextResearchProvider,
    FixtureDecisionModel,
    FixtureSlateProvider,
)
from upset_profiler.integrations.mcp import build_mcp_context_provider
from upset_profiler.logging_config import setup_logging
from upset_profiler.models.preconditions import ValidatedPrecondition
from upset_profiler.models.progress import ProgressEvent, ProgressStage
from upset_profiler.orchestration.pipelines.ingestion import (
    run_historical_ingestion_pipeline,
)
from upset_profiler.orchestration.pipelines.monitoring import (
    run_daily_monitoring_pipeline,
)
from upset_profiler.orchestration.progress import (
    CallbackProgressSink,
    NullProgressSink,
    ProgressReporter,
    ProgressSink,
)
from upset_profiler.rag.chunking import ChunkingConfig, RecursiveChunker
from upset_profiler.security.leakage import LeakageFilter
from upset_profiler.security.source_whitelist import SourceWhitelist
from upset_profiler.services.scoring import ScoringService
from upset_profiler.services.slate import SlateService
from upset_profiler.storage.chroma import (
    DocumentVectorStore,
    InMemoryVectorStore,
    build_chroma_store,
)
from upset_profiler.storage.repositories import Repositories, bootstrap

_NOT_IMPLEMENTED = {
    "extract": "Phase 6 (extractor and statistical validation)",
    "monitor": "Phase 3/7 (daily monitoring and upset scoring)",
    "backtest": "Phase 8 (backtesting and validation)",
}

_DEFAULT_DEMO_DATE = date(2025, 1, 15)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upset-profiler",
        description="NCAA Division I Men's Basketball Upset Condition Profiler",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Path to a .env file (default: project .env)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate-config",
        help="Validate settings and print a redacted summary",
    )
    validate.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when core LLM or storage settings are missing",
    )
    validate.add_argument(
        "--json",
        action="store_true",
        help="Emit the redacted summary as JSON",
    )

    demo = subparsers.add_parser(
        "demo",
        help="Run the fixture-backed Phase 2 end-to-end demo",
    )
    demo.add_argument(
        "--date",
        type=date.fromisoformat,
        default=_DEFAULT_DEMO_DATE,
        help="Fixture slate date (default: 2025-01-15)",
    )
    demo.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Number of parallel Monitor workers (default: 2)",
    )
    demo.add_argument(
        "--game-id",
        action="append",
        dest="game_ids",
        metavar="GAME_ID",
        help="Evaluate only the given game id; may be repeated",
    )
    demo.add_argument(
        "--fixture-root",
        type=Path,
        default=None,
        help="Alternate fixture root (default: tests/fixtures)",
    )
    demo.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the ranked JSON to this path in addition to stdout",
    )
    demo.add_argument(
        "--progress",
        action="store_true",
        help="Write agent progress events to stderr",
    )

    ingest = subparsers.add_parser(
        "ingest",
        help="Ingest a historical season for RAG-backed precondition research",
    )
    ingest.add_argument(
        "--season",
        default="2024-2025",
        help="Season to ingest, e.g. 2024-2025 (default: 2024-2025)",
    )
    ingest.add_argument(
        "--game-id",
        action="append",
        dest="game_ids",
        metavar="GAME_ID",
        help="Ingest only the given game id; may be repeated",
    )
    ingest.add_argument(
        "--provider",
        choices=["fixture", "espn"],
        default="fixture",
        help="Historical statistics provider (default: fixture)",
    )
    ingest.add_argument(
        "--context-provider",
        choices=["auto", "fixture", "mcp"],
        default="auto",
        help="Contextual research provider (default: fixture for fixture stats)",
    )
    ingest.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of discovered games",
    )
    ingest.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Games per research batch (default: RESEARCH_BATCH_SIZE)",
    )
    ingest.add_argument(
        "--database",
        default=None,
        help="SQLite path (default: SQLITE_PATH; use :memory: for a dry run)",
    )
    ingest.add_argument(
        "--vector-store",
        choices=["auto", "memory", "chroma"],
        default="auto",
        help="Vector store backend (default: Chroma when installed, else memory)",
    )
    ingest.add_argument(
        "--embedding-provider",
        choices=["fixture", "http"],
        default="fixture",
        help="Embedding backend (default: deterministic fixture)",
    )
    ingest.add_argument(
        "--fixture-root",
        type=Path,
        default=None,
        help="Alternate fixture root (default: tests/fixtures)",
    )
    ingest.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the ingestion summary JSON to this path in addition to stdout",
    )
    ingest.add_argument(
        "--progress",
        action="store_true",
        help="Write agent progress events to stderr",
    )

    for command in _NOT_IMPLEMENTED:
        subparsers.add_parser(command, help=f"Not implemented until {_NOT_IMPLEMENTED[command]}")

    return parser


def _validate_config(args: argparse.Namespace) -> int:
    env_file = args.env_file if args.env_file and args.env_file.exists() else None
    reset_settings_cache()
    settings = Settings.load(env_file=env_file)
    setup_logging(settings, force=True)

    summary = settings.safe_summary()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("Configuration loaded successfully.")
        for group, values in summary.items():
            print(f"\n[{group}]")
            if isinstance(values, dict):
                for key, value in values.items():
                    print(f"  {key}: {value}")
            else:
                print(f"  {values}")

    missing = _missing_required(settings, strict=False)
    if missing:
        print("\nMissing optional configuration for later phases:", file=sys.stderr)
        for key in missing:
            print(f"  - {key}", file=sys.stderr)

    if args.strict:
        required_missing = _missing_required(settings, strict=True)
        if required_missing:
            raise ConfigurationError(
                "strict configuration validation failed",
                context={"missing": ", ".join(required_missing)},
            )
    return 0


def _missing_required(settings: Settings, *, strict: bool) -> list[str]:
    missing: list[str] = []
    if settings.llm.base_url is None:
        missing.append("OPEN_AI_ENDPOINT or LLM_BASE_URL")
    if settings.llm.api_key is None:
        missing.append("OPEN_AI_API_KEY or LLM_API_KEY")
    if settings.llm.model is None:
        missing.append("OPEN_AI_MODEL or LLM_MODEL")
    if not settings.pipeline.source_whitelist_path.exists():
        missing.append(f"SOURCE_WHITELIST_PATH ({settings.pipeline.source_whitelist_path})")
    if strict and not settings.storage.sqlite_path.parent.exists():
        missing.append(f"SQLITE_PATH parent directory ({settings.storage.sqlite_path.parent})")
    return missing


def _print_progress(event: ProgressEvent) -> None:
    totals = f" ({event.completed}/{event.total})" if event.total is not None else ""
    game = f" [{event.game_id}]" if event.game_id else ""
    print(
        f"[{event.stage.value}] {event.status.value} {event.agent}{game}: "
        f"{event.message}{totals}",
        file=sys.stderr,
    )


def _run_demo(args: argparse.Namespace) -> int:
    if args.fixture_root is not None:
        os.environ[FIXTURE_ROOT_ENV] = str(args.fixture_root.expanduser().resolve())
    if args.workers < 1:
        raise ConfigurationError("--workers must be at least 1")

    payloads = load_json("historical", "validated_preconditions.json")
    if not isinstance(payloads, list):
        raise ConfigurationError("validated preconditions fixture must be a JSON list")
    try:
        preconditions = [
            ValidatedPrecondition.model_validate(payload) for payload in payloads
        ]
    except ValidationError as exc:
        raise ConfigurationError(
            "invalid validated preconditions fixture",
            context={"error": str(exc)},
        ) from exc

    repositories = bootstrap(":memory:")
    try:
        for precondition in preconditions:
            repositories.validated_preconditions.upsert(precondition)

        progress_sink: ProgressSink = (
            CallbackProgressSink(_print_progress) if args.progress else NullProgressSink()
        )
        ranking = run_daily_monitoring_pipeline(
            slate_service=SlateService(FixtureSlateProvider()),
            context_provider=FixtureContextProvider(),
            precondition_source=repositories.validated_preconditions,
            scoring_service=ScoringService(
                UpsetScorer(decision_model=FixtureDecisionModel())
            ),
            slate_date=args.date,
            selected_game_ids=args.game_ids,
            run_context=RunContext.create(actor="demo"),
            worker_count=args.workers,
            progress_sink=progress_sink,
            repositories=repositories,
        )
    finally:
        repositories.close()

    text = json.dumps(ranking.model_dump(mode="json"), indent=2, sort_keys=True)
    print(text)
    if args.output is not None:
        output = args.output.expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


def _load_settings(args: argparse.Namespace) -> Settings:
    env_file = args.env_file if args.env_file and args.env_file.exists() else None
    reset_settings_cache()
    return Settings.load(env_file=env_file)


def _build_vector_store(kind: str, settings: Settings) -> DocumentVectorStore:
    if kind == "memory":
        return InMemoryVectorStore()
    if kind == "chroma":
        return build_chroma_store(settings)
    try:
        return build_chroma_store(settings)
    except ConfigurationError:
        print(
            "note: chromadb is not installed; using the in-memory vector store",
            file=sys.stderr,
        )
        return InMemoryVectorStore()


def _build_embedding_provider(args: argparse.Namespace, settings: Settings) -> EmbeddingProvider:
    if args.embedding_provider == "fixture":
        return DeterministicEmbeddingProvider()
    base_url = settings.embeddings.embedding_base_url or settings.llm.base_url
    if base_url is None:
        raise ConfigurationError(
            "EMBEDDING_BASE_URL or OPEN_AI_ENDPOINT is required for HTTP embeddings"
        )
    api_key = settings.embeddings.embedding_api_key or settings.llm.api_key
    headers = (
        {"Authorization": f"Bearer {api_key.get_secret_value()}"}
        if api_key is not None
        else {}
    )
    client = create_http_client(
        HttpClientConfig(
            base_url=base_url,
            timeout_seconds=settings.llm.llm_timeout_seconds,
            headers=headers,
        )
    )
    return OpenAICompatibleEmbeddingProvider(
        client,
        model_name=settings.embeddings.embedding_model,
        batch_size=settings.embeddings.embedding_batch_size,
    )


def _run_ingest(args: argparse.Namespace) -> int:
    if args.fixture_root is not None:
        os.environ[FIXTURE_ROOT_ENV] = str(args.fixture_root.expanduser().resolve())
    if args.limit is not None and args.limit < 1:
        raise ConfigurationError("--limit must be at least 1")
    if args.batch_size is not None and args.batch_size < 1:
        raise ConfigurationError("--batch-size must be at least 1")

    settings = _load_settings(args)
    setup_logging(settings, force=True)
    whitelist = SourceWhitelist.from_path(settings.pipeline.source_whitelist_path)

    historical_provider = (
        FixtureHistoricalStatsProvider()
        if args.provider == "fixture"
        else build_espn_historical_provider(settings)
    )
    game_ids = list(args.game_ids or historical_provider.list_game_ids(args.season))
    if args.limit is not None:
        game_ids = game_ids[: args.limit]
    if not game_ids:
        raise ConfigurationError("historical ingestion requires at least one game id")

    context_kind = args.context_provider
    if context_kind == "auto":
        context_kind = "fixture" if args.provider == "fixture" else "mcp"
    context_provider = (
        FixtureContextResearchProvider()
        if context_kind == "fixture"
        else build_mcp_context_provider(settings, whitelist=whitelist)
    )

    repositories: Repositories = bootstrap(args.database or settings.storage.sqlite_path)
    store = _build_vector_store(args.vector_store, settings)
    embeddings = _build_embedding_provider(args, settings)
    chunker = RecursiveChunker(
        ChunkingConfig(
            chunk_size_tokens=settings.pipeline.chunk_size_tokens,
            overlap_tokens=settings.pipeline.chunk_overlap_tokens,
        )
    )
    run_context = RunContext.create(actor="ingest")
    sink: ProgressSink = (
        CallbackProgressSink(_print_progress) if args.progress else NullProgressSink()
    )
    reporter = ProgressReporter(
        run_id=run_context.run_id,
        sink=sink,
        stage=ProgressStage.INGEST,
        agent="research-orchestrator",
        total=len(game_ids),
    )
    orchestrator = ResearchOrchestrator(
        historical_provider=historical_provider,
        context_provider=context_provider,
        source_whitelist=whitelist,
        leakage_filter=LeakageFilter(strict=settings.pipeline.leakage_strict),
        repositories=repositories,
        vector_store=store,
        embedding_provider=embeddings,
        chunker=chunker,
        batch_size=args.batch_size or settings.pipeline.research_batch_size,
        progress=reporter,
    )
    try:
        result = run_historical_ingestion_pipeline(
            orchestrator=orchestrator,
            season=args.season,
            game_ids=game_ids,
            run_context=run_context,
        )
    finally:
        repositories.close()
        close = getattr(historical_provider, "close", None)
        if callable(close):
            close()

    text = json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True)
    print(text)
    if args.output is not None:
        output = args.output.expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "validate-config":
            return _validate_config(args)
        if args.command == "demo":
            return _run_demo(args)
        if args.command == "ingest":
            return _run_ingest(args)
    except UpsetProfilerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"error: invalid input: {exc}", file=sys.stderr)
        return 1

    print(
        f"Command {args.command!r} is not implemented until {_NOT_IMPLEMENTED[args.command]}.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

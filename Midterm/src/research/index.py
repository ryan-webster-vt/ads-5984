"""Build the persistent ChromaDB research index and its version manifest."""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

import config
from models import EvidenceChunk, Faculty, IndexManifest, Publication
from src.io_utils import write_json_atomic
from src.llm import EmbeddingClient
from src.research.chunking import chunk_faculty_profile, chunk_publication
from src.research.dedupe import dedupe_publications
from src.research.identity import build_eligible_faculty_index
from src.research.loaders import load_faculty, load_publications, sha256_file

ProgressCallback = Callable[[str], None]

REBUILD_COMMAND = "python3 scripts/build_index.py --rebuild"


class IndexMismatchError(RuntimeError):
    """Raised when the stored index cannot be used with the current configuration."""

    def __init__(self, issues: list[str]) -> None:
        detail = "; ".join(issues)
        super().__init__(
            f"Research index must be rebuilt with `{REBUILD_COMMAND}`: {detail}"
        )
        self.issues = issues


class ConfiguredEmbeddingFunction(EmbeddingFunction):
    """Chroma adapter that uses the same embedding service for ingest and query."""

    def __init__(self) -> None:
        self.embedder = EmbeddingClient()

    def __call__(self, input: Documents) -> Embeddings:
        return self.embedder.embed(list(input))


def _report(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def load_manifest(path: Path | None = None) -> IndexManifest | None:
    path = path or config.INDEX_MANIFEST_PATH
    if not path.exists():
        return None
    return IndexManifest.model_validate_json(path.read_text(encoding="utf-8"))


def current_corpus_hashes() -> tuple[str, str, str]:
    authors_path = config.RAW_DATA_DIR / "authors.csv"
    publications_path = config.RAW_DATA_DIR / "publications.csv"
    authors_hash = sha256_file(authors_path)
    publications_hash = sha256_file(publications_path)
    return authors_hash, publications_hash, f"{authors_hash}:{publications_hash}"


def index_compatibility_issues(
    manifest: IndexManifest,
    *,
    expected_model: str | None = None,
    expected_corpus_hash: str | None = None,
) -> list[str]:
    """Return every reason the persisted index must be rebuilt."""
    issues: list[str] = []
    expected_model = expected_model or config.optional_env("EMBED_MODEL")

    if manifest.index_version != config.INDEX_VERSION:
        issues.append(
            f"index version is {manifest.index_version!r}; "
            f"expected {config.INDEX_VERSION!r}"
        )
    if manifest.collection_name != config.COLLECTION_NAME:
        issues.append(
            f"collection is {manifest.collection_name!r}; "
            f"expected {config.COLLECTION_NAME!r}"
        )
    if manifest.schema_version != config.SCHEMA_VERSION:
        issues.append(
            f"manifest schema is {manifest.schema_version!r}; "
            f"expected {config.SCHEMA_VERSION!r}"
        )
    if manifest.chunker_version != config.CHUNKER_VERSION:
        issues.append(
            f"chunker version is {manifest.chunker_version!r}; "
            f"expected {config.CHUNKER_VERSION!r}"
        )
    if expected_model is None:
        issues.append("EMBED_MODEL is not configured")
    elif manifest.embedding_model != expected_model:
        issues.append(
            f"embedding model is {manifest.embedding_model!r}; "
            f"expected {expected_model!r}"
        )
    if expected_corpus_hash and manifest.corpus_sha256 != expected_corpus_hash:
        issues.append("corpus data changed since the index was built")
    return issues


def assert_index_compatible(manifest: IndexManifest, **kwargs: str | None) -> None:
    issues = index_compatibility_issues(manifest, **kwargs)
    if issues:
        raise IndexMismatchError(issues)


def select_index_publications(
    publications: list[Publication],
    *,
    limit: int | None = None,
) -> list[Publication]:
    """Select a deterministic, documented publication set for indexing.

    The full corpus is the default. A limit keeps the newest and most-cited
    attributed works and stores the resulting scope in the manifest so a preview
    index can never be mistaken for the full corpus.
    """
    ordered = sorted(
        publications,
        key=lambda publication: (
            -publication.year,
            -(publication.cited_by or 0),
            publication.eid,
        ),
    )
    selected = ordered if limit is None else ordered[: max(0, limit)]
    return sorted(selected, key=lambda publication: publication.eid)


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.BASE_DIR,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _load_research_data(
    source: str,
    limit: int | None,
) -> tuple[
    list[Faculty],
    list[Publication],
    list[Publication],
    list[Publication],
    list[Publication],
]:
    if source != "csv":
        raise RuntimeError(
            "Elsevier live ingestion is not enabled in Phase 2. The adapter is "
            "fixture-tested only; run with --source csv."
        )
    faculty = load_faculty()
    eligible = build_eligible_faculty_index(faculty)
    raw_publications = load_publications(eligible)
    canonical = dedupe_publications(raw_publications)
    attributed = [
        publication for publication in canonical if publication.faculty_ids
    ]
    selected = select_index_publications(attributed, limit=limit)
    return faculty, canonical, attributed, selected, raw_publications


def _existing_collection(
    client: chromadb.ClientAPI,
    name: str,
) -> chromadb.Collection | None:
    try:
        return client.get_collection(name=name)
    except Exception:
        return None


def _delete_collection(client: chromadb.ClientAPI, name: str) -> None:
    with contextlib.suppress(Exception):
        client.delete_collection(name=name)


def build_index(
    *,
    rebuild: bool = False,
    incremental: bool = False,
    source: str = config.DEFAULT_INDEX_SOURCE,
    max_publications: int | None = None,
    progress: ProgressCallback | None = None,
) -> IndexManifest:
    """Build or reuse the persistent research index.

    - ``rebuild`` always replaces the collection.
    - ``incremental`` may replace the collection when only CSV content changed.
    - model, chunker, schema, or index-version changes always require ``rebuild``.
    """
    config.ensure_directories()
    authors_hash, publications_hash, corpus_hash = current_corpus_hashes()
    faculty, canonical, attributed, selected, raw_publications = _load_research_data(
        source, max_publications
    )
    eligible = build_eligible_faculty_index(faculty)
    faculty_names = {
        faculty_id: member.author_name for faculty_id, member in eligible.items()
    }

    _report(
        progress,
        f"Loaded {len(raw_publications):,} raw rows; "
        f"{len(canonical):,} canonical works; "
        f"{len(selected):,} selected for indexing.",
    )

    chunks: list[EvidenceChunk] = []
    for publication in selected:
        chunks.extend(
            chunk_publication(
                publication,
                faculty_names,
                index_version=config.INDEX_VERSION,
            )
        )
    for faculty_id in sorted(eligible):
        chunks.append(
            chunk_faculty_profile(
                eligible[faculty_id],
                index_version=config.INDEX_VERSION,
            )
        )

    ids = [chunk.evidence_id for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate evidence IDs generated; refusing to index")

    embedder = ConfiguredEmbeddingFunction()
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    existing = _existing_collection(client, config.COLLECTION_NAME)
    existing_manifest = load_manifest()
    hard_issues: list[str] = []
    corpus_issues: list[str] = []
    if existing is not None:
        if existing_manifest is None:
            hard_issues = ["index manifest is missing"]
        else:
            all_issues = index_compatibility_issues(
                existing_manifest,
                expected_model=embedder.embedder.model,
                expected_corpus_hash=corpus_hash,
            )
            hard_issues = [
                issue
                for issue in all_issues
                if not issue.startswith("corpus data changed")
            ]
            corpus_issues = [
                issue
                for issue in all_issues
                if issue.startswith("corpus data changed")
            ]
        if existing.count() != len(chunks):
            count_issue = (
                f"collection has {existing.count():,} vectors; "
                f"expected {len(chunks):,}"
            )
            (corpus_issues if incremental else hard_issues).append(count_issue)

    can_reuse = (
        existing is not None
        and existing_manifest is not None
        and not rebuild
        and not hard_issues
        and not corpus_issues
        and existing.count() == len(chunks)
    )
    if can_reuse:
        _report(
            progress,
            f"Index {config.INDEX_VERSION} is up to date; reusing "
            f"{len(chunks):,} vectors.",
        )
        return existing_manifest

    if not rebuild:
        blocking = hard_issues + ([] if incremental else corpus_issues)
        if blocking:
            raise IndexMismatchError(blocking)

    temp_name = f"{config.COLLECTION_NAME}_building"
    _delete_collection(client, temp_name)
    collection = client.create_collection(
        name=temp_name,
        embedding_function=embedder,
        metadata={
            "hnsw:space": "cosine",
            "index_version": config.INDEX_VERSION,
            "source": source,
        },
    )

    total_batches = max(1, (len(chunks) + config.CHROMA_BATCH_SIZE - 1) // config.CHROMA_BATCH_SIZE)
    try:
        for batch_index, start in enumerate(
            range(0, len(chunks), config.CHROMA_BATCH_SIZE), start=1
        ):
            batch = chunks[start : start + config.CHROMA_BATCH_SIZE]
            texts = [chunk.text for chunk in batch]
            vectors = embedder.embedder.embed(texts)
            if len(vectors) != len(batch):
                raise RuntimeError("embedding count does not match chunk count")
            collection.add(
                ids=[chunk.evidence_id for chunk in batch],
                documents=texts,
                metadatas=[chunk.metadata() for chunk in batch],
                embeddings=vectors,
            )
            _report(
                progress,
                f"Embedded batch {batch_index:,}/{total_batches:,} "
                f"({min(start + len(batch), len(chunks)):,}/{len(chunks):,} chunks).",
            )
    except Exception:
        _delete_collection(client, temp_name)
        raise

    _delete_collection(client, config.COLLECTION_NAME)
    collection.modify(name=config.COLLECTION_NAME)
    built_collection = _existing_collection(client, config.COLLECTION_NAME)
    if built_collection is None or built_collection.count() != len(chunks):
        raise RuntimeError("index swap failed; the collection is incomplete")

    unattributed_rows = sum(
        1 for publication in raw_publications if not publication.faculty_ids
    )
    unattributed_canonical = len(canonical) - len(attributed)
    publication_chunks = sum(
        1 for chunk in chunks if chunk.chunk_type == "publication"
    )
    faculty_chunks = sum(
        1 for chunk in chunks if chunk.chunk_type == "faculty_profile"
    )
    is_full_corpus = len(selected) == len(attributed)
    manifest = IndexManifest(
        index_version=config.INDEX_VERSION,
        collection_name=config.COLLECTION_NAME,
        source=source,
        index_scope=config.DEFAULT_INDEX_SCOPE if is_full_corpus else "subset",
        is_full_corpus=is_full_corpus,
        schema_version=config.SCHEMA_VERSION,
        chunker_version=config.CHUNKER_VERSION,
        embedding_model=embedder.embedder.model,
        embedding_dimension=embedder.embedder.dimension,
        corpus_sha256=corpus_hash,
        authors_sha256=authors_hash,
        publications_sha256=publications_hash,
        publication_rows=len(raw_publications),
        canonical_publications=len(canonical),
        attributed_publications=len(attributed),
        unattributed_publication_rows=unattributed_rows,
        unattributed_canonical_publications=unattributed_canonical,
        multi_faculty_publications=sum(
            1 for publication in attributed if len(publication.faculty_ids) >= 2
        ),
        selected_publications=len(selected),
        faculty_rows=len(faculty),
        unique_faculty=len({member.scopus_id for member in faculty}),
        conflict_ids=len(
            {
                member.scopus_id
                for member in faculty
                if member.identity_conflict
            }
        ),
        publication_chunks=publication_chunks,
        faculty_chunks=faculty_chunks,
        total_chunks=len(chunks),
        git_commit=_git_commit(),
        app_version=config.APP_VERSION,
        selection_scope={
            "limit": max_publications,
            "order": "year_desc,cited_by_desc,eid_asc",
            "is_full_corpus": is_full_corpus,
        },
        created_at=datetime.now(UTC).isoformat(),
    )
    write_json_atomic(config.INDEX_MANIFEST_PATH, manifest.model_dump(mode="json"))
    _report(
        progress,
        f"Built {config.INDEX_VERSION}: {len(chunks):,} vectors "
        f"({publication_chunks:,} publication + {faculty_chunks:,} profile).",
    )
    return manifest


def get_collection(*, validate: bool = True) -> chromadb.Collection:
    manifest = load_manifest()
    if validate:
        if manifest is None:
            raise IndexMismatchError(["index manifest is missing"])
        _, _, corpus_hash = current_corpus_hashes()
        issues = index_compatibility_issues(
            manifest,
            expected_model=config.optional_env("EMBED_MODEL"),
            expected_corpus_hash=corpus_hash,
        )
        if issues:
            raise IndexMismatchError(issues)

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    collection = client.get_collection(
        name=config.COLLECTION_NAME,
        embedding_function=ConfiguredEmbeddingFunction(),
    )
    if validate and manifest and collection.count() != manifest.total_chunks:
        raise IndexMismatchError(
            [
                f"collection has {collection.count():,} vectors; "
                f"manifest expects {manifest.total_chunks:,}"
            ]
        )
    return collection

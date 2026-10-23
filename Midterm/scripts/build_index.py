"""CLI for building the persistent research index."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.research.index import IndexMismatchError, build_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete and rebuild the Chroma collection.",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "Reuse a compatible index; rebuild automatically when only the CSV "
            "corpus changed. Model, chunker, and schema changes still require "
            "--rebuild."
        ),
    )
    parser.add_argument(
        "--source",
        choices=("csv", "elsevier"),
        default=config.DEFAULT_INDEX_SOURCE,
        help="Publication source. Elsevier is fixture-tested only in Phase 2.",
    )
    parser.add_argument(
        "--limit",
        "--max-publications",
        dest="limit",
        type=int,
        default=None,
        help=(
            "Optional cap on selected attributed publications for a preview "
            "build. The manifest records the reduced scope."
        ),
    )
    args = parser.parse_args()

    try:
        manifest = build_index(
            rebuild=args.rebuild,
            incremental=args.incremental,
            source=args.source,
            max_publications=args.limit,
            progress=print,
        )
    except IndexMismatchError as exc:
        print(f"Index mismatch: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        print(f"Index build failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Index version: {manifest.index_version}")
    print(f"Collection: {manifest.collection_name}")
    print(f"Source: {manifest.source} ({manifest.index_scope})")
    print(f"Full corpus: {manifest.is_full_corpus}")
    print(f"Embedding model: {manifest.embedding_model}")
    print(f"Embedding dimension: {manifest.embedding_dimension}")
    print(f"Raw publication rows: {manifest.publication_rows}")
    print(f"Canonical publications: {manifest.canonical_publications}")
    print(f"Attributed publications: {manifest.attributed_publications}")
    print(f"Unattributed canonical publications: {manifest.unattributed_canonical_publications}")
    print(f"Multi-faculty publications: {manifest.multi_faculty_publications}")
    print(f"Selected publications: {manifest.selected_publications}")
    print(f"Publication chunks: {manifest.publication_chunks}")
    print(f"Faculty profile chunks: {manifest.faculty_chunks}")
    print(f"Total vectors: {manifest.total_chunks}")
    print(f"Git commit: {manifest.git_commit or 'unknown'}")
    print(f"Manifest: {config.INDEX_MANIFEST_PATH}")


if __name__ == "__main__":
    main()

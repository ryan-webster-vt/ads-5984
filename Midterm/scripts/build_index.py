"""CLI for building the persistent research index."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow the CLI to import project modules when executed from any directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.research.index import IndexMismatchError, build_index


def main() -> None:
    # The CLI mirrors build_index's options so both CSV and Elsevier builds are
    # reproducible from the shell.
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
            "Reuse a compatible index; rebuild automatically when only the "
            "source corpus changed. Model, chunker, and schema changes still "
            "require --rebuild."
        ),
    )
    parser.add_argument(
        "--source",
        choices=config.PUBLICATION_SOURCES,
        default=config.configured_publication_source(),
        help=(
            "Publication source. CSV is the reproducible default; Elsevier is "
            "key-gated and only verified against fixtures without a live key."
        ),
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
    # These options bound Scopus quota; defaults live in config for consistency.
    parser.add_argument(
        "--elsevier-max-results-per-faculty",
        type=int,
        default=config.ELSEVIER_MAX_RESULTS_PER_FACULTY,
        help="Optional per-author Scopus result cap to bound quota use.",
    )
    parser.add_argument(
        "--elsevier-page-size",
        type=int,
        default=config.ELSEVIER_SEARCH_PAGE_SIZE,
        help="Scopus Search page size (default 25).",
    )
    parser.add_argument(
        "--no-elsevier-abstracts",
        action="store_true",
        help=(
            "Use Scopus Search metadata only. Abstracts are required for full "
            "evidence, so this is a quota-saving preview mode."
        ),
    )
    args = parser.parse_args()

    try:
        # Progress is printed as plain lines so long builds are observable in a
        # terminal or CI log.
        manifest = build_index(
            rebuild=args.rebuild,
            incremental=args.incremental,
            source=args.source,
            max_publications=args.limit,
            elsevier_page_size=args.elsevier_page_size,
            elsevier_max_results_per_faculty=(
                args.elsevier_max_results_per_faculty
            ),
            elsevier_fetch_abstracts=not args.no_elsevier_abstracts,
            progress=print,
        )
    except IndexMismatchError as exc:
        print(f"Index mismatch: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        # Includes ElsevierNotConfigured/API errors; messages are already safe.
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

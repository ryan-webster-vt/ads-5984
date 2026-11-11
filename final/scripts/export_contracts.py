#!/usr/bin/env python3
"""Export or verify the checked-in JSON Schemas under ``docs/contracts``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from upset_profiler.models.export import check_schemas, write_schemas  # noqa: E402

DEFAULT_OUTPUT = ROOT / "docs" / "contracts"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if checked-in schemas differ from generated schemas",
    )
    args = parser.parse_args(argv)

    if args.check:
        differences = check_schemas(args.output)
        if differences:
            for difference in differences:
                print(difference, file=sys.stderr)
            print(
                "Run 'python3 scripts/export_contracts.py' to refresh the schemas.",
                file=sys.stderr,
            )
            return 1
        print(f"Contracts are up to date in {args.output}.")
        return 0

    written = write_schemas(args.output)
    print(f"Wrote {len(written)} contract schemas to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

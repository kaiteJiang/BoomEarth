"""Prepare one fail-closed V2 production-revision brief."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.production_revision import (  # noqa: E402
    ProductionRevisionError,
    prepare_production_revision,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ProductionRevisionError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Prepare a V2 production revision.")
    parser.add_argument("work_id")
    parser.add_argument("--original-active-project", required=True)
    parser.add_argument("--archive-slug", required=True)
    parser.add_argument("--duration-target-s", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        record = prepare_production_revision(
            root,
            arguments.work_id,
            original_active_project=arguments.original_active_project,
            archive_slug=arguments.archive_slug,
            duration_target_s=arguments.duration_target_s,
        )
        del record
        safe_id = arguments.work_id.replace("-", "")[:12]
        print(f"work={safe_id} revision=2 status=brief-created")
        return 0
    except ProductionRevisionError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

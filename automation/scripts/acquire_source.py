"""Plan or run one authorized P2 source acquisition."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.source_acquisition import (  # noqa: E402
    SourceAcquisitionError,
    plan_source_acquisition,
    run_source_acquisition,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SourceAcquisitionError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Plan or run one approved source acquisition.")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("work_id")
    plan.add_argument("--provider", required=True, choices=("yt-dlp", "tikhub"))
    run = commands.add_parser("run")
    run.add_argument("work_id")
    run.add_argument("--provider", required=True, choices=("yt-dlp", "tikhub"))
    run.add_argument("--approval", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "plan":
            plan_source_acquisition(root, arguments.work_id, arguments.provider)
            status = "planned"
        else:
            run_source_acquisition(
                root,
                arguments.work_id,
                arguments.provider,
                arguments.approval,
            )
            status = "created"
        safe_id = arguments.work_id.replace("-", "")[:12]
        print(f"work={safe_id} action=source-acquisition status={status}")
        return 0
    except SourceAcquisitionError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

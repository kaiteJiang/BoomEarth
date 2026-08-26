"""Compile or recover one source-free P2 production handoff."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.handoff_compiler import (  # noqa: E402
    HandoffCompilerError,
    compile_source_handoff,
    recover_handoff_receipt,
)
from boomearth.workbench.production_revision import (  # noqa: E402
    ProductionRevisionError,
    compile_production_revision,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise HandoffCompilerError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Compile or recover a source-free handoff.")
    commands = parser.add_subparsers(dest="command", required=True)
    compile_command = commands.add_parser("compile")
    compile_command.add_argument("work_id")
    compile_command.add_argument("--candidate", required=True, type=Path)
    compile_command.add_argument("--review", required=True, type=Path)
    recover = commands.add_parser("recover")
    recover.add_argument("work_id")
    revise = commands.add_parser("revise")
    revise.add_argument("work_id")
    revise.add_argument("--candidate", required=True, type=Path)
    revise.add_argument("--review", required=True, type=Path)
    revise.add_argument("--original-active-project", required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "compile":
            publication = compile_source_handoff(
                root,
                arguments.work_id,
                arguments.candidate,
                arguments.review,
            )
            status = "created"
        elif arguments.command == "recover":
            publication = recover_handoff_receipt(root, arguments.work_id)
            status = "recovered"
        else:
            publication = compile_production_revision(
                root,
                arguments.work_id,
                arguments.candidate,
                arguments.review,
                original_active_project=arguments.original_active_project,
            )
            safe_id = publication.work_id.replace("-", "")[:12]
            print(f"work={safe_id} revision=2 status=created")
            return 0
        safe_id = publication.work_id.replace("-", "")[:12]
        print(f"work={safe_id} stage=handoff_ready status={status}")
        return 0
    except (HandoffCompilerError, ProductionRevisionError) as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

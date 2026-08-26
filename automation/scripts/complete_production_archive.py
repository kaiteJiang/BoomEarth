"""Bind or verify one archived P1 delivery report as the terminal P2 event."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.production_archive import (  # noqa: E402
    ProductionArchiveError,
    complete_production_archive,
    verify_production_archive,
)


class _Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise ProductionArchiveError("production-archive-invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("apply", "verify"):
        command = commands.add_parser(name)
        command.add_argument("work_id")
        command.add_argument("--archive-project", required=True)
        command.add_argument("--workspace-root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        operation = (
            complete_production_archive
            if arguments.command == "apply"
            else verify_production_archive
        )
        result = operation(
            arguments.workspace_root,
            arguments.work_id,
            arguments.archive_project,
        )
        safe_id = result.work_id.replace("-", "")[:12]
        print(
            f"work={safe_id} stage=production_archived status={result.status}"
        )
        return 0
    except ProductionArchiveError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

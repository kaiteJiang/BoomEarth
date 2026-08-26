"""Prepare one private source-bound rewrite brief."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.rewrite_package import (  # noqa: E402
    RewritePackageError,
    prepare_rewrite_brief,
)
from boomearth.workbench.source_ledger import (  # noqa: E402
    SourceLedgerError,
    WashEventLedger,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise RewritePackageError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Prepare one private rewrite brief.")
    parser.add_argument("work_id")
    parser.add_argument("--platform", required=True)
    parser.add_argument("--duration-target-s", required=True, type=int)
    parser.add_argument("--archive-slug", required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        prepare_rewrite_brief(
            root,
            arguments.work_id,
            platform=arguments.platform,
            duration_target_s=arguments.duration_target_s,
            archive_slug=arguments.archive_slug,
        )
        stage = WashEventLedger(root).current(arguments.work_id).stage
        safe_id = arguments.work_id.replace("-", "")[:12]
        print(f"work={safe_id} stage={stage} status=brief-created")
        return 0
    except SourceLedgerError:
        print("error=rewrite-source-invalid", file=sys.stderr)
        return 2
    except RewritePackageError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

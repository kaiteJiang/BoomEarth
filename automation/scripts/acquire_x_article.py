"""Plan or run one authorized private X Article acquisition."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.x_article_acquisition import (  # noqa: E402
    HIGH_MEDIA_PROFILE,
    STANDARD_PROFILE,
    XArticleAcquisitionError,
    plan_x_article_acquisition,
    run_x_article_acquisition,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise XArticleAcquisitionError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Plan or run one approved X Article acquisition.")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("work_id")
    plan.add_argument(
        "--profile",
        choices=(STANDARD_PROFILE, HIGH_MEDIA_PROFILE),
        default=STANDARD_PROFILE,
    )
    run = commands.add_parser("run")
    run.add_argument("work_id")
    run.add_argument("--approval", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "plan":
            action_plan = plan_x_article_acquisition(
                root,
                arguments.work_id,
                profile=arguments.profile,
            )
            action = action_plan.action
            status = "planned"
        else:
            run_x_article_acquisition(root, arguments.work_id, arguments.approval)
            action = "x-article-acquisition"
            status = "created"
        safe_id = arguments.work_id.replace("-", "")[:12]
        print(f"work={safe_id} action={action} status={status}")
        return 0
    except XArticleAcquisitionError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

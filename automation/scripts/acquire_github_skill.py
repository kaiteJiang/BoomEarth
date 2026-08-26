"""Plan, run, or recover one private GitHub Skill acquisition."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.github_skill_acquisition import (  # noqa: E402
    GitHubSkillAcquisitionError,
    plan_github_skill_acquisition,
    recover_github_skill_acquisition,
    run_github_skill_acquisition,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise GitHubSkillAcquisitionError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Plan, run, or recover a GitHub Skill snapshot.")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("work_id")
    run = commands.add_parser("run")
    run.add_argument("work_id")
    run.add_argument("--approval", required=True, type=Path)
    recover = commands.add_parser("recover")
    recover.add_argument("work_id")
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "plan":
            plan_github_skill_acquisition(root, arguments.work_id)
            status = "planned"
        elif arguments.command == "run":
            run_github_skill_acquisition(
                root, arguments.work_id, arguments.approval
            )
            status = "created"
        else:
            recover_github_skill_acquisition(root, arguments.work_id)
            status = "recovered"
        safe_id = arguments.work_id.replace("-", "")[:12]
        print(
            f"work={safe_id} action=github-skill-acquisition status={status}"
        )
        return 0
    except GitHubSkillAcquisitionError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

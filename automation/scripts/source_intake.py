"""Create one authorized private P2 source work order."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.source_artifacts import SourceContractError  # noqa: E402
from boomearth.workbench.source_intake import (  # noqa: E402
    create_github_skill_intake,
    create_local_intake,
    create_url_intake,
    create_x_article_intake,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SourceContractError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Create an authorized private source work order.")
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("local")
    local.add_argument("path", type=Path)
    local.add_argument("--authorized", action="store_true")
    url = commands.add_parser("url")
    url.add_argument("--input-file", required=True, type=Path)
    url.add_argument("--authorized", action="store_true")
    article = commands.add_parser("x-article")
    article.add_argument("--input-file", required=True, type=Path)
    article.add_argument("--authorized", action="store_true")
    github_skill = commands.add_parser("github-skill")
    github_skill.add_argument("--input-file", required=True, type=Path)
    github_skill.add_argument("--authorized", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "local":
            order = create_local_intake(
                root,
                arguments.path,
                authorized=arguments.authorized,
            )
        elif arguments.command == "url":
            order = create_url_intake(
                root,
                arguments.input_file,
                authorized=arguments.authorized,
            )
        elif arguments.command == "x-article":
            order = create_x_article_intake(
                root,
                arguments.input_file,
                authorized=arguments.authorized,
            )
        else:
            order = create_github_skill_intake(
                root,
                arguments.input_file,
                authorized=arguments.authorized,
            )
        safe_id = order.work_id.replace("-", "")[:12]
        print(f"work={safe_id} stage=source_registered status=created")
        return 0
    except SourceContractError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

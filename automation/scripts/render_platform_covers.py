"""Render deterministic platform covers locally; no provider is called."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.video.platform_covers import PlatformCoverError, render_platform_covers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    parser.add_argument("source_image", type=Path)
    parser.add_argument("headline")
    parser.add_argument("--font", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = render_platform_covers(
            args.project_root,
            args.source_image,
            args.headline,
            font_path=args.font,
        )
        project = Path(args.project_root).absolute()
        relative_paths = [
            path.relative_to(project).as_posix() for path in result.artifacts
        ]
    except (OSError, PlatformCoverError, ValueError) as error:
        rule = str(error) if isinstance(error, PlatformCoverError) else "cover-local-stage"
        print(f"status=error rule={rule}")
        return 2
    print(f"status=pass reused={'true' if result.reused else 'false'}")
    for relative in relative_paths:
        print(f"artifact={relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

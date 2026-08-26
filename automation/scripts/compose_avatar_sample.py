from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.video.avatar_composite import (  # noqa: E402
    AvatarCompositeError,
    plan_circle_avatar,
    probe_video_size,
    render_circle_avatar,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--background", required=True, type=Path)
    parser.add_argument("--master", required=True, type=Path)
    parser.add_argument("--narration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--caption-safe-top", type=int, default=900)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        plan = plan_circle_avatar(
            source_size=probe_video_size(arguments.master),
            occupied=(),
            caption_safe_top=arguments.caption_safe_top,
        )
        render_circle_avatar(
            arguments.background,
            arguments.master,
            arguments.narration,
            arguments.output,
            plan,
        )
    except (AvatarCompositeError, OSError, RuntimeError, SystemExit):
        print("status=failed")
        return 2
    print("status=pass")
    print("profile=headroom_08-circle-lower-left")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

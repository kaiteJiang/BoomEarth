"""Compile a formal final-audio-bound motion plan."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.video.motion_plan import MotionPlanError, compile_motion_plan  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_root", type=Path)
    try:
        args = parser.parse_args(argv)
        snapshot = compile_motion_plan(project_root=args.project_root)
        print(f"motion={snapshot.snapshot.sha256[:12]} status=created")
        return 0
    except (MotionPlanError, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code == 0:
            return 0
        print("error=motion-plan-failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

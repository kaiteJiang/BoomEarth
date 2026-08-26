"""Publish one reviewed content plan for a canonical active project; offline only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from boomearth.video.content_plan import ContentPlanError, compile_content_plan
from run_v2_production_sample import load_production_project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--active-project", required=True)
    parser.add_argument("--candidate", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        relative = Path(args.candidate)
        if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
            raise ContentPlanError("candidate path is invalid")
        project = load_production_project(
            workspace_root=args.workspace_root,
            active_project=args.active_project,
        )
        candidate = project.active_dir / relative
        result = compile_content_plan(
            project_root=project.active_dir,
            candidate_path=candidate,
        )
    except (ContentPlanError, OSError, RuntimeError, TypeError, ValueError):
        print("rule=content-plan-compile")
        return 2
    print("status=content-plan-published")
    print("artifact=工程/content-plan.json")
    print(f"scenes={len(result.plan.scenes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

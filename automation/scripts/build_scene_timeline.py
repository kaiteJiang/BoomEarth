"""Publish one final-audio scene timeline for a canonical active project."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from boomearth.video.scene_timeline import SceneTimelineError, build_scene_timeline
from run_v2_production_sample import load_production_project


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--active-project", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = load_production_project(
            workspace_root=args.workspace_root,
            active_project=args.active_project,
        )
        result = build_scene_timeline(project_root=project.active_dir)
    except (SceneTimelineError, OSError, RuntimeError, TypeError, ValueError):
        print("rule=scene-timeline-build")
        return 2
    print("status=scene-timeline-published")
    print("artifact=工程/scene-timeline.json")
    print(f"scenes={len(result.timeline.scenes)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

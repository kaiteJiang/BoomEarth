from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from boomearth.video.artifacts import ArtifactError, capture_regular_file
from boomearth.video.profiled_generation import (
    ProfiledGenerationError,
    complete_profiled_generation_call,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--theme", required=True)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--candidate-index", required=True, type=int, choices=(1, 2))
    parser.add_argument("--candidate-source", required=True, type=Path)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--intent-sha256", required=True)
    parser.add_argument("--adapt-runtime-native", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        candidate = capture_regular_file(arguments.candidate_source)
        artifact = complete_profiled_generation_call(
            project_root=arguments.project_root,
            theme_id=arguments.theme,
            scene_id=arguments.scene,
            candidate_index=arguments.candidate_index,
            call_id=arguments.call_id,
            intent_sha256=arguments.intent_sha256,
            candidate_payload=candidate.payload,
            adapt_runtime_native=arguments.adapt_runtime_native,
        )
    except (ArtifactError, ProfiledGenerationError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(artifact, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

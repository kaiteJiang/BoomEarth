from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from boomearth.video.artifacts import ArtifactError, capture_regular_file
from boomearth.video.illustration_manifest import (
    IllustrationManifestError,
    validate_v4_prompt_contract,
)
from boomearth.video.profiled_generation import (
    ProfiledGenerationError,
    prepare_profiled_generation_call,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--theme", required=True)
    parser.add_argument("--scene", required=True)
    parser.add_argument("--candidate-index", required=True, type=int, choices=(1, 2))
    parser.add_argument("--prompt-source", required=True, type=Path)
    parser.add_argument("--call-id")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        prompt = capture_regular_file(arguments.prompt_source)
        prompt_values = validate_v4_prompt_contract(prompt.payload)
        if (
            prompt_values["scene_id"] != arguments.scene
            or prompt_values["visual_style"] != arguments.theme
            or prompt_values["visual_theme"] != arguments.theme
        ):
            raise IllustrationManifestError("illustration-prompt-invalid")
        ticket = prepare_profiled_generation_call(
            project_root=arguments.project_root,
            theme_id=arguments.theme,
            scene_id=arguments.scene,
            prompt_payload=prompt.payload,
            candidate_index=arguments.candidate_index,
            call_id=arguments.call_id,
        )
    except (ArtifactError, IllustrationManifestError, ProfiledGenerationError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(ticket.payload(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

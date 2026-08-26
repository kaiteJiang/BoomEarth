"""Normalize one verified P2 source-media snapshot into canonical private WAV."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.media.source_audio import (  # noqa: E402
    SourceAudioError,
    normalize_source_audio,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise SourceAudioError("invalid-arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Normalize verified private source audio.")
    parser.add_argument("work_id")
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    try:
        arguments = _parser().parse_args(argv)
        result = normalize_source_audio(root, arguments.work_id)
        safe_id = arguments.work_id.replace("-", "")[:12]
        print(
            f"work={safe_id} stage=audio_ready status=created "
            f"duration_s={result.duration_s:.3f}"
        )
        return 0
    except SourceAudioError as error:
        print(f"error={error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

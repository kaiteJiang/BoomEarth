"""Prepare ignored local media for the deterministic BoomEarth video sample."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import wave


SAMPLE_DURATION_SECONDS = 10.0
CAPTIONS = [
    {
        "start": 0.35,
        "end": 2.05,
        "text": "先把想法说清楚。",
        "source": "synthetic-local-sample",
    },
    {
        "start": 2.25,
        "end": 4.1,
        "text": "画面、声音和节奏，再慢慢对齐。",
        "source": "synthetic-local-sample",
    },
    {
        "start": 5.2,
        "end": 7.1,
        "text": "十秒钟，也可以讲明白一件事。",
        "source": "synthetic-local-sample",
    },
    {
        "start": 7.35,
        "end": 9.45,
        "text": "每一步，都留在本地。",
        "source": "synthetic-local-sample",
    },
]
CAPTION_QC = {
    "status": "pass",
    "timing_source": "synthetic-local-sample",
    "synthetic": True,
    "alignment_note": "Synthetic source-free timings for the deterministic 10-second local sample; not ASR output.",
    "groups": 4,
    "duration_seconds": SAMPLE_DURATION_SECONDS,
    "one_group_visible_at_a_time": True,
}
OUTPUT_FILENAMES = ("narration.wav", "captions.json", "caption-qc.json")


class PreparationError(Exception):
    """An intentionally redacted preparation failure."""


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return path.is_symlink() or bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _validate_source_wav(source_wav: Path, media_dir: Path) -> Path:
    if source_wav.suffix.lower() != ".wav" or _is_reparse_point(source_wav):
        raise PreparationError("source-wav-invalid")
    try:
        if not source_wav.is_file():
            raise PreparationError("source-wav-invalid")
        source_wav.absolute().relative_to(media_dir.absolute())
    except ValueError:
        return source_wav
    except OSError:
        raise PreparationError("source-wav-invalid") from None
    raise PreparationError("source-wav-invalid")


def _media_dir_for(workspace_root: Path) -> Path:
    media_dir = workspace_root / "video-sample" / "media"
    if _is_reparse_point(media_dir) or not media_dir.is_dir():
        raise PreparationError("sample-media-invalid")
    return media_dir


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_narration(source_wav: Path, narration_wav: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise PreparationError("ffmpeg-unavailable")
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            os.fspath(source_wav),
            "-t",
            "10",
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            os.fspath(narration_wav),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise PreparationError("source-wav-invalid")
    try:
        with wave.open(str(narration_wav), "rb") as narration:
            duration = narration.getnframes() / narration.getframerate()
    except (OSError, wave.Error, ZeroDivisionError):
        raise PreparationError("sample-narration-invalid") from None
    if duration != SAMPLE_DURATION_SECONDS:
        raise PreparationError("sample-narration-invalid")


def _publish_no_clobber(staging_dir: Path, media_dir: Path) -> None:
    destinations = [media_dir / name for name in OUTPUT_FILENAMES]
    if any(destination.exists() or _is_reparse_point(destination) for destination in destinations):
        raise PreparationError("sample-artifacts-exist")

    published: list[Path] = []
    try:
        for destination in destinations:
            os.link(staging_dir / destination.name, destination)
            published.append(destination)
    except OSError:
        for destination in published:
            try:
                destination.unlink()
            except OSError:
                pass
        raise PreparationError("sample-artifacts-exist") from None


def prepare_sample(*, source_wav: Path, workspace_root: Path) -> None:
    media_dir = _media_dir_for(workspace_root)
    source_wav = _validate_source_wav(source_wav, media_dir)
    if any((media_dir / name).exists() or _is_reparse_point(media_dir / name) for name in OUTPUT_FILENAMES):
        raise PreparationError("sample-artifacts-exist")

    staging_dir = Path(tempfile.mkdtemp(prefix=".sample-prepare-", dir=media_dir))
    try:
        _render_narration(source_wav, staging_dir / "narration.wav")
        _write_json(staging_dir / "captions.json", CAPTIONS)
        _write_json(staging_dir / "caption-qc.json", CAPTION_QC)
        _publish_no_clobber(staging_dir, media_dir)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-wav", required=True, type=Path)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        prepare_sample(source_wav=args.source_wav, workspace_root=args.workspace_root)
    except PreparationError as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    print("status=prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

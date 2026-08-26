#!/usr/bin/env python3
"""Extract an exact inclusive video-frame range with ffmpeg."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, text=True, capture_output=True)


def probe(video: Path) -> dict:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=width,height,pix_fmt,r_frame_rate,avg_frame_rate,start_time,duration,nb_frames,nb_read_frames",
            "-of",
            "json",
            str(video),
        ]
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise SystemExit(f"No video stream found: {video}")
    return streams[0]


def frame_number(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else -1


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract an exact inclusive video-frame range.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--start-frame", type=int, required=True, help="Zero-based source frame index.")
    end_group = parser.add_mutually_exclusive_group(required=True)
    end_group.add_argument("--end-frame", type=int, help="Inclusive zero-based source frame index.")
    end_group.add_argument("--frame-count", type=int, help="Number of frames to extract.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--digits", type=int, default=6)
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit("ffmpeg and ffprobe are required")
    if args.start_frame < 0:
        raise SystemExit("--start-frame must be non-negative")
    if args.frame_count is not None and args.frame_count < 1:
        raise SystemExit("--frame-count must be positive")

    end_frame = (
        args.end_frame
        if args.end_frame is not None
        else args.start_frame + args.frame_count - 1
    )
    if end_frame < args.start_frame:
        raise SystemExit("--end-frame must be greater than or equal to --start-frame")

    video = args.video.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    for old_frame in out.glob("frame-*.png"):
        old_frame.unlink()

    expected_count = end_frame - args.start_frame + 1
    output_pattern = out / f"frame-%0{args.digits}d.png"
    select = f"select=between(n\\,{args.start_frame}\\,{end_frame})"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-vf",
            select,
            "-vsync",
            "0",
            str(output_pattern),
        ],
        check=True,
    )

    frames = sorted(out.glob("frame-*.png"), key=frame_number)
    manifest = {
        "video": str(video),
        "start_frame": args.start_frame,
        "end_frame": end_frame,
        "expected_frames": expected_count,
        "extracted_frames": len(frames),
        "probe": probe(video),
        "first_output": str(frames[0]) if frames else None,
        "last_output": str(frames[-1]) if frames else None,
    }
    (out / "frame-range-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    if len(frames) != expected_count:
        raise SystemExit(
            f"Extracted {len(frames)} frames; expected {expected_count} for "
            f"inclusive range {args.start_frame}-{end_frame}"
        )


if __name__ == "__main__":
    main()

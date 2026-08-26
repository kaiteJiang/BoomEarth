#!/usr/bin/env python3
"""Verify that every sprite cell is pixel-identical to its source frame."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from PIL import Image, ImageChops
except ModuleNotFoundError as error:
    raise SystemExit("Pillow is required; use the workspace Python runtime") from error


def frame_number(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else -1


def numbered_frames(directory: Path) -> list[Path]:
    return sorted(directory.glob("frame-*.png"), key=frame_number)


def max_channel_delta(diff: Image.Image) -> int:
    return max(channel[1] for channel in diff.getextrema())


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify sprite rows against source frames.")
    parser.add_argument("frames", type=Path)
    parser.add_argument("sprites", type=Path)
    parser.add_argument("--prefix", default="frame-sprite-row")
    parser.add_argument("--frames-per-row", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.frames_per_row < 1:
        raise SystemExit("--frames-per-row must be positive")

    frames_dir = args.frames.expanduser().resolve()
    sprites_dir = args.sprites.expanduser().resolve()
    frames = numbered_frames(frames_dir)
    if not frames:
        raise SystemExit(f"No frame-*.png files found in {frames_dir}")

    with Image.open(frames[0]) as first:
        width, height = first.size

    failures = []
    for frame_index, frame_path in enumerate(frames):
        row_index = frame_index // args.frames_per_row
        column_index = frame_index % args.frames_per_row
        sprite_path = sprites_dir / f"{args.prefix}-{row_index}.png"
        if not sprite_path.exists():
            failures.append({"frame": frame_index + 1, "error": f"missing {sprite_path}"})
            continue
        with Image.open(frame_path) as source_image, Image.open(sprite_path) as sprite_image:
            source = source_image.convert("RGB")
            sprite = sprite_image.convert("RGB")
            cell = sprite.crop(
                (
                    column_index * width,
                    0,
                    (column_index + 1) * width,
                    height,
                )
            )
            diff = ImageChops.difference(source, cell)
            if diff.getbbox() is not None:
                failures.append(
                    {
                        "frame": frame_index + 1,
                        "row": row_index,
                        "column": column_index,
                        "max_channel_delta": max_channel_delta(diff),
                    }
                )

    report = {
        "ok": not failures,
        "checked_frames": len(frames),
        "zero_error_frames": len(frames) - len(failures),
        "failed_frames": failures,
    }
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()

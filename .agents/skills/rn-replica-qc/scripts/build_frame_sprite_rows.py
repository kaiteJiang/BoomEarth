#!/usr/bin/env python3
"""Build fixed-width lossless sprite rows from numbered PNG frames."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

try:
    from PIL import Image
except ModuleNotFoundError as error:
    raise SystemExit("Pillow is required; use the workspace Python runtime") from error


def frame_number(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else -1


def numbered_frames(directory: Path) -> list[Path]:
    return sorted(directory.glob("frame-*.png"), key=frame_number)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build sprite rows from numbered PNG frames.")
    parser.add_argument("frames", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--prefix", default="frame-sprite-row")
    parser.add_argument("--frames-per-row", type=int, default=7)
    parser.add_argument("--max-width", type=int, default=16384)
    args = parser.parse_args()

    if args.frames_per_row < 1:
        raise SystemExit("--frames-per-row must be positive")

    frames_dir = args.frames.expanduser().resolve()
    out = args.out.expanduser().resolve()
    frames = numbered_frames(frames_dir)
    if not frames:
        raise SystemExit(f"No frame-*.png files found in {frames_dir}")

    with Image.open(frames[0]) as first:
        width, height = first.size
    row_width = width * args.frames_per_row
    if row_width > args.max_width:
        raise SystemExit(
            f"Sprite row width {row_width} exceeds --max-width {args.max_width}; "
            "reduce --frames-per-row"
        )

    out.mkdir(parents=True, exist_ok=True)
    for old_row in out.glob(f"{args.prefix}-*.png"):
        old_row.unlink()

    row_count = math.ceil(len(frames) / args.frames_per_row)
    outputs = []
    for row_index in range(row_count):
        row = Image.new("RGB", (row_width, height), (0, 0, 0))
        row_frames = frames[
            row_index * args.frames_per_row : (row_index + 1) * args.frames_per_row
        ]
        for column_index, frame_path in enumerate(row_frames):
            with Image.open(frame_path) as source:
                source_rgb = source.convert("RGB")
                if source_rgb.size != (width, height):
                    raise SystemExit(
                        f"Frame size mismatch: {frame_path} is {source_rgb.size}, "
                        f"expected {(width, height)}"
                    )
                row.paste(source_rgb, (column_index * width, 0))
        output = out / f"{args.prefix}-{row_index}.png"
        row.save(output, format="PNG", compress_level=9)
        outputs.append(str(output))

    manifest = {
        "frames": len(frames),
        "frames_per_row": args.frames_per_row,
        "rows": row_count,
        "frame_width": width,
        "frame_height": height,
        "row_width": row_width,
        "outputs": outputs,
    }
    (out / f"{args.prefix}-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()

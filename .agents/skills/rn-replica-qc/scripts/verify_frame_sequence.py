#!/usr/bin/env python3
"""Compare complete PNG frame sequences with pixel and temporal evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageStat
except ModuleNotFoundError as error:
    raise SystemExit("Pillow is required; use the workspace Python runtime") from error


def frame_number(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else -1


def numbered_frames(directory: Path) -> list[Path]:
    return sorted(directory.glob("frame-*.png"), key=frame_number)


def mae(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a, b)
    return sum(ImageStat.Stat(diff).mean) / 3


def pixel_metrics(a: Image.Image, b: Image.Image) -> dict:
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    histogram = diff.convert("L").histogram()
    return {
        "mae": sum(stat.mean) / 3,
        "max_channel_delta": max(channel[1] for channel in diff.getextrema()),
        "changed_pixels": a.width * a.height - histogram[0],
    }


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGB").copy()


def thumbnail(image: Image.Image, width: int) -> Image.Image:
    height = max(1, round(image.height * width / image.width))
    resampling = getattr(Image, "Resampling", Image).BILINEAR
    return image.resize((width, height), resampling)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare complete numbered PNG frame sequences.")
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--frame-count", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-mae", type=float, default=2.5)
    parser.add_argument("--max-excess-mae", type=float, default=0.5)
    parser.add_argument("--boundary-every", type=int, default=0)
    parser.add_argument("--temporal-radius", type=int, default=2)
    parser.add_argument("--temporal-lead", type=float, default=0.1)
    parser.add_argument("--thumbnail-width", type=int, default=384)
    args = parser.parse_args()

    if args.frame_count is not None and args.frame_count < 1:
        raise SystemExit("--frame-count must be positive")
    if args.boundary_every < 0 or args.temporal_radius < 0:
        raise SystemExit("boundary and temporal values must be non-negative")
    if args.thumbnail_width < 1:
        raise SystemExit("--thumbnail-width must be positive")

    reference_dir = args.reference.expanduser().resolve()
    candidate_dir = args.candidate.expanduser().resolve()
    baseline_dir = args.baseline_dir.expanduser().resolve() if args.baseline_dir else None
    reference_paths = numbered_frames(reference_dir)
    candidate_paths = numbered_frames(candidate_dir)
    baseline_paths = numbered_frames(baseline_dir) if baseline_dir else []

    expected = args.frame_count if args.frame_count is not None else len(reference_paths)
    counts = {
        "expected": expected,
        "reference": len(reference_paths),
        "candidate": len(candidate_paths),
        "baseline": len(baseline_paths) if baseline_dir else None,
    }
    if len(reference_paths) != expected or len(candidate_paths) != expected:
        raise SystemExit(f"Frame count mismatch: {counts}")
    if baseline_dir and len(baseline_paths) != expected:
        raise SystemExit(f"Baseline frame count mismatch: {counts}")

    reference_thumbs = []
    candidate_thumbs = []
    for reference_path, candidate_path in zip(reference_paths, candidate_paths):
        reference = load_rgb(reference_path)
        candidate = load_rgb(candidate_path)
        if reference.size != candidate.size:
            raise SystemExit(
                f"Frame size mismatch: {reference_path} {reference.size} != "
                f"{candidate_path} {candidate.size}"
            )
        reference_thumbs.append(thumbnail(reference, args.thumbnail_width))
        candidate_thumbs.append(thumbnail(candidate, args.thumbnail_width))

    frames = []
    for index, (reference_path, candidate_path) in enumerate(
        zip(reference_paths, candidate_paths)
    ):
        reference = load_rgb(reference_path)
        candidate = load_rgb(candidate_path)
        pixel = pixel_metrics(candidate, reference)

        baseline_mae = None
        excess_mae = None
        if baseline_dir:
            baseline = load_rgb(baseline_paths[index])
            if baseline.size != reference.size:
                raise SystemExit(
                    f"Baseline size mismatch: {baseline_paths[index]} {baseline.size} != "
                    f"{reference_path} {reference.size}"
                )
            baseline_mae = mae(baseline, reference)
            excess_mae = max(0.0, pixel["mae"] - baseline_mae)

        candidates = []
        for offset in range(-args.temporal_radius, args.temporal_radius + 1):
            reference_index = index + offset
            if reference_index < 0 or reference_index >= expected:
                continue
            candidates.append(
                {
                    "offset": offset,
                    "mae": mae(candidate_thumbs[index], reference_thumbs[reference_index]),
                }
            )
        candidates.sort(key=lambda item: (item["mae"], abs(item["offset"])))
        best = candidates[0]
        same = next(item for item in candidates if item["offset"] == 0)
        temporal_lead = same["mae"] - best["mae"]
        significant_mismatch = (
            best["offset"] != 0 and temporal_lead > args.temporal_lead
        )

        frames.append(
            {
                "frame": index + 1,
                **pixel,
                "baseline_mae": baseline_mae,
                "excess_mae": excess_mae,
                "best_temporal_offset": best["offset"],
                "temporal_lead": temporal_lead,
                "significant_temporal_mismatch": significant_mismatch,
                "temporal_candidates": candidates,
            }
        )

    boundary_frames = (
        [
            frame
            for frame in frames
            if frame["frame"] > 1 and (frame["frame"] - 1) % args.boundary_every == 0
        ]
        if args.boundary_every
        else []
    )
    temporal_mismatches = [
        frame for frame in frames if frame["significant_temporal_mismatch"]
    ]
    ambiguous_frames = [
        frame
        for frame in frames
        if frame["best_temporal_offset"] != 0
        and not frame["significant_temporal_mismatch"]
    ]
    max_mae = max(frame["mae"] for frame in frames)
    max_boundary_mae = max((frame["mae"] for frame in boundary_frames), default=0.0)
    max_excess_mae = max(
        (frame["excess_mae"] for frame in frames if frame["excess_mae"] is not None),
        default=0.0,
    )
    worst_frames = sorted(frames, key=lambda frame: frame["mae"], reverse=True)[:15]
    baseline_ok = not baseline_dir or max_excess_mae <= args.max_excess_mae
    report = {
        "ok": (
            not temporal_mismatches
            and max_mae <= args.max_mae
            and max_boundary_mae <= args.max_mae
            and baseline_ok
        ),
        "thresholds": {
            "max_mae": args.max_mae,
            "max_excess_mae": args.max_excess_mae if baseline_dir else None,
            "temporal_lead": args.temporal_lead,
            "temporal_radius": args.temporal_radius,
            "boundary_every": args.boundary_every,
        },
        "counts": counts,
        "summary": {
            "frames": expected,
            "average_mae": sum(frame["mae"] for frame in frames) / expected,
            "max_mae": max_mae,
            "max_channel_delta": max(frame["max_channel_delta"] for frame in frames),
            "frames_mae_over_threshold": sum(
                frame["mae"] > args.max_mae for frame in frames
            ),
            "boundary_frames": len(boundary_frames),
            "max_boundary_mae": max_boundary_mae,
            "max_excess_mae": max_excess_mae if baseline_dir else None,
            "temporal_mismatch_frames": [
                {
                    "frame": frame["frame"],
                    "best_temporal_offset": frame["best_temporal_offset"],
                    "temporal_lead": frame["temporal_lead"],
                }
                for frame in temporal_mismatches
            ],
            "temporally_ambiguous_frames": [
                {
                    "frame": frame["frame"],
                    "best_temporal_offset": frame["best_temporal_offset"],
                    "temporal_lead": frame["temporal_lead"],
                }
                for frame in ambiguous_frames
            ],
            "worst_frames": [
                {
                    "frame": frame["frame"],
                    "mae": frame["mae"],
                    "max_channel_delta": frame["max_channel_delta"],
                    "changed_pixels": frame["changed_pixels"],
                    "baseline_mae": frame["baseline_mae"],
                    "excess_mae": frame["excess_mae"],
                }
                for frame in worst_frames
            ],
        },
        "frames": frames,
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

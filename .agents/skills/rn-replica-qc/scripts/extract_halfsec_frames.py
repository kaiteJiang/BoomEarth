#!/usr/bin/env python3
"""Extract fixed-interval video frames and optional contact sheets."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def duration(video: Path) -> float:
    out = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ]
    )
    return float(out)


def safe_time(t: float) -> str:
    return f"{t:.3f}".replace(".", "_")


def extract_frame(video: Path, out: Path, t: float, scale: str) -> None:
    vf = f"scale={scale}" if scale else "null"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            vf,
            str(out),
        ],
        check=True,
    )


def make_contact_sheet(frame_paths: list[Path], out: Path, cols: int, title: str) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False

    if not frame_paths:
        return False

    font_path = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    font = ImageFont.truetype(str(font_path), 22) if font_path.exists() else ImageFont.load_default()
    small = ImageFont.truetype(str(font_path), 18) if font_path.exists() else ImageFont.load_default()

    labeled = []
    for path in frame_paths:
        img = Image.open(path).convert("RGB")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        label = path.stem.replace("frame_t", "").replace("_", ".") + "s"
        d.rectangle((0, 0, img.width, 34), fill=(0, 0, 0, 178))
        d.text((12, 6), label, fill=(255, 255, 255), font=font)
        labeled.append(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

    w, h = labeled[0].size
    rows = math.ceil(len(labeled) / cols)
    pad = 18
    header = 46
    canvas = Image.new("RGB", (cols * w + (cols + 1) * pad, rows * h + (rows + 1) * pad + header), (12, 12, 12))
    d = ImageDraw.Draw(canvas)
    d.text((pad, 12), title, fill=(255, 255, 255), font=small)
    for idx, img in enumerate(labeled):
        x = pad + (idx % cols) * (w + pad)
        y = header + pad + (idx // cols) * (h + pad)
        canvas.paste(img, (x, y))
    canvas.save(out, quality=88)
    return True


def parse_explicit_times(value: str) -> list[float]:
    times = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        times.append(round(float(item), 3))
    return times


def build_times(start: float, end: float, interval: float) -> list[float]:
    times = []
    current = start
    while current <= end + 0.001:
        times.append(round(current, 3))
        current += interval
    return times


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract fixed-interval frames from a video.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--start", type=float, default=0.0, help="Start timestamp in seconds.")
    parser.add_argument("--end", type=float, help="End timestamp in seconds. Defaults to video duration.")
    parser.add_argument("--times", help="Comma-separated explicit timestamps, overriding interval sampling.")
    parser.add_argument("--scale", default="480:270")
    parser.add_argument("--contact", action="store_true", help="Create contact sheets if Pillow is available.")
    parser.add_argument("--cols", type=int, default=3)
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit("ffmpeg and ffprobe are required")
    if args.interval <= 0:
        raise SystemExit("--interval must be positive")

    video = args.video.expanduser().resolve()
    out = args.out.expanduser().resolve()
    frames_dir = out / "frames"
    contact_dir = out / "contact"
    frames_dir.mkdir(parents=True, exist_ok=True)
    contact_dir.mkdir(parents=True, exist_ok=True)

    dur = duration(video)
    start = max(0.0, args.start)
    end = min(args.end if args.end is not None else dur, dur)
    if start > end:
        raise SystemExit("--start must be less than or equal to --end")

    if args.times:
        times = [t for t in parse_explicit_times(args.times) if 0 <= t <= dur + 0.001]
    else:
        times = build_times(start, end, args.interval)

    frames = []
    for t in times:
        path = frames_dir / f"frame_t{safe_time(t)}.jpg"
        extract_frame(video, path, t, args.scale)
        frames.append(path)

    manifest = {
        "video": str(video),
        "duration": dur,
        "start": start,
        "end": end,
        "interval": args.interval,
        "explicit_times": bool(args.times),
        "scale": args.scale,
        "frame_count": len(frames),
        "frames": [str(p) for p in frames],
    }
    (out / "frames-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if args.contact:
        per_sheet = args.cols * 6
        for start in range(0, len(frames), per_sheet):
            chunk = frames[start : start + per_sheet]
            sheet = contact_dir / f"contact_{start // per_sheet + 1:02d}.jpg"
            make_contact_sheet(chunk, sheet, args.cols, f"{video.name} frames {start + 1}-{start + len(chunk)}")

    print(json.dumps({"out": str(out), "frames": len(frames), "duration": dur}, indent=2))


if __name__ == "__main__":
    main()

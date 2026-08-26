#!/usr/bin/env python3
"""Compare videos with hashes, media probes, aggregate metrics, and full-frame evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffprobe(path: Path) -> dict:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-show_entries",
            "format=duration,start_time,size,bit_rate",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate,start_time,duration,nb_frames,nb_read_frames,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def videos_cmp(a: Path, b: Path) -> bool:
    return run(["cmp", "-s", str(a), str(b)], check=False).returncode == 0


def frame_number(path: Path) -> int:
    match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else -1


def decode_range(video: Path, out: Path, start_frame: int, frame_count: int | None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    for old_frame in out.glob("frame-*.png"):
        old_frame.unlink()
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
    ]
    if frame_count is not None:
        end_frame = start_frame + frame_count - 1
        cmd += ["-vf", f"select=between(n\\,{start_frame}\\,{end_frame})"]
    elif start_frame:
        cmd += ["-vf", f"select=gte(n\\,{start_frame})"]
    cmd += ["-vsync", "0", str(out / "frame-%06d.png")]
    subprocess.run(cmd, check=True)
    return sorted(out.glob("frame-*.png"), key=frame_number)


def sequence_metric(reference: Path, candidate: Path, out: Path, name: str) -> str:
    log = out / f"{name}.log"
    result = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-framerate",
            "1",
            "-i",
            str(reference / "frame-%06d.png"),
            "-framerate",
            "1",
            "-i",
            str(candidate / "frame-%06d.png"),
            "-lavfi",
            f"[0:v][1:v]{name}=stats_file={log}",
            "-f",
            "null",
            "-",
        ],
        check=False,
    )
    combined = (result.stderr or "") + "\n" + (result.stdout or "")
    lines = [
        line.strip()
        for line in combined.splitlines()
        if name.upper() in line or f"Parsed_{name}" in line
    ]
    return lines[-1] if lines else "metric unavailable"


def write_report(out: Path, data: dict) -> Path:
    summary = data["frame_metrics"].get("summary", {})
    report = out / "comparison-report.md"
    lines = [
        "# Video Comparison Report",
        "",
        "## Files",
        "",
        f"- Reference: `{data['reference']}`",
        f"- Candidate: `{data['candidate']}`",
        f"- Baseline: `{data.get('baseline')}`",
        "",
        "## Claims",
        "",
        f"- Byte-identical `cmp`: `{data['bit_exact']}`",
        f"- Full-frame delivery gate: `{data['frame_metrics'].get('ok', False)}`",
        f"- Compared frames: `{summary.get('frames')}`",
        f"- Average MAE: `{summary.get('average_mae')}`",
        f"- Maximum MAE: `{summary.get('max_mae')}`",
        f"- Maximum boundary MAE: `{summary.get('max_boundary_mae')}`",
        f"- Significant temporal mismatches: `{len(summary.get('temporal_mismatch_frames', []))}`",
        "",
        "## Aggregate Decoded Metrics",
        "",
        f"- PSNR: `{data.get('psnr', 'not run')}`",
        f"- SSIM: `{data.get('ssim', 'not run')}`",
        "",
        "## Hashes",
        "",
        f"- Reference SHA-256: `{data['reference_sha256']}`",
        f"- Candidate SHA-256: `{data['candidate_sha256']}`",
        "",
        "## Media Info",
        "",
        "```json",
        json.dumps(
            {
                "reference": data["reference_probe"],
                "candidate": data["candidate_probe"],
                "baseline": data.get("baseline_probe"),
            },
            indent=2,
        ),
        "```",
        "",
        "Complete per-frame evidence is in `frame-metrics.json`.",
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare reference and candidate videos.")
    parser.add_argument("reference", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline", type=Path, help="Control encode of the same reference frames.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference-start-frame", type=int, default=0)
    parser.add_argument("--candidate-start-frame", type=int, default=0)
    parser.add_argument("--baseline-start-frame", type=int, default=0)
    parser.add_argument("--frame-count", type=int)
    parser.add_argument("--boundary-every", type=int, default=0)
    parser.add_argument("--max-mae", type=float, default=2.5)
    parser.add_argument("--max-excess-mae", type=float, default=0.5)
    parser.add_argument("--temporal-radius", type=int, default=2)
    parser.add_argument("--temporal-lead", type=float, default=0.1)
    parser.add_argument("--skip-metrics", action="store_true", help="Skip aggregate PSNR/SSIM.")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise SystemExit("ffmpeg and ffprobe are required")
    if args.frame_count is not None and args.frame_count < 1:
        raise SystemExit("--frame-count must be positive")
    for value in (
        args.reference_start_frame,
        args.candidate_start_frame,
        args.baseline_start_frame,
    ):
        if value < 0:
            raise SystemExit("start-frame values must be non-negative")

    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    reference = args.reference.expanduser().resolve()
    candidate = args.candidate.expanduser().resolve()
    baseline = args.baseline.expanduser().resolve() if args.baseline else None

    decoded = out / "decoded"
    reference_frames = decode_range(
        reference, decoded / "reference", args.reference_start_frame, args.frame_count
    )
    candidate_frames = decode_range(
        candidate, decoded / "candidate", args.candidate_start_frame, args.frame_count
    )
    baseline_frames = (
        decode_range(baseline, decoded / "baseline", args.baseline_start_frame, args.frame_count)
        if baseline
        else []
    )

    expected = args.frame_count if args.frame_count is not None else len(reference_frames)
    metrics_path = out / "frame-metrics.json"
    verify_cmd = [
        sys.executable,
        str(SCRIPT_DIR / "verify_frame_sequence.py"),
        str(decoded / "reference"),
        str(decoded / "candidate"),
        "--frame-count",
        str(expected),
        "--output",
        str(metrics_path),
        "--max-mae",
        str(args.max_mae),
        "--max-excess-mae",
        str(args.max_excess_mae),
        "--boundary-every",
        str(args.boundary_every),
        "--temporal-radius",
        str(args.temporal_radius),
        "--temporal-lead",
        str(args.temporal_lead),
    ]
    if baseline:
        verify_cmd += ["--baseline-dir", str(decoded / "baseline")]
    verify_result = run(verify_cmd, check=False)
    if not metrics_path.exists():
        raise SystemExit(verify_result.stderr or verify_result.stdout or "Frame verifier failed")
    frame_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

    data = {
        "reference": str(reference),
        "candidate": str(candidate),
        "baseline": str(baseline) if baseline else None,
        "reference_sha256": sha256(reference),
        "candidate_sha256": sha256(candidate),
        "bit_exact": videos_cmp(reference, candidate),
        "reference_probe": ffprobe(reference),
        "candidate_probe": ffprobe(candidate),
        "baseline_probe": ffprobe(baseline) if baseline else None,
        "decoded_counts": {
            "expected": expected,
            "reference": len(reference_frames),
            "candidate": len(candidate_frames),
            "baseline": len(baseline_frames) if baseline else None,
        },
        "frame_metrics": frame_metrics,
    }
    if not args.skip_metrics and len(reference_frames) == len(candidate_frames):
        data["psnr"] = sequence_metric(decoded / "reference", decoded / "candidate", out, "psnr")
        data["ssim"] = sequence_metric(decoded / "reference", decoded / "candidate", out, "ssim")

    (out / "comparison.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    report = write_report(out, data)
    result = {
        "ok": frame_metrics.get("ok", False),
        "report": str(report),
        "frame_metrics": str(metrics_path),
        "bit_exact": data["bit_exact"],
    }
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()

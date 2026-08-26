"""Create an offline, isolated V1 sample; live APIs are not called."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from boomearth.audio.indextts2 import CURRENT_VOICE_ID
from check_delivery import DeliveryResult, check_delivery


REPARSE_POINT = 0x0400
CONTACT_TIMES_S = [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]


@dataclass(frozen=True, slots=True)
class SampleResult:
    exit_code: int
    diagnostics: tuple[str, ...]
    archive_path: str | None = None


def _is_reparse_point(path: Path) -> bool:
    """Check junctions/symlinks with lstat so an untrusted ancestor is never followed."""
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(entry, "st_file_attributes", 0) & REPARSE_POINT
    )


def _has_safe_ancestors(path: Path) -> bool:
    current = path.absolute()
    while True:
        if _is_reparse_point(current):
            return False
        if current == current.parent:
            return True
        current = current.parent


def _is_contained(root: Path, target: Path) -> bool:
    root_absolute = root.absolute()
    target_absolute = target.absolute()
    try:
        target_absolute.relative_to(root_absolute)
    except ValueError:
        return False
    return _has_safe_ancestors(root_absolute) and _has_safe_ancestors(target_absolute)


def _safe_existing_file(path: Path) -> bool:
    try:
        return _has_safe_ancestors(path) and path.is_file() and not _is_reparse_point(path)
    except OSError:
        return False


def _safe_existing_directory(path: Path) -> bool:
    try:
        return _has_safe_ancestors(path) and path.is_dir() and not _is_reparse_point(path)
    except OSError:
        return False


def _safe_mkdir(root: Path, target: Path) -> None:
    """Create a directory only after every existing ancestor passes the reparse check."""
    if not _is_contained(root, target) or _is_reparse_point(target):
        raise RuntimeError("unsafe-directory")
    target.mkdir(parents=True, exist_ok=True)
    if not _is_contained(root, target) or not _safe_existing_directory(target):
        raise RuntimeError("unsafe-directory")


def _safe_destination(root: Path, target: Path) -> None:
    """Validate an output path before its parent can be created or overwritten."""
    if not _is_contained(root, target) or _is_reparse_point(target):
        raise RuntimeError("unsafe-output")
    _safe_mkdir(root, target.parent)
    if not _is_contained(root, target) or _is_reparse_point(target):
        raise RuntimeError("unsafe-output")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_slug(slug: str) -> bool:
    return re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug) is not None


def _run_command(argv: list[str], *, cwd: Path) -> None:
    """Execute a fixed local argv, preserving redaction at the caller boundary."""
    result = subprocess.run(argv, cwd=cwd, capture_output=True, check=False, timeout=240)
    if result.returncode != 0:
        raise RuntimeError("local-command-failed")


def _copy_file(source: Path, destination: Path, *, root: Path) -> None:
    if not _safe_existing_file(source):
        raise RuntimeError("unsafe-source")
    _safe_destination(root, destination)
    shutil.copy2(source, destination)
    if not _is_contained(root, destination) or not _safe_existing_file(destination):
        raise RuntimeError("unsafe-output")


def _write_json(root: Path, path: Path, value: object) -> None:
    _safe_destination(root, path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(root: Path, path: Path, value: str) -> None:
    _safe_destination(root, path)
    path.write_text(value, encoding="utf-8")


def _period(today: date) -> str:
    return f"{today.month}月{'上旬' if today.day <= 15 else '下旬'}"


def _handoff(slug: str, status: str) -> str:
    receipt = ""
    if status == "已完成":
        receipt = (
            "\n## 制作回执\n\n"
            "- 交付检查：pass\n"
            "- 归档状态：已完成\n"
            "\n## QC结果\n\n"
            "- 联系表：6 帧（3x2），覆盖开场、中段和结尾\n"
            "- 字幕：synthetic-local-sample（仅离线样片）\n"
        )
    return (
        "---\n"
        f"status: {status}\n"
        "platform: local-v1\n"
        "ratio: '16:9'\n"
        "duration_target_s: 10\n"
        "word_count: 80\n"
        f"voice: {CURRENT_VOICE_ID}\n"
        "voice_provider: indextts2-local\n"
        "captions: asr-word-timestamps\n"
        "caption_style: anchor-dark\n"
        "visual: deterministic-sample\n"
        "illustration_skill: none\n"
        f"archive_slug: {slug}\n"
        "---\n\n"
        "## 新稿分段\n\n"
        "这是无来源的合成十秒样片。\n"
        f"{receipt}"
    )


def _format_timestamp(value: float, separator: str) -> str:
    milliseconds = round(value * 1000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"00:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"


def _subtitle_text(captions: list[dict[str, Any]], *, vtt: bool) -> str:
    blocks: list[str] = []
    for index, caption in enumerate(captions, 1):
        timing = (
            f"{_format_timestamp(float(caption['start']), '.' if vtt else ',')} --> "
            f"{_format_timestamp(float(caption['end']), '.' if vtt else ',')}"
        )
        body = f"{timing}\n{caption['text']}" if vtt else f"{index}\n{timing}\n{caption['text']}"
        blocks.append(body)
    return ("WEBVTT\n\n" if vtt else "") + "\n\n".join(blocks) + "\n"


def _tokenize_caption_phrases(captions: list[dict[str, Any]]) -> list[dict[str, object]]:
    """Make explicit synthetic token timings; reject empty phrase text before division."""
    words: list[dict[str, object]] = []
    for phrase in captions:
        text = phrase.get("text")
        if not isinstance(text, str):
            raise ValueError("synthetic-caption-text")
        tokens = [character for character in text if not character.isspace()]
        if not tokens:
            raise ValueError("synthetic-caption-empty-tokens")
        try:
            start = float(phrase["start"])
            end = float(phrase["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("synthetic-caption-timing") from exc
        if end <= start:
            raise ValueError("synthetic-caption-timing")
        step = (end - start) / len(tokens)
        for index, token in enumerate(tokens):
            words.append(
                {
                    "text": token,
                    "start": round(start + step * index, 6),
                    "end": round(start + step * (index + 1), 6),
                    "isGap": False,
                }
            )
    return words


def _create_contact_sheet(final_media: Path, output: Path) -> None:
    """Create deterministic explicit opening/middle/end frames in a 3x2 contact sheet."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg-unavailable")
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    for timestamp in CONTACT_TIMES_S:
        argv.extend(["-ss", f"{timestamp:.1f}", "-i", str(final_media)])
    filters = ";".join(
        f"[{index}:v]scale=480:270,setsar=1[f{index}]" for index in range(len(CONTACT_TIMES_S))
    )
    layout = "|".join(("0_0", "480_0", "960_0", "0_270", "480_270", "960_270"))
    filters += ";" + "".join(f"[f{index}]" for index in range(len(CONTACT_TIMES_S)))
    filters += f"xstack=inputs=6:layout={layout}[sheet]"
    argv.extend(
        [
            "-filter_complex",
            filters,
            "-map",
            "[sheet]",
            "-frames:v",
            "1",
            "-y",
            str(output),
        ]
    )
    result = subprocess.run(argv, capture_output=True, check=False, timeout=60)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("contact-sheet-failed")


def _finalize_media(render: Path, narration: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg-unavailable")
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(render),
            "-i",
            str(narration),
            "-t",
            "10",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(output),
        ],
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError("final-media-failed")


def _preflight(repo_root: Path) -> tuple[Path, Path, Path]:
    preparer = repo_root / "automation" / "scripts" / "prepare_video_sample.py"
    hyperframes = repo_root / "node_modules" / ".bin" / "hyperframes.cmd"
    gsap = repo_root / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    sample_index = repo_root / "video-sample" / "index.html"
    required = (preparer, hyperframes, gsap, sample_index)
    if (
        not _has_safe_ancestors(repo_root)
        or not all(_safe_existing_file(item) for item in required)
        or shutil.which("ffmpeg") is None
        or shutil.which("ffprobe") is None
    ):
        raise RuntimeError("local-prerequisite")
    return preparer, hyperframes, gsap


def _create_runtime(repo_root: Path, workspace: Path, gsap: Path) -> Path:
    runtime = workspace / "runtime" / f"v1-sample-{uuid.uuid4().hex}"
    sample = runtime / "video-sample"
    if not _is_contained(workspace, runtime) or runtime.exists() or _is_reparse_point(runtime):
        raise RuntimeError("unsafe-runtime")
    _safe_mkdir(workspace, sample / "media")
    _copy_file(repo_root / "video-sample" / "index.html", sample / "index.html", root=workspace)
    design = repo_root / "video-sample" / "DESIGN.md"
    if _safe_existing_file(design):
        _copy_file(design, sample / "DESIGN.md", root=workspace)
    _copy_file(
        gsap,
        sample / "node_modules" / "gsap" / "dist" / "gsap.min.js",
        root=workspace,
    )
    return runtime


def _tree_has_reparse_point(root: Path) -> bool:
    """Inspect the complete task-owned tree before cleanup without following links."""
    pending = [root]
    while pending:
        current = pending.pop()
        if _is_reparse_point(current):
            return True
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    child = Path(entry.path)
                    if _is_reparse_point(child):
                        return True
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(child)
        except OSError:
            return True
    return False


def _remove_tree_if_safe(workspace: Path, target: Path | None) -> None:
    if target is None or not target.exists():
        return
    if (
        not _is_contained(workspace, target)
        or not _safe_existing_directory(target)
        or _tree_has_reparse_point(target)
    ):
        return
    try:
        if target.is_dir():
            shutil.rmtree(target)
    except OSError:
        pass


def _write_sample_artifacts(
    source_media: Path, active: Path, source_wav: Path
) -> tuple[Path, Path]:
    media = active / "工程" / "media"
    if not _is_contained(active, media):
        raise RuntimeError("unsafe-active-media")
    _safe_mkdir(active, media)
    narration = media / "narration.wav"
    _copy_file(source_media / "narration.wav", narration, root=active)
    narration_hash = _sha256(narration)
    source_hash = _sha256(source_wav)
    captions = json.loads((source_media / "captions.json").read_text(encoding="utf-8"))
    if not isinstance(captions, list) or not captions:
        raise ValueError("synthetic-captions")
    for caption in captions:
        if not isinstance(caption, dict):
            raise ValueError("synthetic-captions")
        caption["source"] = "synthetic-local-sample"
    words = _tokenize_caption_phrases(captions)
    _write_json(
        active,
        media / "voice_manifest.json",
        {
            "provider": "indextts2-local",
            "voice_id": CURRENT_VOICE_ID,
            "model": "IndexTTS2",
            "output_path": "工程/media/narration.wav",
            "output_sha256": narration_hash,
            "used_fallback": False,
            "sample_mode": True,
            "issuance": "synthetic-local-sample",
            "authorized_source_sha256": source_hash,
        },
    )
    _write_json(active, media / "asr-result.json", {"synthetic": True, "words": words})
    _write_json(active, media / "captions_words.json", words)
    _write_json(active, media / "captions.json", captions)
    _write_text(active, media / "captions.srt", _subtitle_text(captions, vtt=False))
    _write_text(active, media / "captions.vtt", _subtitle_text(captions, vtt=True))
    qc = json.loads((source_media / "caption-qc.json").read_text(encoding="utf-8"))
    if not isinstance(qc, dict):
        raise ValueError("synthetic-caption-qc")
    qc.update(
        {
            "status": "pass",
            "timing_source": "synthetic-local-sample",
            "narration_sha256": narration_hash,
        }
    )
    _write_json(active, media / "caption-qc.json", qc)
    handoff = active / "交接稿.md"
    _write_text(active, handoff, _handoff(active.name.split("-", 3)[-1], "制作中"))
    return handoff, narration


def run_sample(
    *,
    source_wav: Path,
    workspace_root: Path,
    archive_slug: str,
    audio_approved: bool,
    run_command: Callable[..., None] = _run_command,
    check_delivery_fn: Callable[..., DeliveryResult] = check_delivery,
    contact_sheet_fn: Callable[[Path, Path], None] = _create_contact_sheet,
    repo_root: Path = ROOT,
) -> SampleResult:
    """Run only local sample steps and atomically archive a checked V1 package."""
    if not audio_approved:
        return SampleResult(2, ("rule=audio-approval-required",))
    source_wav = Path(source_wav)
    workspace = Path(workspace_root)
    repo_root = Path(repo_root)
    if (
        source_wav.suffix.lower() != ".wav"
        or not _safe_existing_file(source_wav)
        or not _safe_slug(archive_slug)
    ):
        return SampleResult(2, ("rule=sample-input",))
    if not _has_safe_ancestors(workspace):
        return SampleResult(2, ("rule=workspace-safe-path",))
    try:
        preparer, hyperframes, gsap = _preflight(repo_root)
    except RuntimeError:
        return SampleResult(2, ("rule=sample-prerequisite",))

    today = date.today()
    project_name = f"{today.isoformat()}-{archive_slug}"
    active = workspace / "01-内容生产" / "视频工作台" / "制作中" / project_name
    archive = workspace / "01-内容生产" / "视频工作台" / "已制作" / _period(today) / project_name
    runtime_root = workspace / "runtime"
    safety_targets = (runtime_root, active, archive, archive.parent, active.parent)
    if not all(_is_contained(workspace, target) for target in safety_targets):
        return SampleResult(2, ("rule=workspace-safe-path",))
    if active.exists() or _is_reparse_point(active) or archive.exists() or _is_reparse_point(archive):
        return SampleResult(2, ("rule=archive-collision",))

    runtime: Path | None = None
    active_created = False
    try:
        runtime = _create_runtime(repo_root, workspace, gsap)
        sample = runtime / "video-sample"
        if not _is_contained(workspace, active) or active.exists():
            raise RuntimeError("unsafe-active")
        _safe_mkdir(workspace, active)
        active_created = True

        run_command(
            [
                sys.executable,
                str(preparer),
                "--source-wav",
                str(source_wav),
                "--workspace-root",
                str(runtime),
            ],
            cwd=repo_root,
        )
        for command in ("lint", "validate"):
            run_command([str(hyperframes), command, str(sample)], cwd=repo_root)
        run_command(
            [str(hyperframes), "inspect", str(sample), "--samples", "15"], cwd=repo_root
        )
        render = sample / "renders" / "boomearth-v1-sample.mp4"
        run_command(
            [
                str(hyperframes),
                "render",
                str(sample),
                "-o",
                str(render),
                "--fps",
                "30",
                "--quality",
                "standard",
                "--workers",
                "1",
            ],
            cwd=repo_root,
        )
        source_media = sample / "media"
        required = (
            source_media / "narration.wav",
            source_media / "captions.json",
            source_media / "caption-qc.json",
            render,
        )
        if any(not _is_contained(runtime, item) or not _safe_existing_file(item) for item in required):
            raise RuntimeError("sample-artifacts")

        handoff, narration = _write_sample_artifacts(source_media, active, source_wav)
        final_media = active / "成片" / "boomearth-v1-sample.mp4"
        if not _is_contained(workspace, final_media):
            raise RuntimeError("unsafe-final")
        _safe_destination(workspace, final_media)
        _finalize_media(render, narration, final_media)
        contact_sheet = active / "质检" / "contact-sheet.jpg"
        _safe_destination(workspace, contact_sheet)
        contact_sheet_fn(final_media, contact_sheet)
        _write_json(
            active,
            active / "质检" / "contact-sheet-qc.json",
            {
                "frame_count": 6,
                "times_s": CONTACT_TIMES_S,
                "layout": "3x2",
                "coverage": ["open", "early", "mid", "transition", "late", "near-end"],
            },
        )

        checked = check_delivery_fn(handoff, active, final_media, sample_mode=True)
        if checked.exit_code != 0:
            return SampleResult(checked.exit_code, checked.diagnostics)
        report = active / "delivery-report.json"
        engine_report = active / "工程" / "delivery-report.json"
        if not (
            _is_contained(workspace, report)
            and _is_contained(workspace, engine_report)
            and _safe_existing_file(report)
        ):
            raise RuntimeError("unsafe-delivery-report")
        _copy_file(report, engine_report, root=workspace)
        if not _safe_existing_file(report):
            raise RuntimeError("unsafe-delivery-report")
        report.unlink()
        _write_text(active, handoff, _handoff(archive_slug, "已完成"))
        if not _is_contained(workspace, archive.parent) or _is_reparse_point(archive.parent):
            raise RuntimeError("unsafe-archive")
        _safe_mkdir(workspace, archive.parent)
        if archive.exists() or _is_reparse_point(archive) or not _is_contained(workspace, archive):
            raise RuntimeError("archive-collision")
        os.replace(active, archive)
        active_created = False
        return SampleResult(0, ("status=pass mode=sample",), str(archive))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return SampleResult(2, ("rule=sample-local-stage",))
    finally:
        _remove_tree_if_safe(workspace, runtime)
        if active_created:
            _remove_tree_if_safe(workspace, active)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create an offline V1 sample; live APIs are not called."
    )
    parser.add_argument("--source-wav", required=True, type=Path)
    parser.add_argument("--audio-approved", action="store_true")
    parser.add_argument("--workspace-root", type=Path, default=ROOT)
    parser.add_argument("--archive-slug", default="v1-sample")
    args = parser.parse_args(argv)
    result = run_sample(
        source_wav=args.source_wav,
        workspace_root=args.workspace_root,
        archive_slug=args.archive_slug,
        audio_approved=args.audio_approved,
    )
    for diagnostic in result.diagnostics:
        print(diagnostic)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

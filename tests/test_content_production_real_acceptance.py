"""Retained real-production acceptance; skips only while the archive is absent."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


CHECKER_SCRIPT = Path(__file__).parents[1] / "automation" / "scripts" / "check_delivery.py"
PROJECT_NAME = "2026-08-13-first-principles-content-acceptance"
CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_workspace() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=CHECKER_SCRIPT.parents[2],
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = CHECKER_SCRIPT.parents[2] / common
    return common.resolve(strict=True).parent


def _archive() -> Path:
    workspace = _canonical_workspace()
    matches = tuple(
        (workspace / "01-内容生产" / "视频工作台" / "已制作").glob(
            f"*/{PROJECT_NAME}"
        )
    )
    if not matches:
        pytest.skip("real acceptance archive is not present in this checkout")
    assert len(matches) == 1, "the canonical real acceptance archive is ambiguous"
    return matches[0]


def _checker():
    spec = importlib.util.spec_from_file_location(
        "real_acceptance_delivery_checker", CHECKER_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_retained_real_content_production_is_complete_and_revalidates() -> None:
    """Catches any present archive that is incomplete, synthetic, or hash-unbound."""

    archive = _archive()
    workspace = _canonical_workspace()
    active = (
        workspace
        / "01-内容生产"
        / "视频工作台"
        / "制作中"
        / PROJECT_NAME
    )
    assert archive.is_dir()
    assert not active.exists()
    relative_archive = archive.relative_to(workspace).as_posix()
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", relative_archive],
        cwd=workspace,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0

    handoff = archive / "交接稿.md"
    narration = archive / "工程" / "media" / "narration.wav"
    manifest_path = archive / "工程" / "media" / "voice_manifest.json"
    captions_dir = archive / "工程" / "media" / "captions"
    plan_path = archive / "工程" / "content-plan.json"
    timeline_path = archive / "工程" / "scene-timeline.json"
    final = archive / "成片" / "boomearth-v2-production.mp4"
    scene_qc_path = archive / "质检" / "scene-preview-qc.json"
    contact_qc_path = archive / "质检" / "contact-sheet-qc.json"
    caption_render_qc_path = archive / "质检" / "caption-render-qc.json"
    receipt_path = archive / "工程" / "delivery-report.json"
    publication_path = archive / "工程" / "publication-manifest.json"
    required_files = (
        handoff,
        narration,
        manifest_path,
        *(captions_dir / name for name in CAPTION_FILES),
        plan_path,
        timeline_path,
        final,
        archive / "质检" / "contact-sheet.jpg",
        contact_qc_path,
        caption_render_qc_path,
        scene_qc_path,
        receipt_path,
        publication_path,
    )
    assert all(path.is_file() for path in required_files)
    assert "status: 已完成" in handoff.read_text(encoding="utf-8")
    assert "### segment-004" in handoff.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    narration_hash = _sha256(narration)
    assert manifest["provider"] == "indextts2-local"
    assert manifest["voice_id"] == "user-indextts2-calm-v2"
    assert manifest["model"] == "IndexTTS2"
    assert manifest["used_fallback"] is False
    assert manifest["playback_speed"] == 1.12
    assert manifest["segment_count"] == 4
    assert manifest["output_sha256"] == narration_hash

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(final),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert probe.returncode == 0
    media = json.loads(probe.stdout)
    video = next(stream for stream in media["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in media["streams"] if stream["codec_type"] == "audio")
    duration = float(media["format"]["duration"])
    assert 30.0 <= duration <= 60.0
    assert (video["codec_name"], audio["codec_name"]) == ("h264", "aac")
    assert (int(video["width"]), int(video["height"])) == (1920, 1080)
    assert video["avg_frame_rate"] == "30/1"

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    scenes = plan["scenes"]
    assert len(scenes) >= 4
    assert len(timeline["scenes"]) == len(scenes)
    assert timeline["narration_sha256"] == narration_hash
    assert timeline["content_plan_sha256"] == _sha256(plan_path)
    assert timeline["captions_words_sha256"] == _sha256(
        captions_dir / "captions_words.json"
    )
    assets = [archive / scene["visual_asset"] for scene in scenes]
    assert all(asset.is_file() for asset in assets)
    assert len({_sha256(asset) for asset in assets}) == len(assets)

    caption_qc = json.loads(
        (captions_dir / "caption-qc.json").read_text(encoding="utf-8")
    )
    assert caption_qc["status"] == "pass"
    assert caption_qc["timing_source"] == "volcengine-word-timestamps"
    assert float(caption_qc["alignment_coverage"]) >= 0.90
    assert caption_qc["narration_sha256"] == narration_hash

    previews = tuple((archive / "质检" / "scene-previews").glob("*.png"))
    scene_qc = json.loads(scene_qc_path.read_text(encoding="utf-8"))
    assert scene_qc["status"] == "pass"
    assert scene_qc["final_video_sha256"] == _sha256(final)
    assert scene_qc["content_plan_sha256"] == _sha256(plan_path)
    assert scene_qc["scene_timeline_sha256"] == _sha256(timeline_path)
    assert len(previews) == len(scenes) * 3
    assert len(scene_qc["evidence"]) == len(previews)
    preview_hashes = {path.name: _sha256(path) for path in previews}
    assert all(
        preview_hashes[f"{item['scene_id']}-{item['kind']}.png"]
        == item["preview_sha256"]
        for item in scene_qc["evidence"]
    )

    contact_qc = json.loads(contact_qc_path.read_text(encoding="utf-8"))
    assert contact_qc["coverage"] == "all-scene-midpoints"
    assert contact_qc["frame_count"] == len(scenes)
    assert len(contact_qc["times_s"]) == len(scenes)
    caption_render_qc = json.loads(
        caption_render_qc_path.read_text(encoding="utf-8")
    )
    assert caption_render_qc["status"] == "pass"
    assert caption_render_qc["final_video_sha256"] == _sha256(final)

    publication = json.loads(publication_path.read_text(encoding="utf-8"))[
        "artifacts"
    ]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for path in required_files:
        if path in {receipt_path, publication_path}:
            continue
        relative = path.relative_to(archive).as_posix()
        assert publication[relative] == _sha256(path)
    assert receipt["status"] == "pass"
    assert receipt["mode"] == "production"
    receipt_artifacts = receipt["artifact_sha256"]
    assert set(receipt_artifacts) <= set(publication)
    handoff_relative = handoff.relative_to(archive).as_posix()
    assert all(
        (archive / relative).is_file()
        and digest == _sha256(archive / relative)
        and publication[relative] == digest
        for relative, digest in receipt_artifacts.items()
        if relative != handoff_relative
    )
    assert receipt_artifacts[handoff_relative] != _sha256(handoff)
    assert publication[handoff_relative] == _sha256(handoff)

    before = (_sha256(receipt_path), _sha256(publication_path))
    result = _checker().check_delivery(
        handoff,
        archive,
        final,
        sample_mode=False,
    )
    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=historical",)
    assert (_sha256(receipt_path), _sha256(publication_path)) == before

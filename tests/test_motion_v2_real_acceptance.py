"""Explicit, default-disabled acceptance for the retained-audio Motion V2 sample."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest


def _root() -> Path:
    if os.environ.get("BOOMEARTH_RUN_MOTION_V2_ACCEPTANCE") != "1":
        pytest.skip("real Motion V2 acceptance is disabled by default")
    value = os.environ.get("BOOMEARTH_MOTION_V2_ACCEPTANCE_ROOT")
    if not value:
        pytest.fail("BOOMEARTH_MOTION_V2_ACCEPTANCE_ROOT is required")
    root = Path(value).absolute()
    assert root.is_dir()
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_retained_audio_motion_v2_sample_is_real_dynamic_and_hash_bound() -> None:
    root = _root()
    acceptance = json.loads((root / "acceptance.json").read_text(encoding="utf-8"))
    final = root / acceptance["final_video"]
    plan_path = root / "工程" / "content-plan.json"
    timeline_path = root / "工程" / "scene-timeline.json"
    motion_path = root / "工程" / "motion-plan.json"
    narration = root / "工程" / "media" / "narration.wav"
    words = root / "工程" / "media" / "captions" / "captions_words.json"
    qc_path = root / "质检" / "motion-preview-qc.json"
    contact = root / "质检" / "motion-contact-sheet.jpg"

    assert acceptance["schema_version"] == 1
    assert acceptance["status"] == "pass"
    assert acceptance["cloud_calls"] == 0
    assert all(acceptance["manual_qc"].values())
    for path, field in (
        (final, "final_video_sha256"),
        (narration, "narration_sha256"),
        (words, "captions_words_sha256"),
        (plan_path, "content_plan_sha256"),
        (timeline_path, "scene_timeline_sha256"),
        (motion_path, "motion_plan_sha256"),
        (qc_path, "motion_qc_sha256"),
        (contact, "motion_contact_sheet_sha256"),
    ):
        assert _sha256(path) == acceptance[field]

    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(final),
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    media = json.loads(probe.stdout)
    video = next(item for item in media["streams"] if item["codec_type"] == "video")
    audio = next(item for item in media["streams"] if item["codec_type"] == "audio")
    duration = float(media["format"]["duration"])
    assert 8.0 <= duration <= 12.0
    assert (int(video["width"]), int(video["height"])) == (1920, 1080)
    assert video["codec_name"] == "h264"
    assert video["avg_frame_rate"] == "30/1"
    assert audio["codec_name"] == "aac"

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    motion = json.loads(motion_path.read_text(encoding="utf-8"))
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    assert plan["visual_system"] == "editorial-motion-v2"
    assert len(plan["scenes"]) == len(timeline["scenes"]) == len(motion["scenes"]) == 2
    effects = {
        entry["motion"]
        for scene in motion["scenes"]
        for entry in scene["entries"]
    }
    assert {"line-reveal", "scale-settle", "wipe-right", "fade-up"} <= effects
    assert all(scene["ambient"]["motion"] == "slow-parallax" for scene in motion["scenes"])
    assert qc["status"] == "pass"
    assert qc["final_video_sha256"] == acceptance["final_video_sha256"]
    assert qc["motion_plan_sha256"] == acceptance["motion_plan_sha256"]
    assert len(qc["evidence"]) >= 12
    assert len({item["frame_sha256"] for item in qc["evidence"]}) >= 8
    assert all(
        _sha256(root / "质检" / "motion-previews" / f"{item['scene_id']}-{item['kind']}.png")
        == item["preview_sha256"]
        for item in qc["evidence"]
    )

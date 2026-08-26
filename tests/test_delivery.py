"""Offline contract tests for the redacted V1 delivery checker."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest
from PIL import Image

from boomearth.audio import indextts2
from boomearth.audio.indextts2 import CURRENT_VOICE_ID


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "automation" / "scripts" / "check_delivery.py"
SAMPLE_SCRIPT = REPO_ROOT / "automation" / "scripts" / "run_v1_sample.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("delivery_checker_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_sample_emitter():
    spec = importlib.util.spec_from_file_location("sample_emitter_under_test", SAMPLE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wav(path: Path, *, seconds: float = 10.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 8_000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * round(rate * seconds))


def _write_mp4(path: Path, *, seconds: float = 10.0, size: str = "1920x1080") -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for the tiny-media delivery contract")
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={size}:r=30:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=8000:cl=mono:d={seconds}",
            "-vf",
            "drawbox=x=48:y=950:w=640:h=70:color=white:t=fill",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _handoff(
    *,
    status: str = "制作中",
    archive_slug: str = "2026-08-11-sample",
    extra: str = "",
) -> str:
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
        f"archive_slug: {archive_slug}\n"
        "---\n\n"
        "## 新稿分段\n\n这是无来源的合成样片交接稿。\n"
        f"{extra}"
    )


def _write_caption_text_artifacts(media: Path, captions: list[dict[str, object]]) -> None:
    srt_blocks = []
    vtt_blocks = []
    for index, caption in enumerate(captions, 1):
        start = float(caption["start"])
        end = float(caption["end"])
        text = str(caption["text"])
        def stamp(seconds: float, separator: str) -> str:
            milliseconds = round(seconds * 1000)
            minutes, milliseconds = divmod(milliseconds, 60_000)
            whole_seconds, milliseconds = divmod(milliseconds, 1000)
            return f"00:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"
        srt_blocks.append(f"{index}\n{stamp(start, ',')} --> {stamp(end, ',')}\n{text}")
        vtt_blocks.append(f"{stamp(start, '.')} --> {stamp(end, '.')}\n{text}")
    (media / "captions.srt").write_text("\n\n".join(srt_blocks) + "\n", encoding="utf-8")
    (media / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(vtt_blocks) + "\n", encoding="utf-8")


def _write_contact_sheet(final: Path, contact_sheet: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for the tiny-media delivery contract")
    contact_sheet.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(final), "-frames:v", "1", "-y", str(contact_sheet)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _write_contact_sheet_qc(project: Path) -> None:
    (project / "质检" / "contact-sheet-qc.json").write_text(
        json.dumps(
            {
                "frame_count": 6,
                "times_s": [0.5, 2.5, 4.5, 5.5, 7.5, 9.5],
                "layout": "3x2",
                "coverage": ["open", "early", "mid", "transition", "late", "near-end"],
            }
        ),
        encoding="utf-8",
    )


def _frame_evidence(path: Path, timestamp: float) -> tuple[str, int, int]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required for caption-frame delivery evidence")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    pixels = result.stdout
    safe_zone = pixels[(1080 - 150) * 1920 * 3:]
    bright = sum(1 for index in range(0, len(safe_zone), 3) if min(safe_zone[index:index + 3]) >= 185)
    dark = sum(1 for index in range(0, len(safe_zone), 3) if max(safe_zone[index:index + 3]) <= 65)
    return hashlib.sha256(pixels).hexdigest(), bright, dark


def _write_caption_frame_qc(project: Path, final: Path, captions_path: Path) -> None:
    captions = json.loads(captions_path.read_text(encoding="utf-8"))
    checks = []
    for caption in captions:
        time_s = round((float(caption["start"]) + float(caption["end"])) / 2, 3)
        frame_sha256, bright, dark = _frame_evidence(final, time_s)
        checks.append(
            {
                "time_s": time_s,
                "frame_sha256": frame_sha256,
                "bright_pixels": bright,
                "dark_pixels": dark,
            }
        )
    (project / "质检" / "caption-render-qc.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "timing_source": "volcengine-word-timestamps",
                "caption_style": "anchor-dark",
                "final_video_sha256": _sha256(final),
                "captions_sha256": _sha256(captions_path),
                "frame_checks": checks,
            }
        ),
        encoding="utf-8",
    )


def _make_delivery(
    tmp_path: Path,
    *,
    sample_mode: bool = True,
    project: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    project = project or tmp_path / "active-project"
    media = project / "media"
    final = project / "成片" / "sample.mp4"
    handoff = project / "交接稿.md"
    _write_wav(media / "narration.wav")
    narration_hash = _sha256(media / "narration.wav")
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text(_handoff(), encoding="utf-8")
    manifest = {
        "provider": "indextts2-local",
        "voice_id": CURRENT_VOICE_ID,
        "model": "IndexTTS2",
        "output_path": "media/narration.wav",
        "output_sha256": narration_hash,
        "used_fallback": False,
    }
    if sample_mode:
        manifest.update(
            {
                "sample_mode": True,
                "issuance": "synthetic-local-sample",
                "authorized_source_sha256": narration_hash,
            }
        )
    else:
        manifest.update(
            {
                "reference_audio_sha256": "a" * 64,
                "segment_contract_sha256": "b" * 64,
                "pronunciation_contract_sha256": "c" * 64,
                "segment_count": 1,
                "playback_speed": 1.12,
            }
        )
    (media / "voice_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    captions = [
        {"start": 0.2, "end": 4.8, "text": "合成字幕一", "source": "synthetic-local-sample"},
        {"start": 5.0, "end": 9.6, "text": "合成字幕二", "source": "synthetic-local-sample"},
    ]
    (media / "captions.json").write_text(json.dumps(captions, ensure_ascii=False), encoding="utf-8")
    words = [
        {"start": 0.2, "end": 2.4, "text": "合成字幕", "isGap": False},
        {"start": 2.4, "end": 4.8, "text": "一", "isGap": False},
        {"start": 5.0, "end": 7.3, "text": "合成字幕", "isGap": False},
        {"start": 7.3, "end": 9.6, "text": "二", "isGap": False},
    ]
    (media / "asr-result.json").write_text(json.dumps({"synthetic": sample_mode, "words": words}, ensure_ascii=False), encoding="utf-8")
    (media / "captions_words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    _write_caption_text_artifacts(media, captions)
    (media / "caption-qc.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "timing_source": "synthetic-local-sample" if sample_mode else "volcengine-word-timestamps",
                "alignment_coverage": 1.0,
                "narration_sha256": narration_hash,
            }
        ),
        encoding="utf-8",
    )
    _write_mp4(final)
    contact_sheet = project / "质检" / "contact-sheet.jpg"
    _write_contact_sheet(final, contact_sheet)
    _write_contact_sheet_qc(project)
    if not sample_mode:
        _write_caption_frame_qc(project, final, media / "captions.json")
    return handoff, project, final, media / "narration.wav"


def _enable_cover_delivery(handoff: Path) -> None:
    handoff.write_text(
        handoff.read_text(encoding="utf-8").replace(
            "archive_slug:", "covers: platform-defaults-v1\narchive_slug:", 1
        ),
        encoding="utf-8",
    )


def _enable_post_delivery_punk_cover(handoff: Path) -> None:
    handoff.write_text(
        handoff.read_text(encoding="utf-8").replace(
            "archive_slug:",
            "covers: punk-cover-giant-title-3x4-v1\narchive_slug:",
            1,
        ),
        encoding="utf-8",
    )


def _write_cover_evidence(project: Path) -> dict[str, object]:
    dimensions = {
        "封面/抖音-作品封面-1080x1920.png": [1080, 1920],
        "封面/抖音-主页预览-1080x1440.png": [1080, 1440],
        "封面/视频号-作品封面-1080x1260.png": [1080, 1260],
        "质检/cover-contact-sheet.jpg": [960, 640],
    }
    safe_boxes = {
        "封面/抖音-作品封面-1080x1920.png": [72, 300, 1008, 1620],
        "封面/抖音-主页预览-1080x1440.png": [72, 60, 1008, 1380],
        "封面/视频号-作品封面-1080x1260.png": [72, 60, 1008, 1200],
    }
    upload = Image.new("RGB", (1080, 1920), (24, 38, 52))
    for y in range(240, 1680):
        upload.paste((y % 251, 80, 160), (0, y, 1080, y + 1))
    upload_path = project / "封面" / "抖音-作品封面-1080x1920.png"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload.save(upload_path)
    upload.crop((0, 240, 1080, 1680)).save(
        project / "封面" / "抖音-主页预览-1080x1440.png"
    )
    upload.crop((0, 330, 1080, 1590)).save(
        project / "封面" / "视频号-作品封面-1080x1260.png"
    )
    contact = project / "质检" / "cover-contact-sheet.jpg"
    contact.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (960, 640), (236, 232, 220)).save(
        contact, quality=92, subsampling=0
    )
    qc: dict[str, object] = {
        "schema_version": 1,
        "status": "pass",
        "contract": "platform-defaults-v1",
        "source_sha256": "1" * 64,
        "headline_sha256": "2" * 64,
        "font_sha256": "3" * 64,
        "font_size_px": 96,
        "headline_line_count": 2,
        "dimensions": dimensions,
        "safe_boxes": safe_boxes,
        "artifacts_sha256": {
            relative: _sha256(project / relative) for relative in dimensions
        },
    }
    (project / "质检" / "cover-qc.json").write_text(
        json.dumps(qc, ensure_ascii=False), encoding="utf-8"
    )
    return qc


def test_cover_marker_requires_all_public_cover_artifacts(tmp_path: Path) -> None:
    """Would fail if a marked delivery silently shipped without its platform cover set."""

    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    _enable_cover_delivery(handoff)

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 3
    assert result.diagnostics == ("rule=cover-artifact-missing",)


def test_punk_cover_marker_keeps_core_delivery_post_archive(tmp_path: Path) -> None:
    """The new default cover is generated after core video delivery, not by the legacy renderer."""

    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    _enable_post_delivery_punk_cover(handoff)

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 0
    report = json.loads((project / "delivery-report.json").read_text(encoding="utf-8"))
    assert not any(relative.startswith("封面/") for relative in report["artifact_sha256"])


def test_valid_cover_evidence_enters_the_publication_receipt(tmp_path: Path) -> None:
    """Would fail if verified public cover bytes were omitted from the delivery hash set."""

    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    _enable_cover_delivery(handoff)
    _write_cover_evidence(project)

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 0
    report = json.loads((project / "delivery-report.json").read_text(encoding="utf-8"))
    cover_paths = {
        "封面/抖音-作品封面-1080x1920.png",
        "封面/抖音-主页预览-1080x1440.png",
        "封面/视频号-作品封面-1080x1260.png",
        "质检/cover-contact-sheet.jpg",
        "质检/cover-qc.json",
    }
    assert cover_paths <= set(report["artifact_sha256"])
    assert all(
        token not in relative.casefold()
        for relative in report["artifact_sha256"]
        for token in ("heygen", "signed", "provider", "private-id")
    )


def test_cover_qc_rejects_equal_palette_indices_with_different_decoded_colors(
    tmp_path: Path,
) -> None:
    """Would fail if raw palette indices substituted for decoded crop pixels."""

    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    _enable_cover_delivery(handoff)
    qc = _write_cover_evidence(project)
    upload = project / "封面" / "抖音-作品封面-1080x1920.png"
    preview = project / "封面" / "抖音-主页预览-1080x1440.png"
    upload_image = Image.new("P", (1080, 1920), 0)
    upload_image.putpalette([255, 0, 0] + [0] * 765)
    upload_image.save(upload)
    preview_image = Image.new("P", (1080, 1440), 0)
    preview_image.putpalette([0, 0, 255] + [0] * 765)
    preview_image.save(preview)
    qc["artifacts_sha256"]["封面/抖音-作品封面-1080x1920.png"] = _sha256(
        upload
    )
    qc["artifacts_sha256"]["封面/抖音-主页预览-1080x1440.png"] = _sha256(
        preview
    )
    (project / "质检" / "cover-qc.json").write_text(
        json.dumps(qc, ensure_ascii=False), encoding="utf-8"
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 2
    assert "rule=cover-qc" in result.diagnostics


@pytest.mark.parametrize(
    "change", ["dimension", "safe-box", "hash", "status", "contract", "crop"]
)
def test_cover_qc_rejects_decoded_or_evidence_tampering(
    tmp_path: Path, change: str
) -> None:
    """Would fail if a forged QC JSON could bless changed platform cover bytes."""

    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    _enable_cover_delivery(handoff)
    qc = _write_cover_evidence(project)
    qc_path = project / "质检" / "cover-qc.json"
    upload = project / "封面" / "抖音-作品封面-1080x1920.png"
    preview = project / "封面" / "抖音-主页预览-1080x1440.png"
    if change == "dimension":
        Image.new("RGB", (1080, 1919), "black").save(upload)
        qc["artifacts_sha256"]["封面/抖音-作品封面-1080x1920.png"] = _sha256(upload)
    elif change == "safe-box":
        qc["safe_boxes"]["封面/视频号-作品封面-1080x1260.png"][1] = 61
    elif change == "hash":
        qc["artifacts_sha256"]["质检/cover-contact-sheet.jpg"] = "0" * 64
    elif change == "status":
        qc["status"] = "fail"
    elif change == "contract":
        qc["contract"] = "platform-defaults-v2"
    else:
        with Image.open(preview) as image:
            changed = image.convert("RGB")
        changed.putpixel((10, 10), (255, 255, 255))
        changed.save(preview)
        qc["artifacts_sha256"]["封面/抖音-主页预览-1080x1440.png"] = _sha256(preview)
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 2
    assert "rule=cover-qc" in result.diagnostics


def _make_production_delivery(
    tmp_path: Path, *, project: Path | None = None
) -> tuple[Path, Path, Path, Path]:
    """Create the actual Task 6/Task 7-shaped production acceptance fixture."""
    handoff, project, final, narration = _make_delivery(tmp_path, project=project)
    media = narration.parent
    manifest = json.loads((media / "voice_manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "reference_audio_path": "D:/private/reference.wav",
            "reference_audio_sha256": "a" * 64,
            "output_path": str(narration.resolve()),
            "segment_contract_path": "D:/private/segments.jsonl",
            "segment_contract_sha256": "b" * 64,
            "segment_count": 1,
            "playback_speed": 1.12,
            "pronunciation_contract_path": "D:/private/pronunciations.json",
            "pronunciation_contract_sha256": "c" * 64,
        }
    )
    manifest.pop("sample_mode")
    manifest.pop("issuance")
    manifest.pop("authorized_source_sha256")
    (media / "voice_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    captions = json.loads((media / "captions.json").read_text(encoding="utf-8"))
    for caption in captions:
        caption["source"] = "volcengine-word-timestamps"
    (media / "captions.json").write_text(json.dumps(captions, ensure_ascii=False), encoding="utf-8")
    qc = json.loads((media / "caption-qc.json").read_text(encoding="utf-8"))
    qc.update(
        {
            "timing_source": "volcengine-word-timestamps",
            "source_media": narration.name,
            "asr_resource_id": "task7-fixture-resource",
        }
    )
    (media / "caption-qc.json").write_text(json.dumps(qc), encoding="utf-8")
    (media / "asr-result.json").write_text(
        json.dumps({"results": [{"text": "Task7 fixture"}]}), encoding="utf-8"
    )
    _write_caption_frame_qc(project, final, media / "captions.json")
    return handoff, project, final, narration


def test_production_delivery_accepts_canonical_nested_caption_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if delivery still required production captions in the media root."""

    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    media = narration.parent
    captions_dir = media / "captions"
    captions_dir.mkdir()
    for name in (
        "asr-result.json",
        "captions_words.json",
        "captions.json",
        "captions.srt",
        "captions.vtt",
        "caption-qc.json",
    ):
        (media / name).replace(captions_dir / name)

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=production",)


def test_production_delivery_does_not_fallback_past_a_caption_path_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if an unsafe canonical caption path silently selected stale root files."""

    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    (narration.parent / "captions").write_text("not-a-directory", encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 3
    assert result.diagnostics == ("rule=delivery-artifact-missing",)


def test_production_delivery_does_not_fallback_to_root_captions_in_canonical_media_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a current 工程/media project could retain the retired caption topology."""

    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    canonical_media = project / "工程" / "media"
    canonical_media.parent.mkdir(parents=True)
    narration.parent.replace(canonical_media)

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 3
    assert result.diagnostics == ("rule=delivery-artifact-missing",)


def test_production_delivery_requires_video_bound_caption_render_qc(tmp_path: Path) -> None:
    """Would fail if production delivery accepted a self-declared caption render result."""

    checker = _load_checker()
    handoff, project, final, _ = _make_production_delivery(tmp_path)
    qc_path = project / "质检" / "caption-render-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["final_video_sha256"] = "0" * 64
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=caption-render-qc" in result.diagnostics


def test_production_delivery_reextracts_frames_instead_of_trusting_reported_pixel_counts(
    tmp_path: Path,
) -> None:
    """Would fail if forged bright/dark counts with correct file digests could pass production delivery."""

    checker = _load_checker()
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    qc_path = project / "质检" / "caption-render-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["frame_checks"] = [
        {"time_s": 2.5, "bright_pixels": 999999, "dark_pixels": 999999},
        {"time_s": 7.3, "bright_pixels": 999999, "dark_pixels": 999999},
    ]
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=caption-render-qc" in result.diagnostics


def test_production_delivery_recomputes_safe_zone_counts_for_captionless_frames(
    tmp_path: Path,
) -> None:
    """Would fail if correct frame hashes alone could bless a captionless/damaged final video."""

    checker = _load_checker()
    handoff, project, final, _ = _make_production_delivery(tmp_path)
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    replacement = final.with_name("captionless.mp4")
    run = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30:d=10",
            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:d=10", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-y", str(replacement),
        ],
        capture_output=True,
        check=False,
    )
    assert run.returncode == 0
    replacement.replace(final)
    qc_path = project / "质检" / "caption-render-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["final_video_sha256"] = _sha256(final)
    for check in qc["frame_checks"]:
        frame_sha256, bright, dark = _frame_evidence(final, float(check["time_s"]))
        check.update({"frame_sha256": frame_sha256, "bright_pixels": bright, "dark_pixels": dark})
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=caption-render-qc" in result.diagnostics


def test_checker_maps_checked_artifact_disappearance_to_redacted_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a final-MP4 disappearance after validation exposed an OS path or traceback."""

    checker = _load_checker()
    handoff, project, final, _ = _make_production_delivery(tmp_path)
    native_extract = checker._caption_frame_evidence
    disappeared = False

    def disappear_after_validation(path: Path, timestamp: float) -> tuple[str, int, int] | None:
        nonlocal disappeared
        if not disappeared:
            disappeared = True
            path.unlink()
        return native_extract(path, timestamp)

    monkeypatch.setattr(checker, "_caption_frame_evidence", disappear_after_validation)
    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    rendered = repr(result) + "\n" + "\n".join(result.diagnostics)
    assert result.exit_code == 2
    assert all(diagnostic.startswith("rule=") for diagnostic in result.diagnostics)
    assert str(tmp_path) not in rendered
    assert "traceback" not in rendered.casefold()
    assert "a" * 64 not in rendered


def _make_canonical_archived_v1_delivery(
    checker,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    archive_slug: str = "old-v1",
) -> tuple[Path, Path, Path, Path, Path]:
    """Build an isolated canonical archive and its Task 2-shaped v1 ledger entry."""
    workspace = tmp_path / "isolated-workspace"
    archive = (
        workspace
        / "01-内容生产"
        / "视频工作台"
        / "已制作"
        / "8月上旬"
        / archive_slug
    )
    handoff, project, final, narration = _make_production_delivery(
        tmp_path,
        project=archive,
    )
    reference = (
        workspace
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "voice"
        / "user-indextts2-calm-v1.wav"
    )
    _write_wav(reference, seconds=1.0)
    reference_hash = _sha256(reference)
    ledger = reference.with_name("indextts2-provenance-ledger.json")
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "issued_output_sha256": [],
                "canonical_reference_provenance": [
                    {
                        "voice_id": "user-indextts2-calm-v1",
                        "reference_audio_path": str(reference.resolve()),
                        "reference_audio_sha256": reference_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "ROOT", workspace)
    monkeypatch.setattr(
        indextts2,
        "LOCKED_PROVENANCE_LEDGER_PATH",
        ledger,
    )
    handoff.write_text(
        _handoff(status="已完成", archive_slug=archive_slug).replace(
            f"voice: {CURRENT_VOICE_ID}", "voice: user-indextts2-calm-v1"
        ),
        encoding="utf-8",
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "voice_id": "user-indextts2-calm-v1",
            "reference_audio_path": str(reference.resolve()),
            "reference_audio_sha256": reference_hash,
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return handoff, project, final, narration, reference


def _make_locked_v2_production_delivery(
    checker,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    archived: bool = False,
    archived_project_name: str = "old-v2",
    handoff_archive_slug: str | None = None,
    stale_active_output: bool = False,
    active_project_name: str | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    """Build a current delivery, or an explicitly historical v2 archive."""
    workspace = tmp_path / "isolated-v2-workspace"
    project = (
        workspace
        / "01-内容生产"
        / "视频工作台"
        / "已制作"
        / "8月上旬"
        / archived_project_name
        if archived
        else (
            workspace
            / "01-内容生产"
            / "视频工作台"
            / "制作中"
            / active_project_name
            if active_project_name is not None
            else workspace / "active-project"
        )
    )
    handoff, project, final, narration = _make_production_delivery(
        tmp_path,
        project=project,
    )
    voice_id = "user-indextts2-calm-v2" if archived else CURRENT_VOICE_ID
    reference = (
        workspace
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "voice"
        / f"{voice_id}.wav"
    )
    _write_wav(reference, seconds=1.0)
    reference_hash = _sha256(reference)
    ledger = reference.with_name("indextts2-provenance-ledger.json")
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "issued_output_sha256": [],
                "canonical_reference_provenance": [
                    {
                        "voice_id": voice_id,
                        "reference_audio_path": str(reference.resolve()),
                        "reference_audio_sha256": reference_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(indextts2, "LOCKED_PROVENANCE_LEDGER_PATH", ledger)
    if archived:
        monkeypatch.setattr(checker, "ROOT", workspace)
        handoff.write_text(
            _handoff(
                status="已完成",
                archive_slug=handoff_archive_slug or archived_project_name,
            ).replace(f"voice: {CURRENT_VOICE_ID}", f"voice: {voice_id}"),
            encoding="utf-8",
        )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "voice_id": voice_id,
            "reference_audio_path": str(reference.resolve()),
            "reference_audio_sha256": reference_hash,
        }
    )
    if archived and stale_active_output:
        manifest["output_path"] = str(
            workspace
            / "01-内容生产"
            / "视频工作台"
            / "制作中"
            / archived_project_name
            / narration.relative_to(project)
        )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger_payload = json.loads(ledger.read_text(encoding="utf-8"))
    ledger_payload["issued_output_sha256"] = [manifest["output_sha256"]]
    ledger_payload["issued_manifest_sha256_by_output_sha256"] = {
        manifest["output_sha256"]: _sha256(manifest_path)
    }
    ledger.write_text(json.dumps(ledger_payload), encoding="utf-8")
    return handoff, project, final, narration, reference


def test_historical_dated_v2_accepts_short_slug_and_ledger_bound_prearchive_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if an atomically archived V2 project could not be revalidated in place."""

    checker = _load_checker()
    handoff, project, final, _, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        archived=True,
        archived_project_name="2026-08-12-v2-production-acceptance",
        handoff_archive_slug="v2-production-acceptance",
        stale_active_output=True,
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=historical",)


def test_historical_retired_v2_revalidates_after_its_reference_enters_recycle_bin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if safe byte retirement made an immutable v2 archive unverifiable."""
    checker = _load_checker()
    handoff, project, final, _, reference = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        archived=True,
        archived_project_name="2026-08-12-retired-v2",
        handoff_archive_slug="retired-v2",
    )
    ledger = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["retired_voice_ids"] = [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
    ]
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    reference.unlink()

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=historical",)


def test_historical_dated_project_retains_full_name_slug_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if the V2 fix invalidated an older dated archive contract."""

    checker = _load_checker()
    project_name = "2026-08-11-legacy-contract"
    handoff, project, final, _, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        archived=True,
        archived_project_name=project_name,
        handoff_archive_slug=project_name,
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=historical",)


def test_historical_dated_v2_rejects_other_ledger_bound_stale_output_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if historical mode accepted any stale absolute path with a valid hash."""

    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        archived=True,
        archived_project_name="2026-08-12-v2-production-acceptance",
        handoff_archive_slug="v2-production-acceptance",
        stale_active_output=True,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_path"] = str(
        project.parents[2]
        / "制作中"
        / "2026-08-12-other-project"
        / narration.relative_to(project)
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger_path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["issued_manifest_sha256_by_output_sha256"][manifest["output_sha256"]] = _sha256(
        manifest_path
    )
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=voice-manifest",)


def test_dated_active_project_accepts_ledger_bound_undated_predecessor_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_checker()
    project_name = "2026-08-14-x-article-v2-acceptance"
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        active_project_name=project_name,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_path"] = str(
        project.parent
        / "x-article-v2-acceptance"
        / narration.relative_to(project)
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger_path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["issued_manifest_sha256_by_output_sha256"][manifest["output_sha256"]] = _sha256(
        manifest_path
    )
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=production",)


def test_dated_active_project_accepts_explicit_multi_manifest_narration_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        active_project_name="2026-08-15-semantic-v3-acceptance",
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = _sha256(manifest_path)
    ledger_path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["issued_manifest_sha256_by_output_sha256"][manifest["output_sha256"]] = [
        "a" * 64,
        manifest_hash,
    ]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=production",)


def test_historical_dated_project_accepts_ledger_bound_undated_predecessor_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _load_checker()
    project_name = "2026-08-14-x-article-v2-acceptance"
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        archived=True,
        archived_project_name=project_name,
        handoff_archive_slug="x-article-v2-acceptance",
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_path"] = str(
        project.parents[2]
        / "制作中"
        / "x-article-v2-acceptance"
        / narration.relative_to(project)
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger_path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["issued_manifest_sha256_by_output_sha256"][manifest["output_sha256"]] = _sha256(
        manifest_path
    )
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=historical",)


def test_checker_accepts_a_complete_synthetic_sample_and_writes_redacted_report(tmp_path: Path) -> None:
    """Would fail if a valid offline sample could not pass its delivery gate."""
    checker = _load_checker()
    handoff, project, final, narration = _make_delivery(tmp_path)

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 0
    report = json.loads((project / "delivery-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["mode"] == "sample"
    assert report["media"]["width"] == 1920
    assert report["artifacts"]["narration"] == "工程/media/narration.wav"
    serialized = json.dumps(report, ensure_ascii=False)
    assert str(project) not in serialized
    assert str(narration) not in serialized
    assert "https://" not in serialized


def test_sample_delivery_does_not_consult_the_canonical_provenance_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic sample validation remains outside the canonical production binding lane."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    monkeypatch.setattr(
        checker.indextts2,
        "_load_provenance_ledger_document",
        lambda *args, **kwargs: pytest.fail("sample lane must not load the production ledger"),
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 0


def test_new_sample_artifacts_emit_the_current_voice_id(tmp_path: Path) -> None:
    """Would fail if a newly emitted offline sample reverted to the retired v1 voice."""
    emitter = _load_sample_emitter()
    source_media = tmp_path / "source-media"
    source_wav = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source_media / "narration.wav")
    _write_wav(source_wav)
    (source_media / "captions.json").write_text(
        json.dumps([{"start": 0.2, "end": 9.6, "text": "合成字幕"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (source_media / "caption-qc.json").write_text("{}", encoding="utf-8")

    handoff, narration = emitter._write_sample_artifacts(
        source_media,
        tmp_path / "制作中" / "new-sample",
        source_wav,
    )

    assert f"voice: {CURRENT_VOICE_ID}" in handoff.read_text(encoding="utf-8")
    manifest = json.loads((narration.parent / "voice_manifest.json").read_text(encoding="utf-8"))
    assert manifest["voice_id"] == CURRENT_VOICE_ID


@pytest.mark.parametrize(
    "legacy_voice_id",
    [
        "user-indextts2-calm-v2",
        "user-indextts2-calm-v1",
        "pluvio-indextts2-calm-v1",
    ],
)
def test_new_production_rejects_legacy_handoff_voice(
    tmp_path: Path, legacy_voice_id: str
) -> None:
    """Would fail if caller-controlled new handoffs could select a historical voice."""
    checker = _load_checker()
    handoff, project, final, _ = _make_production_delivery(tmp_path)
    handoff.write_text(
        _handoff().replace(f"voice: {CURRENT_VOICE_ID}", f"voice: {legacy_voice_id}"),
        encoding="utf-8",
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=handoff-voice" in result.diagnostics


@pytest.mark.parametrize(
    "legacy_voice_id",
    [
        "user-indextts2-calm-v2",
        "user-indextts2-calm-v1",
        "pluvio-indextts2-calm-v1",
    ],
)
def test_new_production_rejects_legacy_manifest_voice(
    tmp_path: Path, legacy_voice_id: str
) -> None:
    """Would fail if a new production manifest could use a historical voice."""
    checker = _load_checker()
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["voice_id"] = legacy_voice_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=voice-manifest" in result.diagnostics


def test_archived_v1_delivery_passes_only_at_the_canonical_workspace_archive_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a genuine archived v1 package lost its locked compatibility lane."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    ledger_before = indextts2.LOCKED_PROVENANCE_LEDGER_PATH.read_bytes()

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert indextts2.LOCKED_PROVENANCE_LEDGER_PATH.read_bytes() == ledger_before


def test_fake_tmp_archive_name_never_enables_historical_v1(tmp_path: Path) -> None:
    """Would fail if caller-selected directory names alone could bypass the v2 default."""
    checker = _load_checker()
    fake_archive = tmp_path / "已制作" / "8月上旬" / "old-v1"
    handoff, project, final, narration = _make_production_delivery(
        tmp_path,
        project=fake_archive,
    )
    handoff.write_text(
        _handoff(status="已完成", archive_slug="old-v1").replace(
            f"voice: {CURRENT_VOICE_ID}", "voice: user-indextts2-calm-v1"
        ),
        encoding="utf-8",
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["voice_id"] = "user-indextts2-calm-v1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=handoff-voice" in result.diagnostics


@pytest.mark.parametrize("change", ["fake-reference", "mismatched-reference-hash"])
def test_archived_v1_rejects_manifest_reference_not_locked_by_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Would fail if an archive trusted v1 reference metadata outside its canonical ledger entry."""
    checker = _load_checker()
    handoff, project, final, narration, reference = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if change == "fake-reference":
        fake_reference = reference.with_name("unlocked-v1.wav")
        _write_wav(fake_reference, seconds=1.0)
        manifest["reference_audio_path"] = str(fake_reference.resolve())
        manifest["reference_audio_sha256"] = _sha256(fake_reference)
    else:
        manifest["reference_audio_sha256"] = "e" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=voice-manifest" in result.diagnostics


def test_archived_v1_rejects_handoff_slug_mismatched_to_archive_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if an archived handoff could claim a different project identity."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    handoff.write_text(
        _handoff(status="已完成", archive_slug="different-project").replace(
            f"voice: {CURRENT_VOICE_ID}", "voice: user-indextts2-calm-v1"
        ),
        encoding="utf-8",
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=handoff-archive-slug" in result.diagnostics


def test_archived_delivery_preserves_an_existing_report_on_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a successful historical validation rewrote an archived report."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    report = project / "delivery-report.json"
    sentinel = b"historical-report-must-not-change"
    report.write_bytes(sentinel)

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert report.read_bytes() == sentinel


def test_archived_delivery_preserves_an_existing_report_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a rejected historical validation deleted an archived report."""
    checker = _load_checker()
    handoff, project, final, narration, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    report = project / "delivery-report.json"
    sentinel = b"historical-report-must-not-change"
    report.write_bytes(sentinel)
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert report.read_bytes() == sentinel


def test_canonical_archive_rejects_dotdot_handoff_without_touching_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a lexical dotdot handoff escaped strict historical path validation."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    report = project / "delivery-report.json"
    sentinel = b"historical-report-must-not-change"
    report.write_bytes(sentinel)
    dotdot_handoff = Path(str(project / "nested") + "/../交接稿.md")

    result = checker.check_delivery(dotdot_handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert report.read_bytes() == sentinel


def test_canonical_archive_rejects_dotdot_project_without_touching_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a dotdot project path could turn an archive into a mutable new delivery."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    report = project / "delivery-report.json"
    sentinel = b"historical-report-must-not-change"
    report.write_bytes(sentinel)
    dotdot_project = Path(str(project.parent / "placeholder") + "/../" + project.name)
    dotdot_handoff = dotdot_project / handoff.name
    dotdot_final = dotdot_project / final.relative_to(project)

    result = checker.check_delivery(dotdot_handoff, dotdot_project, dotdot_final, sample_mode=False)

    assert result.exit_code == 2
    assert report.read_bytes() == sentinel


@pytest.mark.parametrize("input_kind", ["outside-handoff", "dotdot-final"])
def test_canonical_archive_rejects_untrusted_inputs_without_touching_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_kind: str,
) -> None:
    """Would fail if an archived checker accepted an outside or lexical-escape input."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    report = project / "delivery-report.json"
    sentinel = b"historical-report-must-not-change"
    report.write_bytes(sentinel)
    if input_kind == "outside-handoff":
        outside_handoff = tmp_path / "outside-handoff.md"
        outside_handoff.write_text(handoff.read_text(encoding="utf-8"), encoding="utf-8")
        result = checker.check_delivery(outside_handoff, project, final, sample_mode=False)
    else:
        dotdot_final = Path(str(project / "nested") + "/../" + str(final.relative_to(project)))
        result = checker.check_delivery(handoff, project, dotdot_final, sample_mode=False)

    assert result.exit_code == 2
    assert report.read_bytes() == sentinel


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_canonical_archive_rejects_junction_handoff_without_touching_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if an archive checker followed a junction to a handoff outside the project."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_canonical_archived_v1_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    report = project / "delivery-report.json"
    sentinel = b"historical-report-must-not-change"
    report.write_bytes(sentinel)
    outside = tmp_path / "outside-handoff"
    outside.mkdir()
    shutil.copy2(handoff, outside / handoff.name)
    junction = project / "handoff-junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junctions are unavailable")
    try:
        result = checker.check_delivery(junction / handoff.name, project, final, sample_mode=False)

        assert result.exit_code == 2
        assert report.read_bytes() == sentinel
    finally:
        if junction.exists():
            os.rmdir(junction)


def test_delivery_diagnostics_never_expose_reference_path_or_hash(tmp_path: Path) -> None:
    """Would fail if rejected production metadata exposed private reference details."""
    checker = _load_checker()
    reference_path = r"D:\private\voice\user-indextts2-calm-v2.wav"
    reference_hash = "d" * 64
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reference_audio_path"] = reference_path
    manifest["reference_audio_sha256"] = reference_hash
    manifest["voice_id"] = "user-indextts2-calm-v1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)
    rendered = "\n".join(result.diagnostics)

    assert result.exit_code == 2
    assert reference_path not in rendered
    assert reference_hash not in rendered


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("missing-field", 2),
        ("missing-artifact", 3),
        ("fallback", 2),
        ("wrong-voice", 2),
        ("manifest-hash", 2),
        ("caption-hash", 2),
        ("failed-qc", 2),
        ("production-timing", 2),
        ("media-spec", 2),
        ("missing-contact-sheet", 3),
    ],
)
def test_checker_blocks_each_delivery_contract_break(tmp_path: Path, change: str, expected: int) -> None:
    """Would fail if a broken artifact branch were incorrectly delivered."""
    checker = _load_checker()
    handoff, project, final, narration = _make_delivery(tmp_path)
    media = narration.parent
    if change == "missing-field":
        handoff.write_text(_handoff().replace("archive_slug: 2026-08-11-sample\n", ""), encoding="utf-8")
    elif change == "missing-artifact":
        (media / "captions.srt").unlink()
    elif change in {"fallback", "wrong-voice", "manifest-hash"}:
        manifest_path = media / "voice_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if change == "fallback":
            manifest["used_fallback"] = True
        elif change == "wrong-voice":
            manifest["voice_id"] = "other-voice"
        else:
            manifest["output_sha256"] = "f" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif change in {"caption-hash", "failed-qc", "production-timing"}:
        qc_path = media / "caption-qc.json"
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        if change == "caption-hash":
            qc["narration_sha256"] = "e" * 64
        elif change == "failed-qc":
            qc["status"] = "fail"
        else:
            qc["timing_source"] = "volcengine-word-timestamps"
        qc_path.write_text(json.dumps(qc), encoding="utf-8")
    elif change == "media-spec":
        _write_mp4(final, size="1280x720")
    elif change == "missing-contact-sheet":
        (project / "质检" / "contact-sheet.jpg").unlink()

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == expected
    assert not (project / "delivery-report.json").exists()


@pytest.mark.parametrize(
    "change",
    ["missing-vtt", "empty-captions", "empty-words", "corrupt-words", "srt-mismatch", "vtt-mismatch"],
)
def test_checker_requires_all_six_nonempty_consistent_task7_caption_artifacts(tmp_path: Path, change: str) -> None:
    """Would fail if a missing, empty, corrupt, or mismatched subtitle artifact could ship."""
    checker = _load_checker()
    handoff, project, final, narration = _make_delivery(tmp_path)
    media = narration.parent
    if change == "missing-vtt":
        (media / "captions.vtt").unlink()
    elif change == "empty-captions":
        (media / "captions.json").write_text("[]", encoding="utf-8")
    elif change == "empty-words":
        (media / "captions_words.json").write_text("[]", encoding="utf-8")
    elif change == "corrupt-words":
        (media / "captions_words.json").write_text("{bad", encoding="utf-8")
    elif change == "srt-mismatch":
        (media / "captions.srt").write_text("1\n00:00:00,200 --> 00:00:04,800\n错误\n", encoding="utf-8")
    else:
        (media / "captions.vtt").write_text("WEBVTT\n\n00:00:00.200 --> 00:00:04.800\n错误\n", encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code in {2, 3}
    assert not (project / "delivery-report.json").exists()


def test_checker_rejects_empty_or_undecodable_contact_sheet(tmp_path: Path) -> None:
    """Would fail if a placeholder byte file satisfied visual QC."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    contact = project / "质检" / "contact-sheet.jpg"
    contact.write_bytes(b"not-an-image")

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 2
    assert "rule=contact-sheet-image" in result.diagnostics


def test_checker_invalidates_a_prior_pass_report_before_a_failed_recheck(tmp_path: Path) -> None:
    """Would fail if a later rejection left a stale pass commit marker behind."""
    checker = _load_checker()
    handoff, project, final, narration = _make_delivery(tmp_path)
    assert checker.check_delivery(handoff, project, final, sample_mode=True).exit_code == 0
    qc_path = narration.parent / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["status"] = "fail"
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    rejected = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert rejected.exit_code == 2
    assert not (project / "delivery-report.json").exists()


def test_checker_report_commit_does_not_follow_a_predictable_reparse_temp(tmp_path: Path) -> None:
    """Would fail if a precreated report temp symlink could receive report bytes."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    outside = tmp_path / "outside-report.json"
    outside.write_text("outside-unchanged", encoding="utf-8")
    predictable = project / ".delivery-report.json.tmp"
    try:
        os.symlink(outside, predictable)
    except OSError:
        pytest.skip("symlinks are unavailable")

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 0
    assert outside.read_text(encoding="utf-8") == "outside-unchanged"


def test_checker_requires_a_positive_integer_word_count(tmp_path: Path) -> None:
    """Would fail if an empty production word count were accepted as a delivery contract."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    handoff.write_text(_handoff().replace("word_count: 80", "word_count: 0"), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 2
    assert "rule=handoff-word-count" in result.diagnostics


def test_checker_requires_real_word_timestamps_for_production(tmp_path: Path) -> None:
    """Would fail if production accepted synthetic timings as ASR evidence."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path, sample_mode=False)

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2


def test_checker_returns_privacy_exit_without_echoing_private_handoff_text(tmp_path: Path) -> None:
    """Would fail if the privacy wall were downgraded to a contract failure or leaked text."""
    checker = _load_checker()
    secret_url = "https://example.invalid/private-source"
    handoff, project, final, _ = _make_delivery(tmp_path)
    handoff.write_text(_handoff(extra=f"参考网址：{secret_url}\n"), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=True)
    rendered = "\n".join(result.diagnostics)

    assert result.exit_code == 4
    assert secret_url not in rendered
    assert "rule=privacy-URL-scheme" in rendered


def test_checker_rejects_outside_and_reparse_artifacts_without_following_them(tmp_path: Path) -> None:
    """Would fail if an untrusted final path or junction could escape the active project."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    outside = tmp_path / "outside.mp4"
    _write_mp4(outside)

    assert checker.check_delivery(handoff, project, outside, sample_mode=True).exit_code == 2
    redirected = project / "media" / "captions.srt"
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    redirected.unlink()
    try:
        os.symlink(target, redirected)
    except OSError:
        pytest.skip("symlinks are unavailable")
    assert checker.check_delivery(handoff, project, final, sample_mode=True).exit_code == 3


def test_checker_rejects_an_outside_handoff_before_reading_it(tmp_path: Path) -> None:
    """Would fail if a public handoff outside the supplied project could enter delivery."""
    checker = _load_checker()
    _, project, final, _ = _make_delivery(tmp_path)
    outside_handoff = tmp_path / "outside-handoff.md"
    outside_handoff.write_text(_handoff(), encoding="utf-8")

    result = checker.check_delivery(outside_handoff, project, final, sample_mode=True)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=handoff-project-containment",)


def test_checker_cli_help_and_diagnostics_are_explicitly_offline_and_redacted(tmp_path: Path) -> None:
    """Would fail if the CLI hid its offline boundary or printed absolute/private inputs."""
    help_result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, check=False, text=True
    )
    assert help_result.returncode == 0
    assert "offline" in help_result.stdout.lower()
    assert "live APIs are not called" in help_result.stdout

    handoff, project, final, _ = _make_delivery(tmp_path)
    handoff.write_text(_handoff(extra="参考网址：https://example.invalid/private\n"), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(handoff), str(project), str(final), "--sample-mode"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert run.returncode == 4
    assert str(handoff) not in run.stdout + run.stderr
    assert "example.invalid" not in run.stdout + run.stderr


def test_checker_accepts_the_real_task6_and_task7_production_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production words deliberately have no per-word source field in Task 7."""
    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0
    assert (project / "delivery-report.json").is_file()
    words = json.loads((narration.parent / "captions_words.json").read_text(encoding="utf-8"))
    assert all(set(word) == {"text", "start", "end", "isGap"} for word in words)


def test_active_dotdot_handoff_fails_before_report_mutation(tmp_path: Path) -> None:
    """Would fail if an active project could erase its report before rejecting project-dotdot handoff input."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    report = project / "delivery-report.json"
    sentinel = b"active-report-must-not-change"
    report.write_bytes(sentinel)
    dotdot_handoff = Path(str(project / "nested") + "/../" + handoff.name)

    result = checker.check_delivery(dotdot_handoff, project, final, sample_mode=True)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=handoff-project-containment",)
    assert report.read_bytes() == sentinel


def test_active_outside_absolute_handoff_fails_before_report_mutation(tmp_path: Path) -> None:
    """Would fail if an absolute handoff outside an active project could mutate its report."""
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    report = project / "delivery-report.json"
    sentinel = b"active-report-must-not-change"
    report.write_bytes(sentinel)
    outside_handoff = tmp_path / "outside-handoff.md"
    outside_handoff.write_text(handoff.read_text(encoding="utf-8"), encoding="utf-8")

    result = checker.check_delivery(outside_handoff.resolve(), project, final, sample_mode=True)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=handoff-project-containment",)
    assert report.read_bytes() == sentinel


@pytest.mark.parametrize("change", ["fake-reference", "mismatched-reference-hash"])
def test_new_v2_production_rejects_reference_not_bound_to_canonical_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Would fail if current-voice production trusted an unregistered reference path or hash."""
    checker = _load_checker()
    handoff, project, final, narration, reference = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if change == "fake-reference":
        fake_reference = reference.with_name("unlocked-v2.wav")
        _write_wav(fake_reference, seconds=1.0)
        manifest["reference_audio_path"] = str(fake_reference.resolve())
        manifest["reference_audio_sha256"] = _sha256(fake_reference)
    else:
        manifest["reference_audio_sha256"] = "e" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=voice-manifest",)


def test_historical_v2_rejects_reference_not_bound_to_canonical_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if an archived current-voice package could bypass the provenance ledger."""
    checker = _load_checker()
    handoff, project, final, narration, reference = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
        archived=True,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fake_reference = reference.with_name("unlocked-v2.wav")
    _write_wav(fake_reference, seconds=1.0)
    manifest["reference_audio_path"] = str(fake_reference.resolve())
    manifest["reference_audio_sha256"] = _sha256(fake_reference)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=voice-manifest",)


def test_new_v2_production_passes_with_its_canonical_ledger_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a valid current-voice production no longer matched Task 2 provenance."""
    checker = _load_checker()
    handoff, project, final, _, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 0


def test_new_v2_production_rejects_manifest_bytes_not_bound_by_its_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A v2 delivery cannot pass after any manifest provenance field changes."""
    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pronunciation_contract_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=voice-manifest",)


def test_new_v2_production_rejects_legacy_ledger_without_manifest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current-voice production must not treat a legacy issued hash as a manifest binding."""
    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    ledger_path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger.pop("issued_manifest_sha256_by_output_sha256")
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=voice-manifest",)


def test_delivery_rejects_manifest_swapped_between_payload_and_ledger_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delivery must not parse one manifest object then hash a restored ledger-bound one."""
    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    ledger_bound_bytes = manifest_path.read_bytes()
    manifest = json.loads(ledger_bound_bytes)
    manifest["pronunciation_contract_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    real_open = Path.open
    manifest_opens = 0

    def restore_ledger_bound_manifest_on_second_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal manifest_opens
        if path == manifest_path:
            manifest_opens += 1
            if manifest_opens == 2:
                manifest_path.write_bytes(ledger_bound_bytes)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", restore_ledger_bound_manifest_on_second_open)

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=voice-manifest",)
    assert manifest_opens == 1


def test_delivery_rejects_duplicate_manifest_keys_even_when_ledger_hash_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sample, archive, and v2 modes share duplicate-key rejection for manifests."""
    checker = _load_checker()
    handoff, project, final, narration, _ = _make_locked_v2_production_delivery(
        checker,
        monkeypatch,
        tmp_path,
    )
    manifest_path = narration.parent / "voice_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    duplicate_bytes = (
        "{"
        + ",".join(
            [
                f'"voice_id":{json.dumps(payload["voice_id"])}',
                f'"voice_id":{json.dumps(payload["voice_id"])}',
                *[
                    f"{json.dumps(key)}:{json.dumps(value)}"
                    for key, value in payload.items()
                    if key != "voice_id"
                ],
            ]
        )
        + "}"
    ).encode("utf-8")
    manifest_path.write_bytes(duplicate_bytes)
    ledger_path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["issued_manifest_sha256_by_output_sha256"][payload["output_sha256"]] = _sha256(
        manifest_path
    )
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=voice-manifest", "rule=voice-manifest-json")


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("provider", "other-provider"),
        ("voice_id", "other-voice"),
        ("model", "other-model"),
        ("reference_audio_path", ""),
        ("reference_audio_sha256", "not-a-sha256"),
        ("output_path", "工程/media/other.wav"),
        ("output_sha256", "0" * 64),
        ("segment_contract_path", ""),
        ("segment_contract_sha256", "not-a-sha256"),
        ("segment_count", 0),
        ("playback_speed", 0),
        ("playback_speed", 1.0),
        ("pronunciation_contract_path", ""),
        ("pronunciation_contract_sha256", "not-a-sha256"),
        ("used_fallback", True),
    ],
)
def test_production_rejects_each_invalid_task6_manifest_field(
    tmp_path: Path, field: str, invalid_value: object
) -> None:
    """Would fail if any malformed canonical Task 6 field passed production delivery."""
    checker = _load_checker()
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = invalid_value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=voice-manifest" in result.diagnostics


def test_production_rejects_extra_task6_manifest_fields(tmp_path: Path) -> None:
    """Would fail if the delivery checker accepted a manifest other than Task 6's schema."""
    checker = _load_checker()
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unexpected"] = "not-a-Task6-field"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=voice-manifest" in result.diagnostics


@pytest.mark.parametrize("field", [
    "provider",
    "voice_id",
    "model",
    "reference_audio_path",
    "reference_audio_sha256",
    "output_path",
    "output_sha256",
    "segment_contract_path",
    "segment_contract_sha256",
    "segment_count",
    "playback_speed",
    "pronunciation_contract_path",
    "pronunciation_contract_sha256",
    "used_fallback",
])
def test_production_rejects_missing_task6_manifest_fields(tmp_path: Path, field: str) -> None:
    """Would fail if an incomplete Task 6 manifest were incorrectly accepted as production."""
    checker = _load_checker()
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop(field)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2
    assert "rule=voice-manifest" in result.diagnostics


def test_production_rejects_manifest_output_path_for_another_file(tmp_path: Path) -> None:
    checker = _load_checker()
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    manifest_path = narration.parent / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_path"] = str((project / "other.wav").resolve())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert checker.check_delivery(handoff, project, final, sample_mode=False).exit_code == 2


@pytest.mark.parametrize("change", ["phrase-source", "qc-source-media", "qc-resource", "word-source"])
def test_production_rejects_task7_provenance_but_not_its_word_schema(tmp_path: Path, change: str) -> None:
    checker = _load_checker()
    handoff, project, final, narration = _make_production_delivery(tmp_path)
    media = narration.parent
    if change == "phrase-source":
        captions = json.loads((media / "captions.json").read_text(encoding="utf-8"))
        captions[0]["source"] = "synthetic-local-sample"
        (media / "captions.json").write_text(json.dumps(captions), encoding="utf-8")
    elif change == "word-source":
        words = json.loads((media / "captions_words.json").read_text(encoding="utf-8"))
        words[0]["source"] = "invented-per-word-source"
        (media / "captions_words.json").write_text(json.dumps(words), encoding="utf-8")
    else:
        qc_path = media / "caption-qc.json"
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        qc["source_media" if change == "qc-source-media" else "asr_resource_id"] = ""
        qc_path.write_text(json.dumps(qc), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=False)

    assert result.exit_code == 2


@pytest.mark.parametrize(
    "change", ["empty-times", "not-enough-frames", "bad-layout", "no-transition"]
)
def test_checker_requires_representative_contact_sheet_coverage(tmp_path: Path, change: str) -> None:
    checker = _load_checker()
    handoff, project, final, _ = _make_delivery(tmp_path)
    metadata_path = project / "质检" / "contact-sheet-qc.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if change == "empty-times":
        metadata["times_s"] = []
    elif change == "not-enough-frames":
        metadata["frame_count"] = 4
    elif change == "no-transition":
        metadata["coverage"] = ["open", "early", "mid", "late", "near-end", "near-end"]
    else:
        metadata["layout"] = "2x3"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    result = checker.check_delivery(handoff, project, final, sample_mode=True)

    assert result.exit_code == 2
    assert "rule=contact-sheet-coverage" in result.diagnostics

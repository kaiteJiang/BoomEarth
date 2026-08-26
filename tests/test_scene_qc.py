"""Real final-MP4 scene evidence and contact-sheet tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

from boomearth.video.scene_qc import (
    SceneQCError,
    create_scene_contact_sheet,
    frame_pixel_counts,
    render_scene_qc,
    scene_contact_sheet_qc,
    scene_sample_times,
)
from boomearth.video.scene_timeline import build_scene_timeline
from test_scene_timeline import _populate_project, _words


def _make_video(path: Path, *, panel: bool = True, foreground: bool = True) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is unavailable")
    filters: list[str] = []
    if foreground:
        filters.append("drawbox=x=900:y=350:w=120:h=220:color=black:t=fill")
    if panel:
        filters.extend(
            (
                "drawbox=x=0:y=930:w=1920:h=150:color=black:t=fill",
                "drawbox=x=760:y=985:w=400:h=24:color=white:t=fill",
            )
        )
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=white:s=1920x1080:r=30:d=16",
    ]
    if filters:
        command.extend(["-vf", ",".join(filters)])
    command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)])
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(command, capture_output=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "2026-08-13-scene-qc"
    _populate_project(root)
    captions = root / "工程" / "media" / "captions"
    cues = [
        {
            "start": 0.2,
            "end": 1.5,
            "text": "第一段介绍",
            "source": "volcengine-word-timestamps",
        }
    ]
    (captions / "captions.json").write_text(
        json.dumps(cues, ensure_ascii=False), encoding="utf-8"
    )
    build_scene_timeline(project_root=root)
    _make_video(root / "成片" / "final.mp4")
    return root


def test_scene_sample_formula_is_exact_and_bounded() -> None:
    assert scene_sample_times(start=4.2, end=7.4) == pytest.approx(
        (4.648, 5.8, 7.08)
    )
    with pytest.raises(SceneQCError, match="scene timing is invalid"):
        scene_sample_times(start=1.0, end=1.9)


def test_anchor_dark_semtransparent_panel_counts_as_dark_caption_pixels() -> None:
    frame = bytearray(b"\xff" * (1920 * 1080 * 3))
    panel_start = (1080 - 100) * 1920 * 3
    for index in range(panel_start, panel_start + 600 * 3, 3):
        frame[index : index + 3] = b"\x55\x55\x55"

    _foreground, dark, bright = frame_pixel_counts(bytes(frame))

    assert dark == 600
    assert bright > 100


def test_archived_scene_qc_can_recompute_the_legacy_dark_pixel_threshold() -> None:
    frame = bytearray(b"\xff" * (1920 * 1080 * 3))
    panel_start = (1080 - 100) * 1920 * 3
    for index in range(panel_start, panel_start + 600 * 3, 3):
        frame[index : index + 3] = b"\x55\x55\x55"

    _foreground, dark, bright = frame_pixel_counts(
        bytes(frame), dark_threshold=65
    )

    assert dark == 0
    assert bright > 100


def test_real_scene_qc_extracts_three_frames_per_scene(project: Path) -> None:
    result = render_scene_qc(
        final_mp4=project / "成片" / "final.mp4",
        content_plan=project / "工程" / "content-plan.json",
        scene_timeline=project / "工程" / "scene-timeline.json",
        qc_root=project / "质检",
    )

    assert len(result.evidence) == 12
    assert result.qc_path == project / "质检" / "scene-preview-qc.json"
    assert all(item.foreground_pixels >= 2_000 for item in result.evidence)
    assert len(list((project / "质检" / "scene-previews").glob("*.png"))) == 12
    payload = json.loads(result.qc_path.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert len(payload["evidence"]) == 12


def test_all_white_frame_fails_foreground_gate(tmp_path: Path, project: Path) -> None:
    white = project / "成片" / "white.mp4"
    _make_video(white, panel=False, foreground=False)

    with pytest.raises(SceneQCError, match="scene frame visibility failed"):
        render_scene_qc(
            final_mp4=white,
            content_plan=project / "工程" / "content-plan.json",
            scene_timeline=project / "工程" / "scene-timeline.json",
            qc_root=project / "质检",
        )

    assert not (project / "质检" / "scene-preview-qc.json").exists()


def test_caption_active_sample_requires_dark_panel_and_bright_glyphs(project: Path) -> None:
    no_panel = project / "成片" / "no-panel.mp4"
    _make_video(no_panel, panel=False, foreground=True)

    with pytest.raises(SceneQCError, match="scene caption visibility failed"):
        render_scene_qc(
            final_mp4=no_panel,
            content_plan=project / "工程" / "content-plan.json",
            scene_timeline=project / "工程" / "scene-timeline.json",
            qc_root=project / "质检",
        )


def test_caption_ending_before_next_render_frame_is_not_required_visible(project: Path) -> None:
    no_panel = project / "成片" / "no-panel.mp4"
    _make_video(no_panel, panel=False, foreground=True)
    timeline = json.loads(
        (project / "工程" / "scene-timeline.json").read_text(encoding="utf-8")
    )
    first = timeline["scenes"][0]
    sample = scene_sample_times(start=first["start"], end=first["end"])[0]
    captions = project / "工程" / "media" / "captions" / "captions.json"
    captions.write_text(
        json.dumps(
            [
                {
                    "start": sample - 0.01,
                    "end": sample + 0.01,
                    "text": "边界字幕",
                    "source": "volcengine-word-timestamps",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = render_scene_qc(
        final_mp4=no_panel,
        content_plan=project / "工程" / "content-plan.json",
        scene_timeline=project / "工程" / "scene-timeline.json",
        qc_root=project / "质检",
    )

    assert len(result.evidence) == 12


def test_ffmpeg_failure_is_redacted(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boomearth.video.scene_qc as scene_qc

    monkeypatch.setattr(
        scene_qc,
        "shutil",
        SimpleNamespace(which=lambda _name: None),
    )

    with pytest.raises(SceneQCError, match="^scene frame extraction failed$"):
        render_scene_qc(
            final_mp4=project / "成片" / "final.mp4",
            content_plan=project / "工程" / "content-plan.json",
            scene_timeline=project / "工程" / "scene-timeline.json",
            qc_root=project / "质检",
        )


def test_replaced_final_video_invalidates_qc(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boomearth.video.scene_qc as scene_qc

    final = project / "成片" / "final.mp4"

    def replace_video(_evidence) -> None:
        final.write_bytes(b"replaced")

    monkeypatch.setattr(scene_qc, "_after_frames_extracted", replace_video)

    with pytest.raises(SceneQCError, match="scene QC input changed"):
        render_scene_qc(
            final_mp4=final,
            content_plan=project / "工程" / "content-plan.json",
            scene_timeline=project / "工程" / "scene-timeline.json",
            qc_root=project / "质检",
        )

    assert not (project / "质检" / "scene-preview-qc.json").exists()


def test_contact_sheet_covers_every_scene_midpoint(project: Path) -> None:
    output = project / "质检" / "contact-sheet.jpg"
    times = create_scene_contact_sheet(
        final_mp4=project / "成片" / "final.mp4",
        scene_timeline=project / "工程" / "scene-timeline.json",
        output=output,
    )
    payload = scene_contact_sheet_qc(
        scene_timeline=project / "工程" / "scene-timeline.json",
        times=times,
    )

    assert times == (2.1, 5.8, 9.2, 13.5)
    assert output.is_file()
    assert payload == {
        "frame_count": 4,
        "times_s": [2.1, 5.8, 9.2, 13.5],
        "layout": "2x2",
        "coverage": "all-scene-midpoints",
    }


def test_duplicate_scene_ids_cannot_publish(project: Path) -> None:
    timeline_path = project / "工程" / "scene-timeline.json"
    value = json.loads(timeline_path.read_text(encoding="utf-8"))
    value["scenes"][1]["id"] = "scene-01"
    timeline_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(SceneQCError, match="scene QC inputs are invalid"):
        render_scene_qc(
            final_mp4=project / "成片" / "final.mp4",
            content_plan=project / "工程" / "content-plan.json",
            scene_timeline=timeline_path,
            qc_root=project / "质检",
        )

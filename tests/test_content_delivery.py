"""Closed content-lane delivery artifact tests."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest

from boomearth.video.scene_qc import render_scene_qc
from boomearth.video.motion_plan import compile_motion_plan
from boomearth.video.scene_qc import extract_rgb_frame, motion_preview_times
from boomearth.video.scene_timeline import build_scene_timeline
from test_scene_qc import _make_video
from test_scene_timeline import _populate_project
from test_motion_plan import _write_inputs as _write_motion_inputs


SCRIPT = Path(__file__).parents[1] / "automation" / "scripts" / "check_delivery.py"


def _checker():
    spec = importlib.util.spec_from_file_location("content_delivery_checker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def content_project(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    project = tmp_path / "2026-08-13-content-delivery"
    _populate_project(project)
    for index in range(1, 5):
        asset = project / "工程" / "assets" / "xiaohei-illustrations" / f"scene-{index:02d}.png"
        asset.write_bytes(asset.read_bytes() + bytes([index]))
    captions = project / "工程" / "media" / "captions"
    (captions / "captions.json").write_text(
        json.dumps(
            [
                {
                    "start": 0.2,
                    "end": 1.5,
                    "text": "第一段介绍",
                    "source": "volcengine-word-timestamps",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    build_scene_timeline(project_root=project)
    final = project / "成片" / "final.mp4"
    _make_video(final)
    render_scene_qc(
        final_mp4=final,
        content_plan=project / "工程" / "content-plan.json",
        scene_timeline=project / "工程" / "scene-timeline.json",
        qc_root=project / "质检",
    )
    coverage = {
        "frame_count": 4,
        "times_s": [2.1, 5.8, 9.2, 13.5],
        "layout": "2x2",
        "coverage": "all-scene-midpoints",
    }
    return project, final, coverage


def test_any_content_marker_requires_the_closed_artifact_set(tmp_path: Path) -> None:
    checker = _checker()
    project = tmp_path / "partial"
    (project / "工程").mkdir(parents=True)
    (project / "工程" / "content-plan.json").write_text("{}\n", encoding="utf-8")
    errors: list[str] = []

    artifacts = checker._content_delivery_artifacts(project, project / "final.mp4", {}, errors)

    assert artifacts == ()
    assert errors == ["rule=content-plan"]


def test_legacy_xiaohei_asset_marker_still_requires_closed_v1_artifact_set(
    tmp_path: Path,
) -> None:
    checker = _checker()
    project = tmp_path / "partial-v1"
    (project / "工程" / "assets" / "xiaohei-illustrations").mkdir(parents=True)
    errors: list[str] = []

    artifacts = checker._content_delivery_artifacts(
        project, project / "final.mp4", {}, errors
    )

    assert artifacts == ()
    assert errors == ["rule=content-plan"]


def test_complete_content_artifacts_are_returned_for_receipt(content_project) -> None:
    checker = _checker()
    project, final, coverage = content_project
    errors: list[str] = []

    artifacts = checker._content_delivery_artifacts(project, final, coverage, errors)
    relative = {path.relative_to(project).as_posix() for path in artifacts}

    assert errors == []
    assert "工程/content-plan.json" in relative
    assert "工程/scene-timeline.json" in relative
    assert "质检/scene-preview-qc.json" in relative
    assert len([item for item in relative if item.startswith("质检/scene-previews/")]) == 12
    assert len([item for item in relative if item.startswith("工程/assets/")]) == 4


def test_changed_preview_fails_hash_binding(content_project) -> None:
    checker = _checker()
    project, final, coverage = content_project
    preview = project / "质检" / "scene-previews" / "scene-01-settle.png"
    preview.write_bytes(b"changed")
    errors: list[str] = []

    checker._content_delivery_artifacts(project, final, coverage, errors)

    assert "rule=scene-preview-qc" in errors


def test_scene_qc_evidence_is_reauthenticated_from_final_video(
    content_project,
) -> None:
    checker = _checker()
    project, final, coverage = content_project
    qc_path = project / "质检" / "scene-preview-qc.json"
    original = json.loads(qc_path.read_text(encoding="utf-8"))
    mutations = [
        ("set", "time_s", 0.25),
        ("set", "time_s", True),
        ("set", "time_s", float("nan")),
        ("set", "frame_sha256", "0" * 64),
        ("set", "foreground_pixels", 1),
        ("set", "foreground_pixels", True),
        ("set", "dark_caption_pixels", 1),
        ("set", "bright_caption_pixels", 1),
        ("set", "unexpected", True),
        ("delete", "preview_sha256", None),
    ]
    for action, field, value in mutations:
        qc = json.loads(json.dumps(original))
        if action == "delete":
            del qc["evidence"][0][field]
        else:
            qc["evidence"][0][field] = value
        qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
        errors: list[str] = []

        checker._content_delivery_artifacts(
            project,
            final,
            coverage,
            errors,
            historical=True,
        )

        assert "rule=scene-preview-qc" in errors, (action, field, value)


@pytest.mark.parametrize("target", ["qc", "preview"])
def test_scene_qc_rejects_concurrent_evidence_replacement(
    content_project,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    checker = _checker()
    project, final, coverage = content_project

    def replace_validated_artifact() -> None:
        path = (
            project / "质检" / "scene-preview-qc.json"
            if target == "qc"
            else project / "质检" / "scene-previews" / "scene-01-settle.png"
        )
        path.write_bytes(path.read_bytes() + b"changed")

    monkeypatch.setattr(
        checker,
        "_after_content_evidence_validation",
        replace_validated_artifact,
    )
    errors: list[str] = []

    checker._content_delivery_artifacts(
        project,
        final,
        coverage,
        errors,
        historical=True,
    )

    assert "rule=scene-preview-qc" in errors


def test_wrong_contact_sheet_midpoints_fail_closed(content_project) -> None:
    checker = _checker()
    project, final, coverage = content_project
    coverage["times_s"] = [2.0, 5.8, 9.2, 13.5]
    errors: list[str] = []

    checker._content_delivery_artifacts(project, final, coverage, errors)

    assert "rule=contact-sheet-coverage" in errors


def _motion_delivery_project(root: Path) -> tuple[Path, Path]:
    _write_motion_inputs(root)
    motion = compile_motion_plan(project_root=root)
    final = root / "成片" / "final.mp4"
    _make_video(final)
    qc_root = root / "质检"
    previews = qc_root / "motion-previews"
    previews.mkdir(parents=True)
    evidence: list[dict[str, object]] = []
    for scene_id, values in motion_preview_times(motion.plan).items():
        for kind, timestamp in values.items():
            preview = previews / f"{scene_id}-{kind}.png"
            preview.write_bytes(f"{scene_id}:{kind}".encode("ascii"))
            frame = extract_rgb_frame(
                ffmpeg="ffmpeg", final_mp4=final, timestamp=timestamp
            )
            evidence.append(
                {
                    "scene_id": scene_id,
                    "kind": kind,
                    "time_s": timestamp,
                    "frame_sha256": hashlib.sha256(frame).hexdigest(),
                    "preview_sha256": hashlib.sha256(preview.read_bytes()).hexdigest(),
                }
            )
    contact = qc_root / "motion-contact-sheet.jpg"
    contact.write_bytes(b"contact-sheet")
    value = {
        "schema_version": 1,
        "status": "pass",
        "final_video_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "content_plan_sha256": motion.content_plan_snapshot.sha256,
        "scene_timeline_sha256": motion.scene_timeline_snapshot.sha256,
        "motion_plan_sha256": motion.snapshot.sha256,
        "contact_sheet_sha256": hashlib.sha256(contact.read_bytes()).hexdigest(),
        "evidence": evidence,
    }
    (qc_root / "motion-preview-qc.json").write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return root, final


def test_motion_v2_delivery_reauthenticates_formal_plan_and_preview_evidence(
    tmp_path: Path,
) -> None:
    checker = _checker()
    project, final = _motion_delivery_project(tmp_path / "motion-delivery")
    errors: list[str] = []

    artifacts = checker._motion_delivery_artifacts(project, final, errors)
    relative = {path.relative_to(project).as_posix() for path in artifacts}

    assert errors == []
    assert "工程/motion-plan.json" in relative
    assert "质检/motion-preview-qc.json" in relative
    assert "质检/motion-contact-sheet.jpg" in relative
    assert any(path.startswith("质检/motion-previews/") for path in relative)


@pytest.mark.parametrize("target", ["plan", "preview", "contact", "qc"])
def test_motion_v2_delivery_fails_closed_after_evidence_replacement(
    tmp_path: Path,
    target: str,
) -> None:
    checker = _checker()
    project, final = _motion_delivery_project(tmp_path / f"motion-{target}")
    paths = {
        "plan": project / "工程" / "motion-plan.json",
        "preview": project / "质检" / "motion-previews" / "scene-01-start.png",
        "contact": project / "质检" / "motion-contact-sheet.jpg",
        "qc": project / "质检" / "motion-preview-qc.json",
    }
    paths[target].write_bytes(paths[target].read_bytes() + b"changed")
    errors: list[str] = []

    checker._motion_delivery_artifacts(project, final, errors)

    assert "rule=motion-preview-qc" in errors

"""Fully offline integration proof for the semantic-handdrawn V3 lane."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from boomearth.video.content_plan import load_content_plan_snapshot
from boomearth.video.illustration_manifest import load_illustration_manifest_snapshot
from boomearth.video.render_project import prepare_content_render_project
from boomearth.video.scene_qc import (
    create_scene_contact_sheet,
    publish_motion_preview_qc,
    render_scene_qc,
    scene_contact_sheet_qc,
)
from boomearth.video.semantic_qc import load_semantic_qc_snapshot
from boomearth.video.scene_timeline import validate_scene_timeline
from boomearth.video.artifacts import load_json_snapshot
from test_content_render_project import v3_project
from test_scene_qc import _make_video


ROOT = Path(__file__).parents[1]
CHECKER = ROOT / "automation" / "scripts" / "check_delivery.py"
CONTENT_RUNNER = ROOT / "automation" / "scripts" / "run_content_production.py"


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_semantic_plan_assets_qc_motion_render_and_delivery_are_connected(
    v3_project: Path,
) -> None:
    marker = v3_project / "质检" / "test-fixture-only.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "fixture_only": True,
                "real_visual_evidence": False,
                "reason": "generated local colors exercise wiring, not visual judgment",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    plan = load_content_plan_snapshot(project_root=v3_project)
    illustration = load_illustration_manifest_snapshot(v3_project)
    semantic = load_semantic_qc_snapshot(
        v3_project,
        content_plan=plan.plan,
    )
    prepared = prepare_content_render_project(
        project_root=v3_project,
        output_dir=v3_project / "工程" / "render-project",
        repo_root=ROOT,
    )
    final = v3_project / "成片" / "semantic-v3-offline-fixture.mp4"
    _make_video(final)
    render_scene_qc(
        final_mp4=final,
        content_plan=v3_project / "工程" / "content-plan.json",
        scene_timeline=v3_project / "工程" / "scene-timeline.json",
        qc_root=v3_project / "质检",
    )
    publish_motion_preview_qc(final, v3_project)
    times = create_scene_contact_sheet(
        final_mp4=final,
        scene_timeline=v3_project / "工程" / "scene-timeline.json",
        output=v3_project / "质检" / "contact-sheet.jpg",
    )
    contact_qc = scene_contact_sheet_qc(
        scene_timeline=v3_project / "工程" / "scene-timeline.json",
        times=times,
    )
    (v3_project / "质检" / "contact-sheet-qc.json").write_text(
        json.dumps(contact_qc, sort_keys=True) + "\n", encoding="utf-8"
    )

    runner = _module(CONTENT_RUNNER, "scheme_a_v3_content_runner")
    checker = _module(CHECKER, "scheme_a_v3_delivery_checker")
    timeline, protected, _handoff, motion, _cover_sources = runner._content_contract(
        SimpleNamespace(active_dir=v3_project)
    )
    errors: list[str] = []
    delivered = checker._content_delivery_artifacts(
        v3_project,
        final,
        contact_qc,
        errors,
    )

    assert errors == []
    assert "visual-system-v3" in (
        prepared.output_dir / "index.html"
    ).read_text(encoding="utf-8")
    assert (
        motion is not None
        and motion.plan.motion_profile == "semantic-handdrawn-v3"
    )
    assert {scene.visual_mode for scene in plan.plan.scenes} >= {
        "human-action",
        "handdrawn-flow",
        "source-collage",
    }
    assert all(scene.visual_mode != "type-led" for scene in plan.plan.scenes)
    assert semantic.report.reviewer_type == "multimodal-review"
    assert json.loads(marker.read_text(encoding="utf-8"))["real_visual_evidence"] is False
    assert (prepared.output_dir / "illustration-manifest.json").is_file()
    assert (prepared.output_dir / "motion-plan.json").is_file()
    assert {path.relative_to(v3_project).as_posix() for path in delivered} >= {
        "工程/content-plan.json",
        "工程/scene-timeline.json",
        "工程/motion-plan.json",
        "工程/assets/semantic-handdrawn/illustration-manifest.json",
        "工程/assets/semantic-handdrawn/semantic-qc.json",
    }
    assert {path.relative_to(v3_project).as_posix() for path in protected} >= {
        "工程/content-plan.json",
        "工程/scene-timeline.json",
        "工程/motion-plan.json",
        "工程/assets/semantic-handdrawn/semantic-qc.json",
    }
    timeline_value, _snapshot = load_json_snapshot(
        v3_project / "工程" / "scene-timeline.json", within=v3_project
    )
    validated_timeline = validate_scene_timeline(
        timeline_value, project_root=v3_project
    )
    narration_hash = hashlib.sha256(
        (v3_project / "工程" / "media" / "narration.wav").read_bytes()
    ).hexdigest()
    assert timeline.narration_sha256 == narration_hash
    assert validated_timeline.narration_sha256 == narration_hash


def test_v3_integration_modules_do_not_import_network_clients() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            ROOT / "src" / "boomearth" / "video" / "content_plan.py",
            ROOT / "src" / "boomearth" / "video" / "semantic_qc.py",
            ROOT / "src" / "boomearth" / "video" / "illustration_manifest.py",
            ROOT / "src" / "boomearth" / "video" / "render_project.py",
            ROOT / "automation" / "scripts" / "build_semantic_visual_gate.py",
        )
    )

    assert "import requests" not in sources
    assert "import httpx" not in sources
    assert "from urllib" not in sources

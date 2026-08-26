"""Content production extensions over the proven P0 finalizer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from test_v2_production_orchestration import (
    REPO_ROOT,
    _load_orchestrator,
    _prepared_runtime,
    _write_production_caption_package,
    _write_canonical_synthetic_inputs,
    _write_synthetic_wav,
    _format_cue,
)
from test_scene_timeline import PNG, _candidate, _populate_project
from boomearth.audio.indextts2 import CURRENT_VOICE_ID
from boomearth.video.scene_timeline import build_scene_timeline
from boomearth.video.content_plan import ContentPlanError, compile_content_plan
from boomearth.video.artifacts import capture_regular_file

CONTENT_SCRIPT = REPO_ROOT / "automation" / "scripts" / "run_content_production.py"


def _load_content_runner():
    spec = importlib.util.spec_from_file_location(
        "content_production_runner_under_test", CONTENT_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _enable_cover_contract(project: Path, headline: str) -> None:
    """Rebind the existing content-plan fixture to a cover-enabled handoff."""

    handoff = project / "交接稿.md"
    text = handoff.read_text(encoding="utf-8")
    text = text.replace(
        "archive_slug:",
        "covers: platform-defaults-v1\narchive_slug:",
        1,
    ).replace(
        "## 新稿分段",
        f"## 标题候选\n\n- {headline}\n\n## 新稿分段",
        1,
    )
    handoff.write_text(text, encoding="utf-8")
    (project / "工程" / "content-plan.json").unlink()
    compile_content_plan(
        project_root=project,
        candidate_path=project / "工程" / "content-plan.candidate.json",
    )


def test_new_punk_cover_contract_skips_legacy_in_process_renderer(
    tmp_path: Path,
) -> None:
    runner = _load_content_runner()
    handoff = tmp_path / "交接稿.md"
    handoff.write_text(
        "---\ncovers: punk-cover-giant-title-3x4-v1\n---\n\n## 标题候选\n\n- 新封面\n",
        encoding="utf-8",
    )

    assert runner._cover_handoff(capture_regular_file(handoff)) == (False, None)


def _runtime_doubles(orchestrator, project, *, mutate_during_render=None):
    def run_command(argv: list[str], **_: object) -> None:
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render")
            if mutate_during_render is not None:
                mutate_during_render()

    def mux(_render: Path, _narration: Path, output: Path, _duration: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def caption_qc(_final: Path, _captions: Path, output: Path) -> None:
        output.write_text('{"status":"pass"}\n', encoding="utf-8")

    return run_command, mux, caption_qc


def _write_final_frame_cover_evidence(content_runner, project, timeline) -> Path:
    project.render_path.parent.mkdir(parents=True, exist_ok=True)
    project.render_path.write_bytes(b"verified-final-video")
    project.contact_sheet_path.parent.mkdir(parents=True, exist_ok=True)
    project.contact_sheet_path.write_bytes(PNG)
    midpoints = tuple(
        round((scene.start + scene.end) / 2.0, 6) for scene in timeline.scenes
    )
    project.contact_sheet_qc_path.write_text(
        json.dumps(
            content_runner.scene_contact_sheet_qc(
                scene_timeline=project.active_dir / "工程" / "scene-timeline.json",
                times=midpoints,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    previews = project.active_dir / "质检" / "scene-previews"
    previews.mkdir(parents=True, exist_ok=True)
    evidence = []
    selected: Path | None = None
    for scene in timeline.scenes:
        for kind in ("settle", "midpoint", "late"):
            preview = previews / f"{scene.id}-{kind}.png"
            preview.write_bytes(PNG + f"-{scene.id}-{kind}".encode("ascii"))
            evidence.append(
                {
                    "scene_id": scene.id,
                    "time_s": round((scene.start + scene.end) / 2.0, 6),
                    "kind": kind,
                    "frame_sha256": "a" * 64,
                    "preview_sha256": hashlib.sha256(preview.read_bytes()).hexdigest(),
                    "foreground_pixels": 5000,
                    "dark_caption_pixels": 1500,
                    "bright_caption_pixels": 500,
                }
            )
            if scene == timeline.scenes[-1] and kind == "late":
                selected = preview
    assert selected is not None
    scene_qc = {
        "schema_version": 1,
        "status": "pass",
        "final_video_sha256": hashlib.sha256(
            project.render_path.read_bytes()
        ).hexdigest(),
        "content_plan_sha256": hashlib.sha256(
            (project.active_dir / "工程" / "content-plan.json").read_bytes()
        ).hexdigest(),
        "scene_timeline_sha256": hashlib.sha256(
            (project.active_dir / "工程" / "scene-timeline.json").read_bytes()
        ).hexdigest(),
        "evidence": evidence,
    }
    (project.active_dir / "质检" / "scene-preview-qc.json").write_text(
        json.dumps(scene_qc, ensure_ascii=False), encoding="utf-8"
    )
    return selected


def _receipt_checker(orchestrator, project, extra_paths_fn):
    def check(handoff: Path, active: Path, _final: Path, *, sample_mode: bool):
        assert sample_mode is False
        extra = extra_paths_fn(project)
        publication = orchestrator._capture_publication_manifest(
            active, project, handoff, extra_paths=extra
        )
        (active / "delivery-report.json").write_text(
            json.dumps(
                {
                    "status": "pass",
                    "mode": "production",
                    "rules": [{"id": "delivery-hard-gates", "status": "pass"}],
                    "artifacts": {
                        "handoff": "交接稿.md",
                        "narration": "工程/media/narration.wav",
                        "final": "成片/",
                        "contact_sheet": "质检/contact-sheet.jpg",
                        "contact_sheet_qc": "质检/contact-sheet-qc.json",
                    },
                    "artifact_sha256": publication,
                    "media": {},
                    "skipped_stages": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return SimpleNamespace(
            exit_code=0, diagnostics=("status=pass mode=production",)
        )

    return check


def test_no_extension_callback_runs_before_audio_approval_and_hash_validation(
    tmp_path: Path,
) -> None:
    """Would fail if content callbacks could mutate a project before audio approval."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-approval"
    )
    calls: list[str] = []

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=False,
        extra_input_paths=(project.active_dir / "工程" / "content-plan.json",),
        extra_publication_paths_fn=lambda _project: calls.append("publication") or (),
        post_render_qc_fn=lambda _project, _duration: calls.append("post-qc"),
        expected_contact_sheet_times_fn=lambda _duration: calls.append("times") or [],
        contact_sheet_qc_payload_fn=lambda _times: calls.append("payload") or {},
    )

    assert result.diagnostics == ("rule=audio-approval-required",)
    assert calls == []


def test_custom_hooks_publish_content_outputs_and_contact_sheet_payload(
    tmp_path: Path,
) -> None:
    """Would fail if extensions were omitted from receipt/archive or P0 QC was forced."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-hooks"
    )
    _write_production_caption_package(project)
    plan = project.active_dir / "工程" / "content-plan.json"
    plan.write_text('{"schema_version":1}\n', encoding="utf-8")
    scene_qc = project.active_dir / "质检" / "scene-preview-qc.json"
    preview = project.active_dir / "质检" / "scene-previews" / "scene-01-midpoint.png"
    callback_order: list[str] = []

    def contact(_final: Path, output: Path, _duration: float) -> list[float]:
        callback_order.append("contact")
        output.write_bytes(b"sheet")
        return [2.0, 8.0]

    def post_qc(_project, _duration: float) -> None:
        callback_order.append("post-qc")
        scene_qc.parent.mkdir(parents=True, exist_ok=True)
        scene_qc.write_text('{"status":"pass"}\n', encoding="utf-8")
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"preview")

    def extra_publications(_project) -> tuple[Path, ...]:
        callback_order.append("publication")
        return (plan, scene_qc, preview)

    run_command, mux, caption_qc = _runtime_doubles(orchestrator, project)
    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(
            project.narration_path.read_bytes()
        ).hexdigest(),
        extra_input_paths=(plan,),
        extra_publication_paths_fn=extra_publications,
        post_render_qc_fn=post_qc,
        expected_contact_sheet_times_fn=lambda _duration: [2.0, 8.0],
        contact_sheet_qc_payload_fn=lambda times: {
            "frame_count": 2,
            "times_s": times,
            "layout": "2x1",
            "coverage": "all-scene-midpoints",
        },
        handoff_text_fn=lambda _project, status, _duration, _words: (
            "---\nstatus: " + status + "\n---\n\n## 新稿分段\n\n### segment-001\n\n保留正文。\n"
        ),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (
            REPO_ROOT / "node-fixture",
            REPO_ROOT / "hyperframes-fixture",
        ),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact,
        caption_render_qc_fn=caption_qc,
        check_delivery_fn=_receipt_checker(orchestrator, project, extra_publications),
    )

    archive = Path(result.archive_path)
    assert result.exit_code == 0
    assert callback_order == ["contact", "post-qc", "publication", "publication"]
    assert json.loads(
        (archive / "质检" / "contact-sheet-qc.json").read_text(encoding="utf-8")
    ) == {
        "frame_count": 2,
        "times_s": [2.0, 8.0],
        "layout": "2x1",
        "coverage": "all-scene-midpoints",
    }
    publication = json.loads(
        (archive / "工程" / "publication-manifest.json").read_text(encoding="utf-8")
    )["artifacts"]
    assert "工程/content-plan.json" in publication
    assert "质检/scene-preview-qc.json" in publication
    assert "质检/scene-previews/scene-01-midpoint.png" in publication
    assert "### segment-001" in (archive / "交接稿.md").read_text(encoding="utf-8")


def test_changed_extra_input_after_render_blocks_archive(tmp_path: Path) -> None:
    """Would fail if a replaced content plan could cross the archive boundary."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-input-race"
    )
    _write_production_caption_package(project)
    plan = project.active_dir / "工程" / "content-plan.json"
    plan.write_text("approved\n", encoding="utf-8")
    run_command, mux, caption_qc = _runtime_doubles(
        orchestrator,
        project,
        mutate_during_render=lambda: plan.write_text("replaced\n", encoding="utf-8"),
    )

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(
            project.narration_path.read_bytes()
        ).hexdigest(),
        extra_input_paths=(plan,),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (
            REPO_ROOT / "node-fixture",
            REPO_ROOT / "hyperframes-fixture",
        ),
        run_command=run_command,
        mux_fn=mux,
        caption_render_qc_fn=caption_qc,
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=production-local-stage",)
    assert project.active_dir.is_dir()
    assert not project.archive_dir.exists()


def test_cli_reports_only_diagnostics_and_workspace_relative_archive(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Would fail if the CLI exposed an absolute local path on success."""

    content_runner = _load_content_runner()
    workspace = tmp_path / "workspace"
    archive = workspace / "已完成" / "content-cli"
    archive.mkdir(parents=True)
    monkeypatch.setattr(
        content_runner,
        "load_production_project",
        lambda **_: SimpleNamespace(),
    )
    monkeypatch.setattr(
        content_runner,
        "finalize_content_production",
        lambda **_: SimpleNamespace(
            exit_code=0,
            diagnostics=("status=pass mode=production",),
            archive_path=str(archive),
        ),
    )

    exit_code = content_runner.main(
        [
            "finalize",
            "--workspace-root",
            str(workspace),
            "--active-project",
            "content-cli",
            "--audio-approved",
            "--approved-narration-sha256",
            "0" * 64,
        ]
    )

    output = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert output == [
        "status=pass mode=production",
        "archive=已完成/content-cli",
    ]
    assert str(tmp_path) not in "\n".join(output)


def test_cli_rejects_archive_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    """Would fail if an unexpected archive path could be reported as accepted output."""

    content_runner = _load_content_runner()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(
        content_runner,
        "load_production_project",
        lambda **_: SimpleNamespace(),
    )
    monkeypatch.setattr(
        content_runner,
        "finalize_content_production",
        lambda **_: SimpleNamespace(
            exit_code=0,
            diagnostics=("status=pass mode=production",),
            archive_path=str(outside),
        ),
    )

    exit_code = content_runner.main(
        [
            "finalize",
            "--workspace-root",
            str(workspace),
            "--active-project",
            "content-cli",
            "--audio-approved",
            "--approved-narration-sha256",
            "0" * 64,
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().out.splitlines() == [
        "status=pass mode=production",
        "rule=content-production-archive",
    ]


def test_cli_passes_avatar_master_to_content_finalizer(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Would fail if the finalize CLI discarded an explicitly selected avatar master."""

    content_runner = _load_content_runner()
    workspace = tmp_path / "workspace"
    archive = workspace / "已完成" / "content-cli"
    archive.mkdir(parents=True)
    avatar_master = workspace / "进行中" / "content-cli" / "工程" / "avatar-master.mp4"
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        content_runner,
        "load_production_project",
        lambda **_: SimpleNamespace(),
    )

    def fake_finalizer(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            exit_code=0,
            diagnostics=("status=pass mode=production",),
            archive_path=str(archive),
        )

    monkeypatch.setattr(content_runner, "finalize_content_production", fake_finalizer)

    exit_code = content_runner.main(
        [
            "finalize",
            "--workspace-root",
            str(workspace),
            "--active-project",
            "content-cli",
            "--audio-approved",
            "--approved-narration-sha256",
            "0" * 64,
            "--avatar-master",
            str(avatar_master),
        ]
    )

    assert exit_code == 0
    assert calls["avatar_master"] == avatar_master
    assert capsys.readouterr().out.splitlines() == [
        "status=pass mode=production",
        "archive=已完成/content-cli",
    ]


def test_cli_passes_cover_overrides_to_content_finalizer(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Would fail if the finalize CLI discarded an approved cover override."""

    content_runner = _load_content_runner()
    workspace = tmp_path / "workspace"
    background = workspace / "进行中" / "content-cli" / "工程" / "cover.png"
    calls: dict[str, object] = {}
    monkeypatch.setattr(content_runner, "load_production_project", lambda **_: SimpleNamespace())

    def fake_finalizer(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(exit_code=2, diagnostics=("rule=expected",), archive_path=None)

    monkeypatch.setattr(content_runner, "finalize_content_production", fake_finalizer)
    exit_code = content_runner.main(
        [
            "finalize",
            "--workspace-root",
            str(workspace),
            "--active-project",
            "content-cli",
            "--audio-approved",
            "--approved-narration-sha256",
            "0" * 64,
            "--cover-background",
            str(background),
            "--cover-headline",
            "显式公开封面标题",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().out.splitlines() == ["rule=expected"]
    assert calls["cover_background"] == background
    assert calls["cover_headline"] == "显式公开封面标题"


def test_content_wrapper_wires_formal_inputs_and_content_qc_hooks(
    tmp_path: Path, monkeypatch
) -> None:
    """Would fail if the wrapper bypassed the proven finalizer or omitted content artifacts."""

    content_runner = _load_content_runner()

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    project = orchestrator.initialize_production_project(
        workspace_root=workspace, archive_slug="content-wrapper"
    )
    _populate_project(project.active_dir)
    build_scene_timeline(project_root=project.active_dir)
    calls: dict[str, object] = {}

    def fake_scene_qc(**kwargs):
        calls["scene_qc"] = kwargs
        previews = project.active_dir / "质检" / "scene-previews"
        previews.mkdir(parents=True)
        for scene in range(1, 5):
            for kind in ("settle", "midpoint", "late"):
                (previews / f"scene-{scene:02d}-{kind}.png").write_bytes(b"preview")
        path = project.active_dir / "质检" / "scene-preview-qc.json"
        path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(content_runner, "render_scene_qc", fake_scene_qc)

    def fake_finalizer(**kwargs):
        calls["finalizer"] = kwargs
        calls["active_handoff"] = kwargs["handoff_text_fn"](
            project, "制作中", 16.0, 24
        )
        calls["completed_handoff"] = kwargs["handoff_text_fn"](
            project, "已完成", 16.0, 24
        )
        kwargs["post_render_qc_fn"](project, 16.0)
        calls["publications"] = kwargs["extra_publication_paths_fn"](project)
        calls["times"] = kwargs["expected_contact_sheet_times_fn"](16.0)
        calls["payload"] = kwargs["contact_sheet_qc_payload_fn"](
            [2.1, 5.8, 9.2, 13.5]
        )
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",), archive_path=str(project.archive_dir))

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        finalize_fn=fake_finalizer,
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 0
    kwargs = calls["finalizer"]
    assert "mux_fn" not in kwargs
    assert "pre_archive_private_inputs_fn" not in kwargs
    protected = {path.relative_to(project.active_dir).as_posix() for path in kwargs["extra_input_paths"]}
    assert protected == {
        "工程/content-plan.json",
        "工程/scene-timeline.json",
        *(f"工程/assets/xiaohei-illustrations/scene-{index:02d}.png" for index in range(1, 5)),
    }
    publications = {
        path.relative_to(project.active_dir).as_posix() for path in calls["publications"]
    }
    assert protected <= publications
    assert "质检/scene-preview-qc.json" in publications
    assert len([path for path in publications if path.startswith("质检/scene-previews/")]) == 12
    assert calls["times"] == [2.1, 5.8, 9.2, 13.5]
    assert calls["payload"] == {
        "frame_count": 4,
        "times_s": [2.1, 5.8, 9.2, 13.5],
        "layout": "2x2",
        "coverage": "all-scene-midpoints",
    }
    assert "### segment-001" in calls["active_handoff"]
    assert "status: 制作中" in calls["active_handoff"]
    assert "### segment-004" in calls["completed_handoff"]
    assert "status: 已完成" in calls["completed_handoff"]
    assert "逐场景预览：pass" in calls["completed_handoff"]


def test_cover_marker_renders_defaults_before_publication_capture(
    tmp_path: Path, monkeypatch
) -> None:
    """Would fail if marker-driven covers used an unbound image/title or missed publication."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-covers"
    )
    _populate_project(project.active_dir)
    headline = "先学会判断，再让 AI 替你动手"
    _enable_cover_contract(project.active_dir, headline)
    build_scene_timeline(project_root=project.active_dir)
    calls: dict[str, object] = {"order": []}
    cover_relatives = (
        "封面/抖音-作品封面-1080x1920.png",
        "封面/抖音-主页预览-1080x1440.png",
        "封面/视频号-作品封面-1080x1260.png",
        "质检/cover-contact-sheet.jpg",
        "质检/cover-qc.json",
    )

    def fake_cover_renderer(root, source, actual_headline, font_path=None):
        calls["order"].append("covers")
        calls["cover"] = (root, source, actual_headline, font_path)
        artifacts = tuple(root / relative for relative in cover_relatives)
        for path in artifacts:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cover")
        return SimpleNamespace(status="pass", artifacts=artifacts, reused=False)

    selected_fallback: Path | None = None

    def fake_scene_qc(**_kwargs):
        nonlocal selected_fallback
        calls["order"].append("scene-qc")
        selected_fallback = _write_final_frame_cover_evidence(
            content_runner, project, content_runner._content_contract(project)[0]
        )

    monkeypatch.setattr(content_runner, "render_platform_covers", fake_cover_renderer)
    monkeypatch.setattr(content_runner, "render_scene_qc", fake_scene_qc)

    def fake_finalizer(**kwargs):
        calls["inputs"] = kwargs["extra_input_paths"]
        project.render_path.parent.mkdir(parents=True, exist_ok=True)
        project.render_path.write_bytes(b"verified-final-video")
        kwargs["post_render_qc_fn"](project, 16.0)
        calls["order"].append("publication")
        calls["publications"] = kwargs["extra_publication_paths_fn"](project)
        return SimpleNamespace(exit_code=0, diagnostics=(), archive_path=None)

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        finalize_fn=fake_finalizer,
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 0
    assert selected_fallback is not None
    assert calls["cover"] == (
        project.active_dir,
        selected_fallback,
        headline,
        None,
    )
    assert calls["order"] == ["scene-qc", "covers", "publication"]
    publications = [path.relative_to(project.active_dir).as_posix() for path in calls["publications"]]
    assert set(cover_relatives) <= set(publications)
    assert selected_fallback.relative_to(project.active_dir).as_posix() in publications


def test_assetless_cover_contract_uses_verified_final_frame_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="assetless-cover"
    )
    _populate_project(project.active_dir)
    _enable_cover_contract(project.active_dir, "没有插画也能安全生成封面")
    build_scene_timeline(project_root=project.active_dir)
    native_contract = content_runner._content_contract
    timeline, protected, handoff, motion, _assets = native_contract(project)
    monkeypatch.setattr(
        content_runner,
        "_content_contract",
        lambda _project: (timeline, protected, handoff, motion, ()),
    )
    selected: dict[str, Path] = {}

    def fake_scene_qc(**_kwargs):
        selected["fallback"] = _write_final_frame_cover_evidence(
            content_runner, project, timeline
        )

    def fake_cover_renderer(_root, source, _headline, font_path=None):
        assert font_path is None
        selected["rendered"] = source
        for path in content_runner.cover_artifact_paths(project.active_dir):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"cover")

    monkeypatch.setattr(content_runner, "render_scene_qc", fake_scene_qc)
    monkeypatch.setattr(content_runner, "render_platform_covers", fake_cover_renderer)

    def fake_finalizer(**kwargs):
        kwargs["post_render_qc_fn"](project, 16.0)
        return SimpleNamespace(exit_code=0, diagnostics=(), archive_path=None)

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        finalize_fn=fake_finalizer,
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 0
    assert selected["rendered"] == selected["fallback"]


def test_bound_xiaohei_override_cannot_bypass_missing_semantic_evidence(
    tmp_path: Path,
) -> None:
    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="cover-override-evidence"
    )
    _populate_project(project.active_dir)
    _enable_cover_contract(project.active_dir, "显式背景也不能绕过证据")
    build_scene_timeline(project_root=project.active_dir)
    bound_but_unverified = (
        project.active_dir
        / "工程"
        / "assets"
        / "xiaohei-illustrations"
        / "scene-01.png"
    )

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        cover_background=bound_but_unverified,
        finalize_fn=lambda **_: (_ for _ in ()).throw(
            AssertionError("must not finalize")
        ),
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=content-production-contract",)


def test_final_frame_lane_rejects_scene_qc_bound_to_another_final(
    tmp_path: Path,
) -> None:
    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="cover-final-hash"
    )
    _populate_project(project.active_dir)
    build_scene_timeline(project_root=project.active_dir)
    timeline = content_runner._content_contract(project)[0]
    _write_final_frame_cover_evidence(content_runner, project, timeline)
    qc_path = project.active_dir / "质检" / "scene-preview-qc.json"
    qc = json.loads(qc_path.read_text("utf-8"))
    qc["final_video_sha256"] = "0" * 64
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(ContentPlanError, match="^cover source is invalid$"):
        content_runner._verified_final_frame_source(project, timeline)


def test_cover_override_must_be_project_contained_before_finalizer_runs(
    tmp_path: Path,
) -> None:
    """Would fail if an explicit external cover background bypassed input snapshotting."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-cover-reject"
    )
    _populate_project(project.active_dir)
    _enable_cover_contract(project.active_dir, "合法公开标题")
    build_scene_timeline(project_root=project.active_dir)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        cover_background=outside,
        finalize_fn=lambda **_: (_ for _ in ()).throw(AssertionError("must not finalize")),
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=content-production-contract",)


def test_cover_override_must_be_a_bound_semantic_asset(
    tmp_path: Path,
) -> None:
    """Would fail if a safe but unreviewed project image could replace semantic evidence."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-cover-unbound"
    )
    _populate_project(project.active_dir)
    _enable_cover_contract(project.active_dir, "合法公开标题")
    build_scene_timeline(project_root=project.active_dir)
    unbound = project.active_dir / "工程" / "unreviewed-cover.png"
    unbound.write_bytes(PNG)

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        cover_background=unbound,
        finalize_fn=lambda **_: (_ for _ in ()).throw(
            AssertionError("must not finalize")
        ),
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=content-production-contract",)


def test_content_wrapper_snapshots_private_avatar_and_muxes_final_narration(
    tmp_path: Path, monkeypatch
) -> None:
    """Would fail if a private avatar were published or muxed against any audio but final narration."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-avatar"
    )
    _populate_project(project.active_dir)
    build_scene_timeline(project_root=project.active_dir)
    avatar_master = project.active_dir / "工程" / "avatar-master.mp4"
    avatar_master.write_bytes(b"private-avatar-master")
    calls: dict[str, object] = {}
    plan = object()

    monkeypatch.setattr(
        content_runner, "probe_video_size", lambda path: calls.setdefault("probe", path) and (720, 1280)
    )
    monkeypatch.setattr(
        content_runner,
        "plan_circle_avatar",
        lambda **kwargs: calls.setdefault("plan", kwargs) and plan,
    )

    def fake_render(background, master, narration, output, actual_plan):
        calls["render"] = (background, master, narration, output, actual_plan)

    monkeypatch.setattr(content_runner, "render_circle_avatar", fake_render)

    def fake_finalizer(**kwargs):
        calls["finalizer"] = kwargs
        background = project.active_dir / "工程" / "background.mp4"
        output = project.active_dir / "成片" / "boomearth-v2-production.mp4"
        kwargs["mux_fn"](background, project.narration_path, output, 16.0)
        kwargs["pre_archive_private_inputs_fn"](project)
        calls["publications"] = kwargs["extra_publication_paths_fn"](project)
        return SimpleNamespace(exit_code=0, diagnostics=(), archive_path=None)

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        avatar_master=avatar_master,
        finalize_fn=fake_finalizer,
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 0
    protected = set(calls["finalizer"]["extra_input_paths"])
    publications = set(calls["publications"])
    assert avatar_master in protected
    assert avatar_master not in publications
    master_sha256 = hashlib.sha256(b"private-avatar-master").hexdigest()
    workbench = project.workspace_root / "01-内容生产" / "视频工作台"
    private_master = (
        workbench
        / ".internal"
        / "heygen"
        / "archived-masters"
        / project.active_dir.name
        / f"{master_sha256}.mp4"
    )
    assert private_master.read_bytes() == b"private-avatar-master"
    assert not (project.workspace_root / ".internal" / "heygen").exists()
    assert not avatar_master.exists()
    assert calls["probe"] == avatar_master
    assert calls["plan"] == {"source_size": (720, 1280), "occupied": ()}
    assert calls["render"] == (
        project.active_dir / "工程" / "background.mp4",
        avatar_master,
        project.narration_path,
        project.active_dir / "成片" / "boomearth-v2-production.mp4",
        plan,
    )


def test_private_avatar_preservation_keeps_source_when_target_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a post-verification target replacement could delete the only verified master."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-avatar-target-race"
    )
    master = project.active_dir / "工程" / "avatar-master.mp4"
    master.write_bytes(b"private-avatar-master")
    master_identity, master_sha256 = content_runner._avatar_master_snapshot(master)
    workbench = project.workspace_root / "01-内容生产" / "视频工作台"
    target = (
        workbench
        / ".internal"
        / "heygen"
        / "archived-masters"
        / project.active_dir.name
        / f"{master_sha256}.mp4"
    )
    native_lock = content_runner._open_private_target_lock
    replaced = False

    def replace_target_before_lock(path: Path) -> int | None:
        nonlocal replaced
        if Path(path) == target and not replaced:
            replaced = True
            target.unlink()
            target.write_bytes(b"replacement")
        return native_lock(path)

    monkeypatch.setattr(content_runner, "_open_private_target_lock", replace_target_before_lock)

    with pytest.raises(RuntimeError, match="^private-avatar-master$"):
        content_runner._preserve_avatar_master(
            project, master, master_identity, master_sha256
        )

    assert replaced is True
    assert master.read_bytes() == b"private-avatar-master"
    assert target.read_bytes() == b"replacement"


@pytest.mark.skipif(os.name != "nt", reason="Windows target-lock semantics required")
def test_private_avatar_preservation_locks_target_during_source_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a target could be replaced at the instant its verified source is unlinked."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-avatar-unlink-race"
    )
    master = project.active_dir / "工程" / "avatar-master.mp4"
    master.write_bytes(b"private-avatar-master")
    master_identity, master_sha256 = content_runner._avatar_master_snapshot(master)
    workbench = project.workspace_root / "01-内容生产" / "视频工作台"
    target = (
        workbench
        / ".internal"
        / "heygen"
        / "archived-masters"
        / project.active_dir.name
        / f"{master_sha256}.mp4"
    )
    native_unlink = Path.unlink
    replacement_blocked = False

    def replace_target_at_source_unlink(path: Path, *args, **kwargs) -> None:
        nonlocal replacement_blocked
        if Path(path) == master:
            try:
                native_unlink(target)
                target.write_bytes(b"replacement")
            except PermissionError:
                replacement_blocked = True
        native_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", replace_target_at_source_unlink)

    content_runner._preserve_avatar_master(
        project, master, master_identity, master_sha256
    )

    assert replacement_blocked is True
    assert not master.exists()
    assert target.read_bytes() == b"private-avatar-master"


def test_content_wrapper_rejects_an_avatar_outside_the_active_project(
    tmp_path: Path,
) -> None:
    """Would fail if an external private provider artifact could reach the renderer."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-avatar-reject"
    )
    avatar_master = tmp_path / "outside" / "avatar-master.mp4"
    avatar_master.parent.mkdir()
    avatar_master.write_bytes(b"private-avatar-master")

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        avatar_master=avatar_master,
        finalize_fn=lambda **_: (_ for _ in ()).throw(AssertionError("must not render")),
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=content-production-contract",)


def test_content_wrapper_rejects_avatar_traversal_outside_the_active_project(
    tmp_path: Path,
) -> None:
    """Would fail if lexical containment allowed a parent traversal into another project path."""

    content_runner = _load_content_runner()
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="content-avatar-traversal"
    )
    _populate_project(project.active_dir)
    build_scene_timeline(project_root=project.active_dir)
    avatar_master = project.active_dir / ".." / "outside" / "avatar-master.mp4"
    avatar_master.parent.mkdir()
    avatar_master.write_bytes(b"private-avatar-master")

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256="a" * 64,
        avatar_master=avatar_master,
        finalize_fn=lambda **_: (_ for _ in ()).throw(AssertionError("must not render")),
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=content-production-contract",)


def _write_real_content_fixture(project, monkeypatch) -> str:
    _write_canonical_synthetic_inputs(project, monkeypatch)
    segments = ("甲。", "乙。", "丙。", "丁。")
    _write_synthetic_wav(project.narration_path, seconds=5.2)
    narration_hash = hashlib.sha256(project.narration_path.read_bytes()).hexdigest()
    batch = project.media_dir / "segments.jsonl"
    batch_bytes = b"".join(
        (json.dumps({"text": text}, ensure_ascii=False) + "\n").encode("utf-8")
        for text in segments
    )
    batch.write_bytes(batch_bytes)
    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "output_sha256": narration_hash,
            "segment_contract_path": str(batch.absolute()),
            "segment_contract_sha256": hashlib.sha256(batch_bytes).hexdigest(),
            "segment_count": 4,
        }
    )
    project.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    ledger = Path(manifest["reference_audio_path"]).parent / "provenance-ledger.json"
    ledger_value = json.loads(ledger.read_text(encoding="utf-8"))
    ledger_value["issued_output_sha256"] = [narration_hash]
    ledger_value["issued_manifest_sha256_by_output_sha256"] = {
        narration_hash: hashlib.sha256(project.manifest_path.read_bytes()).hexdigest()
    }
    ledger.write_text(json.dumps(ledger_value), encoding="utf-8")
    headings = "\n\n".join(
        f"### segment-{index:03d}\n\n{text}"
        for index, text in enumerate(segments, 1)
    )
    project.handoff_path.write_text(
        "---\n"
        "status: 制作中\nplatform: local-v1\nratio: '16:9'\n"
        "duration_target_s: 5.200\nword_count: 4\n"
        f"voice: {CURRENT_VOICE_ID}\nvoice_provider: indextts2-local\n"
        "captions: asr-word-timestamps\ncaption_style: anchor-dark\n"
        "visual: xiaohei-white-first-v1\n"
        "illustration_skill: ian-xiaohei-illustrations\n"
        f"archive_slug: {project.archive_slug}\n---\n\n"
        f"## 新稿分段\n\n{headings}\n\n## 分段视觉意图\n\n- 四场景离线验收。\n",
        encoding="utf-8",
    )
    assets = project.active_dir / "工程" / "assets" / "xiaohei-illustrations"
    assets.mkdir(parents=True)
    for index in range(1, 5):
        (assets / f"scene-{index:02d}.png").write_bytes(PNG + bytes([index]))
    candidate = _candidate(project.active_dir)
    candidate_path = project.active_dir / "工程" / "content-plan.candidate.json"
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    compile_content_plan(project_root=project.active_dir, candidate_path=candidate_path)
    word_times = ((0.2, 1.0), (1.4, 2.2), (2.6, 3.4), (3.8, 4.6))
    words = [
        {"text": text[0], "start": start, "end": end, "isGap": False}
        for text, (start, end) in zip(segments, word_times)
    ]
    captions = [
        {
            "text": text[0],
            "start": start,
            "end": end,
            "source": "volcengine-word-timestamps",
        }
        for text, (start, end) in zip(segments, word_times)
    ]
    project.captions_dir.joinpath("captions_words.json").write_text(
        json.dumps(words, ensure_ascii=False), encoding="utf-8"
    )
    project.captions_dir.joinpath("captions.json").write_text(
        json.dumps(captions, ensure_ascii=False), encoding="utf-8"
    )
    project.captions_dir.joinpath("asr-result.json").write_text(
        json.dumps({"result": {"text": "甲乙丙丁"}}, ensure_ascii=False), encoding="utf-8"
    )
    for extension, vtt in (("srt", False), ("vtt", True)):
        blocks = []
        for index, caption in enumerate(captions, 1):
            prefix = "" if vtt else f"{index}\n"
            blocks.append(
                f"{prefix}{_format_cue(caption['start'], vtt=vtt)} --> "
                f"{_format_cue(caption['end'], vtt=vtt)}\n{caption['text']}"
            )
        header = "WEBVTT\n\n" if vtt else ""
        project.captions_dir.joinpath(f"captions.{extension}").write_text(
            header + "\n\n".join(blocks) + "\n", encoding="utf-8"
        )
    project.captions_dir.joinpath("caption-qc.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "timing_source": "volcengine-word-timestamps",
                "alignment_coverage": 1.0,
                "narration_sha256": narration_hash,
                "source_media": "narration.wav",
                "asr_resource_id": "generated-offline-fixture",
            }
        ),
        encoding="utf-8",
    )
    build_scene_timeline(project_root=project.active_dir)
    return narration_hash


def test_real_offline_content_pipeline_renders_qcs_and_archives(
    tmp_path: Path, monkeypatch
) -> None:
    """Would fail if the content CLI lane could not produce a real local archived MP4."""

    requirements = {
        "node": shutil.which("node.exe") or shutil.which("node"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "hyperframes": REPO_ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs",
    }
    absent = [
        name
        for name, value in requirements.items()
        if not value or (isinstance(value, Path) and not value.is_file())
    ]
    if absent:
        import pytest

        pytest.skip("required local offline tool is absent: " + ", ".join(absent))
    orchestrator = _load_orchestrator()
    content_runner = _load_content_runner()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="real-content-offline"
    )
    narration_hash = _write_real_content_fixture(project, monkeypatch)

    result = content_runner.finalize_content_production(
        project=project,
        audio_approved=True,
        approved_narration_sha256=narration_hash,
        repo_root=REPO_ROOT,
    )

    assert result.exit_code == 0, result.diagnostics
    archive = Path(result.archive_path or "")
    assert archive.is_dir()
    assert len(list((archive / "质检" / "scene-previews").glob("*.png"))) == 12
    assert (archive / "质检" / "scene-preview-qc.json").is_file()
    assert (archive / "工程" / "content-plan.json").is_file()
    assert (archive / "工程" / "scene-timeline.json").is_file()
    assert "### segment-004" in (archive / "交接稿.md").read_text(encoding="utf-8")
    probe = subprocess.run(
        [
            str(requirements["ffprobe"]),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(archive / "成片" / "boomearth-v2-production.mp4"),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert probe.returncode == 0
    payload = json.loads(probe.stdout)
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in payload["streams"] if stream["codec_type"] == "audio")
    assert (video["codec_name"], audio["codec_name"], int(video["width"]), int(video["height"])) == (
        "h264",
        "aac",
        1920,
        1080,
    )

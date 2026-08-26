"""Operator-facing contract for the complete offline content workbench."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_content_production_orchestration import (
    REPO_ROOT,
    _load_orchestrator,
    _write_real_content_fixture,
)


README = REPO_ROOT / "README.md"


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "automation" / "scripts" / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_readme_documents_exact_offline_content_workflow() -> None:
    text = README.read_text(encoding="utf-8")
    commands = (
        "uv run python automation/scripts/compile_content_plan.py --workspace-root "
        r"C:\BoomEarth --active-project <dated-project> --candidate "
        "工程/content-plan.candidate.json",
        "uv run python automation/scripts/build_scene_timeline.py --workspace-root "
        r"C:\BoomEarth --active-project <dated-project>",
        "uv run python automation/scripts/run_content_production.py finalize --workspace-root "
        r"C:\BoomEarth --active-project <dated-project> --audio-approved "
        "--approved-narration-sha256 <approved-hash>",
    )
    for command in commands:
        assert command in text
    assert text.count("纯本地/不调用 provider") >= 3
    assert "content-plan.candidate.json" in text and "人工审核" in text
    assert "--audio-approved" in text and "最终旁白" in text
    assert "不覆盖" in text and "已存在" in text
    assert "渲染器不会生成插图" in text
    assert "TTS、ASR 和插图生成" in text


def test_exact_clis_render_archive_and_historically_revalidate_offline(
    tmp_path: Path, monkeypatch
) -> None:
    requirements = {
        "node": shutil.which("node.exe") or shutil.which("node"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "hyperframes": REPO_ROOT
        / "node_modules"
        / "hyperframes"
        / "bin"
        / "hyperframes.mjs",
    }
    absent = [
        name
        for name, value in requirements.items()
        if not value or (isinstance(value, Path) and not value.is_file())
    ]
    if absent:
        pytest.skip("required local offline tool is absent: " + ", ".join(absent))

    compile_cli = _load_script("content_compile_cli_contract", "compile_content_plan.py")
    timeline_cli = _load_script("scene_timeline_cli_contract", "build_scene_timeline.py")
    production_cli = _load_script("content_production_cli_contract", "run_content_production.py")
    delivery = _load_script("content_delivery_contract", "check_delivery.py")
    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    project = orchestrator.initialize_production_project(
        workspace_root=workspace, archive_slug="whole-lane-offline"
    )
    narration_sha256 = _write_real_content_fixture(project, monkeypatch)
    (project.active_dir / "工程" / "content-plan.json").unlink()
    (project.active_dir / "工程" / "scene-timeline.json").unlink()

    assert compile_cli.main(
        [
            "--workspace-root",
            str(workspace),
            "--active-project",
            project.active_dir.name,
            "--candidate",
            "工程/content-plan.candidate.json",
        ]
    ) == 0
    assert timeline_cli.main(
        [
            "--workspace-root",
            str(workspace),
            "--active-project",
            project.active_dir.name,
        ]
    ) == 0
    assert production_cli.main(
        [
            "finalize",
            "--workspace-root",
            str(workspace),
            "--active-project",
            project.active_dir.name,
            "--audio-approved",
            "--approved-narration-sha256",
            narration_sha256,
        ]
    ) == 0

    archive = project.archive_dir
    final = archive / "成片" / "boomearth-v2-production.mp4"
    assert not project.active_dir.exists()
    assert archive.is_dir() and final.is_file()
    probe = subprocess.run(
        [
            str(requirements["ffprobe"]),
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
    payload = json.loads(probe.stdout)
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in payload["streams"] if stream["codec_type"] == "audio")
    assert (video["codec_name"], audio["codec_name"]) == ("h264", "aac")
    assert (int(video["width"]), int(video["height"])) == (1920, 1080)
    assert video["avg_frame_rate"] == "30/1"

    receipt = json.loads(
        (archive / "工程" / "delivery-report.json").read_text(encoding="utf-8")
    )
    published = set(receipt["artifact_sha256"])
    required = {
        "工程/content-plan.json",
        "工程/scene-timeline.json",
        "质检/scene-preview-qc.json",
        "质检/contact-sheet.jpg",
        "质检/contact-sheet-qc.json",
        "质检/caption-render-qc.json",
        "成片/boomearth-v2-production.mp4",
        "交接稿.md",
    }
    required.update(
        f"工程/assets/xiaohei-illustrations/scene-{index:02d}.png"
        for index in range(1, 5)
    )
    required.update(
        f"质检/scene-previews/scene-{scene:02d}-{kind}.png"
        for scene in range(1, 5)
        for kind in ("settle", "midpoint", "late")
    )
    assert required <= published

    report = archive / "工程" / "delivery-report.json"
    manifest = archive / "工程" / "publication-manifest.json"
    before = (_sha256(report), _sha256(manifest))
    result = delivery.check_delivery(
        archive / "交接稿.md",
        archive,
        final,
        sample_mode=False,
    )
    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=historical",)
    assert (_sha256(report), _sha256(manifest)) == before

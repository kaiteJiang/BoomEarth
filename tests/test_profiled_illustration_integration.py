"""Fully offline integration proof for all profiled V4 illustration themes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from boomearth.video.content_plan import compile_content_plan
from boomearth.video.illustration_themes import THEMES
from boomearth.video.motion_plan import compile_motion_plan
from boomearth.video.profiled_generation import run_profiled_generation_call
from boomearth.video.render_project import (
    ContentRenderProjectError,
    prepare_content_render_project,
)
from boomearth.video.scene_timeline import build_scene_timeline
from test_content_plan import schema4_candidate
from test_content_render_project import _set_handoff_visual, v3_project  # noqa: F401
from test_semantic_qc import profiled_project_factory


ROOT = Path(__file__).resolve().parents[1]
CONTENT_RUNNER = ROOT / "automation" / "scripts" / "run_content_production.py"
SHARED_CHECKS = {
    "subject_match",
    "action_match",
    "evidence_complete",
    "claim_readable",
    "non_generic",
    "forbidden_absent",
    "mobile_readable",
    "caption_safe",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _profiled_render_project(root: Path, theme_id: str) -> Path:
    engineering = root / "工程"
    for stale in (
        engineering / "content-plan.json",
        engineering / "content-plan.candidate.json",
        engineering / "scene-timeline.json",
        engineering / "motion-plan.json",
    ):
        stale.unlink(missing_ok=True)

    candidate_value = schema4_candidate(root, theme_id=theme_id)
    theme_root = (
        engineering / "assets" / "profiled-illustrations" / theme_id
    )
    for index in range(1, 5):
        Image.new("RGB", (3840, 2160), (240 - index, 234, 220)).save(
            theme_root / f"scene-{index:02d}.png", format="PNG"
        )
    candidate_path = engineering / "content-plan.candidate.json"
    _set_handoff_visual(root, theme_id)
    candidate_path.write_text(
        json.dumps(candidate_value, ensure_ascii=False), encoding="utf-8"
    )
    plan = compile_content_plan(
        project_root=root, candidate_path=candidate_path
    ).plan

    prompts = theme_root / "prompts"
    candidates = theme_root / "candidates"
    prompts.mkdir()
    candidates.mkdir()
    qc_scenes: list[dict[str, object]] = []
    manifest_assets: list[dict[str, object]] = []
    checks = {
        name: True
        for name in SHARED_CHECKS | set(THEMES[theme_id].required_qc)
    }
    for scene in plan.scenes:
        prompt = prompts / f"{scene.id}.md"
        prompt_payload = (
            "---\n"
            f"scene_id: {scene.id}\n"
            f"visual_type: {scene.visual_type}\n"
            f"visual_style: {theme_id}\n"
            f"visual_mode: {scene.visual_mode}\n"
            "visual_system: profiled-illustration-v4\n"
            f"visual_theme: {theme_id}\n"
            "ratio: 16:9\n"
            "target_size: 3840x2160\n"
            "text_policy: none\n"
            "caption_safe_zone: bottom-150px\n"
            "---\n\n"
            + "\n".join(
                (
                    "1. 当前场景唯一判断：任务关系发生变化",
                    "2. 语义主体：普通用户和事件轨迹",
                    "3. 核心动作或关系：人物沿路径检查任务",
                    "4. 必须可见的证据：连续路径和正在检查的人",
                    "5. 构图、左侧程序文字区和底部字幕安全区：全部保留",
                    "6. 当前主题的线条、材质和色板：按主题固定合同",
                    "7. 当前主题专项结构：按 content plan 固定",
                    "8. 为什么画面能解释判断：动作直接呈现变化",
                    "9. overlay labels，仅供 renderer：使用计划短标签",
                    "10. 禁止意象、文字、Logo、水印、UI、PPT 页面和无关装饰：全部禁止",
                )
            )
            + "\n"
        ).encode("utf-8")
        selected = theme_root / f"{scene.id}.png"
        generated = candidates / f"{scene.id}-candidate-01.png"
        generated_artifact = run_profiled_generation_call(
            project_root=root,
            theme_id=theme_id,
            scene_id=scene.id,
            prompt_payload=prompt_payload,
            candidate_index=1,
            invoke=lambda _prompt, _call_id, payload=selected.read_bytes(): payload,
            call_id=(
                "00000000-0000-4000-8000-"
                f"{int(scene.id[-2:]):012d}"
            ),
        )
        contract_relative = (
            f"工程/assets/profiled-illustrations/{theme_id}/prompts/{scene.id}.md"
        )
        selected_relative = (
            f"工程/assets/profiled-illustrations/{theme_id}/{scene.id}.png"
        )
        candidate_relative = (
            "工程/assets/profiled-illustrations/"
            f"{theme_id}/candidates/{scene.id}-candidate-01.png"
        )
        qc_scenes.append(
            {
                "scene_id": scene.id,
                "contract_path": contract_relative,
                "contract_sha256": _sha256(prompt),
                "selected_asset_path": selected_relative,
                "selected_asset_sha256": _sha256(selected),
                "relevance_rationale": "人物动作和路径共同解释当前任务变化",
                "checks": checks,
                "status": "pass",
            }
        )
        manifest_assets.append(
            {
                "scene_id": scene.id,
                "visual_type": scene.visual_type,
                "visual_style": theme_id,
                "visual_mode": scene.visual_mode,
                "contract_path": contract_relative,
                "contract_sha256": _sha256(prompt),
                "candidate_artifacts": [generated_artifact],
                "selected_asset_path": selected_relative,
                "selected_asset_sha256": _sha256(selected),
                "width": 3840,
                "height": 2160,
                "format": "png",
                "ratio": "16:9",
                "qc_status": "pass",
            }
        )

    plan_path = engineering / "content-plan.json"
    qc_path = theme_root / "semantic-qc.json"
    qc_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "visual_system": "profiled-illustration-v4",
                "visual_theme": theme_id,
                "project_id": root.name,
                "content_plan_sha256": _sha256(plan_path),
                "reviewer_type": "multimodal-review",
                "scenes": qc_scenes,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (theme_root / "illustration-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "illustration_skill": "ra-video-illustrations",
                "visual_system": "profiled-illustration-v4",
                "visual_theme": theme_id,
                "content_plan_sha256": _sha256(plan_path),
                "semantic_qc_path": (
                    f"工程/assets/profiled-illustrations/{theme_id}/semantic-qc.json"
                ),
                "semantic_qc_sha256": _sha256(qc_path),
                "assets": manifest_assets,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    build_scene_timeline(project_root=root)
    compile_motion_plan(project_root=root)
    return root


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_renderer_loads_only_the_selected_profiled_theme(
    v3_project: Path, theme_id: str
) -> None:
    root = _profiled_render_project(v3_project, theme_id)
    prepared = prepare_content_render_project(
        project_root=root,
        output_dir=root / "工程" / "render-project",
        repo_root=ROOT,
    )

    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")
    assert 'data-visual-system="profiled-illustration-v4"' in html
    assert f'data-visual-theme="{theme_id}"' in html
    assert (
        prepared.output_dir
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "scene-01.png"
    ).is_file()
    copied_themes = {
        path.name
        for path in (
            prepared.output_dir / "assets" / "profiled-illustrations"
        ).iterdir()
    }
    assert copied_themes == {theme_id}
    assert (prepared.output_dir / "motion-plan.json").is_file()
    assert (prepared.output_dir / "illustration-manifest.json").is_file()


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_profiled_factory_has_exact_theme_qc_contract(
    tmp_path: Path, theme_id: str
) -> None:
    _root, plan, candidate = profiled_project_factory(tmp_path, theme_id)

    assert plan.visual_theme == theme_id
    assert candidate["visual_theme"] == theme_id
    assert set(candidate["scenes"][0]["checks"]) == (  # type: ignore[index]
        SHARED_CHECKS | set(THEMES[theme_id].required_qc)
    )


def test_renderer_rejects_a_second_profiled_theme_directory(
    v3_project: Path,
) -> None:
    root = _profiled_render_project(v3_project, "vivid-comic-explainer")
    extra = (
        root
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / "engineering-sketch-explainer"
    )
    extra.mkdir()

    with pytest.raises(ContentRenderProjectError, match="render inputs are invalid"):
        prepare_content_render_project(
            project_root=root,
            output_dir=root / "工程" / "render-project",
            repo_root=ROOT,
        )


def test_profiled_production_contract_protects_theme_provenance(
    v3_project: Path,
) -> None:
    theme_id = "blue-black-whiteboard-explainer"
    root = _profiled_render_project(v3_project, theme_id)
    runner = _module(CONTENT_RUNNER, "profiled_v4_content_runner")

    _timeline, protected, _handoff, motion, _cover_sources = runner._content_contract(
        SimpleNamespace(active_dir=root)
    )

    relative = {path.relative_to(root).as_posix() for path in protected}
    theme_root = f"工程/assets/profiled-illustrations/{theme_id}"
    assert motion.plan.motion_profile == "profiled-illustration-v4"
    assert {
        "工程/content-plan.json",
        "工程/scene-timeline.json",
        "工程/motion-plan.json",
        f"{theme_root}/illustration-manifest.json",
        f"{theme_root}/semantic-qc.json",
        f"{theme_root}/prompts/scene-01.md",
        f"{theme_root}/candidates/scene-01-candidate-01.png",
        f"{theme_root}/scene-01.png",
    } <= relative

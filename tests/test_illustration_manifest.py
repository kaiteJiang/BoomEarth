from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from boomearth.video.content_plan import ContentPlan, ContentScene
from boomearth.video.illustration_manifest import (
    IllustrationManifestError,
    load_illustration_manifest_snapshot,
    validate_illustration_manifest,
)
from boomearth.video.artifacts import capture_regular_file
from boomearth.video.illustration_themes import THEMES
from boomearth.video.profiled_generation import run_profiled_generation_call
import boomearth.video.illustration_manifest as illustration_module


V3_CHECKS = {
    "subject_match": True,
    "action_match": True,
    "evidence_complete": True,
    "claim_readable": True,
    "non_generic": True,
    "forbidden_absent": True,
    "mobile_readable": True,
    "caption_safe": True,
}


def _load_validation_cli():
    script = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "scripts"
        / "validate_illustrations.py"
    )
    spec = importlib.util.spec_from_file_location(
        "validate_illustrations_under_test", script
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(root: Path) -> tuple[ContentPlan, dict[str, object]]:
    assets = root / "工程" / "assets" / "editorial-illustrations"
    prompts = assets / "prompts"
    prompts.mkdir(parents=True)
    prompt = prompts / "scene-01.md"
    prompt.write_text(
        "---\nscene_id: scene-01\nvisual_type: concept-scene\nvisual_style: editorial-scene\nratio: 16:9\ntarget_size: 3840x2160\ntext_policy: none\ncaption_safe_zone: bottom-150px\n---\n\n"
        "1. 唯一判断\n2. 主体动作\n3. 构图留白\n4. 风格色板\n5. 无短标签\n6. 禁止标题字幕水印\n",
        encoding="utf-8",
    )
    image = assets / "scene-01.png"
    Image.new("RGB", (1920, 1080), (240, 230, 220)).save(image, format="PNG")
    scene = ContentScene(
        id="scene-01", narration_segment_ids=("segment-001",), chapter="A", label="B",
        progress="01", title_lines=("T",), subtitle_lines=(), kicker="K", notes=(),
        visual_intent="single idea", visual_asset="工程/assets/editorial-illustrations/scene-01.png",
        layout_variant="standard", visual_type="concept-scene", visual_style="editorial-scene",
    )
    plan = ContentPlan(2, root.name, "16:9", "editorial-motion-v2", "mobile-readable", "anchor-dark", (scene,), "ra-video-illustrations")
    value = {
        "schema_version": 1,
        "illustration_skill": "ra-video-illustrations",
        "visual_system": "editorial-motion-v2",
        "assets": [{
            "scene_id": "scene-01", "visual_type": "concept-scene", "visual_style": "editorial-scene",
            "prompt_path": "工程/assets/editorial-illustrations/prompts/scene-01.md",
            "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "asset_path": "工程/assets/editorial-illustrations/scene-01.png",
            "asset_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "width": 1920, "height": 1080, "qc_status": "pass",
        }],
    }
    return plan, value


def _v3_scene(
    scene_id: str,
    *,
    visual_mode: str,
    visual_style: str,
    visual_asset: str | None,
) -> ContentScene:
    return ContentScene(
        id=scene_id,
        narration_segment_ids=(f"segment-{scene_id[-2:]}",),
        chapter="结构",
        label="解释",
        progress=scene_id[-2:],
        title_lines=("核心关系",),
        subtitle_lines=(),
        kicker="证据",
        notes=(),
        visual_intent="人物沿路径理解任务关系",
        visual_asset=visual_asset,
        layout_variant="standard",
        visual_type="comparison",
        visual_style=visual_style,
        visual_mode=visual_mode,
        semantic_subjects=("普通用户", "事件轨迹"),
        semantic_action="普通用户沿事件轨迹理解任务关系",
        required_visual_evidence=("连续路径", "正在查看的人"),
        forbidden_metaphors=(
            "机器人",
            "齿轮",
            "工厂",
            "机械臂",
            "金属卡匣",
            "电路板",
            "工业流水线",
            "发动机",
            "机械底座",
        ),
        overlay_labels=("可回查", "太复杂"),
    )


def _profiled_png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3840, 2160), (242, 235, 220)).save(output, format="PNG")
    return output.getvalue()


def _v3_project(root: Path) -> tuple[ContentPlan, dict[str, object]]:
    assets = root / "工程" / "assets" / "semantic-handdrawn"
    prompts = assets / "prompts"
    type_led = assets / "type-led"
    candidates = assets / "candidates"
    prompts.mkdir(parents=True)
    type_led.mkdir()
    candidates.mkdir()
    plan_path = root / "工程" / "content-plan.json"
    plan_path.write_text('{"schema_version":3}\n', encoding="utf-8")
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()

    prompt = prompts / "scene-01.md"
    prompt.write_text(
        "---\n"
        "scene_id: scene-01\n"
        "visual_type: comparison\n"
        "visual_style: semantic-handdrawn\n"
        "visual_mode: human-action\n"
        "ratio: 16:9\n"
        "target_size: 3840x2160\n"
        "text_policy: none\n"
        "caption_safe_zone: bottom-150px\n"
        "---\n\n"
        "1. 当前场景唯一判断\n"
        "2. 语义主体\n"
        "3. 核心动作关系\n"
        "4. 必须看到的证据\n"
        "5. 构图与安全区\n"
        "6. 手绘线与淡彩\n"
        "7. 解释判断的理由\n"
        "8. 禁止意象文字水印\n",
        encoding="utf-8",
    )
    type_contract = type_led / "scene-02.json"
    type_contract.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scene_id": "scene-02",
                "visual_mode": "type-led",
                "ratio": "16:9",
                "target_size": "3840x2160",
                "caption_safe_zone": "bottom-150px",
                "labels": ["可回查", "太复杂"],
                "layout": "comparison",
                "connectors": ["path"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    candidate = candidates / "scene-01-candidate-01.png"
    selected = assets / "scene-01.png"
    Image.new("RGB", (3840, 2160), (245, 238, 225)).save(candidate, format="PNG")
    Image.new("RGB", (3840, 2160), (242, 235, 220)).save(selected, format="PNG")

    plan = ContentPlan(
        3,
        root.name,
        "16:9",
        "semantic-handdrawn-v3",
        "mobile-readable",
        "anchor-dark",
        (
            _v3_scene(
                "scene-01",
                visual_mode="human-action",
                visual_style="semantic-handdrawn",
                visual_asset="工程/assets/semantic-handdrawn/scene-01.png",
            ),
            _v3_scene(
                "scene-02",
                visual_mode="type-led",
                visual_style="semantic-type",
                visual_asset=None,
            ),
        ),
        "ra-video-illustrations",
    )
    qc = {
        "schema_version": 1,
        "visual_system": "semantic-handdrawn-v3",
        "project_id": root.name,
        "content_plan_sha256": plan_hash,
        "reviewer_type": "multimodal-review",
        "scenes": [
            {
                "scene_id": "scene-01",
                "contract_path": "工程/assets/semantic-handdrawn/prompts/scene-01.md",
                "contract_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                "selected_asset_path": "工程/assets/semantic-handdrawn/scene-01.png",
                "selected_asset_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
                "relevance_rationale": "人物沿路径理解任务关系",
                "checks": dict(V3_CHECKS),
                "status": "pass",
            },
            {
                "scene_id": "scene-02",
                "contract_path": "工程/assets/semantic-handdrawn/type-led/scene-02.json",
                "contract_sha256": hashlib.sha256(type_contract.read_bytes()).hexdigest(),
                "selected_asset_path": None,
                "selected_asset_sha256": None,
                "relevance_rationale": "短标签直接呈现两种判断",
                "checks": dict(V3_CHECKS),
                "status": "pass",
            },
        ],
    }
    qc_path = assets / "semantic-qc.json"
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    value: dict[str, object] = {
        "schema_version": 2,
        "illustration_skill": "ra-video-illustrations",
        "visual_system": "semantic-handdrawn-v3",
        "content_plan_sha256": plan_hash,
        "semantic_qc_path": "工程/assets/semantic-handdrawn/semantic-qc.json",
        "semantic_qc_sha256": hashlib.sha256(qc_path.read_bytes()).hexdigest(),
        "assets": [
            {
                "scene_id": "scene-01",
                "visual_type": "comparison",
                "visual_style": "semantic-handdrawn",
                "visual_mode": "human-action",
                "contract_path": "工程/assets/semantic-handdrawn/prompts/scene-01.md",
                "contract_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                "candidate_artifacts": [
                    {
                        "path": "工程/assets/semantic-handdrawn/candidates/scene-01-candidate-01.png",
                        "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    }
                ],
                "selected_asset_path": "工程/assets/semantic-handdrawn/scene-01.png",
                "selected_asset_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
                "width": 3840,
                "height": 2160,
                "format": "png",
                "ratio": "16:9",
                "qc_status": "pass",
            },
            {
                "scene_id": "scene-02",
                "visual_type": "comparison",
                "visual_style": "semantic-type",
                "visual_mode": "type-led",
                "contract_path": "工程/assets/semantic-handdrawn/type-led/scene-02.json",
                "contract_sha256": hashlib.sha256(type_contract.read_bytes()).hexdigest(),
                "candidate_artifacts": [],
                "selected_asset_path": None,
                "selected_asset_sha256": None,
                "width": None,
                "height": None,
                "format": None,
                "ratio": "16:9",
                "qc_status": "pass",
            },
        ],
    }
    return plan, value


def _v4_project(
    root: Path, *, theme_id: str = "vivid-comic-explainer"
) -> tuple[ContentPlan, dict[str, object]]:
    assets = root / "工程" / "assets" / "profiled-illustrations" / theme_id
    prompts = assets / "prompts"
    candidates = assets / "candidates"
    prompts.mkdir(parents=True)
    candidates.mkdir()
    plan_path = root / "工程" / "content-plan.json"
    plan_path.write_text('{"schema_version":4}\n', encoding="utf-8")
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()

    prompt_payload = (
        "---\n"
        "scene_id: scene-01\n"
        "visual_type: comparison\n"
        f"visual_style: {theme_id}\n"
        "visual_mode: human-action\n"
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
                "7. 当前主题专项结构：中心主体和清晰关系",
                "8. 为什么画面能解释判断：动作直接呈现变化",
                "9. overlay labels，仅供 renderer：可回查",
                "10. 禁止意象、文字、Logo、水印、UI、PPT 页面和无关装饰：全部禁止",
            )
        )
        + "\n"
    ).encode("utf-8")
    generated_artifact = run_profiled_generation_call(
        project_root=root,
        theme_id=theme_id,
        scene_id="scene-01",
        prompt_payload=prompt_payload,
        candidate_index=1,
        invoke=lambda _prompt, _call_id: _profiled_png_bytes(),
        call_id="11111111-1111-4111-8111-111111111111",
    )
    prompt = prompts / "scene-01.md"
    candidate = candidates / "scene-01-candidate-01.png"
    selected = assets / "scene-01.png"
    selected.write_bytes(candidate.read_bytes())
    scene = replace(
        _v3_scene(
            "scene-01",
            visual_mode="human-action",
            visual_style=theme_id,
            visual_asset=(
                f"工程/assets/profiled-illustrations/{theme_id}/scene-01.png"
            ),
        ),
        theme_structure=("人物动作推动因果关系",),
    )
    plan = ContentPlan(
        4,
        root.name,
        "16:9",
        "profiled-illustration-v4",
        "mobile-readable",
        "anchor-dark",
        (scene,),
        "ra-video-illustrations",
        theme_id,
    )
    checks = dict(V3_CHECKS)
    checks.update({name: True for name in THEMES[theme_id].required_qc})
    qc = {
        "schema_version": 2,
        "visual_system": "profiled-illustration-v4",
        "visual_theme": theme_id,
        "project_id": root.name,
        "content_plan_sha256": plan_hash,
        "reviewer_type": "multimodal-review",
        "scenes": [
            {
                "scene_id": "scene-01",
                "contract_path": (
                    f"工程/assets/profiled-illustrations/{theme_id}/prompts/scene-01.md"
                ),
                "contract_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                "selected_asset_path": (
                    f"工程/assets/profiled-illustrations/{theme_id}/scene-01.png"
                ),
                "selected_asset_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
                "relevance_rationale": "人物动作和路径共同解释任务变化",
                "checks": checks,
                "status": "pass",
            }
        ],
    }
    qc_path = assets / "semantic-qc.json"
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    value: dict[str, object] = {
        "schema_version": 3,
        "illustration_skill": "ra-video-illustrations",
        "visual_system": "profiled-illustration-v4",
        "visual_theme": theme_id,
        "content_plan_sha256": plan_hash,
        "semantic_qc_path": (
            f"工程/assets/profiled-illustrations/{theme_id}/semantic-qc.json"
        ),
        "semantic_qc_sha256": hashlib.sha256(qc_path.read_bytes()).hexdigest(),
        "assets": [
            {
                "scene_id": "scene-01",
                "visual_type": "comparison",
                "visual_style": theme_id,
                "visual_mode": "human-action",
                "contract_path": (
                    f"工程/assets/profiled-illustrations/{theme_id}/prompts/scene-01.md"
                ),
                "contract_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                "candidate_artifacts": [generated_artifact],
                "selected_asset_path": (
                    f"工程/assets/profiled-illustrations/{theme_id}/scene-01.png"
                ),
                "selected_asset_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
                "width": 3840,
                "height": 2160,
                "format": "png",
                "ratio": "16:9",
                "qc_status": "pass",
            }
        ],
    }
    return plan, value


def _rehash_v4_qc(root: Path, value: dict[str, object], theme_id: str) -> None:
    qc_path = (
        root
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "semantic-qc.json"
    )
    value["semantic_qc_sha256"] = hashlib.sha256(qc_path.read_bytes()).hexdigest()


def test_validate_illustration_manifest_binds_prompt_image_and_scene(tmp_path: Path) -> None:
    plan, value = _project(tmp_path)
    manifest = validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)
    assert manifest.assets[0].scene_id == "scene-01"
    assert manifest.assets[0].width == 1920


def test_validate_xiaohei_manifest_binds_prompt_candidate_asset_and_qc(
    tmp_path: Path,
) -> None:
    assets = tmp_path / "工程" / "assets" / "xiaohei-illustrations"
    prompts = assets / "prompts"
    candidates = assets / "candidates"
    prompts.mkdir(parents=True)
    candidates.mkdir()
    prompt = prompts / "scene-01.md"
    prompt.write_text("小黑把任务卡放进三岔选择器。\n", encoding="utf-8")
    candidate = candidates / "scene-01-candidate-01.png"
    selected = assets / "scene-01.png"
    Image.new("RGB", (1672, 941), "white").save(candidate, format="PNG")
    Image.new("RGB", (1920, 1080), "white").save(selected, format="PNG")
    plan_path = tmp_path / "工程" / "content-plan.json"
    plan_path.write_text('{"schema_version":1}\n', encoding="utf-8")
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    scene = ContentScene(
        id="scene-01",
        narration_segment_ids=("segment-001",),
        chapter="选择",
        label="判断",
        progress="01",
        title_lines=("先写任务",),
        subtitle_lines=(),
        kicker="三种路线",
        notes=(),
        visual_intent="小黑把任务卡放进三岔选择器",
        visual_asset="工程/assets/xiaohei-illustrations/scene-01.png",
        layout_variant="standard",
    )
    plan = ContentPlan(
        1,
        tmp_path.name,
        "16:9",
        "xiaohei-white-first-v1",
        "mobile-readable",
        "anchor-dark",
        (scene,),
        "ian-xiaohei-illustrations",
    )
    qc = {
        "schema_version": 1,
        "visual_system": "xiaohei-white-first-v1",
        "project_id": tmp_path.name,
        "content_plan_sha256": plan_hash,
        "reviewer_type": "multimodal-review",
        "status": "pass",
        "scenes": [{
            "scene_id": "scene-01",
            "contract_path": "工程/assets/xiaohei-illustrations/prompts/scene-01.md",
            "contract_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "selected_asset_path": "工程/assets/xiaohei-illustrations/scene-01.png",
            "selected_asset_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
            "relevance_rationale": "任务卡与三岔选择器直接解释选择方法",
            "checks": dict(V3_CHECKS),
            "status": "pass",
        }],
    }
    qc_path = assets / "semantic-qc.json"
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    value = {
        "schema_version": 1,
        "illustration_skill": "ian-xiaohei-illustrations",
        "visual_system": "xiaohei-white-first-v1",
        "content_plan_sha256": plan_hash,
        "semantic_qc_path": "工程/assets/xiaohei-illustrations/semantic-qc.json",
        "semantic_qc_sha256": hashlib.sha256(qc_path.read_bytes()).hexdigest(),
        "provider": "OpenAI runtime-native imagegen",
        "model": "runtime-native-current",
        "cloud_resolution_class": "1K",
        "local_output_contract": {
            "width": 1920,
            "height": 1080,
            "format": "png",
            "ratio": "16:9",
        },
        "assets": [{
            "scene_id": "scene-01",
            "prompt_path": "工程/assets/xiaohei-illustrations/prompts/scene-01.md",
            "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
            "candidate_artifacts": [{
                "path": "工程/assets/xiaohei-illustrations/candidates/scene-01-candidate-01.png",
                "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "status": "accepted",
            }],
            "selected_asset_path": "工程/assets/xiaohei-illustrations/scene-01.png",
            "selected_asset_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
            "qc_status": "pass",
        }],
    }

    manifest = validate_illustration_manifest(
        value, project_root=tmp_path, content_plan=plan
    )

    assert manifest.visual_system == "xiaohei-white-first-v1"
    assert manifest.assets[0].candidate_paths == (
        "工程/assets/xiaohei-illustrations/candidates/scene-01-candidate-01.png",
    )
    assert manifest.assets[0].selected_asset_sha256 == hashlib.sha256(
        selected.read_bytes()
    ).hexdigest()


def test_validate_v3_manifest_binds_contract_candidates_assets_and_semantic_qc(
    tmp_path: Path,
) -> None:
    plan, value = _v3_project(tmp_path)

    manifest = validate_illustration_manifest(
        value,
        project_root=tmp_path,
        content_plan=plan,
    )

    assert manifest.schema_version == 2
    assert manifest.visual_system == "semantic-handdrawn-v3"
    assert manifest.content_plan_sha256 == value["content_plan_sha256"]
    assert manifest.semantic_qc_sha256 == value["semantic_qc_sha256"]
    assert manifest.assets[0].candidate_paths == (
        "工程/assets/semantic-handdrawn/candidates/scene-01-candidate-01.png",
    )
    assert manifest.assets[1].selected_asset_path is None


def test_profiled_manifest_binds_theme_prompt_candidates_asset_and_qc(
    tmp_path: Path,
) -> None:
    plan, value = _v4_project(tmp_path)

    manifest = validate_illustration_manifest(
        value, project_root=tmp_path, content_plan=plan
    )

    assert manifest.schema_version == 3
    assert manifest.visual_system == "profiled-illustration-v4"
    assert manifest.visual_theme == "vivid-comic-explainer"
    assert manifest.assets[0].candidate_sha256 == (
        manifest.assets[0].selected_asset_sha256,
    )


def test_profiled_manifest_rejects_candidate_without_generation_receipt_chain(
    tmp_path: Path,
) -> None:
    plan, value = _v4_project(tmp_path)
    artifact = value["assets"][0]["candidate_artifacts"][0]  # type: ignore[index]
    for key in (
        "generation_intent_path",
        "generation_intent_sha256",
        "generation_receipt_path",
        "generation_receipt_sha256",
    ):
        artifact.pop(key, None)

    with pytest.raises(
        IllustrationManifestError,
        match="illustration-generation-evidence-invalid",
    ):
        validate_illustration_manifest(
            value,
            project_root=tmp_path,
            content_plan=plan,
        )


def test_profiled_manifest_rejects_rehashed_receipt_with_wrong_prompt_binding(
    tmp_path: Path,
) -> None:
    plan, value = _v4_project(tmp_path)
    artifact = value["assets"][0]["candidate_artifacts"][0]  # type: ignore[index]
    receipt = tmp_path / Path(artifact["generation_receipt_path"])
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    receipt_value["prompt_sha256"] = "0" * 64
    receipt.write_text(
        json.dumps(receipt_value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifact["generation_receipt_sha256"] = hashlib.sha256(
        receipt.read_bytes()
    ).hexdigest()

    with pytest.raises(
        IllustrationManifestError,
        match="illustration-generation-evidence-invalid",
    ):
        validate_illustration_manifest(
            value,
            project_root=tmp_path,
            content_plan=plan,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema_version=2),
        lambda value: value.pop("visual_theme"),
        lambda value: value.update(visual_theme="unknown-theme"),
        lambda value: value["assets"][0].update(width=1920),
        lambda value: value["assets"][0].update(ratio="4:3"),
        lambda value: value["assets"][0].update(qc_status="fail"),
        lambda value: value["assets"][0].update(selected_asset_sha256="0" * 64),
        lambda value: value["assets"][0]["candidate_artifacts"][0].update(
            path=(
                "工程/assets/profiled-illustrations/engineering-sketch-explainer/"
                "candidates/scene-01-candidate-01.png"
            )
        ),
    ],
)
def test_profiled_manifest_rejects_schema_theme_path_and_asset_drift(
    tmp_path: Path, mutation
) -> None:
    plan, value = _v4_project(tmp_path)
    mutation(value)

    with pytest.raises(IllustrationManifestError):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_profiled_manifest_requires_exactly_ten_prompt_sections(
    tmp_path: Path,
) -> None:
    theme_id = "vivid-comic-explainer"
    plan, value = _v4_project(tmp_path, theme_id=theme_id)
    prompt = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "prompts"
        / "scene-01.md"
    )
    prompt.write_text(
        prompt.read_text(encoding="utf-8").replace(
            "10. 禁止意象、文字、Logo、水印、UI、PPT 页面和无关装饰：全部禁止\n",
            "",
        ),
        encoding="utf-8",
    )
    prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
    value["assets"][0]["contract_sha256"] = prompt_hash  # type: ignore[index]
    qc_path = prompt.parent.parent / "semantic-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["scenes"][0]["contract_sha256"] = prompt_hash
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    _rehash_v4_qc(tmp_path, value, theme_id)

    with pytest.raises(IllustrationManifestError, match="illustration-prompt-invalid"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_profiled_manifest_rejects_renamed_prompt_section_with_ten_numbers(
    tmp_path: Path,
) -> None:
    theme_id = "vivid-comic-explainer"
    plan, value = _v4_project(tmp_path, theme_id=theme_id)
    prompt = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "prompts"
        / "scene-01.md"
    )
    prompt.write_text(
        prompt.read_text(encoding="utf-8").replace(
            "9. overlay labels，仅供 renderer：可回查",
            "9. 图片模型直接写中文：可回查",
        ),
        encoding="utf-8",
    )
    prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
    value["assets"][0]["contract_sha256"] = prompt_hash  # type: ignore[index]
    qc_path = prompt.parent.parent / "semantic-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["scenes"][0]["contract_sha256"] = prompt_hash
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    _rehash_v4_qc(tmp_path, value, theme_id)

    with pytest.raises(IllustrationManifestError, match="illustration-prompt-invalid"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_v4_prompt_contract_accepts_legacy_production_headings() -> None:
    payload = (
        "---\n"
        "scene_id: scene-01\n"
        "visual_type: comparison\n"
        "visual_style: vivid-comic-explainer\n"
        "visual_mode: human-action\n"
        "visual_system: profiled-illustration-v4\n"
        "visual_theme: vivid-comic-explainer\n"
        "ratio: 16:9\n"
        "target_size: 3840x2160\n"
        "text_policy: none\n"
        "caption_safe_zone: bottom-150px\n"
        "---\n\n"
        "1. 当前场景唯一判断：任务关系发生变化\n"
        "2. 语义主体：普通用户和事件轨迹\n"
        "3. 核心动作或关系：人物沿路径检查任务\n"
        "4. 必须可见的证据：连续路径和正在检查的人\n"
        "5. 构图、左侧程序文字区和底部字幕安全区：全部保留\n"
        "6. 当前主题的线条、材质和色板：按主题固定合同\n"
        "7. 当前主题专项结构：中心主体和清晰关系\n"
        "8. 为什么画面能解释判断：动作直接呈现变化\n"
        "9. overlay labels：提示词。仅供 renderer 后期绘制\n"
        "10. 禁止意象：禁止标题、字幕、Logo、水印、平台按钮、PPT 页面和无关装饰\n"
    ).encode("utf-8")

    values = illustration_module._v4_prompt_contract(payload)

    assert values["scene_id"] == "scene-01"


def test_profiled_manifest_rejects_content_plan_theme_mismatch(
    tmp_path: Path,
) -> None:
    plan, value = _v4_project(tmp_path)
    mismatched = replace(plan, visual_theme="engineering-sketch-explainer")

    with pytest.raises(IllustrationManifestError, match="illustration-manifest-invalid"):
        validate_illustration_manifest(
            value, project_root=tmp_path, content_plan=mismatched
        )


def test_profiled_manifest_rejects_prompt_theme_mismatch_even_when_rehashed(
    tmp_path: Path,
) -> None:
    theme_id = "vivid-comic-explainer"
    plan, value = _v4_project(tmp_path, theme_id=theme_id)
    prompt = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "prompts"
        / "scene-01.md"
    )
    prompt.write_text(
        prompt.read_text(encoding="utf-8").replace(
            f"visual_theme: {theme_id}",
            "visual_theme: engineering-sketch-explainer",
        ),
        encoding="utf-8",
    )
    prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
    value["assets"][0]["contract_sha256"] = prompt_hash  # type: ignore[index]
    qc_path = prompt.parent.parent / "semantic-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["scenes"][0]["contract_sha256"] = prompt_hash
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    _rehash_v4_qc(tmp_path, value, theme_id)

    with pytest.raises(IllustrationManifestError, match="illustration-prompt-invalid"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_profiled_manifest_rejects_more_than_two_candidates(
    tmp_path: Path,
) -> None:
    plan, value = _v4_project(tmp_path)
    artifact = value["assets"][0]["candidate_artifacts"][0]  # type: ignore[index]
    value["assets"][0]["candidate_artifacts"] = [  # type: ignore[index]
        artifact,
        dict(artifact),
        dict(artifact),
    ]

    with pytest.raises(IllustrationManifestError, match="illustration-scene-invalid"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_profiled_manifest_rejects_qc_theme_mismatch_even_when_rehashed(
    tmp_path: Path,
) -> None:
    theme_id = "vivid-comic-explainer"
    plan, value = _v4_project(tmp_path, theme_id=theme_id)
    qc_path = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "semantic-qc.json"
    )
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["visual_theme"] = "engineering-sketch-explainer"
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    _rehash_v4_qc(tmp_path, value, theme_id)

    with pytest.raises(IllustrationManifestError, match="illustration-qc-invalid"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_profiled_manifest_rejects_selected_asset_not_from_candidates(
    tmp_path: Path,
) -> None:
    theme_id = "vivid-comic-explainer"
    plan, value = _v4_project(tmp_path, theme_id=theme_id)
    selected = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "scene-01.png"
    )
    Image.new("RGB", (3840, 2160), (10, 20, 30)).save(selected, format="PNG")
    selected_hash = hashlib.sha256(selected.read_bytes()).hexdigest()
    value["assets"][0]["selected_asset_sha256"] = selected_hash  # type: ignore[index]
    qc_path = selected.parent / "semantic-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["scenes"][0]["selected_asset_sha256"] = selected_hash
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    _rehash_v4_qc(tmp_path, value, theme_id)

    with pytest.raises(IllustrationManifestError, match="illustration-scene-invalid"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_profiled_manifest_rejects_prompt_republished_after_candidate(
    tmp_path: Path,
) -> None:
    theme_id = "vivid-comic-explainer"
    plan, value = _v4_project(tmp_path, theme_id=theme_id)
    prompt = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "prompts"
        / "scene-01.md"
    )
    original = prompt.read_bytes()
    prompt.write_bytes(original)
    candidate = prompt.parent.parent / "candidates" / "scene-01-candidate-01.png"
    candidate_mtime = candidate.stat().st_mtime_ns
    os.utime(
        prompt,
        ns=(candidate_mtime + 2_000_000_000, candidate_mtime + 2_000_000_000),
    )

    with pytest.raises(
        IllustrationManifestError, match="illustration-prompt-order-invalid"
    ):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_load_profiled_manifest_uses_selected_theme_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    theme_id = "engineering-sketch-explainer"
    plan, value = _v4_project(tmp_path, theme_id=theme_id)
    manifest_path = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "illustration-manifest.json"
    )
    manifest_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    plan_path = tmp_path / "工程" / "content-plan.json"
    monkeypatch.setattr(
        illustration_module,
        "load_content_plan_snapshot",
        lambda **_kwargs: SimpleNamespace(
            plan=plan,
            snapshot=capture_regular_file(plan_path, within=tmp_path),
        ),
    )

    snapshot = load_illustration_manifest_snapshot(tmp_path)

    assert snapshot.path == manifest_path.absolute()
    assert snapshot.manifest.visual_theme == theme_id


def test_validate_illustrations_help_is_success(capsys) -> None:
    module = _load_validation_cli()

    assert module.main(["--help"]) == 0

    captured = capsys.readouterr()
    assert "usage: validate_illustrations.py" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda v: v.update(schema_version=1), "illustration-manifest-invalid"),
        (lambda v: v["assets"][0].update(visual_mode="type-led"), "illustration-scene-invalid"),
        (lambda v: v["assets"][0].update(candidate_artifacts=[]), "illustration-scene-invalid"),
        (lambda v: v["assets"][1].update(selected_asset_path="工程/assets/semantic-handdrawn/scene-01.png"), "illustration-scene-invalid"),
        (lambda v: v["assets"][0].update(contract_path="../prompt.md"), "illustration-path-invalid"),
        (lambda v: v["assets"][0]["candidate_artifacts"][0].update(sha256="0" * 64), "illustration-input-changed"),
        (lambda v: v.update(semantic_qc_sha256="0" * 64), "illustration-input-changed"),
    ],
)
def test_validate_v3_manifest_rejects_contract_drift(
    tmp_path: Path, mutation, error: str
) -> None:
    plan, value = _v3_project(tmp_path)
    mutation(value)

    with pytest.raises(IllustrationManifestError, match=f"^{error}$"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_validate_v3_manifest_requires_all_eight_prompt_sections(
    tmp_path: Path,
) -> None:
    plan, value = _v3_project(tmp_path)
    prompt = tmp_path / "工程/assets/semantic-handdrawn/prompts/scene-01.md"
    prompt.write_text(prompt.read_text(encoding="utf-8").replace("8. 禁止意象文字水印", ""), encoding="utf-8")
    prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
    value["assets"][0]["contract_sha256"] = prompt_hash  # type: ignore[index]
    qc_path = tmp_path / "工程/assets/semantic-handdrawn/semantic-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["scenes"][0]["contract_sha256"] = prompt_hash
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    value["semantic_qc_sha256"] = hashlib.sha256(qc_path.read_bytes()).hexdigest()

    with pytest.raises(IllustrationManifestError, match="^illustration-prompt-invalid$"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_validate_v3_manifest_rejects_failed_semantic_qc_even_when_rehashed(
    tmp_path: Path,
) -> None:
    plan, value = _v3_project(tmp_path)
    qc_path = tmp_path / "工程/assets/semantic-handdrawn/semantic-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["scenes"][0]["checks"]["subject_match"] = False
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")
    value["semantic_qc_sha256"] = hashlib.sha256(qc_path.read_bytes()).hexdigest()

    with pytest.raises(IllustrationManifestError, match="^illustration-qc-invalid$"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_load_v3_manifest_returns_contract_candidate_asset_and_qc_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, value = _v3_project(tmp_path)
    manifest_path = (
        tmp_path
        / "工程"
        / "assets"
        / "semantic-handdrawn"
        / "illustration-manifest.json"
    )
    manifest_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    plan_path = tmp_path / "工程" / "content-plan.json"
    monkeypatch.setattr(
        illustration_module,
        "load_content_plan_snapshot",
        lambda **_kwargs: SimpleNamespace(
            plan=plan,
            snapshot=capture_regular_file(plan_path, within=tmp_path),
        ),
    )

    loaded = load_illustration_manifest_snapshot(tmp_path)

    assert loaded.path == manifest_path.absolute()
    assert loaded.manifest.schema_version == 2
    assert len(loaded.prompt_snapshots) == 2
    assert len(loaded.candidate_snapshots) == 1
    assert len(loaded.asset_snapshots) == 1
    assert loaded.semantic_qc_snapshot is not None


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda v: v.update(extra=True), "illustration-manifest-invalid"),
        (lambda v: v["assets"][0].update(qc_status="fail"), "illustration-qc-invalid"),
        (lambda v: v["assets"][0].update(prompt_sha256="0" * 64), "illustration-input-changed"),
        (lambda v: v["assets"][0].update(asset_sha256="0" * 64), "illustration-input-changed"),
        (lambda v: v["assets"][0].update(width=1000), "illustration-image-invalid"),
        (lambda v: v["assets"][0].update(width=1920.0), "illustration-image-invalid"),
        (lambda v: v["assets"][0].update(asset_path="../escape.png"), "illustration-path-invalid"),
        (lambda v: v["assets"].append(dict(v["assets"][0])), "illustration-scene-invalid"),
        (lambda v: v.update(assets=[]), "illustration-scene-invalid"),
    ],
)
def test_validate_illustration_manifest_rejects_schema_drift(
    tmp_path: Path, mutation, error: str
) -> None:
    plan, value = _project(tmp_path)
    mutation(value)
    with pytest.raises(IllustrationManifestError, match=f"^{error}$"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_validate_illustration_manifest_rejects_prompt_or_asset_replacement(tmp_path: Path) -> None:
    plan, value = _project(tmp_path)
    prompt = tmp_path / "工程/assets/editorial-illustrations/prompts/scene-01.md"
    prompt.write_text("replaced", encoding="utf-8")
    with pytest.raises(IllustrationManifestError, match="^illustration-input-changed$"):
        validate_illustration_manifest(value, project_root=tmp_path, content_plan=plan)


def test_load_illustration_manifest_rejects_replacement_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, value = _project(tmp_path)
    manifest_path = (
        tmp_path
        / "工程"
        / "assets"
        / "editorial-illustrations"
        / "illustration-manifest.json"
    )
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    plan_path = tmp_path / "工程" / "content-plan.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        illustration_module,
        "load_content_plan_snapshot",
        lambda **_kwargs: SimpleNamespace(
            plan=plan,
            snapshot=capture_regular_file(plan_path, within=tmp_path),
        ),
    )
    original_validate = illustration_module.validate_illustration_manifest

    def replace_after_validation(*args, **kwargs):
        manifest = original_validate(*args, **kwargs)
        prompt = (
            tmp_path
            / "工程"
            / "assets"
            / "editorial-illustrations"
            / "prompts"
            / "scene-01.md"
        )
        prompt.write_bytes(prompt.read_bytes() + b"\nreplacement")
        return manifest

    monkeypatch.setattr(
        illustration_module,
        "validate_illustration_manifest",
        replace_after_validation,
    )

    with pytest.raises(IllustrationManifestError, match="^illustration-input-changed$"):
        load_illustration_manifest_snapshot(tmp_path)


def test_load_illustration_manifest_forwards_prearchive_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, value = _project(tmp_path)
    manifest_path = (
        tmp_path
        / "工程"
        / "assets"
        / "editorial-illustrations"
        / "illustration-manifest.json"
    )
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    plan_path = tmp_path / "工程" / "content-plan.json"
    plan_path.write_text("{}\n", encoding="utf-8")
    prearchive_root = tmp_path.parent / "制作中" / tmp_path.name
    observed: dict[str, Path] = {}

    def load_plan(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            plan=plan,
            snapshot=capture_regular_file(plan_path, within=tmp_path),
        )

    monkeypatch.setattr(
        illustration_module,
        "load_content_plan_snapshot",
        load_plan,
    )

    snapshot = load_illustration_manifest_snapshot(
        tmp_path,
        prearchive_project_root=prearchive_root,
    )

    assert snapshot.manifest.assets[0].scene_id == "scene-01"
    assert observed == {
        "project_root": tmp_path.absolute(),
        "prearchive_project_root": prearchive_root,
    }

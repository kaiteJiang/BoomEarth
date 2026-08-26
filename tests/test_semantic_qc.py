from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from boomearth.video.content_plan import ContentPlan, ContentScene
from boomearth.video.semantic_qc import (
    SemanticQCError,
    load_semantic_qc_snapshot,
    validate_semantic_qc,
)
from boomearth.video.illustration_themes import THEMES


CHECK_NAMES = (
    "subject_match",
    "action_match",
    "evidence_complete",
    "claim_readable",
    "non_generic",
    "forbidden_absent",
    "mobile_readable",
    "caption_safe",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scene(
    scene_id: str,
    segment_id: str,
    *,
    visual_mode: str,
    visual_style: str,
    visual_asset: str | None,
) -> ContentScene:
    return ContentScene(
        id=scene_id,
        narration_segment_ids=(segment_id,),
        chapter="核心判断",
        label="解释",
        progress=scene_id[-2:],
        title_lines=("任务变化",),
        subtitle_lines=(),
        kicker="证据",
        notes=(),
        visual_intent="人物沿连续路径检查任务变化",
        visual_asset=visual_asset,
        layout_variant="standard",
        visual_type="concept-scene",
        visual_style=visual_style,
        visual_mode=visual_mode,
        semantic_subjects=("普通用户", "事件轨迹"),
        semantic_action="普通用户沿事件轨迹检查任务变化",
        required_visual_evidence=("一条连续路径", "一个正在检查的人"),
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
        overlay_labels=("可回查",),
    )


def profiled_project_factory(
    root: Path, theme_id: str
) -> tuple[Path, ContentPlan, dict[str, object]]:
    project = root / f"2026-08-15-{theme_id}"
    assets = (
        project
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
    )
    prompts = assets / "prompts"
    prompts.mkdir(parents=True)
    plan_path = project / "工程" / "content-plan.json"
    plan_path.write_text('{"schema_version":4}\n', encoding="utf-8")
    prompt = prompts / "scene-01.md"
    prompt.write_text("source-free profiled prompt\n", encoding="utf-8")
    image = assets / "scene-01.png"
    image.write_bytes(b"profiled-raster")
    scene = _scene(
        "scene-01",
        "segment-001",
        visual_mode=(
            "human-action"
            if theme_id
            in {"vivid-comic-explainer", "four-panel-comic-explainer"}
            else "handdrawn-flow"
        ),
        visual_style=theme_id,
        visual_asset=(
            f"工程/assets/profiled-illustrations/{theme_id}/scene-01.png"
        ),
    )
    plan = ContentPlan(
        4,
        project.name,
        "16:9",
        "profiled-illustration-v4",
        "mobile-readable",
        "anchor-dark",
        (scene,),
        "ra-video-illustrations",
        theme_id,
    )
    checks = {
        name: True
        for name in set(CHECK_NAMES) | set(THEMES[theme_id].required_qc)
    }
    candidate: dict[str, object] = {
        "schema_version": 2,
        "visual_system": "profiled-illustration-v4",
        "visual_theme": theme_id,
        "project_id": project.name,
        "content_plan_sha256": _sha256(plan_path),
        "reviewer_type": "multimodal-review",
        "scenes": [
            {
                "scene_id": "scene-01",
                "contract_path": (
                    f"工程/assets/profiled-illustrations/{theme_id}/prompts/scene-01.md"
                ),
                "contract_sha256": _sha256(prompt),
                "selected_asset_path": (
                    f"工程/assets/profiled-illustrations/{theme_id}/scene-01.png"
                ),
                "selected_asset_sha256": _sha256(image),
                "relevance_rationale": "人物动作和软件路径共同解释任务变化",
                "checks": checks,
                "status": "pass",
            }
        ],
    }
    return project, plan, candidate


@pytest.fixture
def semantic_project(tmp_path: Path) -> tuple[Path, ContentPlan, dict[str, object]]:
    root = tmp_path / "2026-08-14-semantic-qc"
    assets = root / "工程" / "assets" / "semantic-handdrawn"
    prompts = assets / "prompts"
    type_led = assets / "type-led"
    prompts.mkdir(parents=True)
    type_led.mkdir()
    plan_path = root / "工程" / "content-plan.json"
    plan_path.write_text('{"schema_version":3}\n', encoding="utf-8")
    prompt = prompts / "scene-01.md"
    prompt.write_text("source-free semantic prompt\n", encoding="utf-8")
    image = assets / "scene-01.png"
    image.write_bytes(b"semantic-raster")
    type_contract = type_led / "scene-02.json"
    type_contract.write_text(
        json.dumps({"scene_id": "scene-02", "labels": ["可回查"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    plan = ContentPlan(
        3,
        root.name,
        "16:9",
        "semantic-handdrawn-v3",
        "mobile-readable",
        "anchor-dark",
        (
            _scene(
                "scene-01",
                "segment-001",
                visual_mode="human-action",
                visual_style="semantic-handdrawn",
                visual_asset="工程/assets/semantic-handdrawn/scene-01.png",
            ),
            _scene(
                "scene-02",
                "segment-002",
                visual_mode="type-led",
                visual_style="semantic-type",
                visual_asset=None,
            ),
        ),
        "ra-video-illustrations",
    )
    candidate: dict[str, object] = {
        "schema_version": 1,
        "visual_system": "semantic-handdrawn-v3",
        "project_id": root.name,
        "content_plan_sha256": _sha256(plan_path),
        "reviewer_type": "multimodal-review",
        "scenes": [
            {
                "scene_id": "scene-01",
                "contract_path": "工程/assets/semantic-handdrawn/prompts/scene-01.md",
                "contract_sha256": _sha256(prompt),
                "selected_asset_path": "工程/assets/semantic-handdrawn/scene-01.png",
                "selected_asset_sha256": _sha256(image),
                "relevance_rationale": "人物沿连续路径检查任务变化",
                "checks": {name: True for name in CHECK_NAMES},
                "status": "pass",
            },
            {
                "scene_id": "scene-02",
                "contract_path": "工程/assets/semantic-handdrawn/type-led/scene-02.json",
                "contract_sha256": _sha256(type_contract),
                "selected_asset_path": None,
                "selected_asset_sha256": None,
                "relevance_rationale": "准确短标签直接呈现核心分类",
                "checks": {name: True for name in CHECK_NAMES},
                "status": "pass",
            },
        ],
    }
    return root, plan, candidate


def test_semantic_qc_binds_every_scene_contract_asset_and_check(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
) -> None:
    root, plan, candidate = semantic_project

    report = validate_semantic_qc(
        candidate,
        project_root=root,
        content_plan=plan,
    )

    assert report.project_id == root.name
    assert report.reviewer_type == "multimodal-review"
    assert tuple(item.scene_id for item in report.scenes) == ("scene-01", "scene-02")
    assert report.scenes[0].selected_asset_sha256 == candidate["scenes"][0]["selected_asset_sha256"]  # type: ignore[index]
    assert report.scenes[1].selected_asset_path is None
    assert all(all(item.checks.values()) for item in report.scenes)


def test_semantic_qc_loader_returns_stable_file_snapshots(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
) -> None:
    root, plan, candidate = semantic_project
    qc_path = root / "工程" / "assets" / "semantic-handdrawn" / "semantic-qc.json"
    qc_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")

    loaded = load_semantic_qc_snapshot(root, content_plan=plan)

    assert loaded.path == qc_path.absolute()
    assert loaded.snapshot.sha256 == _sha256(qc_path)
    assert len(loaded.contract_snapshots) == 2
    assert len(loaded.asset_snapshots) == 1


@pytest.mark.parametrize("check_name", CHECK_NAMES)
def test_semantic_qc_rejects_any_failed_visual_check(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
    check_name: str,
) -> None:
    root, plan, candidate = semantic_project
    candidate["scenes"][0]["checks"][check_name] = False  # type: ignore[index]

    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)


def test_semantic_qc_requires_exact_scene_coverage_and_order(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
) -> None:
    root, plan, candidate = semantic_project
    candidate["scenes"].reverse()  # type: ignore[union-attr]

    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)


def test_semantic_qc_rejects_replaced_contract_or_asset(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
) -> None:
    root, plan, candidate = semantic_project
    prompt = root / "工程" / "assets" / "semantic-handdrawn" / "prompts" / "scene-01.md"
    prompt.write_text("changed prompt\n", encoding="utf-8")

    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)


def test_semantic_qc_enforces_type_led_and_raster_asset_branches(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
) -> None:
    root, plan, candidate = semantic_project
    candidate["scenes"][1]["selected_asset_path"] = candidate["scenes"][0]["selected_asset_path"]  # type: ignore[index]
    candidate["scenes"][1]["selected_asset_sha256"] = candidate["scenes"][0]["selected_asset_sha256"]  # type: ignore[index]

    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)


def test_semantic_qc_rejects_source_metadata_in_rationale(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
) -> None:
    root, plan, candidate = semantic_project
    candidate["scenes"][0]["relevance_rationale"] = "来源链接：https://example.invalid/private"

    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)


def test_semantic_qc_rejects_unknown_fields_and_bad_hashes(
    semantic_project: tuple[Path, ContentPlan, dict[str, object]],
) -> None:
    root, plan, candidate = semantic_project
    candidate["unexpected"] = True
    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)

    candidate.pop("unexpected")
    candidate["content_plan_sha256"] = "0" * 64
    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_profiled_qc_requires_shared_and_exact_theme_checks(
    tmp_path: Path, theme_id: str
) -> None:
    root, plan, candidate = profiled_project_factory(tmp_path, theme_id)

    report = validate_semantic_qc(
        candidate, project_root=root, content_plan=plan
    )

    assert report.schema_version == 2
    assert report.visual_system == "profiled-illustration-v4"
    assert report.visual_theme == theme_id
    assert set(report.scenes[0].checks) == (
        set(CHECK_NAMES) | set(THEMES[theme_id].required_qc)
    )
    assert all(report.scenes[0].checks.values())


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_profiled_qc_loader_uses_only_the_selected_theme_root(
    tmp_path: Path, theme_id: str
) -> None:
    root, plan, candidate = profiled_project_factory(tmp_path, theme_id)
    qc_path = (
        root
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
        / "semantic-qc.json"
    )
    qc_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")

    loaded = load_semantic_qc_snapshot(root, content_plan=plan)

    assert loaded.path == qc_path.absolute()
    assert loaded.report.visual_theme == theme_id
    assert len(loaded.contract_snapshots) == 1
    assert len(loaded.asset_snapshots) == 1


@pytest.mark.parametrize("mutation", ["missing", "extra", "false", "theme", "path"])
def test_profiled_qc_rejects_check_and_theme_drift(
    tmp_path: Path, mutation: str
) -> None:
    theme_id = "engineering-sketch-explainer"
    root, plan, candidate = profiled_project_factory(tmp_path, theme_id)
    scene = candidate["scenes"][0]  # type: ignore[index]
    if mutation == "missing":
        scene["checks"].pop("linework_clean")  # type: ignore[union-attr]
    elif mutation == "extra":
        scene["checks"]["not_ppt_page"] = True  # type: ignore[index]
    elif mutation == "false":
        scene["checks"]["linework_clean"] = False  # type: ignore[index]
    elif mutation == "theme":
        candidate["visual_theme"] = "vivid-comic-explainer"
    else:
        scene["selected_asset_path"] = (
            "工程/assets/profiled-illustrations/vivid-comic-explainer/scene-01.png"
        )

    with pytest.raises(SemanticQCError, match="semantic QC is invalid"):
        validate_semantic_qc(candidate, project_root=root, content_plan=plan)

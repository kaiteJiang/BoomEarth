"""Contract tests for reviewed, source-free content-plan compilation."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from boomearth.video.content_plan import (
    ContentPlanError,
    compile_content_plan,
    load_content_plan_snapshot,
    parse_handoff_segments,
    parse_handoff_visual_contract,
    validate_content_plan,
)
from boomearth.video.illustration_themes import THEMES, TYPE_LED_TARGET, resolve_visual_style


SCRIPT = Path(__file__).parents[1] / "automation" / "scripts" / "compile_content_plan.py"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _segments() -> tuple[str, ...]:
    return (
        "第一段公开口播。",
        "第二段公开口播。",
        "第三段公开口播。",
        "第四段公开口播。",
    )


def _handoff_text(
    segments: tuple[str, ...] | None = None,
    *,
    visual: str = "xiaohei-white-first-v1",
) -> str:
    segments = segments or _segments()
    resolved = resolve_visual_style(visual)
    blocks = "\n\n".join(
        f"### segment-{index:03d}\n\n{text}"
        for index, text in enumerate(segments, 1)
    )
    return f"""---
status: 制作中
visual: "{resolved.target}"
illustration_skill: "{resolved.illustration_skill}"
---

## 新稿分段

{blocks}

## 分段视觉意图

- 已审核。
"""


def _scene(index: int) -> dict[str, object]:
    return {
        "id": f"scene-{index:02d}",
        "narration_segment_ids": [f"segment-{index:03d}"],
        "chapter": "核心问题",
        "label": "为什么",
        "progress": f"0{index}",
        "title_lines": [f"第{index}个结论"],
        "subtitle_lines": ["从底层事实出发"],
        "kicker": "关键点",
        "notes": [{"label": "结论", "text": "先验证再行动"}],
        "visual_intent": "用小黑人物和本地短标签表现具体动作",
        "visual_asset": f"工程/assets/xiaohei-illustrations/scene-{index:02d}.png",
        "overlay_labels": ["动作主体", "关键结果"],
        "layout_variant": ("standard", "long-title", "wide-visual", "close")[index - 1],
    }


def valid_candidate(project: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "project_id": project.name,
        "ratio": "16:9",
        "visual_system": "xiaohei-white-first-v1",
        "typography_scale": "mobile-readable",
        "caption_style": "anchor-dark",
        "scenes": [_scene(index) for index in range(1, 5)],
    }


def valid_v2_candidate(project: Path) -> dict[str, object]:
    candidate = valid_candidate(project)
    candidate["schema_version"] = 2
    candidate["visual_system"] = "editorial-motion-v2"
    candidate["illustration_skill"] = "ra-video-illustrations"
    editorial = project / "工程" / "assets" / "editorial-illustrations"
    editorial.mkdir(exist_ok=True)
    styles = ("editorial-scene", "minimal-vector", "technical-diagram", "screen-print-metaphor")
    types = ("concept-scene", "comparison", "framework", "technical")
    for index, scene in enumerate(candidate["scenes"], 1):  # type: ignore[union-attr]
        path = editorial / f"scene-{index:02d}.png"
        path.write_bytes(PNG)
        scene.pop("overlay_labels")
        scene["visual_asset"] = f"工程/assets/editorial-illustrations/scene-{index:02d}.png"
        scene["visual_type"] = types[index - 1]
        scene["visual_style"] = styles[index - 1]
    return candidate


def valid_v3_candidate(project: Path) -> dict[str, object]:
    candidate = valid_candidate(project)
    candidate["schema_version"] = 3
    candidate["visual_system"] = "semantic-handdrawn-v3"
    candidate["illustration_skill"] = "ra-video-illustrations"
    semantic = project / "工程" / "assets" / "semantic-handdrawn"
    semantic.mkdir(exist_ok=True)
    modes = ("human-action", "handdrawn-flow", "source-collage", "human-action")
    styles = (
        "semantic-handdrawn",
        "semantic-handdrawn",
        "semantic-collage",
        "semantic-handdrawn",
    )
    types = ("concept-scene", "framework", "comparison", "comparison")
    labels = (["模型", "工具"], ["缺口", "创建"], ["之前", "之后"], ["可回查", "太复杂"])
    forbidden = [
        "机器人",
        "齿轮",
        "工厂",
        "机械臂",
        "金属卡匣",
        "电路板",
        "工业流水线",
        "发动机",
        "机械底座",
    ]
    for index, scene in enumerate(candidate["scenes"], 1):  # type: ignore[union-attr]
        scene["visual_type"] = types[index - 1]
        scene["visual_style"] = styles[index - 1]
        scene["visual_mode"] = modes[index - 1]
        scene["semantic_subjects"] = ["普通用户", "事件轨迹"]
        scene["semantic_action"] = "普通用户沿事件轨迹检查任务变化"
        scene["required_visual_evidence"] = ["一条连续路径", "一个正在检查的人"]
        scene["forbidden_metaphors"] = forbidden.copy()
        scene["overlay_labels"] = labels[index - 1]
        path = semantic / f"scene-{index:02d}.png"
        path.write_bytes(PNG)
        scene["visual_asset"] = f"工程/assets/semantic-handdrawn/scene-{index:02d}.png"
    return candidate


def valid_type_led_candidate(project: Path) -> dict[str, object]:
    candidate = valid_v3_candidate(project)
    for scene in candidate["scenes"]:  # type: ignore[union-attr]
        scene["visual_mode"] = "type-led"
        scene["visual_style"] = "semantic-type"
        scene["visual_asset"] = None
    return candidate


def schema4_candidate(
    project: Path, *, theme_id: str
) -> dict[str, object]:
    candidate = valid_v3_candidate(project)
    candidate["schema_version"] = 4
    candidate["visual_system"] = "profiled-illustration-v4"
    candidate["visual_theme"] = theme_id
    theme_root = (
        project
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
    )
    theme_root.mkdir(parents=True, exist_ok=True)
    for index, scene in enumerate(candidate["scenes"], 1):  # type: ignore[union-attr]
        path = theme_root / f"scene-{index:02d}.png"
        path.write_bytes(PNG)
        scene["visual_asset"] = (
            f"工程/assets/profiled-illustrations/{theme_id}/scene-{index:02d}.png"
        )
        scene["visual_style"] = theme_id
        scene["visual_mode"] = (
            "human-action"
            if theme_id
            in {"vivid-comic-explainer", "four-panel-comic-explainer"}
            else "handdrawn-flow"
        )
        scene["theme_structure"] = (
            ["问题", "尝试", "转折", "结果"]
            if theme_id == "four-panel-comic-explainer"
            else ["流程"]
            if theme_id == "blue-black-whiteboard-explainer"
            else ["中心主体", "清晰关系"]
        )
        scene["theme_exceptions"] = []
    return candidate


def _populate_project(root: Path) -> None:
    media = root / "工程" / "media"
    assets = root / "工程" / "assets" / "xiaohei-illustrations"
    media.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    (root / "交接稿.md").write_text(_handoff_text(), encoding="utf-8")
    batch = media / "segments.jsonl"
    batch_bytes = b"".join(
        (json.dumps({"text": text}, ensure_ascii=False) + "\n").encode("utf-8")
        for text in _segments()
    )
    batch.write_bytes(batch_bytes)
    (media / "voice_manifest.json").write_text(
        json.dumps(
            {
                "segment_contract_path": str(batch.absolute()),
                "segment_contract_sha256": hashlib.sha256(batch_bytes).hexdigest(),
                "segment_count": 4,
            }
        ),
        encoding="utf-8",
    )
    for index in range(1, 5):
        (assets / f"scene-{index:02d}.png").write_bytes(PNG)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "2026-08-13-content-plan"
    _populate_project(root)
    return root


def _candidate_visual_target(candidate: dict[str, object]) -> str:
    schema_version = candidate.get("schema_version")
    if schema_version == 4:
        theme = candidate.get("visual_theme")
        return theme if isinstance(theme, str) and theme in THEMES else "vivid-comic-explainer"
    if schema_version == 3:
        return "semantic-handdrawn-v3"
    if schema_version == 2:
        return "editorial-motion-v2"
    return "xiaohei-white-first-v1"


def compile_fixture(
    project: Path,
    candidate: dict[str, object],
    *,
    handoff_visual: str | None = None,
):
    target = handoff_visual or _candidate_visual_target(candidate)
    (project / "交接稿.md").write_text(
        _handoff_text(visual=target), encoding="utf-8"
    )
    path = project / "工程" / "content-plan.candidate.json"
    path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    return compile_content_plan(project_root=project, candidate_path=path)


def test_dated_active_plan_resolves_undated_predecessor_segment_contract(
    tmp_path: Path,
) -> None:
    active = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "制作中"
        / "2026-08-14-x-article-v2-acceptance"
    )
    _populate_project(active)
    compile_fixture(active, valid_candidate(active))
    manifest_path = active / "工程" / "media" / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["segment_contract_path"] = str(
        active.parent
        / "x-article-v2-acceptance"
        / "工程"
        / "media"
        / "segments.jsonl"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    snapshot = load_content_plan_snapshot(project_root=active)

    assert snapshot.plan.project_id == active.name


def test_archived_dated_plan_resolves_undated_predecessor_segment_contract(
    tmp_path: Path,
) -> None:
    workbench = tmp_path / "01-内容生产" / "视频工作台"
    active = workbench / "制作中" / "2026-08-14-x-article-v2-acceptance"
    _populate_project(active)
    compile_fixture(active, valid_candidate(active))
    manifest_path = active / "工程" / "media" / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["segment_contract_path"] = str(
        active.parent
        / "x-article-v2-acceptance"
        / "工程"
        / "media"
        / "segments.jsonl"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    archive = workbench / "已制作" / "8月中旬" / active.name
    archive.parent.mkdir(parents=True)
    active.rename(archive)

    snapshot = load_content_plan_snapshot(
        project_root=archive,
        prearchive_project_root=active,
    )

    assert snapshot.plan.project_id == archive.name


def test_parse_handoff_segments_uses_only_stable_segment_headings() -> None:
    parsed = parse_handoff_segments(_handoff_text())

    assert tuple(item.id for item in parsed) == tuple(
        f"segment-{index:03d}" for index in range(1, 5)
    )
    assert tuple(item.text for item in parsed) == _segments()


def test_compile_publishes_one_deterministic_hash_bound_plan(project: Path) -> None:
    result = compile_fixture(project, valid_candidate(project))

    formal = project / "工程" / "content-plan.json"
    assert result.path == formal.absolute()
    assert result.snapshot.payload == formal.read_bytes()
    assert result.snapshot.sha256 == hashlib.sha256(formal.read_bytes()).hexdigest()
    assert result.plan.project_id == project.name
    assert tuple(scene.layout_variant for scene in result.plan.scenes) == (
        "standard",
        "long-title",
        "wide-visual",
        "close",
    )
    assert formal.read_bytes().endswith(b"\n")


def test_xiaohei_plan_preserves_required_local_text_layer_labels(project: Path) -> None:
    candidate = valid_candidate(project)
    candidate["scenes"][0]["overlay_labels"] = ["已完成成果", "末端报错"]

    compiled = compile_fixture(project, candidate)

    assert compiled.plan.scenes[0].overlay_labels == ("已完成成果", "末端报错")
    stored = json.loads(compiled.path.read_text(encoding="utf-8"))
    assert stored["scenes"][0]["overlay_labels"] == ["已完成成果", "末端报错"]


def test_content_plan_v2_accepts_editorial_fields_without_changing_v1(project: Path) -> None:
    compiled = compile_fixture(project, valid_v2_candidate(project))
    assert compiled.plan.schema_version == 2
    assert compiled.plan.visual_system == "editorial-motion-v2"
    assert compiled.plan.illustration_skill == "ra-video-illustrations"
    assert compiled.plan.scenes[0].visual_type == "concept-scene"
    assert compiled.plan.scenes[0].visual_style == "editorial-scene"
    assert "illustration_skill" not in valid_candidate(project)


def test_content_plan_v3_compiles_semantic_modes_without_changing_v1_or_v2(
    project: Path,
) -> None:
    compiled = compile_fixture(project, valid_v3_candidate(project))

    assert compiled.plan.schema_version == 3
    assert compiled.plan.visual_system == "semantic-handdrawn-v3"
    assert compiled.plan.illustration_skill == "ra-video-illustrations"
    assert tuple(scene.visual_mode for scene in compiled.plan.scenes) == (
        "human-action",
        "handdrawn-flow",
        "source-collage",
        "human-action",
    )
    assert compiled.plan.scenes[-1].visual_asset is not None
    assert compiled.plan.scenes[-1].overlay_labels == ("可回查", "太复杂")

    stored = json.loads(compiled.path.read_text(encoding="utf-8"))
    assert stored["scenes"][0]["required_visual_evidence"] == [
        "一条连续路径",
        "一个正在检查的人",
    ]
    assert stored["scenes"][-1]["visual_asset"] is not None
    assert valid_candidate(project)["schema_version"] == 1
    assert valid_v2_candidate(project)["schema_version"] == 2


def test_content_plan_rejects_theme_mismatch_with_public_handoff(project: Path) -> None:
    candidate = schema4_candidate(
        project, theme_id="engineering-sketch-explainer"
    )

    with pytest.raises(ContentPlanError, match="handoff visual contract is invalid"):
        compile_fixture(
            project,
            candidate,
            handoff_visual="vivid-comic-explainer",
        )


def test_generic_semantic_v3_rejects_implicit_type_led_scene(project: Path) -> None:
    candidate = valid_v3_candidate(project)
    scene = candidate["scenes"][0]  # type: ignore[index]
    scene["visual_mode"] = "type-led"
    scene["visual_style"] = "semantic-type"
    scene["visual_asset"] = None

    with pytest.raises(ContentPlanError, match="handoff visual contract is invalid"):
        compile_fixture(project, candidate)


def test_explicit_type_led_handoff_requires_and_accepts_all_type_led_scenes(
    project: Path,
) -> None:
    candidate = valid_type_led_candidate(project)
    compiled = compile_fixture(
        project,
        candidate,
        handoff_visual=TYPE_LED_TARGET,
    )
    assert all(scene.visual_mode == "type-led" for scene in compiled.plan.scenes)
    assert all(scene.visual_asset is None for scene in compiled.plan.scenes)

    compiled.path.unlink()
    scene = candidate["scenes"][0]  # type: ignore[index]
    scene["visual_mode"] = "human-action"
    scene["visual_style"] = "semantic-handdrawn"
    scene["visual_asset"] = "工程/assets/semantic-handdrawn/scene-01.png"
    with pytest.raises(ContentPlanError, match="handoff visual contract is invalid"):
        compile_fixture(
            project,
            candidate,
            handoff_visual=TYPE_LED_TARGET,
        )


def test_formal_plan_rejects_handoff_theme_changed_after_publication(project: Path) -> None:
    compile_fixture(
        project,
        schema4_candidate(project, theme_id="vivid-comic-explainer"),
    )
    (project / "交接稿.md").write_text(
        _handoff_text(visual="engineering-sketch-explainer"),
        encoding="utf-8",
    )

    with pytest.raises(ContentPlanError, match="handoff visual contract is invalid"):
        load_content_plan_snapshot(project_root=project)


def test_public_handoff_visual_contract_rejects_default_and_skill_drift() -> None:
    with pytest.raises(ContentPlanError, match="public handoff is invalid"):
        parse_handoff_visual_contract(
            _handoff_text().replace(
                'visual: "xiaohei-white-first-v1"', 'visual: "default"'
            )
        )
    with pytest.raises(ContentPlanError, match="public handoff is invalid"):
        parse_handoff_visual_contract(
            _handoff_text().replace(
                'illustration_skill: "ian-xiaohei-illustrations"',
                'illustration_skill: "ra-video-illustrations"',
            )
        )


@pytest.mark.parametrize("theme_id", sorted(THEMES))
def test_schema4_accepts_each_exact_project_theme(
    project: Path, theme_id: str
) -> None:
    compiled = compile_fixture(
        project, schema4_candidate(project, theme_id=theme_id)
    )

    assert compiled.plan.schema_version == 4
    assert compiled.plan.visual_system == "profiled-illustration-v4"
    assert compiled.plan.visual_theme == theme_id
    assert all(scene.visual_style == theme_id for scene in compiled.plan.scenes)
    assert all(scene.theme_structure for scene in compiled.plan.scenes)
    stored = json.loads(compiled.path.read_text("utf-8"))
    assert stored["visual_theme"] == theme_id
    assert stored["scenes"][0]["theme_exceptions"] == []


def test_historical_schemas_have_no_profiled_theme(project: Path) -> None:
    for factory in (valid_candidate, valid_v2_candidate, valid_v3_candidate):
        compiled = compile_fixture(project, factory(project))
        assert compiled.plan.visual_theme is None
        compiled.path.unlink()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing-theme", "content plan schema is invalid"),
        ("unknown-theme", "content plan schema is invalid"),
        ("v3-system", "content plan schema is invalid"),
        ("scene-theme-drift", "content plan schema is invalid"),
        ("cross-theme-asset", "visual asset is invalid"),
    ],
)
def test_schema4_rejects_project_scene_and_asset_theme_drift(
    project: Path, mutation: str, expected: str
) -> None:
    candidate = schema4_candidate(
        project, theme_id="vivid-comic-explainer"
    )
    if mutation == "missing-theme":
        del candidate["visual_theme"]
    elif mutation == "unknown-theme":
        candidate["visual_theme"] = "unknown"
    elif mutation == "v3-system":
        candidate["visual_system"] = "semantic-handdrawn-v3"
    elif mutation == "scene-theme-drift":
        candidate["scenes"][0]["visual_style"] = (  # type: ignore[index]
            "engineering-sketch-explainer"
        )
    else:
        candidate["scenes"][0]["visual_asset"] = (  # type: ignore[index]
            "工程/assets/profiled-illustrations/engineering-sketch-explainer/scene-01.png"
        )

    with pytest.raises(ContentPlanError, match=expected):
        compile_fixture(project, candidate)


@pytest.mark.parametrize("beats", [[], ["一", "二", "三"], ["一"] * 5])
def test_four_panel_theme_requires_exactly_four_nonempty_beats(
    project: Path, beats: list[str]
) -> None:
    candidate = schema4_candidate(
        project, theme_id="four-panel-comic-explainer"
    )
    candidate["scenes"][0]["theme_structure"] = beats  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="four-panel-beats-invalid"):
        compile_fixture(project, candidate)


def test_engineering_mechanical_exception_requires_a_physical_subject(
    project: Path,
) -> None:
    candidate = schema4_candidate(
        project, theme_id="engineering-sketch-explainer"
    )
    scene = candidate["scenes"][0]  # type: ignore[index]
    scene["theme_exceptions"] = ["机器人"]
    scene["forbidden_metaphors"].remove("机器人")  # type: ignore[union-attr]
    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)

    scene["semantic_subjects"] = ["实体机器人"]
    compiled = compile_fixture(project, candidate)
    assert compiled.plan.scenes[0].theme_exceptions == ("机器人",)


@pytest.mark.parametrize(
    "theme_id",
    ["vivid-comic-explainer", "blue-black-whiteboard-explainer"],
)
def test_non_engineering_themes_reject_mechanical_exceptions(
    project: Path, theme_id: str
) -> None:
    candidate = schema4_candidate(project, theme_id=theme_id)
    candidate["scenes"][0]["theme_exceptions"] = ["齿轮"]  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


@pytest.mark.parametrize(
    ("schema_version", "visual_system"),
    [
        (3, "editorial-motion-v2"),
        (2, "semantic-handdrawn-v3"),
        (1, "semantic-handdrawn-v3"),
    ],
)
def test_content_plan_rejects_schema_visual_system_mismatches(
    project: Path, schema_version: int, visual_system: str
) -> None:
    candidate = valid_v3_candidate(project)
    candidate["schema_version"] = schema_version
    candidate["visual_system"] = visual_system

    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("visual_mode", "robot-parts"),
        ("visual_style", "industrial-3d"),
        ("semantic_subjects", []),
        ("semantic_subjects", ["一", "二", "三", "四", "五"]),
        ("required_visual_evidence", []),
        ("required_visual_evidence", ["一", "二", "三", "四", "五"]),
        ("forbidden_metaphors", [str(index) for index in range(13)]),
        ("overlay_labels", ["一", "二", "三", "四", "五"]),
    ],
)
def test_content_plan_v3_rejects_invalid_semantic_field_boundaries(
    project: Path, field: str, value: object
) -> None:
    candidate = valid_v3_candidate(project)
    candidate["scenes"][0][field] = value  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


@pytest.mark.parametrize("label", ["单", "七个汉字长标签", "nineteen-characters!", "两字\n换行"])
def test_content_plan_v3_rejects_unrenderable_overlay_labels(
    project: Path, label: str
) -> None:
    candidate = valid_v3_candidate(project)
    candidate["scenes"][0]["overlay_labels"] = [label]  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


def test_content_plan_v3_requires_the_default_mechanical_blacklist(
    project: Path,
) -> None:
    candidate = valid_v3_candidate(project)
    candidate["scenes"][0]["forbidden_metaphors"].remove("机器人")  # type: ignore[index,union-attr]

    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


def test_content_plan_v3_allows_only_subject_specific_physical_omissions(
    project: Path,
) -> None:
    candidate = valid_v3_candidate(project)
    scene = candidate["scenes"][0]  # type: ignore[index]
    scene["semantic_subjects"] = ["实体机器人"]
    for term in ("机器人", "机械臂", "机械底座"):
        scene["forbidden_metaphors"].remove(term)  # type: ignore[union-attr]

    compiled = compile_fixture(project, candidate)
    assert "机器人" not in compiled.plan.scenes[0].forbidden_metaphors

    compiled.path.unlink()
    scene["forbidden_metaphors"].remove("齿轮")  # type: ignore[union-attr]
    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


def test_content_plan_v3_type_led_rejects_raster_and_raster_modes_require_one(
    project: Path,
) -> None:
    candidate = valid_type_led_candidate(project)
    candidate["scenes"][-1]["visual_asset"] = (  # type: ignore[index]
        "工程/assets/semantic-handdrawn/scene-04.png"
    )
    with pytest.raises(ContentPlanError, match="visual asset is invalid"):
        compile_fixture(project, candidate, handoff_visual=TYPE_LED_TARGET)

    candidate = valid_v3_candidate(project)
    candidate["scenes"][0]["visual_asset"] = None  # type: ignore[index]
    with pytest.raises(ContentPlanError, match="visual asset is invalid"):
        compile_fixture(project, candidate)


def test_content_plan_v3_rejects_assets_outside_the_semantic_directory(
    project: Path,
) -> None:
    candidate = valid_v3_candidate(project)
    candidate["scenes"][0]["visual_asset"] = (
        "工程/assets/editorial-illustrations/scene-01.png"
    )  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="visual asset is invalid"):
        compile_fixture(project, candidate)


def test_content_plan_v2_allows_zero_note_rows_but_v1_still_requires_one(
    project: Path,
) -> None:
    candidate = valid_v2_candidate(project)
    candidate["scenes"][0]["notes"] = []  # type: ignore[index]
    compiled = compile_fixture(project, candidate)
    assert compiled.plan.scenes[0].notes == ()

    (project / "工程" / "content-plan.json").unlink()
    legacy = valid_candidate(project)
    legacy["scenes"][0]["notes"] = []  # type: ignore[index]
    with pytest.raises(ContentPlanError, match="visible content is invalid"):
        compile_fixture(project, legacy)


@pytest.mark.parametrize(
    ("field", "value"),
    [("visual_type", "poster"), ("visual_style", "random-3d")],
)
def test_content_plan_v2_rejects_unknown_visual_taxonomy(
    project: Path, field: str, value: str
) -> None:
    candidate = valid_v2_candidate(project)
    candidate["scenes"][0][field] = value  # type: ignore[index]
    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


def test_content_plan_requires_each_narration_segment_exactly_once(project: Path) -> None:
    candidate = valid_candidate(project)
    candidate["scenes"][1]["narration_segment_ids"] = ["segment-001"]  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="narration segment coverage is invalid"):
        compile_fixture(project, candidate)


@pytest.mark.parametrize(
    "asset",
    [
        "https://example.invalid/a.png",
        "D:/outside/a.png",
        "工程/assets/../media/a.png",
        "工程/assets/xiaohei-illustrations/a.svg",
    ],
)
def test_content_plan_rejects_unsafe_visual_assets(
    project: Path, asset: str
) -> None:
    candidate = valid_candidate(project)
    candidate["scenes"][0]["visual_asset"] = asset  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="visual asset is invalid"):
        compile_fixture(project, candidate)


def test_content_plan_rejects_missing_or_undecodable_visual(project: Path) -> None:
    candidate = valid_candidate(project)
    asset = project / "工程" / "assets" / "xiaohei-illustrations" / "scene-01.png"
    asset.write_bytes(b"not-an-image")

    with pytest.raises(ContentPlanError, match="visual asset is invalid"):
        compile_fixture(project, candidate)


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        (
            ".png",
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            + (1).to_bytes(4, "big")
            + (1).to_bytes(4, "big"),
        ),
        (".jpg", b"\xff\xd8corrupt\xff\xd9"),
        (".webp", b"RIFF\x08\x00\x00\x00WEBPVP8 "),
    ],
)
def test_content_plan_rejects_signature_shaped_corrupt_visuals(
    project: Path,
    suffix: str,
    payload: bytes,
) -> None:
    candidate = valid_candidate(project)
    scene = candidate["scenes"][0]  # type: ignore[index]
    relative = f"工程/assets/xiaohei-illustrations/corrupt{suffix}"
    scene["visual_asset"] = relative
    (project / Path(*relative.split("/"))).write_bytes(payload)

    with pytest.raises(ContentPlanError, match="visual asset is invalid"):
        compile_fixture(project, candidate)


def test_no_visual_requires_a_reason_and_rejects_irrelevant_reason(project: Path) -> None:
    candidate = valid_candidate(project)
    scene = candidate["scenes"][0]  # type: ignore[index]
    scene["visual_asset"] = None

    with pytest.raises(ContentPlanError, match="no-visual exception is invalid"):
        compile_fixture(project, candidate)

    scene["no_visual_reason"] = "本场只保留留白"
    compiled = compile_fixture(project, candidate)
    assert compiled.plan.scenes[0].visual_asset is None


def test_candidate_schema_is_exact_and_project_bound(project: Path) -> None:
    candidate = valid_candidate(project)
    candidate["unexpected"] = True
    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)

    candidate = valid_candidate(project)
    candidate["project_id"] = "2026-08-13-another-project"
    with pytest.raises(ContentPlanError, match="project identity is invalid"):
        compile_fixture(project, candidate)


@pytest.mark.parametrize("location", ["scene", "note"])
def test_nested_content_plan_fields_are_exact(project: Path, location: str) -> None:
    candidate = valid_candidate(project)
    scene = candidate["scenes"][0]  # type: ignore[index]
    if location == "scene":
        scene["unexpected"] = True
    else:
        scene["notes"][0]["unexpected"] = True  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="content plan schema is invalid"):
        compile_fixture(project, candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("layout_variant", "tiny-text"),
        ("title_lines", ["这是一条明显超过十六个阅读单位的移动端标题行"]),
        ("subtitle_lines", ["一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"]),
        ("chapter", "这个章节名字明显超过十二个阅读单位"),
    ],
)
def test_visible_contract_limits_are_hard_failures(
    project: Path, field: str, value: object
) -> None:
    candidate = valid_candidate(project)
    candidate["scenes"][0][field] = value  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="visible content is invalid"):
        compile_fixture(project, candidate)


def test_public_privacy_scan_applies_to_visible_text_and_serialized_plan(
    project: Path,
) -> None:
    candidate = valid_candidate(project)
    candidate["scenes"][0]["kicker"] = "https://bad.invalid"  # type: ignore[index]

    with pytest.raises(ContentPlanError, match="public content is invalid") as error:
        compile_fixture(project, candidate)

    assert "bad.invalid" not in str(error.value)


@pytest.mark.parametrize("manifest_change", ["hash", "count", "text"])
def test_manifest_must_bind_the_exact_display_jsonl(
    project: Path, manifest_change: str
) -> None:
    manifest_path = project / "工程" / "media" / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    batch = project / "工程" / "media" / "segments.jsonl"
    if manifest_change == "hash":
        manifest["segment_contract_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    elif manifest_change == "count":
        manifest["segment_count"] = 3
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        lines = batch.read_text(encoding="utf-8").splitlines()
        lines[0] = json.dumps({"text": "不一致的公开口播"}, ensure_ascii=False)
        changed = ("\n".join(lines) + "\n").encode("utf-8")
        batch.write_bytes(changed)
        manifest["segment_contract_sha256"] = hashlib.sha256(changed).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ContentPlanError, match="narration contract is invalid"):
        compile_fixture(project, valid_candidate(project))


def test_handoff_must_equal_jsonl_display_text_not_tts_substitution(project: Path) -> None:
    batch = project / "工程" / "media" / "segments.jsonl"
    rows = [json.loads(line) for line in batch.read_text(encoding="utf-8").splitlines()]
    rows[0] = {"text": _segments()[0], "tts_text": "发音替换文本"}
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in rows
    )
    batch.write_bytes(payload)
    manifest_path = project / "工程" / "media" / "voice_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["segment_contract_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = compile_fixture(project, valid_candidate(project))

    assert result.plan.scenes[0].narration_segment_ids == ("segment-001",)


def test_formal_plan_is_no_clobber(project: Path) -> None:
    compile_fixture(project, valid_candidate(project))

    with pytest.raises(ContentPlanError, match="content plan publication failed"):
        compile_fixture(project, valid_candidate(project))


def test_candidate_change_before_publication_fails_without_formal_artifact(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boomearth.video.content_plan as content_plan

    candidate = valid_candidate(project)
    candidate_path = project / "工程" / "content-plan.candidate.json"
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    real_matches = content_plan.snapshot_matches
    changed = False

    def change_candidate_once(snapshot) -> bool:
        nonlocal changed
        if snapshot.path == candidate_path.absolute() and not changed:
            changed = True
            candidate_path.write_text("{}\n", encoding="utf-8")
        return real_matches(snapshot)

    monkeypatch.setattr(content_plan, "snapshot_matches", change_candidate_once)

    with pytest.raises(ContentPlanError, match="content plan input changed"):
        compile_content_plan(project_root=project, candidate_path=candidate_path)

    assert not (project / "工程" / "content-plan.json").exists()


def test_validate_content_plan_can_revalidate_a_formal_value(project: Path) -> None:
    candidate = valid_candidate(project)
    plan = validate_content_plan(
        candidate,
        project_root=project,
        project_id=project.name,
        handoff_segments=parse_handoff_segments(_handoff_text()),
        handoff_visual=parse_handoff_visual_contract(_handoff_text()),
        narration_segments=_segments(),
    )

    assert len(plan.scenes) == 4


def test_cli_failure_is_redacted_and_does_not_publish(capsys: pytest.CaptureFixture[str]) -> None:
    spec = importlib.util.spec_from_file_location("content_plan_cli_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    exit_code = module.main(
        [
            "--workspace-root",
            "missing-workspace",
            "--active-project",
            "2026-08-13-missing",
            "--candidate",
            "工程/content-plan.candidate.json",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().out == "rule=content-plan-compile\n"


def test_cli_loads_a_canonical_project_and_prints_only_safe_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = importlib.util.spec_from_file_location("content_plan_cli_success", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import run_v2_production_sample as orchestrator

    workspace = tmp_path / "workspace"
    created = orchestrator.initialize_production_project(
        workspace_root=workspace, archive_slug="content-plan-cli"
    )
    _populate_project(created.active_dir)
    candidate = created.active_dir / "工程" / "content-plan.candidate.json"
    candidate.write_text(
        json.dumps(valid_candidate(created.active_dir), ensure_ascii=False),
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--workspace-root",
            str(workspace),
            "--active-project",
            created.active_dir.name,
            "--candidate",
            "工程/content-plan.candidate.json",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "status=content-plan-published\n"
        "artifact=工程/content-plan.json\n"
        "scenes=4\n"
    )
    assert (created.active_dir / "工程" / "content-plan.json").is_file()

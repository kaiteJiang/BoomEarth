"""Offline immutable content render-project assembly tests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from boomearth.video.content_plan import compile_content_plan
from boomearth.video.motion_plan import AmbientMotion, MotionEntry, MotionPlan, SceneMotion, SemanticCue, compile_motion_plan
from boomearth.video.render_project import _scene_html, _timeline_script

from boomearth.video.render_project import (
    ContentRenderProjectError,
    prepare_content_render_project,
)
from boomearth.video.scene_timeline import build_scene_timeline
from test_scene_timeline import _populate_project, _words
from test_content_plan import valid_v3_candidate
from boomearth.video.illustration_themes import resolve_visual_style


CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "2026-08-13-content-render"
    _populate_project(root)
    captions = root / "工程" / "media" / "captions"
    cue_values = [
        {
            "start": float(word["start"]),
            "end": float(word["end"]),
            "text": str(word["text"]),
            "source": "volcengine-word-timestamps",
        }
        for word in _words()
        if not word["isGap"]
    ]
    (captions / "captions.json").write_text(
        json.dumps(cue_values, ensure_ascii=False), encoding="utf-8"
    )
    (captions / "asr-result.json").write_text("{}\n", encoding="utf-8")
    (captions / "captions.srt").write_text("1\n00:00:00,200 --> 00:00:01,500\n第一段介绍\n", encoding="utf-8")
    (captions / "captions.vtt").write_text("WEBVTT\n\n00:00.200 --> 00:01.500\n第一段介绍\n", encoding="utf-8")
    build_scene_timeline(project_root=root)
    return root


def _prepare(project: Path):
    return prepare_content_render_project(
        project_root=project,
        output_dir=project / "工程" / "render-project",
        repo_root=Path(__file__).parents[1],
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _set_handoff_visual(root: Path, target: str) -> None:
    resolved = resolve_visual_style(target)
    path = root / "交接稿.md"
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"(?m)^visual: .+$", f'visual: "{resolved.target}"', text, count=1
    )
    text = re.sub(
        r"(?m)^illustration_skill: .+$",
        f'illustration_skill: "{resolved.illustration_skill}"',
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def v3_project(tmp_path: Path) -> Path:
    root = tmp_path / "2026-08-14-v3-content-render"
    _populate_project(root)
    formal = root / "工程" / "content-plan.json"
    formal.unlink()
    candidate_path = root / "工程" / "content-plan.candidate.json"
    _set_handoff_visual(root, "semantic-handdrawn-v3")
    candidate_path.write_text(
        json.dumps(valid_v3_candidate(root), ensure_ascii=False), encoding="utf-8"
    )
    plan = compile_content_plan(
        project_root=root,
        candidate_path=candidate_path,
    ).plan

    captions = root / "工程" / "media" / "captions"
    cue_values = [
        {
            "start": float(word["start"]),
            "end": float(word["end"]),
            "text": str(word["text"]),
            "source": "volcengine-word-timestamps",
        }
        for word in _words()
        if not word["isGap"]
    ]
    (captions / "captions.json").write_text(
        json.dumps(cue_values, ensure_ascii=False), encoding="utf-8"
    )
    (captions / "asr-result.json").write_text("{}\n", encoding="utf-8")
    (captions / "captions.srt").write_text(
        "1\n00:00:00,200 --> 00:00:01,500\n第一段介绍\n", encoding="utf-8"
    )
    (captions / "captions.vtt").write_text(
        "WEBVTT\n\n00:00.200 --> 00:01.500\n第一段介绍\n", encoding="utf-8"
    )

    assets = root / "工程" / "assets" / "semantic-handdrawn"
    prompts = assets / "prompts"
    type_led = assets / "type-led"
    candidates = assets / "candidates"
    prompts.mkdir(exist_ok=True)
    type_led.mkdir(exist_ok=True)
    candidates.mkdir(exist_ok=True)
    qc_scenes: list[dict[str, object]] = []
    manifest_assets: list[dict[str, object]] = []
    check_names = (
        "subject_match",
        "action_match",
        "evidence_complete",
        "claim_readable",
        "non_generic",
        "forbidden_absent",
        "mobile_readable",
        "caption_safe",
    )
    for index, scene in enumerate(plan.scenes, 1):
        if scene.visual_mode == "type-led":
            contract = type_led / f"{scene.id}.json"
            contract.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "scene_id": scene.id,
                        "visual_mode": "type-led",
                        "ratio": "16:9",
                        "target_size": "3840x2160",
                        "caption_safe_zone": "bottom-150px",
                        "labels": list(scene.overlay_labels),
                        "layout": "comparison",
                        "connectors": ["path", "divider"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            contract_relative = f"工程/assets/semantic-handdrawn/type-led/{scene.id}.json"
            selected_relative = None
            selected_hash = None
            candidate_artifacts: list[dict[str, str]] = []
            width = height = image_format = None
        else:
            contract = prompts / f"{scene.id}.md"
            contract.write_text(
                "---\n"
                f"scene_id: {scene.id}\n"
                f"visual_type: {scene.visual_type}\n"
                f"visual_style: {scene.visual_style}\n"
                f"visual_mode: {scene.visual_mode}\n"
                "ratio: 16:9\n"
                "target_size: 3840x2160\n"
                "text_policy: none\n"
                "caption_safe_zone: bottom-150px\n"
                "---\n\n"
                "1. 唯一判断\n2. 语义主体\n3. 核心动作关系\n4. 必须看到的证据\n"
                "5. 构图安全区\n6. 手绘淡彩\n7. 解释判断理由\n8. 禁止意象文字水印\n",
                encoding="utf-8",
            )
            contract_relative = f"工程/assets/semantic-handdrawn/prompts/{scene.id}.md"
            candidate = candidates / f"{scene.id}-candidate-01.png"
            selected = assets / f"{scene.id}.png"
            Image.new("RGB", (3840, 2160), (244 - index, 238, 224)).save(
                candidate, format="PNG"
            )
            Image.new("RGB", (3840, 2160), (240 - index, 235, 220)).save(
                selected, format="PNG"
            )
            selected_relative = f"工程/assets/semantic-handdrawn/{scene.id}.png"
            selected_hash = _sha256(selected)
            candidate_artifacts = [
                {
                    "path": f"工程/assets/semantic-handdrawn/candidates/{scene.id}-candidate-01.png",
                    "sha256": _sha256(candidate),
                }
            ]
            width, height, image_format = 3840, 2160, "png"
        contract_hash = _sha256(contract)
        qc_scenes.append(
            {
                "scene_id": scene.id,
                "contract_path": contract_relative,
                "contract_sha256": contract_hash,
                "selected_asset_path": selected_relative,
                "selected_asset_sha256": selected_hash,
                "relevance_rationale": "人物与路径直接解释当前任务关系",
                "checks": {name: True for name in check_names},
                "status": "pass",
            }
        )
        manifest_assets.append(
            {
                "scene_id": scene.id,
                "visual_type": scene.visual_type,
                "visual_style": scene.visual_style,
                "visual_mode": scene.visual_mode,
                "contract_path": contract_relative,
                "contract_sha256": contract_hash,
                "candidate_artifacts": candidate_artifacts,
                "selected_asset_path": selected_relative,
                "selected_asset_sha256": selected_hash,
                "width": width,
                "height": height,
                "format": image_format,
                "ratio": "16:9",
                "qc_status": "pass",
            }
        )
    qc_path = assets / "semantic-qc.json"
    qc_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "visual_system": "semantic-handdrawn-v3",
                "project_id": root.name,
                "content_plan_sha256": _sha256(formal),
                "reviewer_type": "multimodal-review",
                "scenes": qc_scenes,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (assets / "illustration-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "illustration_skill": "ra-video-illustrations",
                "visual_system": "semantic-handdrawn-v3",
                "content_plan_sha256": _sha256(formal),
                "semantic_qc_path": "工程/assets/semantic-handdrawn/semantic-qc.json",
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


def test_render_project_uses_real_scene_timeline_not_equal_splits(project: Path) -> None:
    prepared = _prepare(project)
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")

    assert 'data-scene-start="4.200"' in html
    assert 'data-scene-end="7.400"' in html
    assert 'data-scene-start="8.000"' not in html
    assert prepared.duration_seconds == 16.0
    assert prepared.scene_midpoints == (2.1, 5.8, 9.2, 13.5)


def test_new_xiaohei_snapshot_uses_stable_component_motion(project: Path) -> None:
    prepared = _prepare(project)
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")

    for target in (
        "chapter",
        "title-line-1",
        "subtitle-line-1",
        "visual",
        "note-card",
        "kicker",
        "note-row-1",
    ):
        assert f'id="scene-01--{target}"' in html
    assert "motion-component" in html
    assert "x:-1920" not in html
    assert "opacity:1,duration:0.3,ease:'power3.inOut'" in html
    assert "{opacity:0,x:-28}" in html
    assert "power3.out" in html


def test_prepare_generic_v3_project_stages_semantic_assets_motion_and_background(
    v3_project: Path,
) -> None:
    prepared = _prepare(v3_project)
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")

    assert "visual-system-v3" in html
    assert "type-led-layout type-led-comparison" not in html
    assert '<div class="visual-placeholder">' not in html
    assert "perspective-grid-v5-loop.mp4" in html
    assert (prepared.output_dir / "motion-plan.json").is_file()
    assert (prepared.output_dir / "illustration-manifest.json").is_file()
    assert {
        path.name
        for path in (prepared.output_dir / "assets" / "semantic-handdrawn").glob("*.png")
    } == {"scene-01.png", "scene-02.png", "scene-03.png", "scene-04.png"}


def test_all_content_and_caption_cues_are_embedded_and_escaped(project: Path) -> None:
    prepared = _prepare(project)
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")

    for scene_id, variant in zip(
        ("scene-01", "scene-02", "scene-03", "scene-04"),
        ("standard", "long-title", "wide-visual", "close"),
    ):
        assert f'id="{scene_id}"' in html
        assert f"layout-{variant}" in html
    assert 'data-caption-start="0.200"' in html
    assert 'data-caption-end="1.500"' in html
    assert "第一段介绍" in html


def test_anchor_dark_uses_registered_shrink_wrapped_geometry(project: Path) -> None:
    prepared = _prepare(project)
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")

    assert '<span class="caption-panel">第一段介绍</span>' in html
    assert "bottom: 96px" in html
    assert "font-size: 56px" in html
    assert "max-width: 1500px" in html
    assert "padding: 22px 22px" in html
    assert "border-radius: 16px" in html
    assert "rgba(45, 51, 50, .835)" in html
    assert (
        '@font-face { font-family: "STHeiti Medium"; '
        'src: local("STHeiti Medium"); }'
        in html
    )
    assert "height: 150px" not in html


def test_snapshot_contains_only_local_canonical_inputs(project: Path) -> None:
    prepared = _prepare(project)
    relative_files = {
        path.relative_to(prepared.output_dir).as_posix()
        for path in prepared.output_dir.rglob("*")
        if path.is_file()
    }

    assert relative_files == {
        "index.html",
        "media/narration.wav",
        "media/voice_manifest.json",
        *(f"media/captions/{name}" for name in CAPTION_FILES),
        *(f"assets/xiaohei-illustrations/scene-{index:02d}.png" for index in range(1, 5)),
        "node_modules/gsap/dist/gsap.min.js",
    }
    assert (prepared.output_dir / "media" / "narration.wav").read_bytes() == (
        project / "工程" / "media" / "narration.wav"
    ).read_bytes()


def test_html_is_offline_deterministic_and_has_no_infinite_animation(project: Path) -> None:
    prepared = _prepare(project)
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")
    lowered = html.casefold()

    assert 'src="node_modules/gsap/dist/gsap.min.js"' in html
    for forbidden in (
        "http://",
        "https://",
        "//host",
        "data:",
        "fetch(",
        "xmlhttprequest",
        "websocket",
        "math.random",
        "new date",
        "date.now",
        "repeat: -1",
        "repeat:-1",
    ):
        assert forbidden not in lowered
    assert "__DURATION_SECONDS__" not in html
    assert "__SCENE_BLOCKS__" not in html
    assert "__CAPTION_BLOCKS__" not in html
    assert "__TIMELINE_SCRIPT__" not in html


def test_legacy_visual_system_does_not_inject_v5_background(project: Path) -> None:
    prepared = _prepare(project)
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")

    assert "perspective-grid-v5-loop.mp4" not in html
    assert not (prepared.output_dir / "assets" / "perspective-grid-v5").exists()


def test_asset_replacement_after_capture_leaves_no_output(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boomearth.video.render_project as render_project

    asset = project / "工程" / "assets" / "xiaohei-illustrations" / "scene-02.png"

    def replace_asset(_snapshots) -> None:
        asset.write_bytes(b"changed-after-capture")

    monkeypatch.setattr(render_project, "_after_inputs_captured", replace_asset)

    with pytest.raises(ContentRenderProjectError, match="render input changed"):
        _prepare(project)

    assert not (project / "工程" / "render-project").exists()
    assert list((project / "工程").glob(".render-project-*.tmp")) == []


def test_existing_output_is_never_overwritten_or_deleted(project: Path) -> None:
    output = project / "工程" / "render-project"
    output.mkdir()
    marker = output / "owned-by-operator.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(ContentRenderProjectError, match="render target is unavailable"):
        _prepare(project)

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_current_caption_qc_must_still_pass_and_match_narration(project: Path) -> None:
    qc_path = project / "工程" / "media" / "captions" / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["status"] = "fail"
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(ContentRenderProjectError, match="render captions are invalid"):
        _prepare(project)

    assert not (project / "工程" / "render-project").exists()


def test_template_contract_rejects_repeated_or_unknown_tokens(
    project: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    template_dir = repo / "video-content-template"
    gsap = repo / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    template_dir.mkdir(parents=True)
    gsap.parent.mkdir(parents=True)
    gsap.write_text("window.gsap = {};", encoding="utf-8")
    template_dir.joinpath("index.template.html").write_text(
        "__DURATION_SECONDS____DURATION_SECONDS____SCENE_BLOCKS__"
        "__CAPTION_BLOCKS____TIMELINE_SCRIPT____UNKNOWN_TOKEN__",
        encoding="utf-8",
    )

    with pytest.raises(ContentRenderProjectError, match="render template is invalid"):
        prepare_content_render_project(
            project_root=project,
            output_dir=project / "工程" / "render-project",
            repo_root=repo,
        )
def test_motion_v2_emits_stable_component_targets_and_finite_gsap() -> None:
    scene = SimpleNamespace(
        id="scene-01", layout_variant="standard", title_lines=("One", "Two"),
        subtitle_lines=("Sub",), notes=(SimpleNamespace(label="A", text="B"),),
        chapter="01", progress="1/1", kicker="K", visual_asset="工程/assets/editorial-illustrations/scene-01.png",
    )
    plan = SimpleNamespace(scenes=(scene,), visual_system="editorial-motion-v2")
    timing = SimpleNamespace(id="scene-01", start=0.0, end=4.0)
    timeline = SimpleNamespace(scenes=(timing,))
    motion = MotionPlan(
        1, "editorial-cards-v2", "a" * 64, "b" * 64, "c" * 64, 4.0,
        (SceneMotion(
            "scene-01", 0.0, 4.0,
            (
                MotionEntry("chapter", 0.0, 0.24, "fade-down"),
                MotionEntry("title-line-1", 0.1, 0.3, "line-reveal"),
                MotionEntry("visual", 0.5, 0.4, "scale-settle"),
                MotionEntry("note-card", 0.8, 0.3, "wipe-right"),
                MotionEntry("note-row-1", 1.1, 0.24, "fade-up"),
            ),
            (SemanticCue("One", "title-line-1", 1.5, 0.3, "accent-pulse"),),
            AmbientMotion("visual", "slow-parallax", 1.0, 3.6, 0.35),
        ),),
    )

    markup = _scene_html(plan, timeline, {"scene-01": "scene-01.png"})
    script = _timeline_script(timeline, (), motion)

    for target in ("chapter", "title-line-1", "title-line-2", "subtitle-line-1", "visual", "note-card", "kicker", "note-row-1"):
        assert f'id="scene-01--{target}"' in markup
        assert f'data-motion-target="{target}"' in markup
    assert "motion-component" in markup
    for forbidden in ("repeat:-1", "Date.now", "Math.random", "querySelector", "http://", "https://"):
        assert forbidden not in script
    assert "getElementById" in script
    assert "scale:1.03" in script
    assert "scale:1.02" in script
    for ease in ("power3.out", "expo.out", "back.out(1.2)"):
        assert ease in script


def test_scene_markup_preserves_v1_header_and_separates_v2_motion_targets() -> None:
    scene = SimpleNamespace(
        id="scene-01", layout_variant="standard", title_lines=("One",),
        subtitle_lines=(), notes=(), chapter="01", progress="1/1", kicker="K",
        visual_asset=None,
    )
    timing = SimpleNamespace(id="scene-01", start=0.0, end=4.0)
    timeline = SimpleNamespace(scenes=(timing,))

    legacy = _scene_html(
        SimpleNamespace(scenes=(scene,), visual_system="xiaohei-white-first-v1"),
        timeline,
        {"scene-01": None},
    )
    assert '<div class="chapter">01<span class="progress">1/1</span></div>' in legacy
    assert 'id="scene-01--progress"' not in legacy

    motion = _scene_html(
        SimpleNamespace(scenes=(scene,), visual_system="editorial-motion-v2"),
        timeline,
        {"scene-01": None},
    )
    assert 'id="scene-01--chapter"' in motion
    assert 'id="scene-01--progress"' in motion
    assert 'class="progress motion-component"' in motion


def test_motion_v2_progress_uses_a_non_overlapping_right_header_zone() -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / "video-content-template"
        / "index.template.html"
    ).read_text(encoding="utf-8")

    assert ".motion-v2 > .progress { position: absolute; right: 104px;" in template
    assert ".motion-v2 > .progress { position: absolute; left: 204px;" not in template


def test_multi_scene_motion_uses_push_transitions_without_prefade() -> None:
    scenes = (
        SimpleNamespace(id="scene-01", start=0.0, end=4.0),
        SimpleNamespace(id="scene-02", start=4.0, end=8.0),
    )
    timeline = SimpleNamespace(scenes=scenes)

    script = _timeline_script(timeline, (), None)

    assert "x:-1920" in script
    assert "opacity:1,x:1920" in script
    assert "power3.inOut" in script
    assert "s.end-d" not in script


def test_v3_scene_markup_renders_raster_labels_and_type_led_without_placeholder() -> None:
    raster = SimpleNamespace(
        id="scene-01",
        layout_variant="standard",
        title_lines=("模型只是部分",),
        subtitle_lines=(),
        notes=(),
        chapter="结构",
        progress="01",
        kicker="证据",
        visual_asset="工程/assets/semantic-handdrawn/scene-01.png",
        visual_mode="human-action",
        overlay_labels=("模型", "工具&权限"),
    )
    type_led = SimpleNamespace(
        id="scene-02",
        layout_variant="wide-visual",
        title_lines=("可回查也复杂",),
        subtitle_lines=(),
        notes=(),
        chapter="对比",
        progress="02",
        kicker="取舍",
        visual_asset=None,
        visual_mode="type-led",
        overlay_labels=("可回查", "太复杂"),
    )
    timeline = SimpleNamespace(
        scenes=(
            SimpleNamespace(id="scene-01", start=0.0, end=4.0),
            SimpleNamespace(id="scene-02", start=4.0, end=8.0),
        )
    )

    markup = _scene_html(
        SimpleNamespace(
            scenes=(raster, type_led), visual_system="semantic-handdrawn-v3"
        ),
        timeline,
        {"scene-01": "scene-01.png", "scene-02": None},
        type_led_contracts={
            "scene-02": {
                "layout": "comparison",
                "labels": ["可回查", "太复杂"],
                "connectors": ["path", "divider"],
            }
        },
    )

    assert "motion-v3" in markup
    assert "visual-system-v3" in markup
    assert 'src="assets/semantic-handdrawn/scene-01.png"' in markup
    assert 'id="scene-01--overlay-label-1"' in markup
    assert "工具&amp;权限" in markup
    assert 'class="type-led-layout type-led-comparison"' in markup
    assert 'data-connector="path"' in markup
    assert 'id="scene-02--overlay-label-2"' in markup
    assert "visual-placeholder" not in markup


@pytest.mark.parametrize(
    "theme_id",
    [
        "vivid-comic-explainer",
        "engineering-sketch-explainer",
        "four-panel-comic-explainer",
        "blue-black-whiteboard-explainer",
    ],
)
def test_profiled_scene_markup_binds_selected_theme(theme_id: str) -> None:
    scene = SimpleNamespace(
        id="scene-01",
        layout_variant="standard",
        title_lines=("任务变化",),
        subtitle_lines=(),
        notes=(),
        chapter="结构",
        progress="01",
        kicker="证据",
        visual_asset=(
            f"工程/assets/profiled-illustrations/{theme_id}/scene-01.png"
        ),
        visual_mode="human-action",
        overlay_labels=("可回查",),
    )
    timeline = SimpleNamespace(
        scenes=(SimpleNamespace(id="scene-01", start=0.0, end=4.0),)
    )

    markup = _scene_html(
        SimpleNamespace(
            scenes=(scene,),
            visual_system="profiled-illustration-v4",
            visual_theme=theme_id,
        ),
        timeline,
        {"scene-01": "scene-01.png"},
    )

    assert 'data-visual-system="profiled-illustration-v4"' in markup
    assert f'data-visual-theme="{theme_id}"' in markup
    assert f'data-motion-hint="' in markup
    assert (
        f'src="assets/profiled-illustrations/{theme_id}/scene-01.png"' in markup
    )


def test_profiled_timeline_consumes_theme_visual_effect() -> None:
    timeline = SimpleNamespace(
        scenes=(SimpleNamespace(id="scene-01", start=0.0, end=4.0),)
    )
    motion = MotionPlan(
        1,
        "profiled-illustration-v4",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        4.0,
        (
            SceneMotion(
                "scene-01",
                0.0,
                4.0,
                (MotionEntry("visual", 0.8, 0.4, "line-reveal"),),
                (),
                None,
            ),
        ),
    )

    script = _timeline_script(timeline, (), motion)

    assert "clipPath" not in script
    assert "x:-28" in script
    assert "expo.out" in script


def test_v3_template_has_deterministic_overlay_and_type_led_layouts() -> None:
    template = (
        Path(__file__).resolve().parents[1]
        / "video-content-template"
        / "index.template.html"
    ).read_text(encoding="utf-8")

    assert ".motion-v3 > .progress" in template
    assert ".visual-system-v3 .visual-frame" in template
    assert ".visual-overlay-label" in template
    assert ".type-led-layout" in template
    assert ".type-led-comparison" in template
    assert ".type-led-connector[data-connector=\"path\"]" in template
    assert "bottom: 150px" in template

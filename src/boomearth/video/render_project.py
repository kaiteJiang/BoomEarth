"""Assemble an immutable, offline content-driven HyperFrames snapshot."""

from __future__ import annotations

import ctypes
import html
import json
import math
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boomearth.video.artifacts import (
    ArtifactError,
    FileSnapshot,
    capture_regular_file,
    load_json_snapshot,
    snapshot_matches,
)
from boomearth.video.content_plan import ContentPlanError, load_content_plan_snapshot
from boomearth.video.scene_timeline import SceneTimelineError, validate_scene_timeline
from boomearth.video.motion_plan import (
    MotionEntry,
    MotionPlan,
    MotionPlanError,
    SceneMotion,
    load_motion_plan_snapshot,
)
from boomearth.video.illustration_manifest import (
    IllustrationManifestError,
    SemanticIllustrationAsset,
    load_illustration_manifest_snapshot,
)
from boomearth.video.illustration_themes import (
    PROFILED_VISUAL_SYSTEM,
    IllustrationThemeError,
    get_theme,
)


_CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)
_TOKENS = (
    "__DURATION_SECONDS__",
    "__BACKGROUND_BLOCK__",
    "__SCENE_BLOCKS__",
    "__CAPTION_BLOCKS__",
    "__TIMELINE_SCRIPT__",
)
_TOKEN_PATTERN = re.compile(r"__[A-Z0-9_]+__")
_CAPTION_FIELDS = {"start", "end", "text", "source"}
_V5_BACKGROUND_SOURCE = Path(
    ".agents/skills/ra-video-production-director/assets/perspective-grid-v5/"
    "perspective-grid-v5-loop.mp4"
)
_V5_BACKGROUND_OUTPUT = Path(
    "assets/perspective-grid-v5/perspective-grid-v5-loop.mp4"
)
_V5_BACKGROUND_SHA256 = (
    "4aa1d98d5a00d4ce0039e8ec71cdae6fe3ecccfedcfa777ddf583df0489ad4cf"
)
_THEME_MOTION_HINT = {
    "vivid-comic-explainer": "character-push",
    "engineering-sketch-explainer": "path-reveal",
    "four-panel-comic-explainer": "whole-frame-push",
    "blue-black-whiteboard-explainer": "path-reveal",
    "xiaohuang-warm-first-v1": "character-push",
}


class ContentRenderProjectError(RuntimeError):
    """A fixed, redacted offline render-project failure."""


@dataclass(frozen=True, slots=True)
class PreparedContentProject:
    output_dir: Path
    duration_seconds: float
    scene_midpoints: tuple[float, ...]


def _after_inputs_captured(_snapshots: tuple[FileSnapshot, ...]) -> None:
    """Testing seam after all source bytes have been captured."""


def _strict_json(snapshot: FileSnapshot, *, error: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ContentRenderProjectError(error)
            value[key] = item
        return value

    try:
        return json.loads(
            snapshot.payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ContentRenderProjectError(error)
            ),
        )
    except ContentRenderProjectError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ContentRenderProjectError(error) from None


def _ordinary_directory(path: Path) -> bool:
    try:
        entry = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(entry.st_mode) and not path.is_symlink() and not bool(
        int(getattr(entry, "st_file_attributes", 0)) & 0x0400
    )


def _ordinary_directory_tree(path: Path) -> bool:
    current = Path(path).absolute()
    while True:
        if not _ordinary_directory(current):
            return False
        if current == current.parent:
            return True
        current = current.parent


def _captions(snapshot: FileSnapshot, *, duration: float) -> tuple[dict[str, object], ...]:
    value = _strict_json(snapshot, error="render captions are invalid")
    if not isinstance(value, list) or not value:
        raise ContentRenderProjectError("render captions are invalid")
    result: list[dict[str, object]] = []
    previous_start = -1.0
    for item in value:
        if not isinstance(item, dict) or set(item) != _CAPTION_FIELDS:
            raise ContentRenderProjectError("render captions are invalid")
        start = item["start"]
        end = item["end"]
        text = item["text"]
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < 0.0
            or float(end) <= float(start)
            or float(end) > duration + 0.05
            or float(start) < previous_start
            or not isinstance(text, str)
            or not text.strip()
            or item["source"] != "volcengine-word-timestamps"
        ):
            raise ContentRenderProjectError("render captions are invalid")
        previous_start = float(start)
        result.append({"start": float(start), "end": float(end), "text": text})
    return tuple(result)


def _caption_qc(snapshot: FileSnapshot, *, narration_sha256: str) -> None:
    value = _strict_json(snapshot, error="render captions are invalid")
    if not isinstance(value, dict):
        raise ContentRenderProjectError("render captions are invalid")
    coverage = value.get("alignment_coverage")
    if (
        value.get("status") != "pass"
        or value.get("timing_source") != "volcengine-word-timestamps"
        or value.get("narration_sha256") != narration_sha256
        or isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or not 0.90 <= float(coverage) <= 1.0
    ):
        raise ContentRenderProjectError("render captions are invalid")


def _scene_html(
    plan,
    timeline,
    asset_names: dict[str, str | None],
    *,
    type_led_contracts: dict[str, dict[str, object]] | None = None,
    xiaohei_motion: bool = False,
) -> str:
    blocks: list[str] = []
    motion_v2 = getattr(plan, "visual_system", "") == "editorial-motion-v2"
    motion_v3 = getattr(plan, "visual_system", "") == "semantic-handdrawn-v3"
    motion_v4 = getattr(plan, "visual_system", "") == PROFILED_VISUAL_SYSTEM
    theme_id: str | None = None
    motion_hint: str | None = None
    native_text_theme = False
    if motion_v4:
        try:
            theme = get_theme(getattr(plan, "visual_theme", None))
        except IllustrationThemeError:
            raise ContentRenderProjectError("render inputs are invalid") from None
        theme_id = theme.id
        motion_hint = _THEME_MOTION_HINT[theme.id]
        native_text_theme = "native_labels_correct" in theme.required_qc
    motion_enabled = motion_v2 or motion_v3 or motion_v4 or xiaohei_motion
    component_class = " motion-component" if motion_enabled else ""
    hidden = ' style="opacity:0"' if motion_enabled else ""
    type_led_contracts = type_led_contracts or {}
    for scene, timing in zip(plan.scenes, timeline.scenes):
        title = "".join(
            f'<span id="{scene.id}--title-line-{index}" class="title-line{component_class}" '
            f'data-motion-target="title-line-{index}"{hidden}>{html.escape(line, quote=True)}</span>'
            for index, line in enumerate(scene.title_lines, 1)
        )
        subtitle = "".join(
            f'<span id="{scene.id}--subtitle-line-{index}" class="subtitle-line{component_class}" '
            f'data-motion-target="subtitle-line-{index}"{hidden}>{html.escape(line, quote=True)}</span>'
            for index, line in enumerate(scene.subtitle_lines, 1)
        )
        notes = "".join(
            f'<div id="{scene.id}--note-row-{index}" class="note-row{component_class}" '
            f'data-motion-target="note-row-{index}"{hidden}><span class="note-label">'
            f"{html.escape(note.label, quote=True)}</span><span>"
            f"{html.escape(note.text, quote=True)}</span></div>"
            for index, note in enumerate(scene.notes, 1)
        )
        asset_name = asset_names[scene.id]
        asset_root = (
            f"profiled-illustrations/{theme_id}"
            if motion_v4
            else "semantic-handdrawn"
            if motion_v3
            else "editorial-illustrations"
            if motion_v2
            else "xiaohei-illustrations"
        )
        labels = tuple(getattr(scene, "overlay_labels", ()))
        illustration_text_mode = str(
            getattr(scene, "illustration_text_mode", "legacy-overlay")
        )
        if native_text_theme:
            illustration_text_mode = "embedded"
        if xiaohei_motion and illustration_text_mode not in {
            "embedded",
            "local-fallback",
            "legacy-overlay",
        }:
            raise ContentRenderProjectError("xiaohei text mode is invalid")
        if xiaohei_motion and not labels:
            raise ContentRenderProjectError("xiaohei text layer is required")
        visual_mode = getattr(scene, "visual_mode", None)
        if motion_v3 and visual_mode == "type-led":
            contract = type_led_contracts.get(scene.id)
            if not isinstance(contract, dict):
                raise ContentRenderProjectError("render inputs are invalid")
            contract_labels = contract.get("labels")
            layout = contract.get("layout")
            connectors = contract.get("connectors")
            if (
                contract_labels != list(labels)
                or layout not in {"comparison", "flow", "stack", "quadrant"}
                or not isinstance(connectors, list)
            ):
                raise ContentRenderProjectError("render inputs are invalid")
            label_markup = "".join(
                f'<span id="{scene.id}--overlay-label-{index}" '
                f'class="type-led-label{component_class}" '
                f'data-motion-target="overlay-label-{index}"{hidden}>'
                f'{html.escape(str(label), quote=True)}</span>'
                for index, label in enumerate(labels, 1)
            )
            connector_markup = "".join(
                f'<span class="type-led-connector" data-connector="{html.escape(str(connector), quote=True)}"></span>'
                for connector in connectors
            )
            visual = (
                f'<div class="type-led-layout type-led-{html.escape(str(layout), quote=True)}">'
                f"{label_markup}{connector_markup}</div>"
            )
        elif asset_name is not None:
            if (
                xiaohei_motion and illustration_text_mode == "embedded"
            ) or native_text_theme:
                visual = (
                    f'<img src="assets/{asset_root}/{html.escape(asset_name, quote=True)}" '
                    f'class="xiaohei-art-native" alt="" />'
                )
            else:
                label_class = (
                    "xiaohei-fallback-label"
                    if xiaohei_motion and illustration_text_mode == "local-fallback"
                    else "visual-overlay-label"
                )
                container_class = (
                    "xiaohei-fallback-labels"
                    if xiaohei_motion and illustration_text_mode == "local-fallback"
                    else "visual-overlay-labels"
                )
                label_markup = "".join(
                    f'<span id="{scene.id}--overlay-label-{index}" '
                    f'class="{label_class}{component_class}" '
                    f'data-motion-target="overlay-label-{index}"{hidden}>'
                    f'{html.escape(str(label), quote=True)}</span>'
                    for index, label in enumerate(labels, 1)
                )
                visual = (
                    f'<img src="assets/{asset_root}/{html.escape(asset_name, quote=True)}" alt="" />'
                    f'<div class="{container_class}">{label_markup}</div>'
                )
        else:
            visual = '<div class="visual-placeholder">留白场景</div>'
        header = (
            f'<div id="{scene.id}--chapter" class="chapter{component_class}" '
            f'data-motion-target="chapter"{hidden}>{html.escape(scene.chapter, quote=True)}</div>'
            f'<span id="{scene.id}--progress" class="progress{component_class}" '
            f'data-motion-target="progress"{hidden}>{html.escape(scene.progress, quote=True)}</span>'
            if motion_enabled
            else f'<div class="chapter">{html.escape(scene.chapter, quote=True)}'
            f'<span class="progress">{html.escape(scene.progress, quote=True)}</span></div>'
        )
        profiled_attributes = (
            f' data-visual-system="{PROFILED_VISUAL_SYSTEM}"'
            f' data-visual-theme="{theme_id}"'
            f' data-motion-hint="{motion_hint}"'
            if motion_v4
            else ""
        )
        blocks.append(
            f'<section id="{scene.id}" class="scene layout-{scene.layout_variant}'
            f'{" xiaohei-native-text" if xiaohei_motion and illustration_text_mode == "embedded" else ""}'
            f'{" xiaohuang-native-text" if native_text_theme else ""}'
            f'{" xiaohei-local-fallback" if xiaohei_motion and illustration_text_mode == "local-fallback" else ""}'
            f'{" motion-v2" if motion_v2 else ""}'
            f'{" motion-v3 visual-system-v3 mode-" + str(visual_mode) if motion_v3 else ""}'
            f'{" motion-v4 visual-system-v4 theme-" + str(theme_id) if motion_v4 else ""}" '
            f'data-scene-start="{timing.start:.3f}" data-scene-end="{timing.end:.3f}"'
            f'{profiled_attributes}>'
            f'{header}'
            f'<h1 class="title">{title}</h1><p class="subtitle">{subtitle}</p>'
            f'<div id="{scene.id}--note-card" class="note-card{component_class}" data-motion-target="note-card"{hidden}>'
            f'<div id="{scene.id}--kicker" class="kicker{component_class}" data-motion-target="kicker"{hidden}>'
            f'{html.escape(scene.kicker, quote=True)}</div>{notes}</div>'
            f'<div id="{scene.id}--visual" class="visual-frame{component_class}" data-motion-target="visual" '
            f'data-illustration-text-mode="{html.escape(illustration_text_mode, quote=True)}" '
            f'data-visual-mode="{html.escape(str(visual_mode or "raster"), quote=True)}"{hidden}>{visual}</div></section>'
        )
    return "\n".join(blocks)


def _caption_html(captions: tuple[dict[str, object], ...]) -> str:
    return "\n".join(
        f'<div id="caption-{index}" class="caption" data-caption-start="{caption["start"]:.3f}" '
        f'data-caption-end="{caption["end"]:.3f}"><span class="caption-panel">'
        f'{html.escape(str(caption["text"]), quote=True)}</span></div>'
        for index, caption in enumerate(captions)
    )


def _background_html(*, enabled: bool, duration: float) -> str:
    if not enabled:
        return ""
    return (
        '<video id="perspective-grid-v5" class="perspective-grid-video" '
        f'src="{_V5_BACKGROUND_OUTPUT.as_posix()}" data-start="0" '
        f'data-duration="{duration:.6f}" data-track-index="0" '
        'muted playsinline loop aria-hidden="true"></video>'
    )


def _type_led_contract_map(illustration_snapshot) -> dict[str, dict[str, object]]:
    if illustration_snapshot is None:
        return {}
    result: dict[str, dict[str, object]] = {}
    for item, snapshot in zip(
        illustration_snapshot.manifest.assets,
        illustration_snapshot.prompt_snapshots,
    ):
        if not isinstance(item, SemanticIllustrationAsset) or item.visual_mode != "type-led":
            continue
        value = _strict_json(snapshot, error="render inputs are invalid")
        if not isinstance(value, dict):
            raise ContentRenderProjectError("render inputs are invalid")
        result[item.scene_id] = {
            "labels": value.get("labels"),
            "layout": value.get("layout"),
            "connectors": value.get("connectors"),
        }
    return result


def _timeline_script(
    timeline,
    captions: tuple[dict[str, object], ...],
    motion_plan: MotionPlan | None = None,
    *,
    component_scenes: tuple[SceneMotion, ...] = (),
    stable_page_base: bool = False,
) -> str:
    scenes = [
        {"id": scene.id, "start": scene.start, "end": scene.end}
        for scene in timeline.scenes
    ]
    cues = [
        {"id": f"caption-{index}", "start": cue["start"], "end": cue["end"]}
        for index, cue in enumerate(captions)
    ]
    scene_json = json.dumps(scenes, ensure_ascii=True, separators=(",", ":"))
    cue_json = json.dumps(cues, ensure_ascii=True, separators=(",", ":"))
    if stable_page_base:
        base = (
            "window.__timelines=window.__timelines||{};"
            f"const scenes={scene_json};const cues={cue_json};"
            "const tl=gsap.timeline({paused:true});"
            "scenes.forEach(function(s,i){const el=document.getElementById(s.id);"
            "if(i===0){tl.set(el,{opacity:1,x:0},0);return;}"
            "const prev=scenes[i-1];const old=document.getElementById(prev.id);"
            "const at=Math.max(prev.start,s.start-0.3);"
            "tl.set(el,{opacity:0,x:0},0);"
            "tl.to(old,{opacity:0,duration:0.3,ease:'power3.inOut'},at);"
            "tl.to(el,{opacity:1,duration:0.3,ease:'power3.inOut'},at);"
            "tl.set(old,{opacity:0,x:0},s.start+0.0001);});"
        )
    else:
        base = (
            "window.__timelines=window.__timelines||{};"
            f"const scenes={scene_json};const cues={cue_json};"
            "const tl=gsap.timeline({paused:true});"
            "scenes.forEach(function(s,i){const el=document.getElementById(s.id);"
            "if(i===0){tl.set(el,{opacity:1,x:0},0);return;}"
            "const prev=scenes[i-1];const old=document.getElementById(prev.id);"
            "const d=Math.min(0.45,Math.max(0.3,(s.start-prev.start)*0.08));"
            "const at=Math.max(prev.start,s.start-d);"
            "tl.set(el,{opacity:1,x:1920},at);"
            "tl.to(old,{x:-1920,duration:d,ease:'power3.inOut'},at);"
            "tl.to(el,{x:0,duration:d,ease:'power3.inOut'},at);"
            "tl.set(old,{opacity:0,x:0},s.start+0.0001);});"
        )
    motions = ""
    motion_scenes = motion_plan.scenes if motion_plan is not None else component_scenes
    if motion_scenes:
        effect_values = {
            "fade-down": ("{opacity:0,y:-18}", "{opacity:1,y:0}", "power3.out"),
            "fade-up": ("{opacity:0,y:18}", "{opacity:1,y:0}", "sine.out"),
            "line-reveal": ("{opacity:0,x:-28}", "{opacity:1,x:0}", "expo.out"),
            "scale-settle": ("{opacity:0,scale:0.97}", "{opacity:1,scale:1}", "back.out(1.2)"),
            "wipe-right": ("{opacity:0,x:-36}", "{opacity:1,x:0}", "power2.out"),
        }
        for scene_index, scene in enumerate(motion_scenes):
            for entry in scene.entries:
                before, after, ease = effect_values[entry.motion]
                element_id = json.dumps(f"{scene.scene_id}--{entry.target}")
                motions += (
                    f"tl.fromTo(document.getElementById({element_id}),{before},"
                    f"{{...{after},duration:{entry.duration:.3f},ease:'{ease}'}},{entry.at:.3f});"
                )
            for cue in scene.semantic_cues:
                element_id = json.dumps(f"{scene.scene_id}--{cue.target}")
                motions += (
                    f"tl.to(document.getElementById({element_id}),{{scale:1.03,duration:{cue.duration / 2:.3f},ease:'sine.out'}},{cue.at:.3f});"
                    f"tl.to(document.getElementById({element_id}),{{scale:1,duration:{cue.duration / 2:.3f},ease:'sine.in'}},{cue.at + cue.duration / 2:.3f});"
                )
            if scene.ambient is not None:
                direction = -1 if scene_index % 2 else 1
                distance = round(24 * scene.ambient.strength * direction, 3)
                element_id = json.dumps(f"{scene.scene_id}--visual")
                motions += (
                    f"tl.to(document.getElementById({element_id}),{{x:{distance},scale:1.02,duration:{scene.ambient.end - scene.ambient.start:.3f},ease:'none'}},{scene.ambient.start:.3f});"
                )
    return (
        base
        + motions
        + "cues.forEach(function(c){const el=document.getElementById(c.id);tl.set(el,{opacity:1},c.start);tl.set(el,{opacity:0},c.end+0.0001);});"
        + "window.__timelines['boomearth-content-production']=tl;"
    )


def _xiaohei_component_scenes(plan, timeline) -> tuple[SceneMotion, ...]:
    scenes: list[SceneMotion] = []
    for scene, timing in zip(plan.scenes, timeline.scenes):
        start = float(timing.start)
        end = float(timing.end)
        entries: list[MotionEntry] = [
            MotionEntry("chapter", start + 0.05, 0.24, "fade-down"),
            MotionEntry("visual", start + 0.38, 0.42, "scale-settle"),
            MotionEntry("note-card", start + 0.66, 0.32, "wipe-right"),
            MotionEntry("kicker", start + 0.74, 0.24, "fade-up"),
        ]
        entries.extend(
            MotionEntry(
                f"title-line-{index}",
                start + 0.14 + (index - 1) * 0.10,
                0.32,
                "line-reveal",
            )
            for index, _line in enumerate(scene.title_lines, 1)
        )
        entries.extend(
            MotionEntry(
                f"subtitle-line-{index}",
                start + 0.34 + (index - 1) * 0.10,
                0.28,
                "fade-up",
            )
            for index, _line in enumerate(scene.subtitle_lines, 1)
        )
        entries.extend(
            MotionEntry(
                f"note-row-{index}",
                start + 0.86 + (index - 1) * 0.12,
                0.26,
                "fade-up",
            )
            for index, _note in enumerate(scene.notes, 1)
        )
        if getattr(scene, "illustration_text_mode", "legacy-overlay") != "embedded":
            entries.extend(
                MotionEntry(
                    f"overlay-label-{index}",
                    start + 1.12 + (index - 1) * 0.16,
                    0.24,
                    "fade-up",
                )
                for index, _label in enumerate(scene.overlay_labels, 1)
            )
        scenes.append(
            SceneMotion(
                scene.id,
                start,
                end,
                tuple(entries),
                (),
                None,
            )
        )
    return tuple(scenes)


def _render_template(
    snapshot: FileSnapshot,
    *,
    duration: float,
    background: str,
    scenes: str,
    captions: str,
    timeline_script: str,
    body_attributes: str = "",
) -> bytes:
    try:
        template = snapshot.payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ContentRenderProjectError("render template is invalid") from None
    found = _TOKEN_PATTERN.findall(template)
    if set(found) != set(_TOKENS) or any(template.count(token) != 1 for token in _TOKENS):
        raise ContentRenderProjectError("render template is invalid")
    replacements = {
        "__DURATION_SECONDS__": f"{duration:.6f}",
        "__BACKGROUND_BLOCK__": background,
        "__SCENE_BLOCKS__": scenes,
        "__CAPTION_BLOCKS__": captions,
        "__TIMELINE_SCRIPT__": timeline_script,
    }
    rendered = template
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    if _TOKEN_PATTERN.search(rendered):
        raise ContentRenderProjectError("render template is invalid")
    if rendered.count("<body>") != 1:
        raise ContentRenderProjectError("render template is invalid")
    body_tag = f"<body {body_attributes}>" if body_attributes else "<body>"
    rendered = rendered.replace("<body>", body_tag)
    return rendered.encode("utf-8")


def _profiled_body_attributes(plan) -> str:
    if getattr(plan, "visual_system", "") != PROFILED_VISUAL_SYSTEM:
        return ""
    try:
        theme = get_theme(getattr(plan, "visual_theme", None))
    except IllustrationThemeError:
        raise ContentRenderProjectError("render inputs are invalid") from None
    hint = _THEME_MOTION_HINT[theme.id]
    return (
        f'class="visual-system-v4 theme-{theme.id}" '
        f'data-visual-system="{PROFILED_VISUAL_SYSTEM}" '
        f'data-visual-theme="{theme.id}" data-motion-hint="{hint}"'
    )


def _publish_directory_no_replace(stage: Path, target: Path) -> None:
    if os.name == "nt":
        move_file = ctypes.windll.kernel32.MoveFileW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        move_file.restype = ctypes.c_int
        if not move_file(str(stage), str(target)):
            raise OSError("no-replace publication failed")
        return
    if target.exists():
        raise OSError("no-replace publication failed")
    os.rename(stage, target)


def prepare_content_render_project(
    *, project_root: Path, output_dir: Path, repo_root: Path
) -> PreparedContentProject:
    """Capture all formal inputs and atomically publish one offline renderer snapshot."""

    root = Path(project_root).absolute()
    output = Path(output_dir).absolute()
    repository = Path(repo_root).absolute()
    if (
        output != root / "工程" / "render-project"
        or output.exists()
        or not _ordinary_directory_tree(root)
        or not _ordinary_directory_tree(output.parent)
    ):
        raise ContentRenderProjectError("render target is unavailable")
    stage: Path | None = None
    profiled_theme = None
    try:
        plan_snapshot = load_content_plan_snapshot(project_root=root)
        if plan_snapshot.plan.visual_system == PROFILED_VISUAL_SYSTEM:
            try:
                profiled_theme = get_theme(plan_snapshot.plan.visual_theme)
                profiled_root = (
                    root / "工程" / "assets" / "profiled-illustrations"
                )
                entries = tuple(profiled_root.iterdir())
            except (IllustrationThemeError, OSError):
                raise ContentRenderProjectError("render inputs are invalid") from None
            if (
                len(entries) != 1
                or entries[0].name != profiled_theme.directory
                or not entries[0].is_dir()
            ):
                raise ContentRenderProjectError("render inputs are invalid")
        motion_enabled = plan_snapshot.plan.visual_system in {
            "editorial-motion-v2",
            "semantic-handdrawn-v3",
            PROFILED_VISUAL_SYSTEM,
        }
        motion_snapshot = (
            load_motion_plan_snapshot(root)
            if motion_enabled
            else None
        )
        illustration_snapshot = (
            load_illustration_manifest_snapshot(root)
            if plan_snapshot.plan.visual_system
            in {"editorial-motion-v2", "semantic-handdrawn-v3", PROFILED_VISUAL_SYSTEM}
            else None
        )
        timeline_value, timeline_snapshot = load_json_snapshot(
            root / "工程" / "scene-timeline.json", within=root
        )
        timeline = validate_scene_timeline(timeline_value, project_root=root)
        narration = capture_regular_file(
            root / "工程" / "media" / "narration.wav", within=root
        )
        manifest = capture_regular_file(
            root / "工程" / "media" / "voice_manifest.json", within=root
        )
        caption_snapshots = {
            name: capture_regular_file(
                root / "工程" / "media" / "captions" / name, within=root
            )
            for name in _CAPTION_FILES
        }
        template = capture_regular_file(
            repository / "video-content-template" / "index.template.html"
        )
        gsap = capture_regular_file(
            repository / "node_modules" / "gsap" / "dist" / "gsap.min.js"
        )
        v5_background = (
            capture_regular_file(repository / _V5_BACKGROUND_SOURCE, within=repository)
            if motion_enabled
            else None
        )
        if (
            v5_background is not None
            and v5_background.sha256 != _V5_BACKGROUND_SHA256
        ):
            raise ContentRenderProjectError("render inputs are invalid")
    except (
        ArtifactError,
        ContentPlanError,
        SceneTimelineError,
        MotionPlanError,
        IllustrationManifestError,
    ):
        raise ContentRenderProjectError("render inputs are invalid") from None
    captions = _captions(
        caption_snapshots["captions.json"], duration=timeline.duration_seconds
    )
    _caption_qc(
        caption_snapshots["caption-qc.json"], narration_sha256=narration.sha256
    )
    asset_snapshots: dict[str, FileSnapshot] = {}
    asset_names: dict[str, str | None] = {}
    try:
        for scene in plan_snapshot.plan.scenes:
            if scene.visual_asset is None:
                asset_names[scene.id] = None
                continue
            source = root / Path(*scene.visual_asset.split("/"))
            captured = capture_regular_file(source, within=root)
            asset_snapshots[scene.id] = captured
            asset_names[scene.id] = f"{scene.id}{source.suffix.lower()}"
    except ArtifactError:
        raise ContentRenderProjectError("render inputs are invalid") from None
    snapshots = (
        plan_snapshot.snapshot,
        plan_snapshot.handoff_snapshot,
        plan_snapshot.manifest_snapshot,
        plan_snapshot.narration_contract_snapshot,
        *((motion_snapshot.snapshot,) if motion_snapshot is not None else ()),
        *((
            illustration_snapshot.snapshot,
            *illustration_snapshot.prompt_snapshots,
            *illustration_snapshot.asset_snapshots,
            *illustration_snapshot.candidate_snapshots,
            *((illustration_snapshot.semantic_qc_snapshot,) if illustration_snapshot.semantic_qc_snapshot is not None else ()),
        ) if illustration_snapshot is not None else ()),
        timeline_snapshot,
        narration,
        manifest,
        *caption_snapshots.values(),
        *asset_snapshots.values(),
        template,
        gsap,
        *((v5_background,) if v5_background is not None else ()),
    )
    _after_inputs_captured(snapshots)
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        raise ContentRenderProjectError("render input changed")
    xiaohei_motion = plan_snapshot.plan.visual_system == "xiaohei-white-first-v1"
    rendered = _render_template(
        template,
        duration=timeline.duration_seconds,
        background=_background_html(
            enabled=v5_background is not None,
            duration=timeline.duration_seconds,
        ),
        scenes=_scene_html(
            plan_snapshot.plan,
            timeline,
            asset_names,
            type_led_contracts=_type_led_contract_map(illustration_snapshot),
            xiaohei_motion=xiaohei_motion,
        ),
        captions=_caption_html(captions),
        timeline_script=_timeline_script(
            timeline,
            captions,
            motion_snapshot.plan if motion_snapshot is not None else None,
            component_scenes=(
                _xiaohei_component_scenes(plan_snapshot.plan, timeline)
                if xiaohei_motion
                else ()
            ),
            stable_page_base=xiaohei_motion,
        ),
        body_attributes=_profiled_body_attributes(plan_snapshot.plan),
    )
    try:
        stage = output.parent / f".render-project-{uuid.uuid4().hex}.tmp"
        stage.mkdir()
        (stage / "media" / "captions").mkdir(parents=True)
        if plan_snapshot.plan.visual_system == PROFILED_VISUAL_SYSTEM:
            if profiled_theme is None:
                raise ContentRenderProjectError("render inputs are invalid") from None
            asset_dir = Path("profiled-illustrations") / profiled_theme.directory
        else:
            asset_dir = Path(
                "semantic-handdrawn"
                if plan_snapshot.plan.visual_system == "semantic-handdrawn-v3"
                else "editorial-illustrations"
                if plan_snapshot.plan.visual_system == "editorial-motion-v2"
                else "xiaohei-illustrations"
            )
        (stage / "assets" / asset_dir).mkdir(parents=True)
        (stage / "node_modules" / "gsap" / "dist").mkdir(parents=True)
        if v5_background is not None:
            (stage / _V5_BACKGROUND_OUTPUT.parent).mkdir(parents=True)
        (stage / "index.html").write_bytes(rendered)
        (stage / "media" / "narration.wav").write_bytes(narration.payload)
        (stage / "media" / "voice_manifest.json").write_bytes(manifest.payload)
        for name, snapshot in caption_snapshots.items():
            (stage / "media" / "captions" / name).write_bytes(snapshot.payload)
        for scene_id, snapshot in asset_snapshots.items():
            name = asset_names[scene_id]
            assert name is not None
            (stage / "assets" / asset_dir / name).write_bytes(snapshot.payload)
        if motion_snapshot is not None:
            (stage / "motion-plan.json").write_bytes(motion_snapshot.snapshot.payload)
        if illustration_snapshot is not None:
            (stage / "illustration-manifest.json").write_bytes(
                illustration_snapshot.snapshot.payload
            )
        (stage / "node_modules" / "gsap" / "dist" / "gsap.min.js").write_bytes(
            gsap.payload
        )
        if v5_background is not None:
            (stage / _V5_BACKGROUND_OUTPUT).write_bytes(v5_background.payload)
        if output.exists():
            raise ContentRenderProjectError("render target is unavailable")
        if not all(snapshot_matches(snapshot) for snapshot in snapshots):
            raise ContentRenderProjectError("render input changed")
        _publish_directory_no_replace(stage, output)
        stage = None
    except ContentRenderProjectError:
        raise
    except OSError:
        raise ContentRenderProjectError("render snapshot publication failed") from None
    finally:
        if stage is not None and stage.exists() and stage.parent == output.parent:
            shutil.rmtree(stage, ignore_errors=True)
    return PreparedContentProject(
        output_dir=output,
        duration_seconds=timeline.duration_seconds,
        scene_midpoints=tuple(
            round((scene.start + scene.end) / 2.0, 6) for scene in timeline.scenes
        ),
    )

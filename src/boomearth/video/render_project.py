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
from boomearth.video.scene_timeline import SceneTimelineError, _load_words, validate_scene_timeline
from boomearth.video.comic_semantic_parts import validate_comic_semantic_parts
from boomearth.video.sponge_components import (
    SpongeComponentError, SpongeCueReviewRequired, load_reviewed_cue_captions, resolve_cue_start, validate_drawing_plan,
)
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
    COMPONENT_DRAW_THEMES,
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
    "__SPONGE_DRAW_SCRIPT__",
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
_SPONGE_CURSOR_SOURCE = Path("video-content-template/assets/hand-pencil-cursor.png")
_SPONGE_REGION_DRAW_SOURCE = Path("video-content-template/assets/sponge-region-draw.js")
_SPONGE_COMPONENT_DRAW_SOURCE = Path("video-content-template/assets/sponge-component-draw.js")
_SPONGE_BRUSH_SOURCE = Path("video-content-template/assets/hand-paintbrush-cursor.png")
_THEME_MOTION_HINT = {
    "vivid-comic-component-brush-v1": "character-push",
    "sponge-host-handdrawn-v1": "character-push",
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
    native_sponge_scenes: frozenset[str] = frozenset(),
    sponge_text_layers: dict[str, dict[str, object]] | None = None,
    sponge_host_regions: dict[str, list[list[float]]] | None = None,
    sponge_drawing_plans: dict[str, dict[str, object]] | None = None,
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
    sponge_text_layers = sponge_text_layers or {}
    sponge_host_regions = sponge_host_regions or {}
    sponge_drawing_plans = sponge_drawing_plans or {}
    for scene, timing in zip(plan.scenes, timeline.scenes):
        text_layer = sponge_text_layers.get(scene.id, {})
        title_lines = tuple(str(text_layer["title"]).splitlines()) if text_layer else scene.title_lines
        title = "".join(
            f'<span id="{scene.id}--title-line-{index}" class="title-line{component_class}" '
            f'data-motion-target="title-line-{index}"{hidden}>{html.escape(line, quote=True)}</span>'
            for index, line in enumerate(title_lines, 1)
        )
        subtitle = "".join(
            f'<span id="{scene.id}--subtitle-line-{index}" class="subtitle-line{component_class}" '
            f'data-motion-target="subtitle-line-{index}"{hidden}>{html.escape(line, quote=True)}</span>'
            for index, line in enumerate(scene.subtitle_lines, 1)
        )
        note_items = (
            (("判断", str(text_layer["conclusion"])),)
            if text_layer else tuple((note.label, note.text) for note in scene.notes)
        )
        notes = "".join(
            f'<div id="{scene.id}--note-row-{index}" class="note-row{component_class}" '
            f'data-motion-target="note-row-{index}"{hidden}><span class="note-label">'
            f"{html.escape(label, quote=True)}</span><span>"
            f"{html.escape(value, quote=True)}</span></div>"
            for index, (label, value) in enumerate(note_items, 1)
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
            if scene.id in native_sponge_scenes:
                visual = (
                    f'<img id="{scene.id}--draw-source" class="sponge-draw-source" '
                    f'src="assets/{asset_root}/{html.escape(asset_name, quote=True)}" alt="" />'
                    f'<canvas id="{scene.id}--draw-canvas" class="sponge-draw-canvas"></canvas>'
                    f'<div id="{scene.id}--pencil-cursor" class="sponge-pencil-cursor">'
                    f'<img src="assets/hand-pencil-cursor.png" alt="" /></div>'
                )
            elif (
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
        chapter_text = html.escape(str(text_layer["header"]) if text_layer else scene.chapter, quote=True)
        header = (
            f'<div id="{scene.id}--chapter" class="chapter{component_class}" '
            f'data-motion-target="chapter"{hidden}>{chapter_text}</div>'
            f'<span id="{scene.id}--progress" class="progress{component_class}" '
            f'data-motion-target="progress"{hidden}>{html.escape(scene.progress, quote=True)}</span>'
            if motion_enabled
            else f'<div class="chapter">{chapter_text}'
            f'<span class="progress">{html.escape(scene.progress, quote=True)}</span></div>'
        )
        profiled_attributes = (
            f' data-visual-system="{PROFILED_VISUAL_SYSTEM}"'
            f' data-visual-theme="{theme_id}"'
            f' data-motion-hint="{motion_hint}"'
            if motion_v4
            else ""
        )
        if text_layer and "draw_regions" in text_layer:
            profiled_attributes += (
                f' data-draw-regions="{html.escape(json.dumps(text_layer["draw_regions"], separators=(",", ":")), quote=True)}"'
            )
        if scene.id in sponge_host_regions:
            profiled_attributes += (
                f' data-host-regions="{html.escape(json.dumps(sponge_host_regions[scene.id], separators=(",", ":")), quote=True)}"'
            )
        if scene.id in sponge_drawing_plans:
            profiled_attributes += (
                f' data-drawing-plan="{html.escape(json.dumps(sponge_drawing_plans[scene.id], separators=(",", ":")), quote=True)}"'
                f' data-text-effect="{html.escape(str(text_layer.get("effect", "reveal")), quote=True)}"'
            )
        sponge_card_data = (
            tuple(text_layer["cards"]) if text_layer else tuple(
                {"title": str(label), "detail": "", "cue": str(label)}
                for label in labels[:3]
            )
        )
        sponge_cards = (
            f'<div class="sponge-info-stack{" has-three" if len(sponge_card_data) == 3 else ""}">'
            + ('<span class="sponge-step-rail"><span class="sponge-step-progress"></span></span>'
               if scene.id in sponge_drawing_plans and text_layer.get("effect") == "steps" else "")
            + "".join(
                f'<div id="{scene.id}--info-card-{index}" class="sponge-info-card" '
                f'data-keyword="{html.escape(str(card["cue"]), quote=True)}"'
                f'{" data-cue-at=" + chr(34) + str(card["cue_at"]) + chr(34) if "cue_at" in card else ""}>'
                f'{"<span class=" + chr(34) + "card-marker" + chr(34) + "></span>" if scene.id in sponge_drawing_plans else ""}'
                f'<span class="card-number">{index:02d}</span>'
                f'<span class="card-copy"><strong>{html.escape(str(card["title"]), quote=True)}</strong>'
                f'{"<small>" + html.escape(str(card["detail"]), quote=True) + "</small>" if card["detail"] else ""}</span>'
                f'{"<span class=" + chr(34) + "card-check" + chr(34) + ">✓</span><span class=" + chr(34) + "card-underline" + chr(34) + "></span>" if scene.id in sponge_drawing_plans else ""}'
                f'<span class="card-indicator"></span></div>'
                for index, card in enumerate(sponge_card_data, 1)
            )
            + '</div>'
            if scene.id in native_sponge_scenes else ""
        )
        blocks.append(
            f'<section id="{scene.id}" class="scene layout-{scene.layout_variant}'
            f'{" xiaohei-native-text" if xiaohei_motion and illustration_text_mode == "embedded" else ""}'
            f'{" xiaohuang-native-text" if native_text_theme else ""}'
            f'{" sponge-native-source" if scene.id in native_sponge_scenes else ""}'
            f'{" sponge-components-v2" if scene.id in sponge_drawing_plans else ""}'
            f'{" sponge-three-cards" if scene.id in native_sponge_scenes and len(sponge_card_data) == 3 else ""}'
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
            f'{html.escape(str(text_layer["kicker"]) if text_layer else scene.kicker, quote=True)}</div>{notes}</div>'
            f'{sponge_cards}'
            f'<div id="{scene.id}--visual" class="visual-frame{component_class}" data-motion-target="visual" '
            f'data-illustration-text-mode="{html.escape(illustration_text_mode, quote=True)}" '
            f'data-visual-mode="{html.escape(str(visual_mode or "raster"), quote=True)}"{hidden}>{visual}</div></section>'
        )
    return "\n".join(blocks)


def _caption_emphasis(
    snapshot: FileSnapshot | None,
    *,
    captions_snapshot: FileSnapshot,
    captions: tuple[dict[str, object], ...],
) -> dict[int, str]:
    if snapshot is None:
        return {}
    value = _strict_json(snapshot, error="render caption emphasis is invalid")
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "captions_sha256", "highlights"}
        or isinstance(value["schema_version"], bool)
        or value["schema_version"] != 1
        or value["captions_sha256"] != captions_snapshot.sha256
        or not isinstance(value["highlights"], list)
    ):
        raise ContentRenderProjectError("render caption emphasis is invalid")
    result: dict[int, str] = {}
    for item in value["highlights"]:
        if not isinstance(item, dict) or set(item) != {"index", "text"}:
            raise ContentRenderProjectError("render caption emphasis is invalid")
        index, phrase = item["index"], item["text"]
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(captions)
            or index in result
            or not isinstance(phrase, str)
            or not phrase.strip()
            or len(phrase) > 12
            or str(captions[index]["text"]).count(phrase) != 1
        ):
            raise ContentRenderProjectError("render caption emphasis is invalid")
        result[index] = phrase
    return result


def _caption_html(
    captions: tuple[dict[str, object], ...],
    emphasis: dict[int, str] | None = None,
) -> str:
    emphasis = emphasis or {}
    def panel_style(value: str) -> str:
        visual_units = sum(
            1.0 if "\u3400" <= character <= "\u9fff"
            else 0.58 if character.isascii() and character.isalnum()
            else 0.35 if character.isspace()
            else 0.5
            for character in value
        )
        font_size = min(56, int(1400 / max(visual_units, 1)))
        if font_size < 42:
            raise ContentRenderProjectError("render caption exceeds one line")
        return f' style="font-size:{font_size}px"' if font_size < 56 else ""

    def styled_text(index: int, value: str) -> str:
        phrase = emphasis.get(index)
        if phrase is None:
            return html.escape(value, quote=True)
        prefix, suffix = value.split(phrase, 1)
        return (
            html.escape(prefix, quote=True)
            + '<span class="caption-focus">'
            + html.escape(phrase, quote=True)
            + "</span>"
            + html.escape(suffix, quote=True)
        )
    return "\n".join(
        f'<div id="caption-{index}" class="caption" data-caption-start="{caption["start"]:.3f}" '
        f'data-caption-end="{caption["end"]:.3f}"><span class="caption-panel"{panel_style(str(caption["text"]))}>'
        f'{styled_text(index, str(caption["text"]))}</span></div>'
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
    sponge_native_scenes: frozenset[str] = frozenset(),
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
    cue_json = json.dumps(
        [dict(cue, text=str(caption.get("text", ""))) for cue, caption in zip(cues, captions, strict=True)],
        ensure_ascii=True, separators=(",", ":"),
    ).replace("</", "<\\/")
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
                if scene.scene_id in sponge_native_scenes and (
                    entry.target.startswith("overlay-label-")
                    or entry.target.startswith("note-row-")
                ):
                    continue
                before, after, ease = effect_values[entry.motion]
                element_id = json.dumps(f"{scene.scene_id}--{entry.target}")
                motions += (
                    f"{{const target=document.getElementById({element_id});if(target)"
                    f"tl.fromTo(target,{before},"
                    f"{{...{after},duration:{entry.duration:.3f},ease:'{ease}'}},{entry.at:.3f});}}"
                )
            for cue in scene.semantic_cues:
                element_id = json.dumps(f"{scene.scene_id}--{cue.target}")
                motions += (
                    f"{{const target=document.getElementById({element_id});if(target){{"
                    f"tl.to(target,{{scale:1.03,duration:{cue.duration / 2:.3f},ease:'sine.out'}},{cue.at:.3f});"
                    f"tl.to(target,{{scale:1,duration:{cue.duration / 2:.3f},ease:'sine.in'}},{cue.at + cue.duration / 2:.3f});}}}}"
                )
            if scene.ambient is not None:
                direction = -1 if scene_index % 2 else 1
                distance = round(24 * scene.ambient.strength * direction, 3)
                element_id = json.dumps(f"{scene.scene_id}--visual")
                motions += (
                    f"tl.to(document.getElementById({element_id}),{{x:{distance},scale:1.02,duration:{scene.ambient.end - scene.ambient.start:.3f},ease:'none'}},{scene.ambient.start:.3f});"
                )
    sponge_draw = ""
    if sponge_native_scenes:
        draw_ids = json.dumps(sorted(sponge_native_scenes), ensure_ascii=True)
        sponge_draw = (
            f"window.installSpongeRegionDraw(tl,scenes,cues,new Set({draw_ids}));"
            f"window.installSpongeInfoCards(tl,scenes,cues,new Set({draw_ids}));"
        )
    return (
        base
        + motions
        + sponge_draw
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
    sponge_draw_script: str = "",
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
        "__SPONGE_DRAW_SCRIPT__": sponge_draw_script,
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
    compatibility_class = (" theme-sponge-host-handdrawn-v1" if theme.id == "vivid-comic-component-brush-v1" else "")
    return (
        f'class="visual-system-v4 theme-{theme.id}{compatibility_class}" '
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


def _sponge_text_layers(
    snapshot: FileSnapshot | None, plan, timeline, captions: tuple[dict[str, object], ...], *, words, load_cue_reviews=None
) -> dict[str, dict[str, object]]:
    if snapshot is None:
        return {}
    value = _strict_json(snapshot, error="sponge text layer is invalid")
    if not isinstance(value, dict) or set(value) != {"schema_version", "scenes"} or value["schema_version"] not in {1, 2} or not isinstance(value["scenes"], dict):
        raise ContentRenderProjectError("sponge text layer is invalid")
    enhanced = value["schema_version"] == 2
    known = {scene.id for scene in plan.scenes}
    result: dict[str, dict[str, object]] = {}
    for scene_id, layer in value["scenes"].items():
        required = {"header", "title", "kicker", "cards", "conclusion"}
        if enhanced:
            required.add("effect")
        if scene_id not in known or not isinstance(layer, dict) or not required.issubset(layer) or set(layer) - required not in (set(), {"draw_regions"}):
            raise ContentRenderProjectError("sponge text layer is invalid")
        if any(not isinstance(layer[key], str) or not layer[key].strip() or len(layer[key]) > 40 for key in ("header", "title", "kicker", "conclusion")):
            raise ContentRenderProjectError("sponge text layer is invalid")
        cards = layer["cards"]
        if enhanced and layer["effect"] not in {"reveal", "steps", "checklist", "compare"}:
            raise ContentRenderProjectError("sponge text layer is invalid")
        if not isinstance(cards, list) or not 1 <= len(cards) <= 3:
            raise ContentRenderProjectError("sponge text layer is invalid")
        draw_regions = layer.get("draw_regions")
        if not enhanced and len(cards) == 3 and draw_regions is None:
            raise ContentRenderProjectError("sponge text layer is invalid")
        if draw_regions is not None:
            if not isinstance(draw_regions, list) or len(draw_regions) != len(cards):
                raise ContentRenderProjectError("sponge text layer is invalid")
            for box in draw_regions:
                if not isinstance(box, list) or len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in box) or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
                    raise ContentRenderProjectError("sponge text layer is invalid")
        timing = next(item for item in timeline.scenes if item.id == scene_id)
        own_cues = [cue for cue in captions if timing.start - .001 <= float(cue["start"]) < timing.end - .001]
        own = [str(cue["text"]) for cue in own_cues]
        previous_at = -1.0
        reviewed_captions = frozenset()
        for card in cards:
            if not isinstance(card, dict) or set(card) != {"title", "detail", "cue"}:
                raise ContentRenderProjectError("sponge text layer is invalid")
            if any(not isinstance(card[key], str) or len(card[key]) > 30 for key in ("title", "detail", "cue")) or not card["title"].strip() or not card["cue"].strip() or (not enhanced and not any(card["cue"] in phrase for phrase in own)):
                raise ContentRenderProjectError("sponge text layer is invalid")
            if enhanced:
                try:
                    try:
                        card["cue_at"] = resolve_cue_start(card["cue"], own_cues, after=previous_at, words=words, reviewed_captions=reviewed_captions)
                    except SpongeCueReviewRequired:
                        if load_cue_reviews is None:
                            raise
                        reviewed_captions = load_cue_reviews()
                        card["cue_at"] = resolve_cue_start(card["cue"], own_cues, after=previous_at, words=words, reviewed_captions=reviewed_captions)
                    if not timing.start <= card["cue_at"] < timing.end:
                        raise SpongeComponentError("sponge text cue is outside its scene")
                except SpongeComponentError:
                    raise ContentRenderProjectError("sponge text cue has no complete speech match") from None
                previous_at = card["cue_at"]
        result[scene_id] = layer
    return result


def _sponge_host_regions(
    snapshot: FileSnapshot | None, plan
) -> dict[str, list[list[float]]]:
    if snapshot is None:
        return {}
    value = _strict_json(snapshot, error="sponge host regions are invalid")
    known = {scene.id for scene in plan.scenes}
    if not isinstance(value, dict) or set(value) != known:
        raise ContentRenderProjectError("sponge host regions are invalid")
    result: dict[str, list[list[float]]] = {}
    for scene_id, regions in value.items():
        if not isinstance(regions, list) or not 1 <= len(regions) <= 3:
            raise ContentRenderProjectError("sponge host regions are invalid")
        for box in regions:
            if (
                not isinstance(box, list) or len(box) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)
                or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)
            ):
                raise ContentRenderProjectError("sponge host regions are invalid")
        result[scene_id] = regions
    return result


def prepare_content_render_project(
    *, project_root: Path, output_dir: Path, repo_root: Path
) -> PreparedContentProject:
    """Capture all formal inputs and atomically publish one offline renderer snapshot."""

    root = Path(project_root).absolute()
    output = Path(output_dir).absolute()
    repository = Path(repo_root).absolute()
    if (
        (output != root / "工程" / "render-project" and not re.fullmatch(r"render-project-v\d{2}", output.name))
        or output.parent != root / "工程"
        or output.exists()
        or not _ordinary_directory_tree(root)
        or not _ordinary_directory_tree(output.parent)
    ):
        raise ContentRenderProjectError("render target is unavailable")
    stage: Path | None = None
    profiled_theme = None
    try:
        plan_snapshot = load_content_plan_snapshot(project_root=root)
        if (
            plan_snapshot.plan.visual_theme == "vivid-comic-component-brush-v1"
            and not (root / "工程" / "sponge-drawing-plan.json").is_file()
        ):
            raise ContentRenderProjectError("comic drawing plan is required for component painting")
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
        emphasis_path = root / "工程" / "caption-emphasis.json"
        emphasis_snapshot = (
            capture_regular_file(emphasis_path, within=root)
            if emphasis_path.exists() or emphasis_path.is_symlink()
            else None
        )
        text_layer_path = root / "工程" / "sponge-text-layer.json"
        text_layer_snapshot = (
            capture_regular_file(text_layer_path, within=root)
            if plan_snapshot.plan.visual_theme in COMPONENT_DRAW_THEMES
            and (text_layer_path.exists() or text_layer_path.is_symlink())
            else None
        )
        host_regions_path = root / "工程" / "sponge-host-regions.json"
        host_regions_snapshot = (
            capture_regular_file(host_regions_path, within=root)
            if plan_snapshot.plan.visual_theme in COMPONENT_DRAW_THEMES
            and (host_regions_path.exists() or host_regions_path.is_symlink())
            else None
        )
        drawing_path = root / "工程" / "sponge-drawing-plan.json"
        drawing_snapshot = (
            capture_regular_file(drawing_path, within=root)
            if plan_snapshot.plan.visual_theme in COMPONENT_DRAW_THEMES
            and (drawing_path.exists() or drawing_path.is_symlink()) else None
        )
        sponge_drawing_plans = validate_drawing_plan(
            _strict_json(drawing_snapshot, error="sponge drawing plan is invalid"),
            plan_snapshot.plan, root,
        ) if drawing_snapshot is not None else {}
        sponge_component_draw = capture_regular_file(
            repository / _SPONGE_COMPONENT_DRAW_SOURCE, within=repository
        ) if drawing_snapshot is not None else None
        comic_component_draw = capture_regular_file(
            repository / "video-content-template/assets/vivid-comic-component-draw.js", within=repository
        ) if drawing_snapshot is not None and plan_snapshot.plan.visual_theme == "vivid-comic-component-brush-v1" else None

        comic_semantic_path = root / "工程" / "comic-semantic-parts.json"
        comic_semantic_snapshot = (
            capture_regular_file(comic_semantic_path, within=root)
            if comic_component_draw is not None and (comic_semantic_path.exists() or comic_semantic_path.is_symlink()) else None
        )
        if comic_component_draw is not None and comic_semantic_snapshot is None:
            raise SpongeComponentError("comic semantic pixel partition is required for component painting")
        comic_semantic_parts = validate_comic_semantic_parts(
            _strict_json(comic_semantic_snapshot, error="comic semantic pixel partition is invalid"),
            sponge_drawing_plans, plan_snapshot.plan, root,
        ) if comic_semantic_snapshot is not None else None

        comic_duration_fit = capture_regular_file(

            repository / "video-content-template/assets/vivid-comic-duration-fit.js", within=repository

        ) if drawing_snapshot is not None and plan_snapshot.plan.visual_theme == "vivid-comic-component-brush-v1" else None

        template = capture_regular_file(
            repository / "video-content-template" / "index.template.html"
        )
        gsap = capture_regular_file(
            repository / "node_modules" / "gsap" / "dist" / "gsap.min.js"
        )
        sponge_region_draw = (
            capture_regular_file(
                repository / _SPONGE_REGION_DRAW_SOURCE, within=repository
            )
            if plan_snapshot.plan.visual_theme in COMPONENT_DRAW_THEMES
            else None
        )
        sponge_cursor = (
            capture_regular_file(repository / (_SPONGE_BRUSH_SOURCE if drawing_snapshot is not None else _SPONGE_CURSOR_SOURCE), within=repository)
            if plan_snapshot.plan.visual_theme in COMPONENT_DRAW_THEMES
            else None
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
        SpongeComponentError,
    ):
        raise ContentRenderProjectError("render inputs are invalid") from None
    captions = _captions(
        caption_snapshots["captions.json"], duration=timeline.duration_seconds
    )
    _caption_qc(
        caption_snapshots["caption-qc.json"], narration_sha256=narration.sha256
    )
    caption_emphasis = _caption_emphasis(
        emphasis_snapshot,
        captions_snapshot=caption_snapshots["captions.json"],
        captions=captions,
    )
    cue_review_snapshots = []
    cue_reviewed_captions = None
    cue_words = _load_words(caption_snapshots["captions_words.json"], duration=timeline.duration_seconds)
    def load_cue_reviews():
        nonlocal cue_reviewed_captions
        if cue_reviewed_captions is None:
            cue_reviewed_captions, captured = load_reviewed_cue_captions(
                root, caption_snapshots=caption_snapshots, narration_sha256=narration.sha256,
                captions=captions, words=cue_words,
            )
            cue_review_snapshots.extend(captured)
        return cue_reviewed_captions
    sponge_text_layers = _sponge_text_layers(
        text_layer_snapshot, plan_snapshot.plan, timeline, captions,
        words=cue_words, load_cue_reviews=load_cue_reviews,
    )
    sponge_host_regions = _sponge_host_regions(host_regions_snapshot, plan_snapshot.plan)
    if sponge_drawing_plans and set(sponge_text_layers) != set(sponge_drawing_plans):
        raise ContentRenderProjectError("component painting requires speech-synchronized text layers")
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
        *((plan_snapshot.audio_segment_map_snapshot,) if plan_snapshot.audio_segment_map_snapshot is not None else ()),
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
        *cue_review_snapshots,
        *((emphasis_snapshot,) if emphasis_snapshot is not None else ()),
        *((text_layer_snapshot,) if text_layer_snapshot is not None else ()),
        *((host_regions_snapshot,) if host_regions_snapshot is not None else ()),
        *((drawing_snapshot,) if drawing_snapshot is not None else ()),
        *((sponge_component_draw,) if sponge_component_draw is not None else ()),

        *((comic_semantic_snapshot,) if comic_semantic_snapshot is not None else ()),
        *((comic_component_draw,) if comic_component_draw is not None else ()),
        *((comic_duration_fit,) if comic_duration_fit is not None else ()) ,
        *asset_snapshots.values(),
        template,
        gsap,
        *((sponge_region_draw,) if sponge_region_draw is not None else ()),
        *((sponge_cursor,) if sponge_cursor is not None else ()),
        *((v5_background,) if v5_background is not None else ()),
    )
    _after_inputs_captured(snapshots)
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        raise ContentRenderProjectError("render input changed")
    xiaohei_motion = plan_snapshot.plan.visual_system == "xiaohei-white-first-v1"
    native_sponge_scenes = frozenset(
        asset.scene_id
        for asset, prompt in zip(
            illustration_snapshot.manifest.assets,
            illustration_snapshot.prompt_snapshots,
            strict=True,
        )
        if plan_snapshot.plan.visual_theme in COMPONENT_DRAW_THEMES
        and b"\ntarget_size: native-source\n" in prompt.payload.replace(b"\r\n", b"\n")
    ) if illustration_snapshot is not None else frozenset()
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
            native_sponge_scenes=native_sponge_scenes,
            sponge_text_layers=sponge_text_layers,
            sponge_host_regions=sponge_host_regions,
            sponge_drawing_plans=sponge_drawing_plans,
        ),
        captions=_caption_html(captions, caption_emphasis),
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
            sponge_native_scenes=native_sponge_scenes,
        ),
        sponge_draw_script=(
            '<script src="assets/sponge-region-draw.js"></script>'
            + ('<script src="assets/sponge-component-draw.js"></script>' if sponge_component_draw is not None else "")

            + ('<script>window.__vividComicSemanticParts=' + json.dumps(comic_semantic_parts, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/") + ';</script>' if comic_semantic_parts is not None else "")
            + ('<script src="assets/vivid-comic-component-draw.js"></script>' if comic_component_draw is not None else "")
            + ('<script src="assets/vivid-comic-duration-fit.js"></script>' if comic_duration_fit is not None else "")
            if sponge_region_draw is not None else ""
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
        if sponge_cursor is not None:
            (stage / "assets" / "hand-pencil-cursor.png").write_bytes(sponge_cursor.payload)
        if sponge_region_draw is not None:
            (stage / "assets" / "sponge-region-draw.js").write_bytes(
                sponge_region_draw.payload
            )
        if sponge_component_draw is not None:
            (stage / "assets" / "sponge-component-draw.js").write_bytes(sponge_component_draw.payload)
        if comic_semantic_snapshot is not None:
            (stage / "comic-semantic-parts.json").write_bytes(comic_semantic_snapshot.payload)
        if comic_component_draw is not None:
            (stage / "assets" / "vivid-comic-component-draw.js").write_bytes(comic_component_draw.payload)
        if comic_duration_fit is not None:

            (stage / "assets" / "vivid-comic-duration-fit.js").write_bytes(comic_duration_fit.payload)

        (stage / "node_modules" / "gsap" / "dist").mkdir(parents=True)
        if v5_background is not None:
            (stage / _V5_BACKGROUND_OUTPUT.parent).mkdir(parents=True)
        (stage / "index.html").write_bytes(rendered)
        (stage / "media" / "narration.wav").write_bytes(narration.payload)
        (stage / "media" / "voice_manifest.json").write_bytes(manifest.payload)
        for name, snapshot in caption_snapshots.items():
            (stage / "media" / "captions" / name).write_bytes(snapshot.payload)
        for snapshot in cue_review_snapshots:
            (stage / snapshot.path.name).write_bytes(snapshot.payload)
        if emphasis_snapshot is not None:
            (stage / "caption-emphasis.json").write_bytes(emphasis_snapshot.payload)
        if text_layer_snapshot is not None:
            (stage / "sponge-text-layer.json").write_bytes(text_layer_snapshot.payload)
        if host_regions_snapshot is not None:
            (stage / "sponge-host-regions.json").write_bytes(host_regions_snapshot.payload)
        if drawing_snapshot is not None:
            (stage / "sponge-drawing-plan.json").write_bytes(drawing_snapshot.payload)
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
        # Production snapshots are retained even when publication fails.
        # The uniquely named stage is recovery evidence, not disposable cache.
        pass
    return PreparedContentProject(
        output_dir=output,
        duration_seconds=timeline.duration_seconds,
        scene_midpoints=tuple(
            round((scene.start + scene.end) / 2.0, 6) for scene in timeline.scenes
        ),
    )

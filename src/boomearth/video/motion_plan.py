"""Strict, final-audio-bound component motion plan contracts."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boomearth.video.artifacts import (
    ArtifactError,
    FileSnapshot,
    capture_regular_file,
    encode_canonical_json,
    publish_bytes_no_clobber,
)


_TOP_KEYS = {
    "schema_version", "motion_profile", "content_plan_sha256",
    "scene_timeline_sha256", "captions_words_sha256", "duration_seconds", "scenes",
}
_SCENE_KEYS = {"scene_id", "start", "end", "entries", "semantic_cues", "ambient"}
_ENTRY_KEYS = {"target", "at", "duration", "motion"}
_CUE_KEYS = {"text", "target", "at", "duration", "motion"}
_AMBIENT_KEYS = {"target", "motion", "start", "end", "strength"}
_TARGETS = {
    "chapter", "progress", "title-line-1", "title-line-2", "subtitle-line-1",
    "subtitle-line-2", "visual", "note-card", "kicker", "note-row-1",
    "note-row-2", "note-row-3",
    "overlay-label-1", "overlay-label-2", "overlay-label-3", "overlay-label-4",
    "overlay-label-5", "overlay-label-6", "overlay-label-7", "overlay-label-8",
}
_ENTRY_EFFECTS = {"fade-down", "fade-up", "line-reveal", "scale-settle", "wipe-right"}
_MOTION_PROFILES = {
    "editorial-motion-v2": "editorial-cards-v2",
    "semantic-handdrawn-v3": "semantic-handdrawn-v3",
    "profiled-illustration-v4": "profiled-illustration-v4",
}
_PROFILED_VISUAL_EFFECT = {
    "vivid-comic-explainer": "scale-settle",
    "engineering-sketch-explainer": "line-reveal",
    "four-panel-comic-explainer": "wipe-right",
    "blue-black-whiteboard-explainer": "line-reveal",
}


class MotionPlanError(RuntimeError):
    """A fixed, source-redacted motion contract failure."""


@dataclass(frozen=True, slots=True)
class MotionEntry:
    target: str
    at: float
    duration: float
    motion: str


@dataclass(frozen=True, slots=True)
class SemanticCue:
    text: str
    target: str
    at: float
    duration: float
    motion: str


@dataclass(frozen=True, slots=True)
class AmbientMotion:
    target: str
    motion: str
    start: float
    end: float
    strength: float


@dataclass(frozen=True, slots=True)
class SceneMotion:
    scene_id: str
    start: float
    end: float
    entries: tuple[MotionEntry, ...]
    semantic_cues: tuple[SemanticCue, ...]
    ambient: AmbientMotion | None


@dataclass(frozen=True, slots=True)
class MotionPlan:
    schema_version: int
    motion_profile: str
    content_plan_sha256: str
    scene_timeline_sha256: str
    captions_words_sha256: str
    duration_seconds: float
    scenes: tuple[SceneMotion, ...]


@dataclass(frozen=True, slots=True)
class MotionPlanSnapshot:
    path: Path
    plan: MotionPlan
    snapshot: FileSnapshot
    content_plan_snapshot: FileSnapshot
    scene_timeline_snapshot: FileSnapshot
    captions_words_snapshot: FileSnapshot


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MotionPlanError("motion-plan-timing-invalid")
    result = float(value)
    if not math.isfinite(result):
        raise MotionPlanError("motion-plan-timing-invalid")
    return result


def _json_bytes(payload: bytes, error: str) -> object:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise MotionPlanError(error)
            result[key] = value
        return result
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except MotionPlanError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise MotionPlanError(error) from None


def _snapshots(project_root: Path) -> tuple[FileSnapshot, FileSnapshot, FileSnapshot]:
    engineering = project_root / "工程"
    try:
        return (
            capture_regular_file(engineering / "content-plan.json", within=project_root),
            capture_regular_file(engineering / "scene-timeline.json", within=project_root),
            capture_regular_file(
                engineering / "media" / "captions" / "captions_words.json",
                within=project_root,
            ),
        )
    except ArtifactError:
        raise MotionPlanError("motion-plan-input-changed") from None


def _available_targets(scene: dict[str, object]) -> set[str]:
    targets = {"chapter", "progress"}
    titles = scene.get("title_lines")
    subtitles = scene.get("subtitle_lines")
    notes = scene.get("notes")
    if isinstance(titles, list):
        targets |= {f"title-line-{index}" for index in range(1, min(2, len(titles)) + 1)}
    if isinstance(subtitles, list):
        targets |= {f"subtitle-line-{index}" for index in range(1, min(2, len(subtitles)) + 1)}
    if scene.get("visual_asset") or scene.get("visual_mode") == "type-led":
        targets.add("visual")
    overlay_labels = scene.get("overlay_labels")
    if isinstance(overlay_labels, list):
        targets |= {
            f"overlay-label-{index}"
            for index in range(1, min(8, len(overlay_labels)) + 1)
        }
    if scene.get("kicker"):
        targets.add("kicker")
    if scene.get("kicker") or (isinstance(notes, list) and notes):
        targets.add("note-card")
    if isinstance(notes, list) and notes:
        targets |= {f"note-row-{index}" for index in range(1, min(3, len(notes)) + 1)}
    return targets


def validate_motion_plan(value: object, *, project_root: Path) -> MotionPlan:
    if not isinstance(value, dict) or set(value) != _TOP_KEYS:
        raise MotionPlanError("motion-plan-invalid")
    if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
        raise MotionPlanError("motion-plan-profile-invalid")
    content_snapshot, timeline_snapshot, words_snapshot = _snapshots(Path(project_root))
    if (
        value["content_plan_sha256"] != content_snapshot.sha256
        or value["scene_timeline_sha256"] != timeline_snapshot.sha256
        or value["captions_words_sha256"] != words_snapshot.sha256
    ):
        raise MotionPlanError("motion-plan-input-changed")
    content = _json_bytes(content_snapshot.payload, "motion-plan-input-changed")
    timeline = _json_bytes(timeline_snapshot.payload, "motion-plan-input-changed")
    if not isinstance(content, dict):
        raise MotionPlanError("motion-plan-profile-invalid")
    expected_profile = _MOTION_PROFILES.get(content.get("visual_system"))
    if (
        expected_profile is None
        or value["motion_profile"] != expected_profile
        or content.get("visual_system") == "profiled-illustration-v4"
        and content.get("visual_theme") not in _PROFILED_VISUAL_EFFECT
    ):
        raise MotionPlanError("motion-plan-profile-invalid")
    if not isinstance(timeline, dict) or not isinstance(timeline.get("scenes"), list):
        raise MotionPlanError("motion-plan-input-changed")
    duration = _number(value["duration_seconds"])
    if abs(duration - _number(timeline.get("duration_seconds"))) > 0.05:
        raise MotionPlanError("motion-plan-timing-invalid")
    plan_scenes = value["scenes"]
    content_scenes = content.get("scenes")
    timeline_scenes = timeline["scenes"]
    if not isinstance(plan_scenes, list) or not isinstance(content_scenes, list) or len(plan_scenes) != len(timeline_scenes) or len(plan_scenes) != len(content_scenes):
        raise MotionPlanError("motion-plan-invalid")
    scenes: list[SceneMotion] = []
    for raw, timing, content_scene in zip(plan_scenes, timeline_scenes, content_scenes, strict=True):
        if not isinstance(raw, dict) or set(raw) != _SCENE_KEYS or not isinstance(timing, dict) or not isinstance(content_scene, dict):
            raise MotionPlanError("motion-plan-invalid")
        start, end = _number(raw["start"]), _number(raw["end"])
        if raw["scene_id"] != timing.get("id") or raw["scene_id"] != content_scene.get("id") or abs(start - _number(timing.get("start"))) > 1e-6 or abs(end - _number(timing.get("end"))) > 1e-6 or start < 0 or end <= start:
            raise MotionPlanError("motion-plan-timing-invalid")
        available = _available_targets(content_scene)
        entries_raw = raw["entries"]
        if not isinstance(entries_raw, list) or not entries_raw:
            raise MotionPlanError("motion-plan-invalid")
        entries: list[MotionEntry] = []
        seen: set[str] = set()
        for item in entries_raw:
            if not isinstance(item, dict) or set(item) != _ENTRY_KEYS:
                raise MotionPlanError("motion-plan-invalid")
            target, motion = item["target"], item["motion"]
            if target not in _TARGETS or target not in available:
                raise MotionPlanError("motion-plan-target-invalid")
            if motion not in _ENTRY_EFFECTS:
                raise MotionPlanError("motion-plan-effect-invalid")
            at, item_duration = _number(item["at"]), _number(item["duration"])
            key = str(target)
            if key in seen:
                raise MotionPlanError("motion-plan-target-invalid")
            seen.add(key)
            if item_duration < 0.16 or at < start - 1e-9 or at + item_duration > end + 1e-9:
                raise MotionPlanError("motion-plan-timing-invalid")
            entries.append(MotionEntry(str(target), at, item_duration, str(motion)))
        cues_raw = raw["semantic_cues"]
        if not isinstance(cues_raw, list):
            raise MotionPlanError("motion-plan-invalid")
        cues: list[SemanticCue] = []
        for item in cues_raw:
            if not isinstance(item, dict) or set(item) != _CUE_KEYS or item["motion"] != "accent-pulse" or item["target"] not in available or not isinstance(item["text"], str) or not item["text"]:
                raise MotionPlanError("motion-plan-effect-invalid")
            at, cue_duration = _number(item["at"]), _number(item["duration"])
            if cue_duration < 0.30 or at < start or at + cue_duration > end:
                raise MotionPlanError("motion-plan-timing-invalid")
            cues.append(SemanticCue(item["text"], item["target"], at, cue_duration, "accent-pulse"))
        ambient_raw = raw["ambient"]
        ambient = None
        if ambient_raw is not None:
            if not isinstance(ambient_raw, dict) or set(ambient_raw) != _AMBIENT_KEYS or ambient_raw["target"] != "visual" or ambient_raw["motion"] != "slow-parallax" or "visual" not in available:
                raise MotionPlanError("motion-plan-effect-invalid")
            ambient_start, ambient_end = _number(ambient_raw["start"]), _number(ambient_raw["end"])
            strength = _number(ambient_raw["strength"])
            if ambient_start < start or ambient_end > end or ambient_end <= ambient_start or not 0 < strength <= 1:
                raise MotionPlanError("motion-plan-timing-invalid")
            ambient = AmbientMotion("visual", "slow-parallax", ambient_start, ambient_end, strength)
        scenes.append(SceneMotion(str(raw["scene_id"]), start, end, tuple(entries), tuple(cues), ambient))
    return MotionPlan(1, expected_profile, content_snapshot.sha256, timeline_snapshot.sha256, words_snapshot.sha256, duration, tuple(scenes))


def load_motion_plan_snapshot(project_root: Path) -> MotionPlanSnapshot:
    project_root = Path(project_root)
    path = project_root / "工程" / "motion-plan.json"
    try:
        snapshot = capture_regular_file(path, within=project_root)
    except ArtifactError:
        raise MotionPlanError("motion-plan-required") from None
    value = _json_bytes(snapshot.payload, "motion-plan-invalid")
    plan = validate_motion_plan(value, project_root=project_root)
    content, timeline, words = _snapshots(project_root)
    return MotionPlanSnapshot(path, plan, snapshot, content, timeline, words)


def _entry_value(entry: MotionEntry) -> dict[str, object]:
    return {"target": entry.target, "at": entry.at, "duration": entry.duration, "motion": entry.motion}


def _plan_value(plan: MotionPlan) -> dict[str, object]:
    return {
        "schema_version": 1,
        "motion_profile": plan.motion_profile,
        "content_plan_sha256": plan.content_plan_sha256,
        "scene_timeline_sha256": plan.scene_timeline_sha256,
        "captions_words_sha256": plan.captions_words_sha256,
        "duration_seconds": plan.duration_seconds,
        "scenes": [
            {
                "scene_id": scene.scene_id,
                "start": scene.start,
                "end": scene.end,
                "entries": [_entry_value(entry) for entry in scene.entries],
                "semantic_cues": [
                    {"text": cue.text, "target": cue.target, "at": cue.at, "duration": cue.duration, "motion": cue.motion}
                    for cue in scene.semantic_cues
                ],
                "ambient": None if scene.ambient is None else {
                    "target": scene.ambient.target,
                    "motion": scene.ambient.motion,
                    "start": scene.ambient.start,
                    "end": scene.ambient.end,
                    "strength": scene.ambient.strength,
                },
            }
            for scene in plan.scenes
        ],
    }


def _semantic_cue(
    title: str,
    target: str,
    words: list[object],
    *,
    start: float,
    end: float,
) -> SemanticCue | None:
    normalized = "".join(title.split()).casefold()
    if not normalized:
        return None
    scene_words = [
        item for item in words
        if isinstance(item, dict)
        and item.get("isGap") is False
        and isinstance(item.get("text"), str)
        and isinstance(item.get("start"), (int, float))
        and isinstance(item.get("end"), (int, float))
        and float(item["start"]) >= start - 1e-9
        and float(item["end"]) <= end + 1e-9
    ]
    for index, item in enumerate(scene_words):
        combined = ""
        for last in range(index, len(scene_words)):
            combined += "".join(str(scene_words[last]["text"]).split()).casefold()
            if combined == normalized:
                cue_start = float(item["start"])
                cue_end = float(scene_words[last]["end"])
                duration = max(0.30, cue_end - cue_start)
                if cue_start + duration <= end + 1e-9:
                    return SemanticCue(title, target, cue_start, duration, "accent-pulse")
                return None
            if not normalized.startswith(combined):
                break
    return None


def _semantic_cue_from_title(
    title: str,
    target: str,
    words: list[object],
    *,
    start: float,
    end: float,
) -> SemanticCue | None:
    compact = "".join(title.split())
    for length in range(len(compact), 1, -1):
        for offset in range(0, len(compact) - length + 1):
            phrase = compact[offset : offset + length]
            if not any(character.isalnum() for character in phrase):
                continue
            cue = _semantic_cue(
                phrase,
                target,
                words,
                start=start,
                end=end,
            )
            if cue is not None:
                return cue
    return None


def compile_motion_plan(*, project_root: Path) -> MotionPlanSnapshot:
    """Compile deterministic component reveals from formal content and final words."""

    project_root = Path(project_root)
    content_snapshot, timeline_snapshot, words_snapshot = _snapshots(project_root)
    content = _json_bytes(content_snapshot.payload, "motion-plan-input-changed")
    timeline = _json_bytes(timeline_snapshot.payload, "motion-plan-input-changed")
    words = _json_bytes(words_snapshot.payload, "motion-plan-input-changed")
    if (
        not isinstance(content, dict)
        or content.get("visual_system") not in _MOTION_PROFILES
        or not isinstance(content.get("scenes"), list)
        or not isinstance(timeline, dict)
        or not isinstance(timeline.get("scenes"), list)
        or not isinstance(words, list)
    ):
        raise MotionPlanError("motion-plan-input-changed")
    duration = _number(timeline.get("duration_seconds"))
    if len(content["scenes"]) != len(timeline["scenes"]):
        raise MotionPlanError("motion-plan-input-changed")
    if content["visual_system"] == "profiled-illustration-v4":
        visual_effect = _PROFILED_VISUAL_EFFECT.get(content.get("visual_theme"))
        if visual_effect is None:
            raise MotionPlanError("motion-plan-input-changed")
    else:
        visual_effect = "scale-settle"
    scenes: list[SceneMotion] = []
    for index, (content_scene, timing) in enumerate(
        zip(content["scenes"], timeline["scenes"], strict=True), start=1
    ):
        if not isinstance(content_scene, dict) or not isinstance(timing, dict):
            raise MotionPlanError("motion-plan-input-changed")
        start, end = _number(timing.get("start")), _number(timing.get("end"))
        scene_duration = end - start
        if scene_duration < 0.8:
            raise MotionPlanError("motion-plan-timing-invalid")
        targets: list[tuple[str, float, float, str]] = [
            ("chapter", 0.12, 0.24, "fade-down"),
            ("progress", 0.16, 0.24, "fade-down"),
        ]
        title_lines = content_scene.get("title_lines")
        subtitle_lines = content_scene.get("subtitle_lines")
        notes = content_scene.get("notes")
        if isinstance(title_lines, list):
            for line_index in range(min(2, len(title_lines))):
                targets.append((f"title-line-{line_index + 1}", 0.24 + line_index * 0.18, 0.30, "line-reveal"))
        if isinstance(subtitle_lines, list):
            for line_index in range(min(2, len(subtitle_lines))):
                targets.append((f"subtitle-line-{line_index + 1}", 0.60 + line_index * 0.18, 0.24, "fade-up"))
        if content_scene.get("visual_asset") or content_scene.get("visual_mode") == "type-led":
            targets.append(("visual", 0.82, 0.38, visual_effect))
        overlay_labels = content_scene.get("overlay_labels")
        if isinstance(overlay_labels, list):
            for label_index in range(min(8, len(overlay_labels))):
                targets.append(
                    (
                        f"overlay-label-{label_index + 1}",
                        1.12 + label_index * 0.16,
                        0.24,
                        "fade-up",
                    )
                )
        if content_scene.get("kicker") or (isinstance(notes, list) and notes):
            targets.append(("note-card", 0.98, 0.32, "wipe-right"))
        if content_scene.get("kicker"):
            targets.append(("kicker", 1.08, 0.24, "fade-up"))
        if isinstance(notes, list) and notes:
            for note_index in range(min(3, len(notes))):
                targets.append((f"note-row-{note_index + 1}", 1.28 + note_index * 0.22, 0.24, "fade-up"))
        midpoint = scene_duration / 2
        latest = max(offset + item_duration for _, offset, item_duration, _ in targets)
        scale = min(1.0, midpoint / latest) if latest else 1.0
        entries = tuple(
            MotionEntry(
                target,
                round(start + offset * scale, 3),
                round(max(0.16, item_duration * scale), 3),
                effect,
            )
            for target, offset, item_duration, effect in targets
        )
        if any(entry.at + entry.duration > start + midpoint + 1e-6 for entry in entries):
            raise MotionPlanError("motion-plan-timing-invalid")
        cues: list[SemanticCue] = []
        if isinstance(title_lines, list):
            for line_index, title in enumerate(title_lines[:2], 1):
                if not isinstance(title, str):
                    continue
                cue = _semantic_cue_from_title(
                    title,
                    f"title-line-{line_index}",
                    words,
                    start=start,
                    end=end,
                )
                if cue is not None:
                    cues.append(cue)
        visual_entry = next((entry for entry in entries if entry.target == "visual"), None)
        ambient = None
        if visual_entry is not None and scene_duration >= 2.5:
            ambient_start = round(visual_entry.at + visual_entry.duration, 3)
            ambient_end = round(end - min(0.2, scene_duration * 0.1), 3)
            if ambient_end > ambient_start:
                ambient = AmbientMotion("visual", "slow-parallax", ambient_start, ambient_end, 0.35)
        scenes.append(
            SceneMotion(str(timing.get("id")), start, end, entries, tuple(cues), ambient)
        )
    plan = MotionPlan(
        1,
        _MOTION_PROFILES[str(content["visual_system"])],
        content_snapshot.sha256,
        timeline_snapshot.sha256,
        words_snapshot.sha256,
        duration,
        tuple(scenes),
    )
    path = project_root / "工程" / "motion-plan.json"
    try:
        publish_bytes_no_clobber(path, encode_canonical_json(_plan_value(plan)), within=project_root)
    except ArtifactError as error:
        if path.exists():
            raise MotionPlanError("motion-plan-exists") from None
        raise MotionPlanError("motion-plan-publication-failed") from error
    return load_motion_plan_snapshot(project_root)


__all__ = ["AmbientMotion", "MotionEntry", "MotionPlan", "MotionPlanError", "MotionPlanSnapshot", "SceneMotion", "SemanticCue", "compile_motion_plan", "load_motion_plan_snapshot", "validate_motion_plan"]

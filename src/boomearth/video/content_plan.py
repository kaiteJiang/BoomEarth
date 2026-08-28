"""Compile one reviewed public handoff into a stable local content plan."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from boomearth.captions.align import reading_units
from boomearth.video.artifacts import (
    ArtifactError,
    FileSnapshot,
    capture_regular_file,
    encode_canonical_json,
    load_json_snapshot,
    publish_bytes_no_clobber,
    snapshot_matches,
)
from boomearth.video.illustration_themes import (
    PROFILED_VISUAL_SYSTEM,
    TYPE_LED_TARGET,
    IllustrationTheme,
    IllustrationThemeError,
    get_theme,
    resolve_visual_style,
)
from boomearth.workbench.handoff import validate_public_handoff


_TOP_LEVEL_FIELDS = {
    "schema_version",
    "project_id",
    "ratio",
    "visual_system",
    "typography_scale",
    "caption_style",
    "scenes",
}
_V2_TOP_LEVEL_FIELDS = _TOP_LEVEL_FIELDS | {"illustration_skill"}
_V3_TOP_LEVEL_FIELDS = _V2_TOP_LEVEL_FIELDS
_V4_TOP_LEVEL_FIELDS = _V3_TOP_LEVEL_FIELDS | {"visual_theme"}
_SCENE_REQUIRED_FIELDS = {
    "id",
    "narration_segment_ids",
    "chapter",
    "label",
    "progress",
    "title_lines",
    "subtitle_lines",
    "kicker",
    "notes",
    "visual_intent",
    "visual_asset",
    "layout_variant",
}
_SCENE_OPTIONAL_FIELDS = {"no_visual_reason", "reuse_reason"}
_V1_SCENE_FIELDS = {"overlay_labels"}
_V2_SCENE_FIELDS = {"visual_type", "visual_style"}
_V3_SCENE_FIELDS = _V2_SCENE_FIELDS | {
    "visual_mode",
    "semantic_subjects",
    "semantic_action",
    "required_visual_evidence",
    "forbidden_metaphors",
    "overlay_labels",
}
_V4_SCENE_FIELDS = _V3_SCENE_FIELDS | {
    "theme_structure",
    "theme_exceptions",
}
_NOTE_FIELDS = {"label", "text"}
_LAYOUT_VARIANTS = {"standard", "long-title", "wide-visual", "close"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_VISUAL_TYPES = {"concept-scene", "comparison", "framework", "technical", "clean-collage"}
_VISUAL_STYLES = {"editorial-scene", "minimal-vector", "technical-diagram", "screen-print-metaphor", "clean-collage"}
_V3_VISUAL_MODES = {
    "source-collage",
    "human-action",
    "handdrawn-flow",
    "type-led",
}
_V3_VISUAL_STYLES = {
    "semantic-handdrawn",
    "semantic-type",
    "semantic-collage",
}
_DEFAULT_FORBIDDEN_METAPHORS = frozenset(
    {
        "机器人",
        "齿轮",
        "工厂",
        "机械臂",
        "金属卡匣",
        "电路板",
        "工业流水线",
        "发动机",
        "机械底座",
    }
)
_PHYSICAL_SUBJECT_OMISSIONS = {
    "实体机器人": frozenset({"机器人", "机械臂", "机械底座"}),
    "硬件": frozenset({"电路板"}),
    "机械": frozenset({"齿轮", "机械臂", "发动机", "机械底座"}),
    "制造业": frozenset({"工厂", "机械臂", "工业流水线"}),
    "工业设备": frozenset(
        {"工厂", "机械臂", "工业流水线", "发动机", "机械底座"}
    ),
}
_SEGMENT_HEADING = re.compile(r"^### (segment-(\d{3}))\s*$")
_SAFE_PROJECT_ID = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]{0,79}")


class ContentPlanError(RuntimeError):
    """A fixed, redacted content-plan contract failure."""


@dataclass(frozen=True, slots=True)
class ContentNote:
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class ContentScene:
    id: str
    narration_segment_ids: tuple[str, ...]
    chapter: str
    label: str
    progress: str
    title_lines: tuple[str, ...]
    subtitle_lines: tuple[str, ...]
    kicker: str
    notes: tuple[ContentNote, ...]
    visual_intent: str
    visual_asset: str | None
    layout_variant: str
    visual_type: str | None = None
    visual_style: str | None = None
    visual_mode: str | None = None
    semantic_subjects: tuple[str, ...] = ()
    semantic_action: str | None = None
    required_visual_evidence: tuple[str, ...] = ()
    forbidden_metaphors: tuple[str, ...] = ()
    overlay_labels: tuple[str, ...] = ()
    no_visual_reason: str | None = None
    reuse_reason: str | None = None
    theme_structure: tuple[str, ...] = ()
    theme_exceptions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentPlan:
    schema_version: int
    project_id: str
    ratio: str
    visual_system: str
    typography_scale: str
    caption_style: str
    scenes: tuple[ContentScene, ...]
    illustration_skill: str | None = None
    visual_theme: str | None = None


@dataclass(frozen=True, slots=True)
class HandoffSegment:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class HandoffVisualContract:
    target: str
    schema_version: int
    visual_system: str
    visual_theme: str | None
    illustration_skill: str


@dataclass(frozen=True, slots=True)
class ContentPlanSnapshot:
    path: Path
    plan: ContentPlan
    snapshot: FileSnapshot
    candidate_snapshot: FileSnapshot | None
    handoff_snapshot: FileSnapshot
    manifest_snapshot: FileSnapshot
    narration_contract_snapshot: FileSnapshot


def _normalize_segment_text(text: str) -> str:
    return "\n".join(
        line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).strip()


def parse_handoff_segments(text: str) -> tuple[HandoffSegment, ...]:
    """Parse only the stable segment IDs under the public new-script section."""

    if not isinstance(text, str) or validate_public_handoff(text):
        raise ContentPlanError("public handoff is invalid")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    try:
        section_start = next(
            index for index, line in enumerate(lines) if line.strip() == "## 新稿分段"
        )
    except StopIteration:
        raise ContentPlanError("public handoff is invalid") from None
    section_end = len(lines)
    for index in range(section_start + 1, len(lines)):
        if lines[index].startswith("## "):
            section_end = index
            break
    entries: list[HandoffSegment] = []
    current_id: str | None = None
    current_lines: list[str] = []
    for line in lines[section_start + 1 : section_end]:
        heading = _SEGMENT_HEADING.fullmatch(line)
        if heading:
            if current_id is not None:
                entries.append(
                    HandoffSegment(current_id, _normalize_segment_text("\n".join(current_lines)))
                )
            current_id = heading.group(1)
            current_lines = []
        elif line.startswith("### "):
            raise ContentPlanError("public handoff is invalid")
        elif current_id is not None:
            current_lines.append(line)
        elif line.strip():
            raise ContentPlanError("public handoff is invalid")
    if current_id is not None:
        entries.append(
            HandoffSegment(current_id, _normalize_segment_text("\n".join(current_lines)))
        )
    expected = tuple(f"segment-{index:03d}" for index in range(1, len(entries) + 1))
    if (
        not entries
        or tuple(item.id for item in entries) != expected
        or any(not item.text for item in entries)
    ):
        raise ContentPlanError("public handoff is invalid")
    return tuple(entries)


def _frontmatter_scalar(value: str) -> str:
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            raise ContentPlanError("public handoff is invalid") from None
        if not isinstance(decoded, str):
            raise ContentPlanError("public handoff is invalid")
        return decoded
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value):
        raise ContentPlanError("public handoff is invalid")
    return value


def parse_handoff_visual_contract(text: str) -> HandoffVisualContract:
    """Resolve the exact public visual target and its canonical Skill binding."""

    if not isinstance(text, str) or validate_public_handoff(text):
        raise ContentPlanError("public handoff is invalid")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0] != "---":
        raise ContentPlanError("public handoff is invalid")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ContentPlanError("public handoff is invalid") from None
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line:
            continue
        match = re.fullmatch(r"([a-z][a-z0-9_]*): (.+)", line)
        if match is None or match.group(1) in fields:
            raise ContentPlanError("public handoff is invalid")
        fields[match.group(1)] = match.group(2)
    if "visual" not in fields or "illustration_skill" not in fields:
        raise ContentPlanError("public handoff is invalid")
    target = _frontmatter_scalar(fields["visual"])
    skill = _frontmatter_scalar(fields["illustration_skill"])
    if target == "default":
        raise ContentPlanError("public handoff is invalid")
    try:
        resolved = resolve_visual_style(target)
    except IllustrationThemeError:
        raise ContentPlanError("public handoff is invalid") from None
    if skill != resolved.illustration_skill:
        raise ContentPlanError("public handoff is invalid")
    return HandoffVisualContract(
        target=resolved.target,
        schema_version=resolved.schema_version,
        visual_system=resolved.visual_system,
        visual_theme=resolved.visual_theme,
        illustration_skill=resolved.illustration_skill,
    )


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContentPlanError("narration contract is invalid")
        result[key] = value
    return result


def _parse_narration_contract(snapshot: FileSnapshot) -> tuple[str, ...]:
    try:
        text = snapshot.payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ContentPlanError("narration contract is invalid") from None
    lines = text.splitlines()
    if not lines or any(not line.strip() for line in lines):
        raise ContentPlanError("narration contract is invalid")
    segments: list[str] = []
    for line in lines:
        try:
            value = json.loads(
                line,
                object_pairs_hook=_strict_pairs,
                parse_constant=lambda _value: (_ for _ in ()).throw(
                    ContentPlanError("narration contract is invalid")
                ),
            )
        except ContentPlanError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            raise ContentPlanError("narration contract is invalid") from None
        if not isinstance(value, dict):
            raise ContentPlanError("narration contract is invalid")
        display_text = value.get("text")
        if not isinstance(display_text, str) or not _normalize_segment_text(display_text):
            raise ContentPlanError("narration contract is invalid")
        segments.append(_normalize_segment_text(display_text))
    return tuple(segments)


def _visible_text(value: object, *, minimum: float, maximum: float) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ContentPlanError("visible content is invalid")
    units = reading_units(value)
    if units < minimum or units > maximum:
        raise ContentPlanError("visible content is invalid")
    if validate_public_handoff(value):
        raise ContentPlanError("public content is invalid")
    return value


def _string_lines(
    value: object,
    *,
    minimum_count: int,
    maximum_count: int,
    maximum_units: float,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not minimum_count <= len(value) <= maximum_count
    ):
        raise ContentPlanError("visible content is invalid")
    return tuple(
        _visible_text(item, minimum=1.0, maximum=maximum_units) for item in value
    )


def _image_bytes_are_decodable(payload: bytes, suffix: str) -> bool:
    signature_matches = (
        suffix == ".png" and payload.startswith(b"\x89PNG\r\n\x1a\n")
    ) or (
        suffix in {".jpg", ".jpeg"} and payload.startswith(b"\xff\xd8")
    ) or (
        suffix == ".webp"
        and len(payload) >= 12
        and payload.startswith(b"RIFF")
        and payload[8:12] == b"WEBP"
    )
    if suffix not in _IMAGE_SUFFIXES or not signature_matches:
        return False
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    try:
        decoded = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "image2pipe",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-f",
                "null",
                "-",
            ],
            input=payload,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return decoded.returncode == 0


def _visual_asset(
    value: object,
    *,
    project_root: Path,
    visual_system: str,
    visual_mode: str | None = None,
    visual_theme: str | None = None,
) -> str | None:
    if visual_system in {"semantic-handdrawn-v3", PROFILED_VISUAL_SYSTEM}:
        if visual_mode == "type-led":
            if visual_system == PROFILED_VISUAL_SYSTEM:
                raise ContentPlanError("visual asset is invalid")
            if value is not None:
                raise ContentPlanError("visual asset is invalid")
            return None
        if value is None:
            raise ContentPlanError("visual asset is invalid")
    if value is None:
        return None
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ContentPlanError("visual asset is invalid")
    pure = PurePosixPath(value)
    expected_parts = (
        ("工程", "assets", "profiled-illustrations", visual_theme)
        if visual_system == PROFILED_VISUAL_SYSTEM
        else (
            "工程",
            "assets",
            {
                "editorial-motion-v2": "editorial-illustrations",
                "semantic-handdrawn-v3": "semantic-handdrawn",
            }.get(visual_system, "xiaohei-illustrations"),
        )
    )
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or tuple(pure.parts[: len(expected_parts)]) != expected_parts
        or len(pure.parts) != len(expected_parts) + 1
        or pure.suffix.lower() not in _IMAGE_SUFFIXES
        or pure.as_posix() != value
    ):
        raise ContentPlanError("visual asset is invalid")
    try:
        snapshot = capture_regular_file(project_root / Path(*pure.parts), within=project_root)
    except ArtifactError:
        raise ContentPlanError("visual asset is invalid") from None
    if not _image_bytes_are_decodable(snapshot.payload, pure.suffix.lower()):
        raise ContentPlanError("visual asset is invalid")
    return value


def _notes(value: object, *, minimum_count: int = 1) -> tuple[ContentNote, ...]:
    if not isinstance(value, list) or not minimum_count <= len(value) <= 3:
        raise ContentPlanError("visible content is invalid")
    result: list[ContentNote] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != _NOTE_FIELDS:
            raise ContentPlanError("content plan schema is invalid")
        result.append(
            ContentNote(
                label=_visible_text(item["label"], minimum=1.0, maximum=6.0),
                text=_visible_text(item["text"], minimum=1.0, maximum=20.0),
            )
        )
    return tuple(result)


def _optional_reason(value: object, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if value is None:
        raise ContentPlanError("no-visual exception is invalid")
    try:
        return _visible_text(value, minimum=1.0, maximum=20.0)
    except ContentPlanError:
        if required:
            raise ContentPlanError("no-visual exception is invalid") from None
        raise


def _semantic_phrases(
    value: object,
    *,
    minimum_count: int,
    maximum_count: int,
    maximum_units: float,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not minimum_count <= len(value) <= maximum_count
    ):
        raise ContentPlanError("content plan schema is invalid")
    try:
        result = tuple(
            _visible_text(item, minimum=1.0, maximum=maximum_units)
            for item in value
        )
    except ContentPlanError:
        raise ContentPlanError("content plan schema is invalid") from None
    if len(set(result)) != len(result):
        raise ContentPlanError("content plan schema is invalid")
    return result


def _overlay_labels(
    value: object, *, maximum_count: int = 4
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_count:
        raise ContentPlanError("content plan schema is invalid")
    result: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or item != item.strip()
            or not item
            or "\n" in item
            or "\r" in item
            or validate_public_handoff(item)
        ):
            raise ContentPlanError("content plan schema is invalid")
        if re.search(r"[\u3400-\u9fff]", item):
            if not 2 <= len(item) <= 6:
                raise ContentPlanError("content plan schema is invalid")
        elif len(item) > 18:
            raise ContentPlanError("content plan schema is invalid")
        result.append(item)
    if len(set(result)) != len(result):
        raise ContentPlanError("content plan schema is invalid")
    return tuple(result)


def _validate_forbidden_metaphors(
    value: object, *, semantic_subjects: tuple[str, ...]
) -> tuple[str, ...]:
    forbidden = _semantic_phrases(
        value,
        minimum_count=0,
        maximum_count=12,
        maximum_units=20.0,
    )
    allowed_omissions: set[str] = set()
    for subject in semantic_subjects:
        for marker, omissions in _PHYSICAL_SUBJECT_OMISSIONS.items():
            if marker in subject:
                allowed_omissions.update(omissions)
    missing = _DEFAULT_FORBIDDEN_METAPHORS - set(forbidden)
    if not missing <= allowed_omissions:
        raise ContentPlanError("content plan schema is invalid")
    return forbidden


def _validate_profiled_scene(
    value: dict[str, object],
    *,
    theme: IllustrationTheme,
    semantic_subjects: tuple[str, ...],
    forbidden_metaphors: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        structure = _semantic_phrases(
            value["theme_structure"],
            minimum_count=1,
            maximum_count=8,
            maximum_units=40.0,
        )
    except ContentPlanError:
        if theme.id == "four-panel-comic-explainer":
            raise ContentPlanError("four-panel-beats-invalid") from None
        raise
    exceptions = _semantic_phrases(
        value["theme_exceptions"],
        minimum_count=0,
        maximum_count=len(_DEFAULT_FORBIDDEN_METAPHORS),
        maximum_units=20.0,
    )
    missing = _DEFAULT_FORBIDDEN_METAPHORS - set(forbidden_metaphors)
    if theme.id == "four-panel-comic-explainer":
        if len(structure) != 4 or len(set(structure)) != 4:
            raise ContentPlanError("four-panel-beats-invalid")
    elif theme.id == "blue-black-whiteboard-explainer":
        if len(structure) != 1 or structure[0] not in {
            "流程",
            "系统",
            "分组",
            "对比",
            "循环",
        }:
            raise ContentPlanError("content plan schema is invalid")
    elif len(structure) > 3:
        raise ContentPlanError("content plan schema is invalid")
    if theme.id == "engineering-sketch-explainer":
        if set(exceptions) != missing:
            raise ContentPlanError("content plan schema is invalid")
        allowed: set[str] = set()
        for subject in semantic_subjects:
            for marker, omissions in _PHYSICAL_SUBJECT_OMISSIONS.items():
                if marker in subject:
                    allowed.update(omissions)
        if not set(exceptions) <= allowed:
            raise ContentPlanError("content plan schema is invalid")
    elif exceptions or missing:
        raise ContentPlanError("content plan schema is invalid")
    return structure, exceptions


def validate_content_plan(
    candidate: object,
    *,
    project_root: Path,
    project_id: str,
    handoff_segments: tuple[HandoffSegment, ...],
    handoff_visual: HandoffVisualContract,
    narration_segments: tuple[str, ...],
    require_xiaohei_text_layer: bool = False,
) -> ContentPlan:
    """Validate an exact P1 plan against its public text and local asset contract."""

    if not isinstance(candidate, dict):
        raise ContentPlanError("content plan schema is invalid")
    schema_version = candidate.get("schema_version")
    is_v2 = schema_version == 2 and not isinstance(schema_version, bool)
    is_v3 = schema_version == 3 and not isinstance(schema_version, bool)
    is_v4 = schema_version == 4 and not isinstance(schema_version, bool)
    expected_top = (
        _V4_TOP_LEVEL_FIELDS
        if is_v4
        else _V3_TOP_LEVEL_FIELDS
        if is_v3
        else _V2_TOP_LEVEL_FIELDS
        if is_v2
        else _TOP_LEVEL_FIELDS
    )
    if set(candidate) != expected_top:
        raise ContentPlanError("content plan schema is invalid")
    if (
        not _SAFE_PROJECT_ID.fullmatch(project_id)
        or candidate["project_id"] != project_id
    ):
        raise ContentPlanError("project identity is invalid")
    if (
        schema_version not in {1, 2, 3, 4}
        or isinstance(schema_version, bool)
        or candidate["ratio"] != "16:9"
        or candidate["visual_system"]
        != (
            PROFILED_VISUAL_SYSTEM
            if is_v4
            else "semantic-handdrawn-v3"
            if is_v3
            else "editorial-motion-v2"
            if is_v2
            else "xiaohei-white-first-v1"
        )
        or (
            (is_v2 or is_v3 or is_v4)
            and candidate.get("illustration_skill") != "ra-video-illustrations"
        )
        or candidate["typography_scale"] != "mobile-readable"
        or candidate["caption_style"] != "anchor-dark"
        or not isinstance(candidate["scenes"], list)
        or not candidate["scenes"]
    ):
        raise ContentPlanError("content plan schema is invalid")
    theme: IllustrationTheme | None = None
    if is_v4:
        try:
            theme = get_theme(candidate["visual_theme"])
        except IllustrationThemeError:
            raise ContentPlanError("content plan schema is invalid") from None
    if (
        schema_version != handoff_visual.schema_version
        or candidate["visual_system"] != handoff_visual.visual_system
        or (theme.id if theme is not None else None) != handoff_visual.visual_theme
        or (
            (is_v2 or is_v3 or is_v4)
            and candidate.get("illustration_skill")
            != handoff_visual.illustration_skill
        )
    ):
        raise ContentPlanError("handoff visual contract is invalid")
    handoff_texts = tuple(_normalize_segment_text(item.text) for item in handoff_segments)
    narration_texts = tuple(_normalize_segment_text(item) for item in narration_segments)
    if (
        tuple(item.id for item in handoff_segments)
        != tuple(f"segment-{index:03d}" for index in range(1, len(narration_texts) + 1))
        or handoff_texts != narration_texts
    ):
        raise ContentPlanError("narration contract is invalid")

    scenes: list[ContentScene] = []
    used_segments: list[str] = []
    visual_uses: dict[str, int] = {}
    for index, value in enumerate(candidate["scenes"], start=1):
        version_fields = (
            _V4_SCENE_FIELDS
            if is_v4
            else _V3_SCENE_FIELDS
            if is_v3
            else _V2_SCENE_FIELDS
            if is_v2
            else _V1_SCENE_FIELDS
        )
        required_version_fields = (
            version_fields
            if is_v2 or is_v3 or is_v4 or require_xiaohei_text_layer
            else set()
        )
        if (
            not isinstance(value, dict)
            or not (_SCENE_REQUIRED_FIELDS | required_version_fields) <= set(value)
            or set(value)
            - (_SCENE_REQUIRED_FIELDS | _SCENE_OPTIONAL_FIELDS | version_fields)
        ):
            raise ContentPlanError("content plan schema is invalid")
        expected_scene_id = f"scene-{index:02d}"
        segment_ids = value["narration_segment_ids"]
        if (
            value["id"] != expected_scene_id
            or not isinstance(segment_ids, list)
            or not segment_ids
            or not all(isinstance(item, str) for item in segment_ids)
        ):
            raise ContentPlanError("narration segment coverage is invalid")
        used_segments.extend(segment_ids)
        layout = value["layout_variant"]
        if layout not in _LAYOUT_VARIANTS:
            raise ContentPlanError("visible content is invalid")
        visual_mode = value.get("visual_mode")
        if is_v3 and (
            (
                handoff_visual.target == TYPE_LED_TARGET
                and visual_mode != "type-led"
            )
            or (
                handoff_visual.target == "semantic-handdrawn-v3"
                and visual_mode == "type-led"
            )
        ):
            raise ContentPlanError("handoff visual contract is invalid")
        asset = _visual_asset(
            value["visual_asset"],
            project_root=project_root,
            visual_system=str(candidate["visual_system"]),
            visual_mode=visual_mode if isinstance(visual_mode, str) else None,
            visual_theme=theme.id if theme is not None else None,
        )
        visual_type = value.get("visual_type")
        visual_style = value.get("visual_style")
        if is_v2:
            if visual_type not in _VISUAL_TYPES or visual_style not in _VISUAL_STYLES:
                raise ContentPlanError("content plan schema is invalid")
        elif is_v3 or is_v4:
            if (
                visual_type not in _VISUAL_TYPES
                or (
                    visual_style != theme.id
                    if theme is not None
                    else visual_style not in _V3_VISUAL_STYLES
                )
                or visual_mode not in _V3_VISUAL_MODES
            ):
                raise ContentPlanError("content plan schema is invalid")
        elif visual_type is not None or visual_style is not None:
            raise ContentPlanError("content plan schema is invalid")
        semantic_subjects: tuple[str, ...] = ()
        semantic_action: str | None = None
        required_visual_evidence: tuple[str, ...] = ()
        forbidden_metaphors: tuple[str, ...] = ()
        overlay_labels: tuple[str, ...] = ()
        theme_structure: tuple[str, ...] = ()
        theme_exceptions: tuple[str, ...] = ()
        if is_v3 or is_v4:
            semantic_subjects = _semantic_phrases(
                value["semantic_subjects"],
                minimum_count=1,
                maximum_count=4,
                maximum_units=20.0,
            )
            try:
                semantic_action = _visible_text(
                    value["semantic_action"], minimum=1.0, maximum=80.0
                )
            except ContentPlanError:
                raise ContentPlanError("content plan schema is invalid") from None
            required_visual_evidence = _semantic_phrases(
                value["required_visual_evidence"],
                minimum_count=1,
                maximum_count=4,
                maximum_units=40.0,
            )
            forbidden_metaphors = _validate_forbidden_metaphors(
                value["forbidden_metaphors"],
                semantic_subjects=semantic_subjects,
            )
            overlay_maximum = (
                8
                if theme is not None
                and theme.id == "four-panel-comic-explainer"
                else 5
                if theme is not None
                and theme.id
                in {
                    "vivid-comic-explainer",
                    "blue-black-whiteboard-explainer",
                }
                else 4
            )
            overlay_labels = _overlay_labels(
                value["overlay_labels"], maximum_count=overlay_maximum
            )
            if theme is not None:
                theme_structure, theme_exceptions = _validate_profiled_scene(
                    value,
                    theme=theme,
                    semantic_subjects=semantic_subjects,
                    forbidden_metaphors=forbidden_metaphors,
                )
        elif not is_v2:
            overlay_labels = _overlay_labels(
                value.get("overlay_labels", []), maximum_count=4
            )
            if require_xiaohei_text_layer and not overlay_labels:
                raise ContentPlanError("xiaohei text layer is required")
        if (is_v3 or is_v4) and visual_mode == "type-led":
            if value.get("no_visual_reason") is not None:
                raise ContentPlanError("no-visual exception is invalid")
            no_visual_reason = None
        else:
            no_visual_reason = _optional_reason(
                value.get("no_visual_reason"), required=asset is None
            )
        if asset is not None and no_visual_reason is not None:
            raise ContentPlanError("no-visual exception is invalid")
        reuse_reason = _optional_reason(value.get("reuse_reason"))
        if asset is not None:
            prior_uses = visual_uses.get(asset, 0)
            if prior_uses and reuse_reason is None:
                raise ContentPlanError("visual asset reuse is invalid")
            visual_uses[asset] = prior_uses + 1
        scenes.append(
            ContentScene(
                id=expected_scene_id,
                narration_segment_ids=tuple(segment_ids),
                chapter=_visible_text(value["chapter"], minimum=1.0, maximum=12.0),
                label=_visible_text(value["label"], minimum=1.0, maximum=12.0),
                progress=_visible_text(value["progress"], minimum=0.5, maximum=12.0),
                title_lines=_string_lines(
                    value["title_lines"], minimum_count=1, maximum_count=2, maximum_units=16.0
                ),
                subtitle_lines=_string_lines(
                    value["subtitle_lines"], minimum_count=0, maximum_count=2, maximum_units=28.0
                ),
                kicker=_visible_text(value["kicker"], minimum=1.0, maximum=12.0),
                notes=_notes(value["notes"], minimum_count=0 if is_v2 else 1),
                visual_intent=_visible_text(
                    value["visual_intent"], minimum=1.0, maximum=80.0
                ),
                visual_asset=asset,
                layout_variant=layout,
                visual_type=visual_type if isinstance(visual_type, str) else None,
                visual_style=visual_style if isinstance(visual_style, str) else None,
                visual_mode=visual_mode if isinstance(visual_mode, str) else None,
                semantic_subjects=semantic_subjects,
                semantic_action=semantic_action,
                required_visual_evidence=required_visual_evidence,
                forbidden_metaphors=forbidden_metaphors,
                overlay_labels=overlay_labels,
                no_visual_reason=no_visual_reason,
                reuse_reason=reuse_reason,
                theme_structure=theme_structure,
                theme_exceptions=theme_exceptions,
            )
        )
    expected_segments = [
        f"segment-{index:03d}" for index in range(1, len(narration_segments) + 1)
    ]
    if used_segments != expected_segments:
        raise ContentPlanError("narration segment coverage is invalid")
    plan = ContentPlan(
        schema_version=4 if is_v4 else 3 if is_v3 else 2 if is_v2 else 1,
        project_id=project_id,
        ratio="16:9",
        visual_system=(
            PROFILED_VISUAL_SYSTEM
            if is_v4
            else "semantic-handdrawn-v3"
            if is_v3
            else "editorial-motion-v2"
            if is_v2
            else "xiaohei-white-first-v1"
        ),
        typography_scale="mobile-readable",
        caption_style="anchor-dark",
        scenes=tuple(scenes),
        illustration_skill=(
            "ra-video-illustrations" if is_v2 or is_v3 or is_v4 else None
        ),
        visual_theme=theme.id if theme is not None else None,
    )
    if validate_public_handoff(encode_canonical_json(_plan_dict(plan)).decode("utf-8")):
        raise ContentPlanError("public content is invalid")
    return plan


def _plan_dict(plan: ContentPlan) -> dict[str, object]:
    scene_values: list[dict[str, object]] = []
    for scene in plan.scenes:
        value: dict[str, object] = {
            "id": scene.id,
            "narration_segment_ids": list(scene.narration_segment_ids),
            "chapter": scene.chapter,
            "label": scene.label,
            "progress": scene.progress,
            "title_lines": list(scene.title_lines),
            "subtitle_lines": list(scene.subtitle_lines),
            "kicker": scene.kicker,
            "notes": [{"label": note.label, "text": note.text} for note in scene.notes],
            "visual_intent": scene.visual_intent,
            "visual_asset": scene.visual_asset,
            "layout_variant": scene.layout_variant,
        }
        if scene.no_visual_reason is not None:
            value["no_visual_reason"] = scene.no_visual_reason
        if scene.reuse_reason is not None:
            value["reuse_reason"] = scene.reuse_reason
        if scene.visual_type is not None:
            value["visual_type"] = scene.visual_type
        if scene.visual_style is not None:
            value["visual_style"] = scene.visual_style
        if scene.visual_mode is not None:
            value["visual_mode"] = scene.visual_mode
            value["semantic_subjects"] = list(scene.semantic_subjects)
            value["semantic_action"] = scene.semantic_action
            value["required_visual_evidence"] = list(
                scene.required_visual_evidence
            )
            value["forbidden_metaphors"] = list(scene.forbidden_metaphors)
            value["overlay_labels"] = list(scene.overlay_labels)
        elif plan.schema_version == 1:
            value["overlay_labels"] = list(scene.overlay_labels)
        if scene.theme_structure:
            value["theme_structure"] = list(scene.theme_structure)
            value["theme_exceptions"] = list(scene.theme_exceptions)
        scene_values.append(value)
    result: dict[str, object] = {
        "schema_version": plan.schema_version,
        "project_id": plan.project_id,
        "ratio": plan.ratio,
        "visual_system": plan.visual_system,
        "typography_scale": plan.typography_scale,
        "caption_style": plan.caption_style,
        "scenes": scene_values,
    }
    if plan.illustration_skill is not None:
        result["illustration_skill"] = plan.illustration_skill
    if plan.visual_theme is not None:
        result["visual_theme"] = plan.visual_theme
    return result


def _dated_predecessor_contract_path(
    contract_path: Path,
    *,
    project_root: Path,
    active_project_root: Path,
) -> Path | None:
    match = re.fullmatch(
        r"\d{4}-\d{2}-\d{2}-(?P<slug>[a-z0-9][a-z0-9-]{0,79})",
        active_project_root.name,
    )
    if match is None or active_project_root.parent.name != "制作中":
        return None
    predecessor = active_project_root.parent / match.group("slug")
    try:
        relative = contract_path.relative_to(predecessor)
    except ValueError:
        return None
    return project_root / relative


def _manifest_contract(
    project_root: Path,
    *,
    prearchive_project_root: Path | None = None,
) -> tuple[tuple[str, ...], FileSnapshot, FileSnapshot]:
    manifest_path = project_root / "工程" / "media" / "voice_manifest.json"
    try:
        manifest_value, manifest_snapshot = load_json_snapshot(
            manifest_path, within=project_root
        )
    except ArtifactError:
        raise ContentPlanError("narration contract is invalid") from None
    if not isinstance(manifest_value, dict):
        raise ContentPlanError("narration contract is invalid")
    contract_path_value = manifest_value.get("segment_contract_path")
    expected_hash = manifest_value.get("segment_contract_sha256")
    expected_count = manifest_value.get("segment_count")
    if (
        not isinstance(contract_path_value, str)
        or not contract_path_value
        or not isinstance(expected_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        or not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count <= 0
    ):
        raise ContentPlanError("narration contract is invalid")
    contract_path = Path(contract_path_value)
    if not contract_path.is_absolute():
        contract_path = manifest_path.parent / contract_path
    else:
        active_root = prearchive_project_root or project_root
        if prearchive_project_root is not None:
            try:
                relative = contract_path.relative_to(prearchive_project_root)
            except ValueError:
                pass
            else:
                contract_path = project_root / relative
        predecessor_path = _dated_predecessor_contract_path(
            contract_path,
            project_root=project_root,
            active_project_root=active_root,
        )
        if predecessor_path is not None:
            contract_path = predecessor_path
    try:
        contract_snapshot = capture_regular_file(contract_path)
    except ArtifactError:
        raise ContentPlanError("narration contract is invalid") from None
    narration_segments = _parse_narration_contract(contract_snapshot)
    if (
        contract_snapshot.sha256 != expected_hash
        or len(narration_segments) != expected_count
    ):
        raise ContentPlanError("narration contract is invalid")
    return narration_segments, manifest_snapshot, contract_snapshot


def compile_content_plan(
    *, project_root: Path, candidate_path: Path
) -> ContentPlanSnapshot:
    """Compile and no-clobber publish one reviewed candidate in an active project."""

    root = Path(project_root).absolute()
    candidate = Path(candidate_path).absolute()
    try:
        candidate_value, candidate_snapshot = load_json_snapshot(candidate, within=root)
        handoff_snapshot = capture_regular_file(project_root / "交接稿.md", within=root)
        handoff_value = handoff_snapshot.payload.decode("utf-8")
    except (ArtifactError, UnicodeDecodeError, OSError):
        raise ContentPlanError("content plan input is invalid") from None
    handoff_segments = parse_handoff_segments(handoff_value)
    handoff_visual = parse_handoff_visual_contract(handoff_value)
    narration_segments, manifest_snapshot, narration_snapshot = _manifest_contract(root)
    plan = validate_content_plan(
        candidate_value,
        project_root=root,
        project_id=root.name,
        handoff_segments=handoff_segments,
        handoff_visual=handoff_visual,
        narration_segments=narration_segments,
        require_xiaohei_text_layer=True,
    )
    snapshots = (
        candidate_snapshot,
        handoff_snapshot,
        manifest_snapshot,
        narration_snapshot,
    )
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        raise ContentPlanError("content plan input changed")
    formal = root / "工程" / "content-plan.json"
    try:
        published = publish_bytes_no_clobber(
            formal, encode_canonical_json(_plan_dict(plan)), within=root
        )
    except ArtifactError:
        raise ContentPlanError("content plan publication failed") from None
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        try:
            if snapshot_matches(published):
                published.path.unlink()
        except OSError:
            pass
        raise ContentPlanError("content plan input changed")
    return ContentPlanSnapshot(
        path=formal.absolute(),
        plan=plan,
        snapshot=published,
        candidate_snapshot=candidate_snapshot,
        handoff_snapshot=handoff_snapshot,
        manifest_snapshot=manifest_snapshot,
        narration_contract_snapshot=narration_snapshot,
    )


def load_content_plan_snapshot(
    *,
    project_root: Path,
    prearchive_project_root: Path | None = None,
) -> ContentPlanSnapshot:
    """Reload and revalidate one formal plan against its current public inputs."""

    root = Path(project_root).absolute()
    formal = root / "工程" / "content-plan.json"
    try:
        value, formal_snapshot = load_json_snapshot(formal, within=root)
        handoff_snapshot = capture_regular_file(root / "交接稿.md", within=root)
        handoff_text = handoff_snapshot.payload.decode("utf-8")
    except (ArtifactError, UnicodeDecodeError):
        raise ContentPlanError("content plan input is invalid") from None
    handoff_segments = parse_handoff_segments(handoff_text)
    handoff_visual = parse_handoff_visual_contract(handoff_text)
    narration_segments, manifest_snapshot, narration_snapshot = _manifest_contract(
        root,
        prearchive_project_root=(
            Path(prearchive_project_root).absolute()
            if prearchive_project_root is not None
            else None
        ),
    )
    plan = validate_content_plan(
        value,
        project_root=root,
        project_id=root.name,
        handoff_segments=handoff_segments,
        handoff_visual=handoff_visual,
        narration_segments=narration_segments,
    )
    snapshots = (
        formal_snapshot,
        handoff_snapshot,
        manifest_snapshot,
        narration_snapshot,
    )
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        raise ContentPlanError("content plan input changed")
    return ContentPlanSnapshot(
        path=formal,
        plan=plan,
        snapshot=formal_snapshot,
        candidate_snapshot=None,
        handoff_snapshot=handoff_snapshot,
        manifest_snapshot=manifest_snapshot,
        narration_contract_snapshot=narration_snapshot,
    )

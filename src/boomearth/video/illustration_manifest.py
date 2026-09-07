"""Strict prompt and local raster provenance for editorial video illustrations."""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

from boomearth.video.artifacts import ArtifactError, FileSnapshot, capture_regular_file, load_json_snapshot
from boomearth.video.content_plan import ContentPlan, ContentPlanError, load_content_plan_snapshot
from boomearth.video.illustration_themes import (
    PROFILED_VISUAL_SYSTEM,
    IllustrationThemeError,
    get_theme,
)
from boomearth.video.profiled_generation import (
    GENERATION_ARTIFACT_KEYS,
    HISTORY_ADAPTATION_ARTIFACT_KEYS,
    ProfiledGenerationError,
    validate_profiled_generation_evidence,
    validate_profiled_reuse_evidence,
    validate_user_approved_history_adaptation_evidence,
)
from boomearth.video.semantic_qc import SemanticQCError, validate_semantic_qc


_TOP_KEYS = {"schema_version", "illustration_skill", "visual_system", "assets"}
_V3_TOP_KEYS = _TOP_KEYS | {
    "content_plan_sha256",
    "semantic_qc_path",
    "semantic_qc_sha256",
}
_V4_TOP_KEYS = _V3_TOP_KEYS | {"visual_theme"}
_XIAOHEI_TOP_KEYS = _V3_TOP_KEYS | {
    "provider",
    "model",
    "cloud_resolution_class",
    "local_output_contract",
}
_ASSET_KEYS = {
    "scene_id", "visual_type", "visual_style", "prompt_path", "prompt_sha256",
    "asset_path", "asset_sha256", "width", "height", "qc_status",
}
_PROMPT_KEYS = {
    "scene_id", "visual_type", "visual_style", "ratio", "target_size",
    "text_policy", "caption_safe_zone",
}
_V3_ASSET_KEYS = {
    "scene_id",
    "visual_type",
    "visual_style",
    "visual_mode",
    "contract_path",
    "contract_sha256",
    "candidate_artifacts",
    "selected_asset_path",
    "selected_asset_sha256",
    "width",
    "height",
    "format",
    "ratio",
    "qc_status",
}
_V3_CANDIDATE_KEYS = {"path", "sha256"}
_XIAOHEI_ASSET_KEYS = {
    "scene_id",
    "prompt_path",
    "prompt_sha256",
    "candidate_artifacts",
    "selected_asset_path",
    "selected_asset_sha256",
    "qc_status",
}
_XIAOHEI_CANDIDATE_KEYS = {"path", "sha256", "status"}
_XIAOHEI_QC_TOP_KEYS = {
    "schema_version",
    "visual_system",
    "project_id",
    "content_plan_sha256",
    "reviewer_type",
    "status",
    "scenes",
}
_XIAOHEI_QC_SCENE_KEYS = {
    "scene_id",
    "contract_path",
    "contract_sha256",
    "selected_asset_path",
    "selected_asset_sha256",
    "relevance_rationale",
    "checks",
    "status",
}
_XIAOHEI_QC_CHECK_KEYS = {
    "subject_match",
    "action_match",
    "evidence_complete",
    "claim_readable",
    "non_generic",
    "forbidden_absent",
    "mobile_readable",
    "caption_safe",
}
_V3_PROMPT_KEYS = _PROMPT_KEYS | {"visual_mode"}
_V4_PROMPT_KEYS = _V3_PROMPT_KEYS | {"visual_system", "visual_theme"}
_V4_SECTION_PREFIXES = (
    "当前场景唯一判断",
    "语义主体",
    "核心动作或关系",
    "必须可见的证据",
    "构图、左侧程序文字区和底部字幕安全区",
    "当前主题的线条、材质和色板",
    "当前主题专项结构",
    "为什么画面能解释判断",
    "overlay labels，仅供 renderer",
    "禁止意象、文字、Logo、水印、UI、PPT 页面和无关装饰",
)


def _v4_section_heading_matches(*, index: int, line: str, prefix: str) -> bool:
    if line.startswith(f"{index}. {prefix}"):
        return True
    if index == 9:
        return line.startswith("9. overlay labels：") and "仅供 renderer" in line
    if index == 10:
        return (
            line.startswith("10. 禁止意象：")
            and all(marker in line for marker in ("Logo", "水印", "PPT"))
            and ("文字" in line or "标题" in line)
        )
    return False
_TYPE_CONTRACT_KEYS = {
    "schema_version",
    "scene_id",
    "visual_mode",
    "ratio",
    "target_size",
    "caption_safe_zone",
    "labels",
    "layout",
    "connectors",
}
_TYPE_LAYOUTS = {"comparison", "flow", "stack", "quadrant"}
_TYPE_CONNECTORS = {"path", "underline", "divider", "arrow"}
_DIGEST = re.compile(r"[0-9a-f]{64}")


class IllustrationManifestError(RuntimeError):
    """A fixed, source-redacted illustration provenance failure."""


@dataclass(frozen=True, slots=True)
class IllustrationAsset:
    scene_id: str
    visual_type: str
    visual_style: str
    prompt_path: str
    prompt_sha256: str
    asset_path: str
    asset_sha256: str
    width: int
    height: int
    qc_status: str


@dataclass(frozen=True, slots=True)
class SemanticIllustrationAsset:
    scene_id: str
    visual_type: str
    visual_style: str
    visual_mode: str
    contract_path: str
    contract_sha256: str
    candidate_paths: tuple[str, ...]
    candidate_sha256: tuple[str, ...]
    selected_asset_path: str | None
    selected_asset_sha256: str | None
    width: int | None
    height: int | None
    format: str | None
    ratio: str
    qc_status: str


@dataclass(frozen=True, slots=True)
class IllustrationManifest:
    schema_version: int
    illustration_skill: str
    visual_system: str
    assets: tuple[IllustrationAsset | SemanticIllustrationAsset, ...]
    content_plan_sha256: str | None = None
    semantic_qc_path: str | None = None
    semantic_qc_sha256: str | None = None
    visual_theme: str | None = None


@dataclass(frozen=True, slots=True)
class IllustrationManifestSnapshot:
    path: Path
    manifest: IllustrationManifest
    snapshot: FileSnapshot
    content_plan_snapshot: FileSnapshot
    prompt_snapshots: tuple[FileSnapshot, ...]
    asset_snapshots: tuple[FileSnapshot, ...]
    candidate_snapshots: tuple[FileSnapshot, ...] = ()
    semantic_qc_snapshot: FileSnapshot | None = None


def _safe_path(value: object, *, kind: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise IllustrationManifestError("illustration-path-invalid")
    path = PurePosixPath(value)
    expected = (
        ("工程", "assets", "editorial-illustrations", "prompts")
        if kind == "prompt"
        else ("工程", "assets", "editorial-illustrations")
    )
    if (
        path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or tuple(path.parts[: len(expected)]) != expected
        or len(path.parts) != len(expected) + 1
        or path.as_posix() != value
        or (kind == "prompt" and path.suffix != ".md")
        or (kind == "asset" and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"})
    ):
        raise IllustrationManifestError("illustration-path-invalid")
    return path


def _prompt_contract(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0] != "---":
        raise IllustrationManifestError("illustration-prompt-invalid")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(": ")
        if separator != ": " or key in values:
            raise IllustrationManifestError("illustration-prompt-invalid")
        values[key] = value
    body = "\n".join(lines[end + 1 :]).strip()
    if set(values) != _PROMPT_KEYS or not body or any(f"{index}." not in body for index in range(1, 7)):
        raise IllustrationManifestError("illustration-prompt-invalid")
    if (
        values["ratio"] != "16:9"
        or values["target_size"] != "3840x2160"
        or values["text_policy"] != "none"
        or values["caption_safe_zone"] != "bottom-150px"
    ):
        raise IllustrationManifestError("illustration-prompt-invalid")
    return values


def _v3_path(value: object, *, kind: str, scene_id: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise IllustrationManifestError("illustration-path-invalid")
    path = PurePosixPath(value)
    if kind == "prompt":
        expected_parent = ("工程", "assets", "semantic-handdrawn", "prompts")
        expected_name = f"{scene_id}.md"
    elif kind == "type-led":
        expected_parent = ("工程", "assets", "semantic-handdrawn", "type-led")
        expected_name = f"{scene_id}.json"
    elif kind == "candidate":
        expected_parent = ("工程", "assets", "semantic-handdrawn", "candidates")
        expected_name = None
    elif kind == "asset":
        expected_parent = ("工程", "assets", "semantic-handdrawn")
        expected_name = None
    elif kind == "qc":
        expected_parent = ("工程", "assets", "semantic-handdrawn")
        expected_name = "semantic-qc.json"
    else:
        raise IllustrationManifestError("illustration-path-invalid")
    suffix_ok = (
        path.suffix == ".md"
        if kind == "prompt"
        else path.suffix == ".json"
        if kind in {"type-led", "qc"}
        else path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or tuple(path.parts[:-1]) != expected_parent
        or path.as_posix() != value
        or not suffix_ok
        or (expected_name is not None and path.name != expected_name)
        or (
            kind == "candidate"
            and re.fullmatch(
                rf"{re.escape(scene_id)}-candidate-0[12]\.(?:png|jpg|jpeg|webp)",
                path.name,
                re.IGNORECASE,
            )
            is None
        )
        or (
            kind == "asset"
            and re.fullmatch(
                rf"{re.escape(scene_id)}\.(?:png|jpg|jpeg|webp)",
                path.name,
                re.IGNORECASE,
            )
            is None
        )
    ):
        raise IllustrationManifestError("illustration-path-invalid")
    return path


def _v3_prompt_contract(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0] != "---":
        raise IllustrationManifestError("illustration-prompt-invalid")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, raw = line.partition(": ")
        if separator != ": " or key in values:
            raise IllustrationManifestError("illustration-prompt-invalid")
        values[key] = raw
    body = "\n".join(lines[end + 1 :]).strip()
    if (
        set(values) != _V3_PROMPT_KEYS
        or not body
        or any(f"{index}." not in body for index in range(1, 9))
        or values["ratio"] != "16:9"
        or values["target_size"] != "3840x2160"
        or values["text_policy"] != "none"
        or values["caption_safe_zone"] != "bottom-150px"
    ):
        raise IllustrationManifestError("illustration-prompt-invalid")
    return values


def _profiled_path(
    value: object, *, kind: str, scene_id: str, theme_id: str
) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise IllustrationManifestError("illustration-path-invalid")
    path = PurePosixPath(value)
    root = ("工程", "assets", "profiled-illustrations", theme_id)
    if kind == "prompt":
        expected_parent = root + ("prompts",)
        name_valid = path.name == f"{scene_id}.md"
    elif kind == "generation-prompt":
        expected_parent = root + ("prompts",)
        name_valid = path.name in {
            f"{scene_id}.md",
            f"{scene_id}-repair-02.md",
        }
    elif kind == "candidate":
        expected_parent = root + ("candidates",)
        name_valid = (
            re.fullmatch(
                rf"{re.escape(scene_id)}-candidate-(\d{{2}})\.png",
                path.name,
            )
            is not None
        )
    elif kind == "asset":
        expected_parent = root
        name_valid = path.name == f"{scene_id}.png"
    elif kind == "qc":
        expected_parent = root
        name_valid = path.name == "semantic-qc.json"
    else:
        raise IllustrationManifestError("illustration-path-invalid")
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or tuple(path.parts[:-1]) != expected_parent
        or path.as_posix() != value
        or not name_valid
    ):
        raise IllustrationManifestError("illustration-path-invalid")
    return path


def _v4_prompt_contract(payload: bytes) -> dict[str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0] != "---":
        raise IllustrationManifestError("illustration-prompt-invalid")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, raw = line.partition(": ")
        if separator != ": " or key in values:
            raise IllustrationManifestError("illustration-prompt-invalid")
        values[key] = raw
    body = "\n".join(lines[end + 1 :]).strip()
    section_lines = [
        match.group(0).strip()
        for match in re.finditer(r"(?m)^\s*\d+\.\s+\S.*$", body)
    ]
    if (
        set(values) != _V4_PROMPT_KEYS
        or len(section_lines) != len(_V4_SECTION_PREFIXES)
        or any(
            not _v4_section_heading_matches(
                index=index,
                line=line,
                prefix=prefix,
            )
            for index, (line, prefix) in enumerate(
                zip(section_lines, _V4_SECTION_PREFIXES, strict=True), 1
            )
        )
        or values["visual_system"] != PROFILED_VISUAL_SYSTEM
        or values["ratio"] != "16:9"
        or values["target_size"] != "3840x2160"
        or values["text_policy"]
        != (
            "embedded"
            if values["visual_theme"] in {"xiaohuang-warm-first-v1", "sponge-host-handdrawn-v1"}
            and values["visual_style"] == values["visual_theme"]
            else "none"
        )
        or values["caption_safe_zone"] != "bottom-150px"
    ):
        raise IllustrationManifestError("illustration-prompt-invalid")
    return values


def validate_v4_prompt_contract(payload: bytes) -> dict[str, str]:
    """Validate a V4 prompt before any generation evidence is published."""
    return _v4_prompt_contract(payload)


def _strict_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise IllustrationManifestError("illustration-prompt-invalid")
        result[key] = value
    return result


def _type_contract(payload: bytes, *, scene_id: str, labels: tuple[str, ...]) -> None:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_json_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                IllustrationManifestError("illustration-prompt-invalid")
            ),
        )
    except IllustrationManifestError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    if (
        not isinstance(value, dict)
        or set(value) != _TYPE_CONTRACT_KEYS
        or value["schema_version"] != 1
        or isinstance(value["schema_version"], bool)
        or value["scene_id"] != scene_id
        or value["visual_mode"] != "type-led"
        or value["ratio"] != "16:9"
        or value["target_size"] != "3840x2160"
        or value["caption_safe_zone"] != "bottom-150px"
        or value["labels"] != list(labels)
        or value["layout"] not in _TYPE_LAYOUTS
        or not isinstance(value["connectors"], list)
        or len(value["connectors"]) > 4
        or any(item not in _TYPE_CONNECTORS for item in value["connectors"])
        or len(set(value["connectors"])) != len(value["connectors"])
    ):
        raise IllustrationManifestError("illustration-prompt-invalid")


def _image_contract(
    snapshot: FileSnapshot,
    *,
    relative: PurePosixPath,
    expected_width: int,
    expected_height: int,
    expected_format: str,
) -> None:
    try:
        with Image.open(io.BytesIO(snapshot.payload)) as decoded:
            detected = (decoded.format or "").lower()
            width, height = decoded.size
            decoded.verify()
        with Image.open(io.BytesIO(snapshot.payload)) as decoded:
            decoded.load()
    except (OSError, UnidentifiedImageError):
        raise IllustrationManifestError("illustration-image-invalid") from None
    suffix_format = {
        ".jpg": "jpeg",
        ".jpeg": "jpeg",
        ".png": "png",
        ".webp": "webp",
    }[relative.suffix.lower()]
    if (
        detected != suffix_format
        or detected != expected_format
        or width != expected_width
        or height != expected_height
    ):
        raise IllustrationManifestError("illustration-image-invalid")


def _xiaohei_path(value: object, *, kind: str, scene_id: str) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise IllustrationManifestError("illustration-path-invalid")
    path = PurePosixPath(value)
    root = ("工程", "assets", "xiaohei-illustrations")
    expected = {
        "prompt": (*root, "prompts", f"{scene_id}.md"),
        "candidate": (*root, "candidates", f"{scene_id}-candidate-01.png"),
        "asset": (*root, f"{scene_id}.png"),
        "qc": (*root, "semantic-qc.json"),
    }[kind]
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or path.as_posix() != value
        or path.parts != expected
    ):
        raise IllustrationManifestError("illustration-path-invalid")
    return path


def _xiaohei_candidate_contract(snapshot: FileSnapshot) -> None:
    try:
        with Image.open(io.BytesIO(snapshot.payload)) as decoded:
            detected = (decoded.format or "").lower()
            width, height = decoded.size
            decoded.verify()
        with Image.open(io.BytesIO(snapshot.payload)) as decoded:
            decoded.load()
    except (OSError, UnidentifiedImageError):
        raise IllustrationManifestError("illustration-image-invalid") from None
    if (
        detected != "png"
        or width < 1024
        or height < 576
        or abs((width / height) - (16 / 9)) > 0.01
    ):
        raise IllustrationManifestError("illustration-image-invalid")


def _validate_xiaohei_prompt_contract(payload: bytes, *, scene: object) -> None:
    """Bind new Xiaohei prompts to their reviewed native handwritten labels."""

    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        raise IllustrationManifestError("illustration-prompt-invalid") from None
    text_mode = getattr(scene, "illustration_text_mode", "legacy-overlay")
    if text_mode == "legacy-overlay":
        if not text.strip():
            raise IllustrationManifestError("illustration-prompt-invalid")
        return
    if text_mode not in {"embedded", "local-fallback"}:
        raise IllustrationManifestError("illustration-prompt-invalid")
    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    expected_policy = f"text_policy: {text_mode}"
    label_prefix = "handwritten_labels: "
    label_lines = tuple(line for line in lines if line.startswith(label_prefix))
    labels = tuple(getattr(scene, "overlay_labels", ()))
    if (
        expected_policy not in lines
        or "text_policy: none" in lines
        or len(label_lines) != 1
        or tuple(item.strip() for item in label_lines[0][len(label_prefix) :].split("|"))
        != labels
    ):
        raise IllustrationManifestError("illustration-prompt-invalid")


def _validate_xiaohei_manifest(
    value: object, *, project_root: Path, content_plan: ContentPlan
) -> IllustrationManifest:
    if not isinstance(value, dict) or set(value) != _XIAOHEI_TOP_KEYS:
        raise IllustrationManifestError("illustration-manifest-invalid")
    local_output = value.get("local_output_contract")
    if (
        value.get("schema_version") != 1
        or isinstance(value.get("schema_version"), bool)
        or value.get("illustration_skill") != "katerj-xiaohei-illustrations"
        or value.get("visual_system") != "xiaohei-white-first-v1"
        or content_plan.schema_version != 1
        or content_plan.visual_system != "xiaohei-white-first-v1"
        or value.get("provider") != "OpenAI runtime-native imagegen"
        or value.get("model") != "runtime-native-current"
        or value.get("cloud_resolution_class") != "1K"
        or local_output
        != {"width": 1920, "height": 1080, "format": "png", "ratio": "16:9"}
        or not isinstance(value.get("assets"), list)
        or len(value["assets"]) != len(content_plan.scenes)
        or not isinstance(value.get("content_plan_sha256"), str)
        or _DIGEST.fullmatch(value["content_plan_sha256"]) is None
        or not isinstance(value.get("semantic_qc_sha256"), str)
        or _DIGEST.fullmatch(value["semantic_qc_sha256"]) is None
    ):
        raise IllustrationManifestError("illustration-manifest-invalid")
    root = Path(project_root).absolute()
    try:
        plan_snapshot = capture_regular_file(root / "工程" / "content-plan.json", within=root)
    except ArtifactError:
        raise IllustrationManifestError("illustration-input-changed") from None
    if plan_snapshot.sha256 != value["content_plan_sha256"]:
        raise IllustrationManifestError("illustration-input-changed")
    qc_relative = _xiaohei_path(
        value.get("semantic_qc_path"), kind="qc", scene_id="semantic-qc"
    )
    try:
        qc_value, qc_snapshot = load_json_snapshot(
            root / Path(*qc_relative.parts), within=root
        )
    except ArtifactError:
        raise IllustrationManifestError("illustration-input-changed") from None
    if qc_snapshot.sha256 != value["semantic_qc_sha256"]:
        raise IllustrationManifestError("illustration-input-changed")
    if (
        not isinstance(qc_value, dict)
        or set(qc_value) != _XIAOHEI_QC_TOP_KEYS
        or qc_value.get("schema_version") != 1
        or qc_value.get("visual_system") != "xiaohei-white-first-v1"
        or qc_value.get("project_id") != root.name
        or qc_value.get("content_plan_sha256") != plan_snapshot.sha256
        or qc_value.get("reviewer_type") != "multimodal-review"
        or qc_value.get("status") != "pass"
        or not isinstance(qc_value.get("scenes"), list)
    ):
        raise IllustrationManifestError("illustration-qc-invalid")
    qc_by_scene: dict[str, dict[str, object]] = {}
    for raw_qc in qc_value["scenes"]:
        if (
            not isinstance(raw_qc, dict)
            or set(raw_qc) != _XIAOHEI_QC_SCENE_KEYS
            or not isinstance(raw_qc.get("scene_id"), str)
            or raw_qc["scene_id"] in qc_by_scene
            or raw_qc.get("status") != "pass"
            or not isinstance(raw_qc.get("relevance_rationale"), str)
            or not raw_qc["relevance_rationale"].strip()
            or not isinstance(raw_qc.get("checks"), dict)
            or set(raw_qc["checks"]) != _XIAOHEI_QC_CHECK_KEYS
            or any(check is not True for check in raw_qc["checks"].values())
        ):
            raise IllustrationManifestError("illustration-qc-invalid")
        qc_by_scene[raw_qc["scene_id"]] = raw_qc
    assets: list[SemanticIllustrationAsset] = []
    seen: set[str] = set()
    expected = {scene.id: scene for scene in content_plan.scenes}
    for raw in value["assets"]:
        if not isinstance(raw, dict) or set(raw) != _XIAOHEI_ASSET_KEYS:
            raise IllustrationManifestError("illustration-manifest-invalid")
        scene_id = raw.get("scene_id")
        if not isinstance(scene_id, str) or scene_id in seen or scene_id not in expected:
            raise IllustrationManifestError("illustration-scene-invalid")
        seen.add(scene_id)
        scene = expected[scene_id]
        prompt_relative = _xiaohei_path(raw.get("prompt_path"), kind="prompt", scene_id=scene_id)
        selected_relative = _xiaohei_path(
            raw.get("selected_asset_path"), kind="asset", scene_id=scene_id
        )
        if raw.get("selected_asset_path") != scene.visual_asset or raw.get("qc_status") != "pass":
            raise IllustrationManifestError("illustration-scene-invalid")
        candidates = raw.get("candidate_artifacts")
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise IllustrationManifestError("illustration-manifest-invalid")
        candidate_raw = candidates[0]
        if (
            not isinstance(candidate_raw, dict)
            or set(candidate_raw) != _XIAOHEI_CANDIDATE_KEYS
            or candidate_raw.get("status") != "accepted"
        ):
            raise IllustrationManifestError("illustration-manifest-invalid")
        candidate_relative = _xiaohei_path(
            candidate_raw.get("path"), kind="candidate", scene_id=scene_id
        )
        try:
            prompt = capture_regular_file(root / Path(*prompt_relative.parts), within=root)
            candidate = capture_regular_file(root / Path(*candidate_relative.parts), within=root)
            selected = capture_regular_file(root / Path(*selected_relative.parts), within=root)
        except ArtifactError:
            raise IllustrationManifestError("illustration-input-changed") from None
        for recorded, snapshot in (
            (raw.get("prompt_sha256"), prompt),
            (candidate_raw.get("sha256"), candidate),
            (raw.get("selected_asset_sha256"), selected),
        ):
            if not isinstance(recorded, str) or _DIGEST.fullmatch(recorded) is None or recorded != snapshot.sha256:
                raise IllustrationManifestError("illustration-input-changed")
        _validate_xiaohei_prompt_contract(prompt.payload, scene=scene)
        _xiaohei_candidate_contract(candidate)
        _image_contract(
            selected,
            relative=selected_relative,
            expected_width=1920,
            expected_height=1080,
            expected_format="png",
        )
        qc = qc_by_scene.get(scene_id)
        if (
            qc is None
            or qc.get("contract_path") != raw.get("prompt_path")
            or qc.get("contract_sha256") != raw.get("prompt_sha256")
            or qc.get("selected_asset_path") != raw.get("selected_asset_path")
            or qc.get("selected_asset_sha256") != raw.get("selected_asset_sha256")
        ):
            raise IllustrationManifestError("illustration-qc-invalid")
        assets.append(
            SemanticIllustrationAsset(
                scene_id=scene_id,
                visual_type="concept-scene",
                visual_style="xiaohei-illustration",
                visual_mode="human-action",
                contract_path=str(raw["prompt_path"]),
                contract_sha256=str(raw["prompt_sha256"]),
                candidate_paths=(str(candidate_raw["path"]),),
                candidate_sha256=(str(candidate_raw["sha256"]),),
                selected_asset_path=str(raw["selected_asset_path"]),
                selected_asset_sha256=str(raw["selected_asset_sha256"]),
                width=1920,
                height=1080,
                format="png",
                ratio="16:9",
                qc_status="pass",
            )
        )
    if seen != set(expected) or set(qc_by_scene) != set(expected):
        raise IllustrationManifestError("illustration-scene-invalid")
    return IllustrationManifest(
        schema_version=1,
        illustration_skill="katerj-xiaohei-illustrations",
        visual_system="xiaohei-white-first-v1",
        assets=tuple(assets),
        content_plan_sha256=plan_snapshot.sha256,
        semantic_qc_path=qc_relative.as_posix(),
        semantic_qc_sha256=qc_snapshot.sha256,
    )


def _validate_v2_manifest(
    value: object, *, project_root: Path, content_plan: ContentPlan
) -> IllustrationManifest:
    if not isinstance(value, dict) or set(value) != _TOP_KEYS:
        raise IllustrationManifestError("illustration-manifest-invalid")
    if (
        value["schema_version"] != 1
        or value["illustration_skill"] != "ra-video-illustrations"
        or value["visual_system"] != "editorial-motion-v2"
        or content_plan.schema_version != 2
        or content_plan.visual_system != "editorial-motion-v2"
        or not isinstance(value["assets"], list)
    ):
        raise IllustrationManifestError("illustration-manifest-invalid")
    expected = {scene.id: scene for scene in content_plan.scenes if scene.visual_asset is not None}
    assets: list[IllustrationAsset] = []
    seen: set[str] = set()
    root = Path(project_root)
    for raw in value["assets"]:
        if not isinstance(raw, dict) or set(raw) != _ASSET_KEYS:
            raise IllustrationManifestError("illustration-manifest-invalid")
        scene_id = raw["scene_id"]
        if not isinstance(scene_id, str) or scene_id in seen or scene_id not in expected:
            raise IllustrationManifestError("illustration-scene-invalid")
        seen.add(scene_id)
        scene = expected[scene_id]
        if raw["visual_type"] != scene.visual_type or raw["visual_style"] != scene.visual_style:
            raise IllustrationManifestError("illustration-scene-invalid")
        if raw["qc_status"] != "pass":
            raise IllustrationManifestError("illustration-qc-invalid")
        prompt_relative = _safe_path(raw["prompt_path"], kind="prompt")
        asset_relative = _safe_path(raw["asset_path"], kind="asset")
        if raw["asset_path"] != scene.visual_asset:
            raise IllustrationManifestError("illustration-scene-invalid")
        try:
            prompt = capture_regular_file(root / Path(*prompt_relative.parts), within=root)
            image = capture_regular_file(root / Path(*asset_relative.parts), within=root)
        except ArtifactError:
            raise IllustrationManifestError("illustration-input-changed") from None
        if (
            not isinstance(raw["prompt_sha256"], str)
            or not isinstance(raw["asset_sha256"], str)
            or _DIGEST.fullmatch(raw["prompt_sha256"]) is None
            or _DIGEST.fullmatch(raw["asset_sha256"]) is None
            or prompt.sha256 != raw["prompt_sha256"]
            or image.sha256 != raw["asset_sha256"]
        ):
            raise IllustrationManifestError("illustration-input-changed")
        prompt_values = _prompt_contract(prompt.payload)
        if (
            prompt_values["scene_id"] != scene_id
            or prompt_values["visual_type"] != raw["visual_type"]
            or prompt_values["visual_style"] != raw["visual_style"]
        ):
            raise IllustrationManifestError("illustration-prompt-invalid")
        try:
            with Image.open(io.BytesIO(image.payload)) as decoded:
                detected = (decoded.format or "").lower()
                width, height = decoded.size
                decoded.verify()
            with Image.open(io.BytesIO(image.payload)) as decoded:
                decoded.load()
        except (OSError, UnidentifiedImageError):
            raise IllustrationManifestError("illustration-image-invalid") from None
        suffix_format = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp"}[asset_relative.suffix.lower()]
        if (
            detected != suffix_format
            or type(raw["width"]) is not int
            or type(raw["height"]) is not int
            or width != raw["width"]
            or height != raw["height"]
            or type(width) is not int
            or width < 1920
            or height < 1080
            or abs((width / height) - (16 / 9)) > 0.01
        ):
            raise IllustrationManifestError("illustration-image-invalid")
        assets.append(IllustrationAsset(**raw))  # type: ignore[arg-type]
    if seen != set(expected):
        raise IllustrationManifestError("illustration-scene-invalid")
    return IllustrationManifest(1, "ra-video-illustrations", "editorial-motion-v2", tuple(assets))


def _validate_v3_manifest(
    value: object, *, project_root: Path, content_plan: ContentPlan
) -> IllustrationManifest:
    if not isinstance(value, dict) or set(value) != _V3_TOP_KEYS:
        raise IllustrationManifestError("illustration-manifest-invalid")
    if (
        value["schema_version"] != 2
        or isinstance(value["schema_version"], bool)
        or value["illustration_skill"] != "ra-video-illustrations"
        or value["visual_system"] != "semantic-handdrawn-v3"
        or content_plan.schema_version != 3
        or content_plan.visual_system != "semantic-handdrawn-v3"
        or not isinstance(value["assets"], list)
        or len(value["assets"]) != len(content_plan.scenes)
        or not isinstance(value["content_plan_sha256"], str)
        or _DIGEST.fullmatch(value["content_plan_sha256"]) is None
        or not isinstance(value["semantic_qc_sha256"], str)
        or _DIGEST.fullmatch(value["semantic_qc_sha256"]) is None
    ):
        raise IllustrationManifestError("illustration-manifest-invalid")
    root = Path(project_root).absolute()
    try:
        plan_snapshot = capture_regular_file(
            root / "工程" / "content-plan.json", within=root
        )
    except ArtifactError:
        raise IllustrationManifestError("illustration-input-changed") from None
    if plan_snapshot.sha256 != value["content_plan_sha256"]:
        raise IllustrationManifestError("illustration-input-changed")
    qc_relative = _v3_path(
        value["semantic_qc_path"], kind="qc", scene_id="semantic-qc"
    )
    try:
        qc_value, qc_snapshot = load_json_snapshot(
            root / Path(*qc_relative.parts), within=root
        )
    except ArtifactError:
        raise IllustrationManifestError("illustration-input-changed") from None
    if qc_snapshot.sha256 != value["semantic_qc_sha256"]:
        raise IllustrationManifestError("illustration-input-changed")
    try:
        semantic_qc = validate_semantic_qc(
            qc_value,
            project_root=root,
            content_plan=content_plan,
        )
    except SemanticQCError:
        raise IllustrationManifestError("illustration-qc-invalid") from None
    if semantic_qc.content_plan_sha256 != value["content_plan_sha256"]:
        raise IllustrationManifestError("illustration-qc-invalid")

    semantic_by_scene = {item.scene_id: item for item in semantic_qc.scenes}
    assets: list[SemanticIllustrationAsset] = []
    for raw, scene in zip(value["assets"], content_plan.scenes):
        if (
            not isinstance(raw, dict)
            or set(raw) != _V3_ASSET_KEYS
            or raw["scene_id"] != scene.id
            or raw["visual_type"] != scene.visual_type
            or raw["visual_style"] != scene.visual_style
            or raw["visual_mode"] != scene.visual_mode
            or raw["ratio"] != "16:9"
            or raw["qc_status"] != "pass"
            or not isinstance(raw["contract_sha256"], str)
            or _DIGEST.fullmatch(raw["contract_sha256"]) is None
            or not isinstance(raw["candidate_artifacts"], list)
        ):
            raise IllustrationManifestError("illustration-scene-invalid")
        is_type_led = scene.visual_mode == "type-led"
        contract_kind = "type-led" if is_type_led else "prompt"
        contract_relative = _v3_path(
            raw["contract_path"], kind=contract_kind, scene_id=scene.id
        )
        try:
            contract_snapshot = capture_regular_file(
                root / Path(*contract_relative.parts), within=root
            )
        except ArtifactError:
            raise IllustrationManifestError("illustration-input-changed") from None
        if contract_snapshot.sha256 != raw["contract_sha256"]:
            raise IllustrationManifestError("illustration-input-changed")
        if is_type_led:
            _type_contract(
                contract_snapshot.payload,
                scene_id=scene.id,
                labels=scene.overlay_labels,
            )
        else:
            prompt_values = _v3_prompt_contract(contract_snapshot.payload)
            if (
                prompt_values["scene_id"] != scene.id
                or prompt_values["visual_type"] != scene.visual_type
                or prompt_values["visual_style"] != scene.visual_style
                or prompt_values["visual_mode"] != scene.visual_mode
            ):
                raise IllustrationManifestError("illustration-prompt-invalid")

        candidate_paths: list[str] = []
        candidate_hashes: list[str] = []
        for index, artifact in enumerate(raw["candidate_artifacts"], 1):
            if (
                not isinstance(artifact, dict)
                or set(artifact) != _V3_CANDIDATE_KEYS
                or not isinstance(artifact["sha256"], str)
                or _DIGEST.fullmatch(artifact["sha256"]) is None
            ):
                raise IllustrationManifestError("illustration-scene-invalid")
            candidate_relative = _v3_path(
                artifact["path"], kind="candidate", scene_id=scene.id
            )
            if candidate_relative.stem != f"{scene.id}-candidate-{index:02d}":
                raise IllustrationManifestError("illustration-scene-invalid")
            try:
                candidate_snapshot = capture_regular_file(
                    root / Path(*candidate_relative.parts), within=root
                )
            except ArtifactError:
                raise IllustrationManifestError("illustration-input-changed") from None
            if candidate_snapshot.sha256 != artifact["sha256"]:
                raise IllustrationManifestError("illustration-input-changed")
            _image_contract(
                candidate_snapshot,
                relative=candidate_relative,
                expected_width=3840,
                expected_height=2160,
                expected_format=candidate_relative.suffix.lower().lstrip(".").replace("jpg", "jpeg"),
            )
            candidate_paths.append(candidate_relative.as_posix())
            candidate_hashes.append(candidate_snapshot.sha256)

        selected_path = raw["selected_asset_path"]
        selected_hash = raw["selected_asset_sha256"]
        if is_type_led:
            if (
                raw["candidate_artifacts"]
                or selected_path is not None
                or selected_hash is not None
                or scene.visual_asset is not None
                or raw["width"] is not None
                or raw["height"] is not None
                or raw["format"] is not None
            ):
                raise IllustrationManifestError("illustration-scene-invalid")
            normalized_selected = None
            normalized_hash = None
        else:
            if (
                not 1 <= len(candidate_paths) <= 2
                or selected_path != scene.visual_asset
                or not isinstance(selected_hash, str)
                or _DIGEST.fullmatch(selected_hash) is None
                or raw["width"] != 3840
                or isinstance(raw["width"], bool)
                or raw["height"] != 2160
                or isinstance(raw["height"], bool)
                or raw["format"] not in {"png", "jpeg", "webp"}
            ):
                raise IllustrationManifestError("illustration-scene-invalid")
            selected_relative = _v3_path(
                selected_path, kind="asset", scene_id=scene.id
            )
            try:
                selected_snapshot = capture_regular_file(
                    root / Path(*selected_relative.parts), within=root
                )
            except ArtifactError:
                raise IllustrationManifestError("illustration-input-changed") from None
            if selected_snapshot.sha256 != selected_hash:
                raise IllustrationManifestError("illustration-input-changed")
            _image_contract(
                selected_snapshot,
                relative=selected_relative,
                expected_width=3840,
                expected_height=2160,
                expected_format=str(raw["format"]),
            )
            normalized_selected = selected_relative.as_posix()
            normalized_hash = selected_snapshot.sha256

        qc_scene = semantic_by_scene.get(scene.id)
        if (
            qc_scene is None
            or qc_scene.contract_path != contract_relative.as_posix()
            or qc_scene.contract_sha256 != contract_snapshot.sha256
            or qc_scene.selected_asset_path != normalized_selected
            or qc_scene.selected_asset_sha256 != normalized_hash
        ):
            raise IllustrationManifestError("illustration-qc-invalid")
        assets.append(
            SemanticIllustrationAsset(
                scene_id=scene.id,
                visual_type=str(scene.visual_type),
                visual_style=str(scene.visual_style),
                visual_mode=str(scene.visual_mode),
                contract_path=contract_relative.as_posix(),
                contract_sha256=contract_snapshot.sha256,
                candidate_paths=tuple(candidate_paths),
                candidate_sha256=tuple(candidate_hashes),
                selected_asset_path=normalized_selected,
                selected_asset_sha256=normalized_hash,
                width=raw["width"] if isinstance(raw["width"], int) else None,
                height=raw["height"] if isinstance(raw["height"], int) else None,
                format=raw["format"] if isinstance(raw["format"], str) else None,
                ratio="16:9",
                qc_status="pass",
            )
        )
    return IllustrationManifest(
        schema_version=2,
        illustration_skill="ra-video-illustrations",
        visual_system="semantic-handdrawn-v3",
        assets=tuple(assets),
        content_plan_sha256=str(value["content_plan_sha256"]),
        semantic_qc_path=qc_relative.as_posix(),
        semantic_qc_sha256=qc_snapshot.sha256,
    )


def _validate_v4_manifest(
    value: object, *, project_root: Path, content_plan: ContentPlan
) -> IllustrationManifest:
    try:
        theme = get_theme(content_plan.visual_theme)  # type: ignore[arg-type]
    except IllustrationThemeError:
        raise IllustrationManifestError("illustration-manifest-invalid") from None
    if not isinstance(value, dict) or set(value) != _V4_TOP_KEYS:
        raise IllustrationManifestError("illustration-manifest-invalid")
    if (
        value["schema_version"] != 3
        or isinstance(value["schema_version"], bool)
        or value["illustration_skill"] != "ra-video-illustrations"
        or value["visual_system"] != PROFILED_VISUAL_SYSTEM
        or value["visual_theme"] != theme.id
        or content_plan.schema_version != 4
        or content_plan.visual_system != PROFILED_VISUAL_SYSTEM
        or not isinstance(value["assets"], list)
        or len(value["assets"]) != len(content_plan.scenes)
        or not isinstance(value["content_plan_sha256"], str)
        or _DIGEST.fullmatch(value["content_plan_sha256"]) is None
        or not isinstance(value["semantic_qc_sha256"], str)
        or _DIGEST.fullmatch(value["semantic_qc_sha256"]) is None
    ):
        raise IllustrationManifestError("illustration-manifest-invalid")

    root = Path(project_root).absolute()
    try:
        plan_snapshot = capture_regular_file(
            root / "工程" / "content-plan.json", within=root
        )
    except ArtifactError:
        raise IllustrationManifestError("illustration-input-changed") from None
    if plan_snapshot.sha256 != value["content_plan_sha256"]:
        raise IllustrationManifestError("illustration-input-changed")

    qc_relative = _profiled_path(
        value["semantic_qc_path"],
        kind="qc",
        scene_id="semantic-qc",
        theme_id=theme.id,
    )
    try:
        qc_value, qc_snapshot = load_json_snapshot(
            root / Path(*qc_relative.parts), within=root
        )
    except ArtifactError:
        raise IllustrationManifestError("illustration-input-changed") from None
    if qc_snapshot.sha256 != value["semantic_qc_sha256"]:
        raise IllustrationManifestError("illustration-input-changed")
    try:
        semantic_qc = validate_semantic_qc(
            qc_value,
            project_root=root,
            content_plan=content_plan,
        )
    except SemanticQCError:
        raise IllustrationManifestError("illustration-qc-invalid") from None
    if (
        semantic_qc.schema_version != 2
        or semantic_qc.visual_system != PROFILED_VISUAL_SYSTEM
        or semantic_qc.visual_theme != theme.id
        or semantic_qc.content_plan_sha256 != value["content_plan_sha256"]
    ):
        raise IllustrationManifestError("illustration-qc-invalid")

    semantic_by_scene = {item.scene_id: item for item in semantic_qc.scenes}
    assets: list[SemanticIllustrationAsset] = []
    for raw, scene in zip(value["assets"], content_plan.scenes):
        if (
            not isinstance(raw, dict)
            or set(raw) != _V3_ASSET_KEYS
            or raw["scene_id"] != scene.id
            or raw["visual_type"] != scene.visual_type
            or raw["visual_style"] != theme.id
            or scene.visual_style != theme.id
            or raw["visual_mode"] != scene.visual_mode
            or raw["ratio"] != "16:9"
            or raw["qc_status"] != "pass"
            or not isinstance(raw["contract_sha256"], str)
            or _DIGEST.fullmatch(raw["contract_sha256"]) is None
            or not isinstance(raw["candidate_artifacts"], list)
            or not 1 <= len(raw["candidate_artifacts"]) <= 2
            or raw["width"] != 3840
            or isinstance(raw["width"], bool)
            or raw["height"] != 2160
            or isinstance(raw["height"], bool)
            or raw["format"] != "png"
        ):
            raise IllustrationManifestError("illustration-scene-invalid")

        contract_relative = _profiled_path(
            raw["contract_path"],
            kind="prompt",
            scene_id=scene.id,
            theme_id=theme.id,
        )
        try:
            contract_snapshot = capture_regular_file(
                root / Path(*contract_relative.parts), within=root
            )
        except ArtifactError:
            raise IllustrationManifestError("illustration-input-changed") from None
        if contract_snapshot.sha256 != raw["contract_sha256"]:
            raise IllustrationManifestError("illustration-input-changed")
        prompt_values = _v4_prompt_contract(contract_snapshot.payload)
        if (
            prompt_values["scene_id"] != scene.id
            or prompt_values["visual_type"] != scene.visual_type
            or prompt_values["visual_style"] != theme.id
            or prompt_values["visual_mode"] != scene.visual_mode
            or prompt_values["visual_theme"] != theme.id
        ):
            raise IllustrationManifestError("illustration-prompt-invalid")

        candidate_paths: list[str] = []
        candidate_hashes: list[str] = []
        for index, artifact in enumerate(raw["candidate_artifacts"], 1):
            if (
                not isinstance(artifact, dict)
                or not {
                    "path",
                    "sha256",
                    "generation_prompt_path",
                    "generation_prompt_sha256",
                }.issubset(artifact)
                or not isinstance(artifact["sha256"], str)
                or _DIGEST.fullmatch(artifact["sha256"]) is None
                or not isinstance(artifact["generation_prompt_sha256"], str)
                or _DIGEST.fullmatch(artifact["generation_prompt_sha256"]) is None
            ):
                raise IllustrationManifestError(
                    "illustration-generation-evidence-invalid"
                )
            candidate_relative = _profiled_path(
                artifact["path"],
                kind="candidate",
                scene_id=scene.id,
                theme_id=theme.id,
            )
            if candidate_relative.stem != f"{scene.id}-candidate-{index:02d}":
                raise IllustrationManifestError("illustration-scene-invalid")
            try:
                candidate_snapshot = capture_regular_file(
                    root / Path(*candidate_relative.parts), within=root
                )
            except ArtifactError:
                raise IllustrationManifestError("illustration-input-changed") from None
            if candidate_snapshot.sha256 != artifact["sha256"]:
                raise IllustrationManifestError("illustration-input-changed")
            generation_prompt_relative = _profiled_path(
                artifact["generation_prompt_path"],
                kind="generation-prompt",
                scene_id=scene.id,
                theme_id=theme.id,
            )
            expected_prompt_name = (
                f"{scene.id}.md"
                if index == 1
                else f"{scene.id}-repair-02.md"
            )
            if generation_prompt_relative.name != expected_prompt_name:
                raise IllustrationManifestError(
                    "illustration-generation-evidence-invalid"
                )
            try:
                generation_prompt_snapshot = capture_regular_file(
                    root / Path(*generation_prompt_relative.parts), within=root
                )
            except ArtifactError:
                raise IllustrationManifestError(
                    "illustration-input-changed"
                ) from None
            if (
                generation_prompt_snapshot.sha256
                != artifact["generation_prompt_sha256"]
                or (
                    index == 1
                    and generation_prompt_snapshot.sha256
                    != contract_snapshot.sha256
                )
                or (
                    index == 2
                    and generation_prompt_snapshot.sha256
                    == contract_snapshot.sha256
                )
            ):
                raise IllustrationManifestError(
                    "illustration-generation-evidence-invalid"
                )
            generation_prompt_values = _v4_prompt_contract(
                generation_prompt_snapshot.payload
            )
            if (
                generation_prompt_values["scene_id"] != scene.id
                or generation_prompt_values["visual_type"] != scene.visual_type
                or generation_prompt_values["visual_style"] != theme.id
                or generation_prompt_values["visual_mode"] != scene.visual_mode
                or generation_prompt_values["visual_theme"] != theme.id
            ):
                raise IllustrationManifestError("illustration-prompt-invalid")
            try:
                if set(artifact) == GENERATION_ARTIFACT_KEYS:
                    validate_profiled_generation_evidence(
                        project_root=root,
                        theme_id=theme.id,
                        scene_id=scene.id,
                        candidate_index=index,
                        prompt_path=generation_prompt_relative.as_posix(),
                        prompt_sha256=generation_prompt_snapshot.sha256,
                        candidate_path=candidate_relative.as_posix(),
                        candidate_sha256=candidate_snapshot.sha256,
                        artifact=artifact,
                    )
                elif set(artifact) == HISTORY_ADAPTATION_ARTIFACT_KEYS:
                    validate_user_approved_history_adaptation_evidence(
                        project_root=root,
                        theme_id=theme.id,
                        scene_id=scene.id,
                        candidate_index=index,
                        prompt_path=generation_prompt_relative.as_posix(),
                        prompt_sha256=generation_prompt_snapshot.sha256,
                        candidate_path=candidate_relative.as_posix(),
                        candidate_sha256=candidate_snapshot.sha256,
                        artifact=artifact,
                    )
                else:
                    validate_profiled_reuse_evidence(
                        project_root=root,
                        theme_id=theme.id,
                        scene_id=scene.id,
                        candidate_index=index,
                        prompt_path=generation_prompt_relative.as_posix(),
                        prompt_sha256=generation_prompt_snapshot.sha256,
                        candidate_path=candidate_relative.as_posix(),
                        candidate_sha256=candidate_snapshot.sha256,
                        artifact=artifact,
                    )
            except ProfiledGenerationError:
                raise IllustrationManifestError(
                    "illustration-generation-evidence-invalid"
                ) from None
            try:
                if (
                    generation_prompt_snapshot.path.stat().st_mtime_ns
                    > candidate_snapshot.path.stat().st_mtime_ns
                ):
                    raise IllustrationManifestError(
                        "illustration-prompt-order-invalid"
                    )
            except OSError:
                raise IllustrationManifestError(
                    "illustration-input-changed"
                ) from None
            _image_contract(
                candidate_snapshot,
                relative=candidate_relative,
                expected_width=3840,
                expected_height=2160,
                expected_format="png",
            )
            candidate_paths.append(candidate_relative.as_posix())
            candidate_hashes.append(candidate_snapshot.sha256)

        selected_path = raw["selected_asset_path"]
        selected_hash = raw["selected_asset_sha256"]
        if (
            selected_path != scene.visual_asset
            or not isinstance(selected_hash, str)
            or _DIGEST.fullmatch(selected_hash) is None
        ):
            raise IllustrationManifestError("illustration-scene-invalid")
        selected_relative = _profiled_path(
            selected_path,
            kind="asset",
            scene_id=scene.id,
            theme_id=theme.id,
        )
        try:
            selected_snapshot = capture_regular_file(
                root / Path(*selected_relative.parts), within=root
            )
        except ArtifactError:
            raise IllustrationManifestError("illustration-input-changed") from None
        if selected_snapshot.sha256 != selected_hash:
            raise IllustrationManifestError("illustration-input-changed")
        _image_contract(
            selected_snapshot,
            relative=selected_relative,
            expected_width=3840,
            expected_height=2160,
            expected_format="png",
        )
        if selected_snapshot.sha256 not in candidate_hashes:
            raise IllustrationManifestError("illustration-scene-invalid")

        qc_scene = semantic_by_scene.get(scene.id)
        if (
            qc_scene is None
            or qc_scene.contract_path != contract_relative.as_posix()
            or qc_scene.contract_sha256 != contract_snapshot.sha256
            or qc_scene.selected_asset_path != selected_relative.as_posix()
            or qc_scene.selected_asset_sha256 != selected_snapshot.sha256
        ):
            raise IllustrationManifestError("illustration-qc-invalid")
        assets.append(
            SemanticIllustrationAsset(
                scene_id=scene.id,
                visual_type=str(scene.visual_type),
                visual_style=theme.id,
                visual_mode=str(scene.visual_mode),
                contract_path=contract_relative.as_posix(),
                contract_sha256=contract_snapshot.sha256,
                candidate_paths=tuple(candidate_paths),
                candidate_sha256=tuple(candidate_hashes),
                selected_asset_path=selected_relative.as_posix(),
                selected_asset_sha256=selected_snapshot.sha256,
                width=3840,
                height=2160,
                format="png",
                ratio="16:9",
                qc_status="pass",
            )
        )
    return IllustrationManifest(
        schema_version=3,
        illustration_skill="ra-video-illustrations",
        visual_system=PROFILED_VISUAL_SYSTEM,
        assets=tuple(assets),
        content_plan_sha256=str(value["content_plan_sha256"]),
        semantic_qc_path=qc_relative.as_posix(),
        semantic_qc_sha256=qc_snapshot.sha256,
        visual_theme=theme.id,
    )


def validate_illustration_manifest(
    value: object, *, project_root: Path, content_plan: ContentPlan
) -> IllustrationManifest:
    if not isinstance(value, dict):
        raise IllustrationManifestError("illustration-manifest-invalid")
    if (
        content_plan.schema_version == 1
        and content_plan.visual_system == "xiaohei-white-first-v1"
    ):
        return _validate_xiaohei_manifest(
            value,
            project_root=project_root,
            content_plan=content_plan,
        )
    if content_plan.schema_version == 2:
        return _validate_v2_manifest(
            value,
            project_root=project_root,
            content_plan=content_plan,
        )
    if content_plan.schema_version == 3:
        return _validate_v3_manifest(
            value,
            project_root=project_root,
            content_plan=content_plan,
        )
    if content_plan.schema_version == 4:
        return _validate_v4_manifest(
            value,
            project_root=project_root,
            content_plan=content_plan,
        )
    raise IllustrationManifestError("illustration-manifest-invalid")


def load_illustration_manifest_snapshot(
    project_root: Path,
    *,
    prearchive_project_root: Path | None = None,
) -> IllustrationManifestSnapshot:
    root = Path(project_root).absolute()
    try:
        plan_snapshot = load_content_plan_snapshot(
            project_root=root,
            prearchive_project_root=prearchive_project_root,
        )
        if plan_snapshot.plan.visual_system == PROFILED_VISUAL_SYSTEM:
            theme = get_theme(plan_snapshot.plan.visual_theme)  # type: ignore[arg-type]
            path = (
                root
                / "工程"
                / "assets"
                / "profiled-illustrations"
                / theme.directory
                / "illustration-manifest.json"
            )
        elif plan_snapshot.plan.visual_system == "xiaohei-white-first-v1":
            path = (
                root
                / "工程"
                / "assets"
                / "xiaohei-illustrations"
                / "illustration-manifest.json"
            )
        else:
            asset_directory = (
                "semantic-handdrawn"
                if plan_snapshot.plan.visual_system == "semantic-handdrawn-v3"
                else "editorial-illustrations"
            )
            path = root / "工程" / "assets" / asset_directory / "illustration-manifest.json"
        value, snapshot = load_json_snapshot(path, within=root)
    except (ArtifactError, ContentPlanError, IllustrationThemeError):
        raise IllustrationManifestError("illustration-manifest-invalid") from None
    manifest = validate_illustration_manifest(value, project_root=root, content_plan=plan_snapshot.plan)
    prompts: list[FileSnapshot] = []
    assets: list[FileSnapshot] = []
    candidates: list[FileSnapshot] = []
    for item in manifest.assets:
        prompt_path = (
            item.contract_path
            if isinstance(item, SemanticIllustrationAsset)
            else item.prompt_path
        )
        try:
            prompt = capture_regular_file(
                root / Path(*PurePosixPath(prompt_path).parts), within=root
            )
        except ArtifactError:
            raise IllustrationManifestError("illustration-input-changed") from None
        prompt_sha256 = (
            item.contract_sha256
            if isinstance(item, SemanticIllustrationAsset)
            else item.prompt_sha256
        )
        if prompt.sha256 != prompt_sha256:
            raise IllustrationManifestError("illustration-input-changed")
        prompts.append(prompt)
        if isinstance(item, SemanticIllustrationAsset):
            for candidate_path, candidate_sha256 in zip(
                item.candidate_paths, item.candidate_sha256
            ):
                try:
                    candidate = capture_regular_file(
                        root / Path(*PurePosixPath(candidate_path).parts), within=root
                    )
                except ArtifactError:
                    raise IllustrationManifestError("illustration-input-changed") from None
                if candidate.sha256 != candidate_sha256:
                    raise IllustrationManifestError("illustration-input-changed")
                candidates.append(candidate)
            if item.selected_asset_path is not None:
                try:
                    asset = capture_regular_file(
                        root / Path(*PurePosixPath(item.selected_asset_path).parts),
                        within=root,
                    )
                except ArtifactError:
                    raise IllustrationManifestError("illustration-input-changed") from None
                if asset.sha256 != item.selected_asset_sha256:
                    raise IllustrationManifestError("illustration-input-changed")
                assets.append(asset)
        else:
            try:
                asset = capture_regular_file(
                    root / Path(*PurePosixPath(item.asset_path).parts), within=root
                )
            except ArtifactError:
                raise IllustrationManifestError("illustration-input-changed") from None
            if asset.sha256 != item.asset_sha256:
                raise IllustrationManifestError("illustration-input-changed")
            assets.append(asset)
    semantic_qc_snapshot: FileSnapshot | None = None
    if manifest.semantic_qc_path is not None:
        try:
            semantic_qc_snapshot = capture_regular_file(
                root / Path(*PurePosixPath(manifest.semantic_qc_path).parts), within=root
            )
        except ArtifactError:
            raise IllustrationManifestError("illustration-input-changed") from None
        if semantic_qc_snapshot.sha256 != manifest.semantic_qc_sha256:
            raise IllustrationManifestError("illustration-input-changed")
    return IllustrationManifestSnapshot(
        path,
        manifest,
        snapshot,
        plan_snapshot.snapshot,
        tuple(prompts),
        tuple(assets),
        tuple(candidates),
        semantic_qc_snapshot,
    )


__all__ = ["IllustrationAsset", "SemanticIllustrationAsset", "IllustrationManifest", "IllustrationManifestError", "IllustrationManifestSnapshot", "load_illustration_manifest_snapshot", "validate_illustration_manifest"]

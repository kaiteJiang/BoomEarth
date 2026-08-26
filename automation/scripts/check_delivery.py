"""Check a BoomEarth V1 delivery offline; live APIs are not called."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.audio import indextts2
from boomearth.contracts import HandoffContract
from boomearth.audio.indextts2 import CURRENT_VOICE_ID, SUPPORTED_VOICE_IDS
from boomearth.workbench.handoff import validate_public_handoff
from boomearth.video.artifacts import (
    ArtifactError,
    FileSnapshot,
    capture_regular_file,
    load_json_snapshot,
    snapshot_matches,
)
from boomearth.video.content_plan import ContentPlanError, load_content_plan_snapshot
from boomearth.video.cover_contracts import SUPPORTED_COVER_CONTRACTS
from boomearth.video.illustration_manifest import (
    IllustrationManifestError,
    load_illustration_manifest_snapshot,
)
from boomearth.video.illustration_themes import (
    PROFILED_VISUAL_SYSTEM,
    IllustrationThemeError,
    get_theme,
)
from boomearth.video.motion_plan import MotionPlanError, load_motion_plan_snapshot
from boomearth.video.platform_covers import (
    COVER_CONTRACT,
    COVER_IMAGE_DIMENSIONS,
    COVER_SAFE_BOXES,
    cover_artifact_paths,
)
from boomearth.video.scene_qc import (
    SceneQCError,
    extract_rgb_frame,
    frame_pixel_counts,
    motion_preview_times,
    scene_sample_times,
)
from boomearth.video.scene_timeline import SceneTimelineError, validate_scene_timeline


EXIT_PASS = 0
EXIT_CONTRACT = 2
EXIT_MISSING = 3
EXIT_PRIVACY = 4
REPARSE_POINT = 0x0400
CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)
WORD_FIELDS = {"text", "start", "end", "isGap"}
PRODUCTION_MANIFEST_FIELDS = (
    "provider",
    "voice_id",
    "model",
    "reference_audio_path",
    "reference_audio_sha256",
    "output_path",
    "output_sha256",
    "segment_contract_path",
    "segment_contract_sha256",
    "segment_count",
    "playback_speed",
    "pronunciation_contract_path",
    "pronunciation_contract_sha256",
    "used_fallback",
)
CONTENT_MARKERS = (
    Path("工程/content-plan.json"),
    Path("工程/scene-timeline.json"),
    Path("工程/assets/xiaohei-illustrations"),
    Path("质检/scene-preview-qc.json"),
    Path("质检/scene-previews"),
)
SCENE_QC_FIELDS = {
    "schema_version",
    "status",
    "final_video_sha256",
    "content_plan_sha256",
    "scene_timeline_sha256",
    "evidence",
}
SCENE_EVIDENCE_FIELDS = {
    "scene_id",
    "time_s",
    "kind",
    "frame_sha256",
    "preview_sha256",
    "foreground_pixels",
    "dark_caption_pixels",
    "bright_caption_pixels",
}
MOTION_QC_FIELDS = {
    "schema_version",
    "status",
    "final_video_sha256",
    "content_plan_sha256",
    "scene_timeline_sha256",
    "motion_plan_sha256",
    "contact_sheet_sha256",
    "evidence",
}
MOTION_EVIDENCE_FIELDS = {
    "scene_id",
    "kind",
    "time_s",
    "frame_sha256",
    "preview_sha256",
}


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    exit_code: int
    diagnostics: tuple[str, ...]
    report_path: str | None = None


def _after_content_evidence_validation() -> None:
    """Testing seam before scene-evidence snapshots are rechecked."""


def _motion_delivery_artifacts(
    project: Path,
    final_mp4: Path,
    errors: list[str],
    *,
    validated_snapshots: dict[Path, FileSnapshot] | None = None,
) -> tuple[Path, ...]:
    """Reauthenticate the closed Motion V2 evidence set against the final MP4."""

    qc_path = project / "质检" / "motion-preview-qc.json"
    previews_dir = project / "质检" / "motion-previews"
    contact_path = project / "质检" / "motion-contact-sheet.jpg"
    try:
        motion = load_motion_plan_snapshot(project)
        qc, qc_snapshot = load_json_snapshot(qc_path, within=project)
        final_snapshot = capture_regular_file(final_mp4, within=project)
        contact_snapshot = capture_regular_file(contact_path, within=project)
    except (ArtifactError, MotionPlanError):
        errors.append("rule=motion-preview-qc")
        return ()
    if not _safe_directory(previews_dir):
        errors.append("rule=motion-preview-qc")
        return ()
    expected = [
        (scene_id, kind, timestamp)
        for scene_id, values in motion_preview_times(motion.plan).items()
        for kind, timestamp in values.items()
    ]
    evidence = qc.get("evidence") if isinstance(qc, dict) else None
    valid = (
        isinstance(qc, dict)
        and set(qc) == MOTION_QC_FIELDS
        and qc.get("schema_version") == 1
        and not isinstance(qc.get("schema_version"), bool)
        and qc.get("status") == "pass"
        and qc.get("final_video_sha256") == final_snapshot.sha256
        and qc.get("content_plan_sha256") == motion.content_plan_snapshot.sha256
        and qc.get("scene_timeline_sha256") == motion.scene_timeline_snapshot.sha256
        and qc.get("motion_plan_sha256") == motion.snapshot.sha256
        and qc.get("contact_sheet_sha256") == contact_snapshot.sha256
        and isinstance(evidence, list)
        and len(evidence) == len(expected)
    )
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        valid = False
    preview_snapshots: list[FileSnapshot] = []
    if valid:
        for item, (scene_id, kind, timestamp) in zip(evidence, expected, strict=True):
            preview = previews_dir / f"{scene_id}-{kind}.png"
            if (
                not isinstance(item, dict)
                or set(item) != MOTION_EVIDENCE_FIELDS
                or item.get("scene_id") != scene_id
                or item.get("kind") != kind
                or isinstance(item.get("time_s"), bool)
                or not isinstance(item.get("time_s"), (int, float))
                or not math.isfinite(float(item["time_s"]))
                or abs(float(item["time_s"]) - timestamp) > 1e-6
                or not _is_strictly_contained_regular_file(project, preview)
            ):
                valid = False
                break
            try:
                preview_snapshot = capture_regular_file(preview, within=project)
                frame = extract_rgb_frame(
                    ffmpeg=ffmpeg,
                    final_mp4=final_mp4,
                    timestamp=timestamp,
                )
            except (ArtifactError, SceneQCError):
                valid = False
                break
            if (
                item.get("preview_sha256") != preview_snapshot.sha256
                or item.get("frame_sha256") != hashlib.sha256(frame).hexdigest()
            ):
                valid = False
                break
            preview_snapshots.append(preview_snapshot)
    snapshots = (
        motion.snapshot,
        motion.content_plan_snapshot,
        motion.scene_timeline_snapshot,
        motion.captions_words_snapshot,
        qc_snapshot,
        final_snapshot,
        contact_snapshot,
        *preview_snapshots,
    )
    if valid and not all(snapshot_matches(snapshot) for snapshot in snapshots):
        valid = False
    if not valid:
        errors.append("rule=motion-preview-qc")
        return ()
    if validated_snapshots is not None:
        validated_snapshots.update((snapshot.path, snapshot) for snapshot in snapshots)
    return (
        motion.path,
        qc_path,
        contact_path,
        *(snapshot.path for snapshot in preview_snapshots),
    )


def _content_delivery_artifacts(
    project: Path,
    final_mp4: Path,
    contact_sheet_qc: object,
    errors: list[str],
    *,
    historical: bool = False,
    validated_snapshots: dict[Path, FileSnapshot] | None = None,
) -> tuple[Path, ...]:
    """Validate the closed P1 artifact set when any content marker exists."""

    markers = tuple(project / marker for marker in CONTENT_MARKERS)
    if not any(path.exists() or _is_reparse_point(path) for path in markers):
        return ()
    plan_path = project / "工程" / "content-plan.json"
    timeline_path = project / "工程" / "scene-timeline.json"
    scene_qc_path = project / "质检" / "scene-preview-qc.json"
    previews_dir = project / "质检" / "scene-previews"
    if (
        not _safe_file(plan_path)
        or not _safe_file(timeline_path)
        or not _safe_file(scene_qc_path)
        or not _safe_directory(previews_dir)
    ):
        errors.append("rule=content-plan")
        return ()
    try:
        plan_snapshot = load_content_plan_snapshot(
            project_root=project,
            prearchive_project_root=(
                _prearchive_project_root(project) if historical else None
            ),
        )
    except (ContentPlanError, ArtifactError):
        errors.append("rule=content-plan")
        return ()
    if plan_snapshot.plan.visual_system == PROFILED_VISUAL_SYSTEM:
        try:
            theme = get_theme(plan_snapshot.plan.visual_theme)
        except IllustrationThemeError:
            errors.append("rule=content-plan")
            return ()
        assets_dir = (
            project
            / "工程"
            / "assets"
            / "profiled-illustrations"
            / theme.directory
        )
    else:
        assets_dir = project / "工程" / "assets" / (
            "semantic-handdrawn"
            if plan_snapshot.plan.visual_system == "semantic-handdrawn-v3"
            else "editorial-illustrations"
            if plan_snapshot.plan.visual_system == "editorial-motion-v2"
            else "xiaohei-illustrations"
        )
    if not _safe_directory(assets_dir):
        errors.append("rule=content-plan")
        return ()
    provenance_paths: tuple[Path, ...] = ()
    motion_paths: tuple[Path, ...] = ()
    if plan_snapshot.plan.visual_system in {
        "editorial-motion-v2",
        "semantic-handdrawn-v3",
        PROFILED_VISUAL_SYSTEM,
    }:
        try:
            illustration = load_illustration_manifest_snapshot(
                project,
                prearchive_project_root=(
                    _prearchive_project_root(project) if historical else None
                ),
            )
            provenance_paths = (
                illustration.path,
                *(snapshot.path for snapshot in illustration.prompt_snapshots),
                *(snapshot.path for snapshot in illustration.candidate_snapshots),
                *((illustration.semantic_qc_snapshot.path,) if illustration.semantic_qc_snapshot is not None else ()),
            )
        except (IllustrationManifestError, ArtifactError):
            errors.append("rule=illustration-manifest")
        motion_paths = _motion_delivery_artifacts(
            project,
            final_mp4,
            errors,
            validated_snapshots=validated_snapshots,
        )
    try:
        timeline_value, timeline_snapshot = load_json_snapshot(
            timeline_path, within=project
        )
        timeline = validate_scene_timeline(
            timeline_value,
            project_root=project,
            prearchive_project_root=(
                _prearchive_project_root(project) if historical else None
            ),
        )
    except (ArtifactError, SceneTimelineError):
        errors.append("rule=scene-timeline")
        return (plan_path,)
    visual_paths: list[Path] = []
    visual_hashes: dict[str, str] = {}
    for scene in plan_snapshot.plan.scenes:
        if scene.visual_asset is None:
            continue
        visual = project / Path(*scene.visual_asset.split("/"))
        if not _is_strictly_contained_regular_file(project, visual):
            errors.append("rule=visual-assets")
            continue
        relative = visual.relative_to(project).as_posix()
        digest = _sha256(visual)
        if digest in visual_hashes and scene.reuse_reason is None:
            errors.append("rule=visual-assets")
        visual_hashes[digest] = relative
        visual_paths.append(visual)
    try:
        scene_qc, scene_qc_snapshot = load_json_snapshot(
            scene_qc_path, within=project
        )
        final_snapshot = capture_regular_file(final_mp4, within=project)
    except ArtifactError:
        errors.append("rule=scene-preview-qc")
        return (plan_path, timeline_path, *visual_paths)
    evidence = scene_qc.get("evidence") if isinstance(scene_qc, dict) else None
    try:
        expected_evidence = [
            (scene.id, kind, timestamp)
            for scene in timeline.scenes
            for kind, timestamp in zip(
                ("settle", "midpoint", "late"),
                scene_sample_times(start=scene.start, end=scene.end),
            )
        ]
    except SceneQCError:
        errors.append("rule=scene-preview-qc")
        return (plan_path, timeline_path, *visual_paths)
    preview_paths: list[Path] = []
    preview_snapshots: list[FileSnapshot] = []
    evidence_ok = (
        isinstance(scene_qc, dict)
        and set(scene_qc) == SCENE_QC_FIELDS
        and scene_qc.get("schema_version") == 1
        and not isinstance(scene_qc.get("schema_version"), bool)
        and scene_qc.get("status") == "pass"
        and scene_qc.get("final_video_sha256") == final_snapshot.sha256
        and scene_qc.get("content_plan_sha256") == plan_snapshot.snapshot.sha256
        and scene_qc.get("scene_timeline_sha256") == timeline_snapshot.sha256
        and isinstance(evidence, list)
        and len(evidence) == len(expected_evidence)
    )
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        evidence_ok = False
    if evidence_ok:
        for item, (scene_id, kind, timestamp) in zip(evidence, expected_evidence):
            preview = previews_dir / f"{scene_id}-{kind}.png"
            if (
                not isinstance(item, dict)
                or set(item) != SCENE_EVIDENCE_FIELDS
                or item.get("scene_id") != scene_id
                or item.get("kind") != kind
                or isinstance(item.get("time_s"), bool)
                or not isinstance(item.get("time_s"), (int, float))
                or not math.isfinite(float(item["time_s"]))
                or abs(float(item["time_s"]) - timestamp) > 1e-6
                or not _is_strictly_contained_regular_file(project, preview)
            ):
                evidence_ok = False
                break
            try:
                preview_snapshot = capture_regular_file(preview, within=project)
            except ArtifactError:
                evidence_ok = False
                break
            if item.get("preview_sha256") != preview_snapshot.sha256:
                evidence_ok = False
                break
            try:
                frame = extract_rgb_frame(
                    ffmpeg=ffmpeg,
                    final_mp4=final_mp4,
                    timestamp=timestamp,
                )
                foreground, dark, bright = frame_pixel_counts(frame)
                legacy_counts = frame_pixel_counts(frame, dark_threshold=65)
            except SceneQCError:
                evidence_ok = False
                break
            counts = (
                item.get("foreground_pixels"),
                item.get("dark_caption_pixels"),
                item.get("bright_caption_pixels"),
            )
            if (
                item.get("frame_sha256") != hashlib.sha256(frame).hexdigest()
                or any(
                    not isinstance(value, int) or isinstance(value, bool)
                    for value in counts
                )
                or counts not in {(foreground, dark, bright), legacy_counts}
            ):
                evidence_ok = False
                break
            preview_paths.append(preview)
            preview_snapshots.append(preview_snapshot)
    if evidence_ok:
        _after_content_evidence_validation()
        evidence_snapshots = (
            scene_qc_snapshot,
            final_snapshot,
            *preview_snapshots,
        )
        if not all(snapshot_matches(snapshot) for snapshot in evidence_snapshots):
            evidence_ok = False
        elif validated_snapshots is not None:
            validated_snapshots.update(
                (snapshot.path, snapshot)
                for snapshot in (scene_qc_snapshot, *preview_snapshots)
            )
    if not evidence_ok:
        errors.append("rule=scene-preview-qc")
    expected_times = [
        round((scene.start + scene.end) / 2.0, 6) for scene in timeline.scenes
    ]
    coverage_ok = (
        isinstance(contact_sheet_qc, dict)
        and contact_sheet_qc.get("frame_count") == len(expected_times)
        and contact_sheet_qc.get("times_s") == expected_times
        and contact_sheet_qc.get("coverage") == "all-scene-midpoints"
    )
    if not coverage_ok:
        errors.append("rule=contact-sheet-coverage")
    return (
        plan_path,
        timeline_path,
        *provenance_paths,
        *motion_paths,
        *visual_paths,
        scene_qc_path,
        *preview_paths,
    )


def _is_reparse_point(path: Path) -> bool:
    """Return true for a symlink or Windows junction without following it."""
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(entry, "st_file_attributes", 0) & REPARSE_POINT
    )


def _has_safe_ancestors(path: Path) -> bool:
    """Inspect existing and missing components lexically; never resolve a reparse point."""
    current = path.absolute()
    while True:
        if _is_reparse_point(current):
            return False
        if current == current.parent:
            return True
        current = current.parent


def _is_contained(base: Path, candidate: Path) -> bool:
    """Check lexical containment and all ancestors before any filesystem mutation."""
    base_absolute = base.absolute()
    candidate_absolute = candidate.absolute()
    try:
        candidate_absolute.relative_to(base_absolute)
    except ValueError:
        return False
    return _has_safe_ancestors(base_absolute) and _has_safe_ancestors(candidate_absolute)


def _is_strictly_contained_regular_file(base: Path, candidate: Path) -> bool:
    """Require a non-reparse regular file to resolve inside a non-reparse project."""
    if _contains_dotdot(base) or _contains_dotdot(candidate):
        return False
    if not _safe_directory(base) or not _safe_file(candidate):
        return False
    try:
        candidate.resolve(strict=True).relative_to(base.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _safe_file(path: Path) -> bool:
    try:
        return _has_safe_ancestors(path) and stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _safe_directory(path: Path) -> bool:
    try:
        return _has_safe_ancestors(path) and stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def _reject_duplicate_json_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    document: dict[object, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _read_json(path: Path, rule: str, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append(f"rule={rule}-json")
        return None


def _read_manifest_snapshot(
    path: Path,
    errors: list[str],
) -> tuple[Any, str | None]:
    try:
        with path.open("rb") as source:
            snapshot = source.read()
        payload = json.loads(
            snapshot.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        errors.append("rule=voice-manifest-json")
        return None, None
    return payload, hashlib.sha256(snapshot).hexdigest()


def _parse_frontmatter(text: str) -> tuple[dict[str, str], list[str]]:
    lines = text.splitlines()
    errors: list[str] = []
    if not lines or lines[0].strip() != "---":
        return {}, ["rule=handoff-frontmatter"]
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return {}, ["rule=handoff-frontmatter"]

    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not separator or not key or key in fields:
            errors.append("rule=handoff-frontmatter")
            continue
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        fields[key] = value

    required_fields = set(HandoffContract.required_fields())
    if not required_fields <= set(fields) or set(fields) - required_fields - {"covers"}:
        errors.append("rule=handoff-required-fields")
    return fields, errors


def _contains_dotdot(path: Path) -> bool:
    return ".." in path.parts


def _canonical_archive_root(project: Path | None = None) -> Path:
    if project is None:
        return ROOT / "01-内容生产" / "视频工作台" / "已制作"
    candidate = Path(project).absolute()
    try:
        archive_root = candidate.parents[1]
        if (
            archive_root.name != "已制作"
            or archive_root.parent.name != "视频工作台"
            or archive_root.parent.parent.name != "01-内容生产"
        ):
            return _canonical_archive_root()
    except IndexError:
        return _canonical_archive_root()
    return archive_root


def _prearchive_project_root(project: Path) -> Path:
    archive_root = _canonical_archive_root(project)
    return archive_root.parent / "制作中" / Path(project).name


def _is_canonical_archive_project(project: Path) -> bool:
    """Recognize only a safe, fixed two-level project below this workspace's archive root."""
    archive_root = _canonical_archive_root(project)
    if (
        not _safe_directory(archive_root)
        or not _safe_directory(project)
    ):
        return False
    try:
        relative = project.resolve(strict=True).relative_to(archive_root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return False
    return len(relative.parts) == 2 and all(part not in {".", ".."} for part in relative.parts)


def _is_historical_archive_context(
    project: Path,
    handoff: Path,
    final_mp4: Path,
    fields: dict[str, str],
) -> bool:
    """Require canonical archive paths and a completed handoff before allowing v1."""
    if (
        not _is_canonical_archive_project(project)
        or _contains_dotdot(project)
        or _contains_dotdot(handoff)
        or _contains_dotdot(final_mp4)
        or not _safe_file(handoff)
        or not _safe_file(final_mp4)
        or fields.get("status") != "已完成"
    ):
        return False
    try:
        resolved_project = project.resolve(strict=True)
        handoff.resolve(strict=True).relative_to(resolved_project)
        final_mp4.resolve(strict=True).relative_to(resolved_project)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _validate_handoff_contract(
    fields: dict[str, str], *, historical: bool, archive_project_name: str | None = None
) -> list[str]:
    errors: list[str] = []
    if fields.get("status") not in {"制作中", "已完成"}:
        errors.append("rule=handoff-status")
    if fields.get("ratio") != "16:9":
        errors.append("rule=handoff-ratio")
    try:
        duration = float(fields.get("duration_target_s", ""))
    except ValueError:
        duration = 0.0
    if not math.isfinite(duration) or duration <= 0:
        errors.append("rule=handoff-duration")
    try:
        word_count = int(fields.get("word_count", ""))
    except ValueError:
        word_count = 0
    if word_count <= 0 or str(word_count) != fields.get("word_count", ""):
        errors.append("rule=handoff-word-count")
    voice_id = fields.get("voice")
    if (
        voice_id not in SUPPORTED_VOICE_IDS
        if historical
        else voice_id != CURRENT_VOICE_ID
    ):
        errors.append("rule=handoff-voice")
    if fields.get("voice_provider") != "indextts2-local":
        errors.append("rule=handoff-voice")
    if fields.get("captions") != "asr-word-timestamps":
        errors.append("rule=handoff-captions")
    if fields.get("caption_style") != "anchor-dark":
        errors.append("rule=handoff-caption-style")
    slug = fields.get("archive_slug", "")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug) is None:
        errors.append("rule=handoff-archive-slug")
    elif historical and slug not in {
        archive_project_name,
        _archive_slug_from_project_name(archive_project_name),
    }:
        errors.append("rule=handoff-archive-slug")
    if not all(fields.get(name) for name in ("platform", "visual", "illustration_skill")):
        errors.append("rule=handoff-required-values")
    if "covers" in fields and fields.get("covers") not in SUPPORTED_COVER_CONTRACTS:
        errors.append("rule=handoff-covers")
    return errors


def _archive_slug_from_project_name(project_name: str | None) -> str | None:
    """Return the operator slug from a dated V2 archive name or a legacy name."""

    if project_name is None:
        return None
    dated = re.fullmatch(
        r"\d{4}-\d{2}-\d{2}-(?P<slug>[a-z0-9][a-z0-9-]{0,79})",
        project_name,
    )
    return dated.group("slug") if dated is not None else project_name


def _cue_time(value: str) -> float:
    hours, minutes, seconds, milliseconds = re.split(r"[:.,]", value.strip())
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def _read_cues(path: Path, *, vtt: bool) -> list[tuple[float, float, str]] | None:
    try:
        lines = path.read_text(encoding="utf-8").replace("\r", "").split("\n")
    except (OSError, UnicodeDecodeError):
        return None
    if vtt:
        if not lines or lines[0].strip() != "WEBVTT":
            return None
        lines = lines[1:]

    blocks = [block for block in "\n".join(lines).strip().split("\n\n") if block.strip()]
    cues: list[tuple[float, float, str]] = []
    for block in blocks:
        parts = [line.strip() for line in block.splitlines() if line.strip()]
        if not vtt and parts and parts[0].isdigit():
            parts = parts[1:]
        if len(parts) < 2 or " --> " not in parts[0]:
            return None
        try:
            start, end = (_cue_time(value) for value in parts[0].split(" --> "))
        except (TypeError, ValueError):
            return None
        if end <= start:
            return None
        cues.append((start, end, "\n".join(parts[1:])))
    return cues or None


def _caption_records(
    value: Any, duration: float, expected_source: str
) -> list[tuple[float, float, str]] | None:
    if not isinstance(value, list) or not value:
        return None
    previous_end = 0.0
    records: list[tuple[float, float, str]] = []
    for item in value:
        if not isinstance(item, dict) or item.get("source") != expected_source:
            return None
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (previous_end <= start < end <= duration + 0.001):
            return None
        previous_end = end
        records.append((start, end, text))
    return records


def _word_records(value: Any, duration: float) -> list[tuple[float, float, str]] | None:
    """Validate actual Task 7 word records; their authoritative source is phrase/QC metadata."""
    if not isinstance(value, list) or not value:
        return None
    previous_end = 0.0
    spoken = False
    records: list[tuple[float, float, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != WORD_FIELDS:
            return None
        text = item.get("text")
        is_gap = item.get("isGap")
        if not isinstance(text, str) or not isinstance(is_gap, bool):
            return None
        if not is_gap and not text.strip():
            return None
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (previous_end <= start < end <= duration + 0.001):
            return None
        previous_end = end
        spoken = spoken or not is_gap
        records.append((start, end, text))
    return records if spoken else None


def _same_cues(
    left: list[tuple[float, float, str]] | None,
    right: list[tuple[float, float, str]] | None,
) -> bool:
    if not left or not right or len(left) != len(right):
        return False
    return all(
        abs(first[0] - second[0]) < 0.002
        and abs(first[1] - second[1]) < 0.002
        and first[2] == second[2]
        for first, second in zip(left, right)
    )


def _image_is_decodable(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return False
    try:
        if path.stat().st_size <= 0:
            return False
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            check=False,
            timeout=20,
        )
        streams = json.loads(result.stdout.decode("utf-8", errors="replace")).get("streams", [])
        stream = next(item for item in streams if item.get("codec_type") == "video")
        return result.returncode == 0 and int(stream["width"]) > 0 and int(stream["height"]) > 0
    except (OSError, StopIteration, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return False


def _valid_cover_qc(project: Path) -> bool:
    paths = cover_artifact_paths(project)
    qc_path = paths[-1]
    try:
        qc = json.loads(
            qc_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False
    expected_keys = {
        "schema_version",
        "status",
        "contract",
        "source_sha256",
        "headline_sha256",
        "font_sha256",
        "font_size_px",
        "headline_line_count",
        "dimensions",
        "safe_boxes",
        "artifacts_sha256",
    }
    expected_dimensions = {
        relative: list(dimensions)
        for relative, dimensions in COVER_IMAGE_DIMENSIONS.items()
    }
    expected_safe_boxes = {
        relative: list(box) for relative, box in COVER_SAFE_BOXES.items()
    }
    if (
        not isinstance(qc, dict)
        or set(qc) != expected_keys
        or qc.get("schema_version") != 1
        or qc.get("status") != "pass"
        or qc.get("contract") != COVER_CONTRACT
        or not all(
            _is_sha256(qc.get(key))
            for key in ("source_sha256", "headline_sha256", "font_sha256")
        )
        or type(qc.get("font_size_px")) is not int
        or qc["font_size_px"] not in range(64, 113, 4)
        or qc.get("headline_line_count") not in {2, 3}
        or qc.get("dimensions") != expected_dimensions
        or qc.get("safe_boxes") != expected_safe_boxes
        or not isinstance(qc.get("artifacts_sha256"), dict)
        or set(qc["artifacts_sha256"]) != set(COVER_IMAGE_DIMENSIONS)
    ):
        return False
    for relative, expected_size in COVER_IMAGE_DIMENSIONS.items():
        path = project / relative
        try:
            with Image.open(path) as image:
                image.load()
                actual_size = image.size
        except (
            OSError,
            ValueError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            return False
        if (
            actual_size != expected_size
            or qc["artifacts_sha256"].get(relative) != _sha256(path)
        ):
            return False
    try:
        with Image.open(project / "封面/抖音-作品封面-1080x1920.png") as upload, Image.open(
            project / "封面/抖音-主页预览-1080x1440.png"
        ) as preview:
            upload.load()
            preview.load()
            if upload.mode not in {"RGB", "RGBA"} or preview.mode != upload.mode:
                return False
            decoded_upload = upload.crop((0, 240, 1080, 1680)).convert(upload.mode)
            decoded_preview = preview.convert(upload.mode)
            if (
                decoded_upload.tobytes()
                != decoded_preview.tobytes()
            ):
                return False
    except (
        OSError,
        ValueError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        return False
    return True


def _valid_contact_sheet_coverage(value: Any, duration: float) -> bool:
    if not isinstance(value, dict) or value.get("layout") != "3x2":
        return False
    if value.get("frame_count") != 6 or not isinstance(value.get("times_s"), list):
        return False
    if value.get("coverage") != [
        "open",
        "early",
        "mid",
        "transition",
        "late",
        "near-end",
    ]:
        return False
    times = value["times_s"]
    if len(times) != 6:
        return False
    try:
        numbers = [float(item) for item in times]
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(item) for item in numbers):
        return False
    if not all(0 <= first < second <= duration + 0.001 for first, second in zip(numbers, numbers[1:])):
        return False
    return numbers[0] <= min(1.0, duration * 0.15) and any(
        duration * 0.4 <= item <= duration * 0.6 for item in numbers
    ) and numbers[-1] >= duration * 0.9


def _caption_midpoints(captions: Any) -> list[float] | None:
    if not isinstance(captions, list) or not captions:
        return None
    times: list[float] = []
    try:
        for caption in captions:
            if not isinstance(caption, dict):
                return None
            start, end = float(caption["start"]), float(caption["end"])
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                return None
            times.append(round((start + end) / 2, 3))
    except (KeyError, TypeError, ValueError):
        return None
    return times[:6]


def _caption_frame_evidence(
    final_mp4: Path, timestamp: float, *, dark_threshold: int = 90
) -> tuple[str, int, int] | None:
    if dark_threshold not in {65, 90}:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{timestamp:.3f}", "-i", str(final_mp4), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            capture_output=True, check=False, timeout=60,
        )
        if result.returncode != 0 or len(result.stdout) != 1920 * 1080 * 3:
            return None
        pixels = result.stdout
        safe_zone = pixels[(1080 - 150) * 1920 * 3:]
        bright = sum(1 for index in range(0, len(safe_zone), 3) if min(safe_zone[index:index + 3]) >= 185)
        dark = sum(
            1
            for index in range(0, len(safe_zone), 3)
            if max(safe_zone[index:index + 3]) <= dark_threshold
        )
        return hashlib.sha256(pixels).hexdigest(), bright, dark
    except (OSError, subprocess.SubprocessError):
        return None


def _valid_caption_render_qc(value: Any, final_mp4: Path, captions_path: Path, captions: Any) -> bool:
    if not isinstance(value, dict) or value.get("status") != "pass":
        return False
    if value.get("timing_source") != "volcengine-word-timestamps" or value.get("caption_style") != "anchor-dark":
        return False
    if value.get("final_video_sha256") != _sha256(final_mp4) or value.get("captions_sha256") != _sha256(captions_path):
        return False
    expected_times = _caption_midpoints(captions)
    checks = value.get("frame_checks")
    if expected_times is None or not isinstance(checks, list) or len(checks) != len(expected_times):
        return False
    before_video, before_captions = _sha256(final_mp4), _sha256(captions_path)
    for expected_time, item in zip(expected_times, checks):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("time_s"), (int, float))
            or isinstance(item.get("time_s"), bool)
            or not math.isfinite(float(item["time_s"]))
            or float(item["time_s"]) != expected_time
            or not isinstance(item.get("bright_pixels"), int)
            or isinstance(item.get("bright_pixels"), bool)
            or not isinstance(item.get("dark_pixels"), int)
            or isinstance(item.get("dark_pixels"), bool)
            or not _is_sha256(item.get("frame_sha256"))
        ):
            return False
        evidence = _caption_frame_evidence(final_mp4, expected_time)
        legacy_evidence = None
        if evidence is not None and evidence[2] != item["dark_pixels"]:
            legacy_evidence = _caption_frame_evidence(
                final_mp4, expected_time, dark_threshold=65
            )
        accepted_evidence = (
            evidence
            if evidence is not None
            and evidence[0] == item["frame_sha256"].casefold()
            and evidence[1] == item["bright_pixels"]
            and evidence[2] == item["dark_pixels"]
            else legacy_evidence
        )
        if (
            accepted_evidence is None
            or accepted_evidence[0] != item["frame_sha256"].casefold()
            or accepted_evidence[1] != item["bright_pixels"]
            or accepted_evidence[2] != item["dark_pixels"]
            or accepted_evidence[1] < 12
            or accepted_evidence[2] < 500
        ):
            return False
    return before_video == _sha256(final_mp4) and before_captions == _sha256(captions_path)


def _probe_final_media(path: Path) -> dict[str, object] | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            timeout=20,
        )
        data = json.loads(result.stdout.decode("utf-8", errors="replace"))
        video = next(item for item in data["streams"] if item.get("codec_type") == "video")
        audio = next(item for item in data["streams"] if item.get("codec_type") == "audio")
        numerator, denominator = str(video["r_frame_rate"]).split("/")
        return {
            "width": int(video["width"]),
            "height": int(video["height"]),
            "fps": float(numerator) / float(denominator),
            "duration": float(data["format"]["duration"]),
            "video_codec": video["codec_name"],
            "audio_codec": audio["codec_name"],
            "channels": int(audio["channels"]),
        }
    except (OSError, KeyError, TypeError, ValueError, ZeroDivisionError, StopIteration, json.JSONDecodeError, subprocess.SubprocessError):
        return None


def _invalidate_old_report(project: Path) -> bool:
    report = project / "delivery-report.json"
    if not _is_contained(project, report):
        return False
    if report.exists() and not _safe_file(report):
        return False
    try:
        if report.exists():
            report.unlink()
    except OSError:
        return False
    return True


def _commit_report(
    project: Path,
    mode: str,
    media: dict[str, object],
    artifact_sha256: dict[str, str],
) -> str:
    """Write the pass marker through an unpredictable exclusive same-directory temp file."""
    target = project / "delivery-report.json"
    temporary = project / f".delivery-report-{uuid.uuid4().hex}.tmp"
    if not _is_contained(project, target) or not _is_contained(project, temporary):
        raise OSError("unsafe-report-path")
    if temporary.exists() or _is_reparse_point(temporary):
        raise OSError("unsafe-report-temp")
    payload = {
        "status": "pass",
        "mode": mode,
        "rules": [{"id": "delivery-hard-gates", "status": "pass"}],
        "artifacts": {
            "handoff": "交接稿.md",
            "narration": "工程/media/narration.wav",
            "final": "成片/",
            "contact_sheet": "质检/contact-sheet.jpg",
            "contact_sheet_qc": "质检/contact-sheet-qc.json",
        },
        "artifact_sha256": artifact_sha256,
        "media": media,
        "skipped_stages": (
            [
                {"stage": stage, "status": "sample-skipped"}
                for stage in (
                    "authorized-source-acquisition",
                    "private-transcript",
                    "local-narration",
                    "final-audio-asr",
                )
            ]
            if mode == "sample"
            else []
        ),
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with temporary.open("xb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    if not _safe_file(temporary) or not _is_contained(project, temporary):
        raise OSError("unsafe-report-temp")
    os.replace(temporary, target)
    return target.name


def _manifest_reference_matches_ledger(
    manifest: dict[str, Any],
    manifest_sha256: str | None,
) -> bool:
    """Bind every production voice manifest to its fixed Task 2 reference without diagnostics."""
    voice_id = manifest.get("voice_id")
    reference_value = manifest.get("reference_audio_path")
    reference_hash = manifest.get("reference_audio_sha256")
    if (
        not isinstance(voice_id, str)
        or voice_id not in SUPPORTED_VOICE_IDS
        or not isinstance(reference_value, str)
        or not reference_value
        or not _is_sha256(reference_hash)
    ):
        return False
    reference_path = Path(reference_value)
    ledger_path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH
    if (
        not reference_path.is_absolute()
        or not _safe_file(ledger_path)
        or not isinstance(manifest_sha256, str)
    ):
        return False
    try:
        ledger_snapshot = ledger_path.read_bytes()
        ledger_payload = json.loads(
            ledger_snapshot.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        issued_hashes, canonical_references, manifest_bindings = (
            indextts2._load_provenance_ledger_document(ledger_path)
        )
        if ledger_path.read_bytes() != ledger_snapshot:
            return False
        retired_voice_ids = frozenset(ledger_payload.get("retired_voice_ids", ()))
        canonical = next(
            entry
            for entry in canonical_references
            if entry["voice_id"] == voice_id
        )
        canonical_path = Path(canonical["reference_audio_path"])
        if (
            not indextts2._same_resolved_path(reference_path, canonical_path)
            or reference_hash.casefold() != canonical["reference_audio_sha256"].casefold()
            or any(
                part.casefold()
                in {"generated", "output", "outputs", "render", "renders"}
                for part in reference_path.parts
            )
        ):
            return False
        if voice_id in retired_voice_ids and not reference_path.exists():
            return voice_id != CURRENT_VOICE_ID and not canonical_path.exists()
        if not _safe_file(reference_path) or not _safe_file(canonical_path):
            return False
        if indextts2._contains_generated_directory(reference_path):
            return False
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            return False
        reference = indextts2._probe_pcm_wav(reference_path, ffprobe=ffprobe)
    except (
        OSError,
        RuntimeError,
        StopIteration,
        ValueError,
        indextts2.IndexTTS2ValidationError,
    ):
        return False
    return (
        reference.sha256.casefold() == reference_hash.casefold()
        and reference.sha256.casefold() == canonical["reference_audio_sha256"].casefold()
        and (
            voice_id != CURRENT_VOICE_ID
            or (
                manifest_bindings is not None
                and manifest["output_sha256"].casefold() in issued_hashes
                and manifest_sha256.casefold()
                in (
                    (binding,)
                    if isinstance(
                        binding := manifest_bindings.get(
                            manifest["output_sha256"].casefold()
                        ),
                        str,
                    )
                    else binding or ()
                )
            )
        )
    )


def _voice_manifest_is_valid(
    manifest: Any,
    manifest_sha256: str | None,
    narration: Path,
    project: Path,
    *,
    sample_mode: bool,
    historical: bool,
    handoff_voice_id: str | None,
) -> bool:
    if not isinstance(manifest, dict):
        return False
    voice_id = manifest.get("voice_id")
    if (
        voice_id not in SUPPORTED_VOICE_IDS
        if historical
        else voice_id != CURRENT_VOICE_ID
    ):
        return False
    if (
        manifest.get("provider") != "indextts2-local"
        or voice_id != handoff_voice_id
        or manifest.get("model") != "IndexTTS2"
        or manifest.get("used_fallback") is not False
        or not _is_sha256(manifest.get("output_sha256"))
        or manifest["output_sha256"].casefold() != _sha256(narration)
    ):
        return False
    output_path = manifest.get("output_path")
    if not isinstance(output_path, str) or not output_path:
        return False
    output_candidate = Path(output_path)
    if not output_candidate.is_absolute():
        output_candidate = project / output_candidate
    output_matches = output_candidate.absolute() == narration.absolute()
    if historical and not output_matches:
        output_matches = _matches_prearchive_narration_path(
            output_candidate,
            project,
            narration,
        )
    if not output_matches:
        output_matches = _matches_undated_predecessor_narration_path(
            output_candidate,
            project,
            narration,
        )
    if not output_matches or not _is_contained(project, narration):
        return False
    if sample_mode:
        return (
            manifest.get("sample_mode") is True
            and manifest.get("issuance") == "synthetic-local-sample"
            and _is_sha256(manifest.get("authorized_source_sha256"))
        )
    if set(manifest) != set(PRODUCTION_MANIFEST_FIELDS):
        return False
    if not all(
        isinstance(manifest.get(field), str) and manifest[field]
        for field in ("reference_audio_path", "segment_contract_path", "pronunciation_contract_path")
    ):
        return False
    if not _manifest_reference_matches_ledger(manifest, manifest_sha256):
        return False
    if not all(
        _is_sha256(manifest.get(field))
        for field in (
            "reference_audio_sha256",
            "segment_contract_sha256",
            "pronunciation_contract_sha256",
        )
    ):
        return False
    speed = manifest.get("playback_speed")
    return (
        isinstance(manifest.get("segment_count"), int)
        and not isinstance(manifest.get("segment_count"), bool)
        and manifest["segment_count"] > 0
        and isinstance(speed, (int, float))
        and not isinstance(speed, bool)
        and math.isfinite(float(speed))
        and abs(float(speed) - 1.12) < 0.000001
    )


def _matches_prearchive_narration_path(
    output_candidate: Path,
    project: Path,
    narration: Path,
) -> bool:
    """Accept only the fixed active-path predecessor of one canonical archive file."""

    if (
        not output_candidate.is_absolute()
        or _contains_dotdot(output_candidate)
        or not _is_canonical_archive_project(project)
    ):
        return False
    try:
        relative_narration = narration.absolute().relative_to(project.absolute())
    except ValueError:
        return False
    expected = _prearchive_project_root(project) / relative_narration
    return output_candidate.absolute() == expected.absolute()


def _matches_undated_predecessor_narration_path(
    output_candidate: Path,
    project: Path,
    narration: Path,
) -> bool:
    """Accept only the fixed undated predecessor of one date-prefixed project."""

    if not output_candidate.is_absolute() or _contains_dotdot(output_candidate):
        return False
    active_project = (
        _prearchive_project_root(project)
        if _is_canonical_archive_project(project)
        else project
    )
    match = re.fullmatch(
        r"\d{4}-\d{2}-\d{2}-(?P<slug>[a-z0-9][a-z0-9-]{0,79})",
        active_project.name,
    )
    if match is None or active_project.parent.name != "制作中":
        return False
    try:
        relative_narration = narration.absolute().relative_to(project.absolute())
    except ValueError:
        return False
    expected = active_project.parent / match.group("slug") / relative_narration
    return output_candidate.absolute() == expected.absolute()


def check_delivery(
    handoff: Path, project: Path, final_mp4: Path, *, sample_mode: bool = False
) -> DeliveryResult:
    """Run delivery checks and map filesystem races to redacted fixed rules."""

    try:
        return _check_delivery(handoff, project, final_mp4, sample_mode=sample_mode)
    except (OSError, RuntimeError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, wave.Error, subprocess.SubprocessError):
        return DeliveryResult(EXIT_CONTRACT, ("rule=delivery-local-stage",))


def _check_delivery(
    handoff: Path, project: Path, final_mp4: Path, *, sample_mode: bool = False
) -> DeliveryResult:
    """Run delivery gates without printing any private paths or source content."""
    project = Path(project)
    handoff = Path(handoff)
    final_mp4 = Path(final_mp4)
    if _contains_dotdot(project) or not _safe_directory(project):
        return DeliveryResult(EXIT_CONTRACT, ("rule=project-safe-directory",))
    if _contains_dotdot(handoff) or not _is_contained(project, handoff):
        return DeliveryResult(EXIT_CONTRACT, ("rule=handoff-project-containment",))
    if _contains_dotdot(final_mp4) or not _is_contained(project, final_mp4):
        return DeliveryResult(EXIT_CONTRACT, ("rule=final-project-containment",))
    if not _safe_file(handoff):
        return DeliveryResult(EXIT_MISSING, ("rule=handoff-missing",))
    if not _is_strictly_contained_regular_file(project, handoff):
        return DeliveryResult(EXIT_CONTRACT, ("rule=handoff-project-containment",))
    if final_mp4.exists() and not _is_strictly_contained_regular_file(project, final_mp4):
        return DeliveryResult(EXIT_CONTRACT, ("rule=final-project-containment",))
    archive_project = _is_canonical_archive_project(project)
    if not archive_project and not _invalidate_old_report(project):
        return DeliveryResult(EXIT_CONTRACT, ("rule=delivery-report-safe-target",))
    try:
        handoff_text = handoff.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return DeliveryResult(EXIT_MISSING, ("rule=handoff-readable",))

    privacy_failures = validate_public_handoff(handoff_text)
    if privacy_failures:
        diagnostics = tuple(
            "rule=privacy-" + failure.split(": ", 1)[-1].replace(" ", "-")
            for failure in privacy_failures
        )
        return DeliveryResult(EXIT_PRIVACY, diagnostics)

    fields, errors = _parse_frontmatter(handoff_text)
    historical = _is_historical_archive_context(project, handoff, final_mp4, fields)
    if archive_project and not historical:
        errors.append("rule=historical-archive-context")
    archive_project_name = project.resolve(strict=True).name if historical else None
    errors.extend(
        _validate_handoff_contract(
            fields,
            historical=historical,
            archive_project_name=archive_project_name,
        )
    )
    canonical_media_dir = project / "工程" / "media"
    canonical_media_layout = canonical_media_dir.exists() or _is_reparse_point(
        canonical_media_dir
    )
    media_dir = canonical_media_dir if canonical_media_layout else project / "media"
    canonical_caption_dir = media_dir / "captions"
    caption_dir = media_dir
    if (
        not sample_mode
        and (
            canonical_media_layout
            or canonical_caption_dir.exists()
            or _is_reparse_point(canonical_caption_dir)
        )
    ):
        caption_dir = canonical_caption_dir
    required = [
        media_dir / "narration.wav",
        media_dir / "voice_manifest.json",
        *(caption_dir / name for name in CAPTION_FILES),
        final_mp4,
        project / "质检" / "contact-sheet.jpg",
        project / "质检" / "contact-sheet-qc.json",
    ]
    if not sample_mode:
        required.append(project / "质检" / "caption-render-qc.json")
    if any(not _is_contained(project, path) or not _safe_file(path) for path in required):
        return DeliveryResult(EXIT_MISSING, ("rule=delivery-artifact-missing",))
    cover_artifacts: tuple[Path, ...] = ()
    if fields.get("covers") == COVER_CONTRACT:
        cover_artifacts = cover_artifact_paths(project)
        if any(
            not _is_strictly_contained_regular_file(project, path)
            for path in cover_artifacts
        ):
            return DeliveryResult(EXIT_MISSING, ("rule=cover-artifact-missing",))
        required.extend(cover_artifacts)

    narration = media_dir / "narration.wav"
    manifest_path = media_dir / "voice_manifest.json"
    asr_path, words_path, captions_path, srt_path, vtt_path, qc_path = (
        caption_dir / name for name in CAPTION_FILES
    )
    try:
        with wave.open(str(narration), "rb") as audio:
            narration_duration = audio.getnframes() / audio.getframerate()
    except (OSError, wave.Error, ZeroDivisionError):
        narration_duration = 0.0
        errors.append("rule=narration-wav")

    manifest, manifest_sha256 = _read_manifest_snapshot(manifest_path, errors)
    asr = _read_json(asr_path, "caption", errors)
    words = _read_json(words_path, "caption", errors)
    captions = _read_json(captions_path, "caption", errors)
    qc = _read_json(qc_path, "caption", errors)
    coverage = _read_json(project / "质检" / "contact-sheet-qc.json", "contact-sheet", errors)
    caption_render_qc = (
        _read_json(project / "质检" / "caption-render-qc.json", "caption-render", errors)
        if not sample_mode
        else None
    )
    content_snapshots: dict[Path, FileSnapshot] = {}
    content_artifacts = _content_delivery_artifacts(
        project,
        final_mp4,
        coverage,
        errors,
        historical=historical,
        validated_snapshots=content_snapshots,
    )
    content_lane = any(
        path.exists() or _is_reparse_point(path)
        for path in (project / marker for marker in CONTENT_MARKERS)
    )
    if not _voice_manifest_is_valid(
        manifest,
        manifest_sha256,
        narration,
        project,
        sample_mode=sample_mode,
        historical=historical,
        handoff_voice_id=fields.get("voice"),
    ):
        errors.append("rule=voice-manifest")

    expected_source = "synthetic-local-sample" if sample_mode else "volcengine-word-timestamps"
    phrases = _caption_records(captions, narration_duration, expected_source)
    word_records = _word_records(words, narration_duration)
    asr_ok = isinstance(asr, dict) and (not sample_mode or asr.get("synthetic") is True)
    if not phrases or not word_records or not asr_ok:
        errors.append("rule=caption-artifacts")
    narration_hash = _sha256(narration)
    qc_ok = (
        isinstance(qc, dict)
        and qc.get("status") == "pass"
        and qc.get("narration_sha256") == narration_hash
        and qc.get("timing_source") == expected_source
    )
    if not sample_mode and qc_ok:
        qc_ok = (
            isinstance(qc.get("alignment_coverage"), (int, float))
            and not isinstance(qc.get("alignment_coverage"), bool)
            and float(qc["alignment_coverage"]) >= 0.90
            and qc.get("source_media") == narration.name
            and isinstance(qc.get("asr_resource_id"), str)
            and bool(qc["asr_resource_id"].strip())
        )
    if not qc_ok:
        errors.append("rule=caption-qc")
    if not _same_cues(phrases, _read_cues(srt_path, vtt=False)) or not _same_cues(
        phrases, _read_cues(vtt_path, vtt=True)
    ):
        errors.append("rule=caption-text-consistency")
    if not _image_is_decodable(project / "质检" / "contact-sheet.jpg"):
        errors.append("rule=contact-sheet-image")
    if not content_lane and not _valid_contact_sheet_coverage(coverage, narration_duration):
        errors.append("rule=contact-sheet-coverage")
    if not sample_mode and not _valid_caption_render_qc(caption_render_qc, final_mp4, captions_path, captions):
        errors.append("rule=caption-render-qc")
    if cover_artifacts and not _valid_cover_qc(project):
        errors.append("rule=cover-qc")

    media = _probe_final_media(final_mp4)
    try:
        target_duration = float(fields.get("duration_target_s", "0"))
    except ValueError:
        target_duration = 0.0
    if media is None:
        errors.append("rule=final-media-spec")
    elif media["width"] != 1920 or media["height"] != 1080:
        errors.append("rule=final-media-dimensions")
    elif abs(float(media["fps"]) - 30) > 0.01:
        errors.append("rule=final-media-fps")
    elif (
        media["video_codec"] != "h264"
        or media["audio_codec"] != "aac"
        or int(media["channels"]) < 1
    ):
        errors.append("rule=final-media-codecs")
    elif (
        abs(float(media["duration"]) - target_duration) > 0.15
        or abs(float(media["duration"]) - narration_duration) > 0.15
    ):
        errors.append("rule=final-media-duration")

    if errors:
        return DeliveryResult(EXIT_CONTRACT, tuple(sorted(set(errors))))
    if not all(snapshot_matches(snapshot) for snapshot in content_snapshots.values()):
        return DeliveryResult(EXIT_CONTRACT, ("rule=scene-preview-qc",))
    if historical:
        return DeliveryResult(EXIT_PASS, ("status=pass mode=historical",))
    mode = "sample" if sample_mode else "production"
    try:
        checked_paths = (handoff, *required, *content_artifacts)
        receipt_artifacts = {}
        for path in checked_paths:
            snapshot = content_snapshots.get(path.absolute())
            receipt_artifacts[path.relative_to(project).as_posix()] = (
                snapshot.sha256 if snapshot is not None else _sha256(path)
            )
        if not all(snapshot_matches(snapshot) for snapshot in content_snapshots.values()):
            return DeliveryResult(EXIT_CONTRACT, ("rule=scene-preview-qc",))
        report_path = _commit_report(project, mode, media, receipt_artifacts)
        if not all(snapshot_matches(snapshot) for snapshot in content_snapshots.values()):
            _invalidate_old_report(project)
            return DeliveryResult(EXIT_CONTRACT, ("rule=scene-preview-qc",))
    except OSError:
        return DeliveryResult(EXIT_CONTRACT, ("rule=delivery-report-commit",))
    return DeliveryResult(EXIT_PASS, (f"status=pass mode={mode}",), report_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check V1 delivery offline; live APIs are not called."
    )
    parser.add_argument("handoff", type=Path)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("final_mp4", type=Path)
    parser.add_argument(
        "--sample-mode",
        action="store_true",
        help="offline sample only; live APIs are not called",
    )
    args = parser.parse_args(argv)
    result = check_delivery(args.handoff, args.project_dir, args.final_mp4, sample_mode=args.sample_mode)
    for diagnostic in result.diagnostics:
        print(diagnostic)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

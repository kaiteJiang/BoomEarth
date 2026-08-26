"""Extract and bind per-scene hard-QC evidence from the final MP4."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from PIL import Image as PILImage

from boomearth.video.artifacts import (
    ArtifactError,
    FileSnapshot,
    capture_regular_file,
    encode_canonical_json,
    load_json_snapshot,
    publish_bytes_no_clobber,
    snapshot_matches,
)
from boomearth.video.content_plan import ContentPlanError, load_content_plan_snapshot
from boomearth.video.scene_timeline import SceneTimelineError, validate_scene_timeline
from boomearth.video.motion_plan import MotionPlan, MotionPlanError, load_motion_plan_snapshot
from boomearth.workbench.source_artifacts import ArtifactRecord


_FRAME_BYTES = 1920 * 1080 * 3
_PTS_TIME_RE = re.compile(r"\bn:\s*0\s+pts:\s*\d+\s+pts_time:([0-9.]+)")


class SceneQCError(RuntimeError):
    """A fixed, redacted final-frame QC failure."""


@dataclass(frozen=True, slots=True)
class SceneQCEvidence:
    scene_id: str
    time_s: float
    kind: str
    frame_sha256: str
    preview_sha256: str
    foreground_pixels: int
    dark_caption_pixels: int
    bright_caption_pixels: int


@dataclass(frozen=True, slots=True)
class SceneQCResult:
    qc_path: Path
    previews_dir: Path
    evidence: tuple[SceneQCEvidence, ...]


def _reserve_preview_time(
    preferred: float,
    *,
    lower: float,
    upper: float,
    occupied: set[float],
) -> float:
    """Choose one deterministic open-interval sample without moving the cue."""

    span = upper - lower
    if not math.isfinite(span) or span <= 0.000002:
        raise SceneQCError("motion QC timing is invalid")
    candidates = (
        preferred,
        lower + span * 0.75,
        lower + span * 0.25,
        lower + span * 0.5,
    )
    for candidate in candidates:
        value = round(candidate, 6)
        if lower < value < upper and value not in occupied:
            occupied.add(value)
            return value
    raise SceneQCError("motion QC timing is invalid")


def motion_preview_times(plan: MotionPlan) -> dict[str, dict[str, float]]:
    """Derive deterministic six-state and semantic-sweep preview timestamps."""

    if not isinstance(plan, MotionPlan):
        raise SceneQCError("motion QC inputs are invalid")
    result: dict[str, dict[str, float]] = {}
    for scene in plan.scenes:
        title = next(
            (entry for entry in scene.entries if entry.target == "title-line-1"), None
        )
        visual = next(
            (entry for entry in scene.entries if entry.target == "visual"), None
        )
        note = next(
            (entry for entry in scene.entries if entry.target.startswith("note-row-")),
            None,
        )
        if title is None or visual is None:
            raise SceneQCError("motion QC inputs are invalid")
        duration = scene.end - scene.start
        values: dict[str, float] = {
            "start": round(scene.start + min(0.05, duration * 0.02), 6),
            "title-stable": round(title.at + title.duration + 0.02, 6),
            "visual-stable": round(visual.at + visual.duration + 0.02, 6),
            "notes-progress": round(
                (note.at + note.duration / 2) if note is not None else scene.start + duration * 0.4,
                6,
            ),
            "midpoint": round(scene.start + duration * 0.5, 6),
            "late": round(scene.end - min(0.10, duration * 0.03), 6),
        }
        occupied = set(values.values())
        margin = min(0.05, duration * 0.01)
        for index, cue in enumerate(scene.semantic_cues, 1):
            cue_end = cue.at + cue.duration
            if cue.at - scene.start > 0.000002:
                values[f"cue-{index:02d}-before"] = _reserve_preview_time(
                    max(scene.start + 0.001, cue.at - margin),
                    lower=scene.start,
                    upper=cue.at,
                    occupied=occupied,
                )
            values[f"cue-{index:02d}-during"] = _reserve_preview_time(
                cue.at + cue.duration / 2,
                lower=cue.at,
                upper=cue_end,
                occupied=occupied,
            )
            if scene.end - cue_end > 0.000002:
                values[f"cue-{index:02d}-after"] = _reserve_preview_time(
                    min(scene.end - 0.001, cue_end + margin),
                    lower=cue_end,
                    upper=scene.end,
                    occupied=occupied,
                )
        if (
            len(set(values.values())) != len(values)
            or any(value <= scene.start or value >= scene.end for value in values.values())
        ):
            raise SceneQCError("motion QC timing is invalid")
        result[scene.scene_id] = values
    return result


def publish_motion_preview_qc(
    final_mp4: Path, project_root: Path
) -> ArtifactRecord:
    """Extract, hash-bind, and publish Motion V2 preview evidence."""

    root = Path(project_root).absolute()
    final = Path(final_mp4).absolute()
    qc_root = root / "质检"
    previews = qc_root / "motion-previews"
    sheet = qc_root / "motion-contact-sheet.jpg"
    qc_path = qc_root / "motion-preview-qc.json"
    if (
        final.parent != root / "成片"
        or previews.exists()
        or sheet.exists()
        or qc_path.exists()
    ):
        raise SceneQCError("motion QC target is unavailable")
    try:
        motion = load_motion_plan_snapshot(root)
        final_snapshot = capture_regular_file(final, within=root)
    except (ArtifactError, MotionPlanError):
        raise SceneQCError("motion QC inputs are invalid") from None
    times = motion_preview_times(motion.plan)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SceneQCError("scene frame extraction failed")
    _ensure_qc_root(root, qc_root)
    stage = qc_root / f".motion-previews-{uuid.uuid4().hex}.tmp"
    temporary_sheet = qc_root / f".{sheet.name}-{uuid.uuid4().hex}.tmp.jpg"
    published = False
    sheet_published = False
    evidence: list[dict[str, object]] = []
    try:
        stage.mkdir()
        preview_files: list[Path] = []
        for scene_id, scene_times in times.items():
            for kind, timestamp in scene_times.items():
                preview = stage / f"{scene_id}-{kind}.png"
                frame, snapshot, _actual = _extract_frame(
                    ffmpeg=ffmpeg,
                    final_mp4=final,
                    timestamp=timestamp,
                    preview=preview,
                )
                preview_files.append(preview)
                evidence.append(
                    {
                        "scene_id": scene_id,
                        "kind": kind,
                        "time_s": timestamp,
                        "frame_sha256": hashlib.sha256(frame).hexdigest(),
                        "preview_sha256": snapshot.sha256,
                    }
                )
        if not snapshot_matches(final_snapshot) or not snapshot_matches(motion.snapshot):
            raise SceneQCError("scene QC input changed")
        os.rename(stage, previews)
        published = True
        columns = math.ceil(math.sqrt(len(preview_files)))
        rows = math.ceil(len(preview_files) / columns)
        canvas = PILImage.new("RGB", (columns * 480, rows * 270), "white")
        for index, original in enumerate(preview_files):
            path = previews / original.name
            with PILImage.open(path) as image:
                tile = image.convert("RGB").resize((480, 270), PILImage.Resampling.LANCZOS)
                canvas.paste(tile, ((index % columns) * 480, (index // columns) * 270))
        canvas.save(temporary_sheet, format="JPEG", quality=90, optimize=False)
        generated_sheet = capture_regular_file(temporary_sheet, within=root)
        publish_bytes_no_clobber(sheet, generated_sheet.payload, within=root)
        temporary_sheet.unlink(missing_ok=True)
        sheet_published = True
        payload = {
            "schema_version": 1,
            "status": "pass",
            "final_video_sha256": final_snapshot.sha256,
            "content_plan_sha256": motion.content_plan_snapshot.sha256,
            "scene_timeline_sha256": motion.scene_timeline_snapshot.sha256,
            "motion_plan_sha256": motion.snapshot.sha256,
            "contact_sheet_sha256": capture_regular_file(sheet, within=root).sha256,
            "evidence": evidence,
        }
        publish_bytes_no_clobber(qc_path, encode_canonical_json(payload), within=root)
        snapshot = capture_regular_file(qc_path, within=root)
        return ArtifactRecord(
            relative_path="质检/motion-preview-qc.json",
            sha256=snapshot.sha256,
            size_bytes=snapshot.size,
            path=qc_path,
        )
    except SceneQCError:
        raise
    except (ArtifactError, OSError, ValueError):
        raise SceneQCError("motion QC publication failed") from None
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        temporary_sheet.unlink(missing_ok=True)
        if published and not qc_path.exists():
            shutil.rmtree(previews, ignore_errors=True)
        if sheet_published and not qc_path.exists():
            sheet.unlink(missing_ok=True)


def _after_frames_extracted(_evidence: tuple[SceneQCEvidence, ...]) -> None:
    """Testing seam after real frame extraction and before publication."""


def _ensure_qc_root(root: Path, qc: Path) -> None:
    if qc != root / "质检":
        raise SceneQCError("scene QC target is unavailable")
    try:
        qc.mkdir(parents=False, exist_ok=True)
        entry = qc.lstat()
    except OSError:
        raise SceneQCError("scene QC target is unavailable") from None
    if (
        not stat.S_ISDIR(entry.st_mode)
        or qc.is_symlink()
        or bool(int(getattr(entry, "st_file_attributes", 0)) & 0x0400)
    ):
        raise SceneQCError("scene QC target is unavailable")


def scene_sample_times(*, start: float, end: float) -> tuple[float, float, float]:
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or not math.isfinite(float(start))
        or not math.isfinite(float(end))
        or float(end) - float(start) < 1.0
    ):
        raise SceneQCError("scene timing is invalid")
    start_value = float(start)
    end_value = float(end)
    duration = end_value - start_value
    settle = min(
        start_value + min(1.4, duration * 0.14),
        start_value + duration * 0.35,
    )
    midpoint = start_value + duration * 0.5
    late = max(midpoint, end_value - min(0.65, duration * 0.10))
    if not start_value < settle < end_value or not start_value < midpoint < end_value or not start_value < late < end_value:
        raise SceneQCError("scene timing is invalid")
    return tuple(round(value, 6) for value in (settle, midpoint, late))


def _project_root_from_formal(path: Path, *, name: str) -> Path:
    absolute = Path(path).absolute()
    if absolute.name != name or absolute.parent.name != "工程":
        raise SceneQCError("scene QC inputs are invalid")
    return absolute.parent.parent


def _caption_cues(snapshot: FileSnapshot) -> tuple[tuple[float, float], ...]:
    try:
        value = json.loads(snapshot.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SceneQCError("scene QC inputs are invalid") from None
    if not isinstance(value, list) or not value:
        raise SceneQCError("scene QC inputs are invalid")
    cues: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, dict):
            raise SceneQCError("scene QC inputs are invalid")
        start = item.get("start")
        end = item.get("end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or float(start) < 0.0
            or float(end) <= float(start)
        ):
            raise SceneQCError("scene QC inputs are invalid")
        cues.append((float(start), float(end)))
    return tuple(cues)


def _extract_rgb_frame_with_pts(
    *, ffmpeg: str, final_mp4: Path, timestamp: float
) -> tuple[bytes, float]:
    """Extract one deterministic 1920x1080 RGB frame for QC authentication."""

    try:
        raw = subprocess.run(
            [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(final_mp4),
            "-frames:v",
            "1",
            "-vf",
            "showinfo",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except subprocess.SubprocessError:
        raise SceneQCError("scene frame extraction failed") from None
    match = _PTS_TIME_RE.search(raw.stderr.decode("utf-8", errors="replace"))
    if raw.returncode != 0 or len(raw.stdout) != _FRAME_BYTES or match is None:
        raise SceneQCError("scene frame extraction failed")
    actual_timestamp = timestamp + float(match.group(1))
    if not math.isfinite(actual_timestamp):
        raise SceneQCError("scene frame extraction failed")
    return raw.stdout, actual_timestamp


def extract_rgb_frame(*, ffmpeg: str, final_mp4: Path, timestamp: float) -> bytes:
    """Extract one deterministic 1920x1080 RGB frame for QC authentication."""

    frame, _actual_timestamp = _extract_rgb_frame_with_pts(
        ffmpeg=ffmpeg, final_mp4=final_mp4, timestamp=timestamp
    )
    return frame


def _extract_frame(
    *, ffmpeg: str, final_mp4: Path, timestamp: float, preview: Path
) -> tuple[bytes, FileSnapshot, float]:
    frame, actual_timestamp = _extract_rgb_frame_with_pts(
        ffmpeg=ffmpeg,
        final_mp4=final_mp4,
        timestamp=timestamp,
    )
    try:
        png = subprocess.run(
            [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(final_mp4),
            "-frames:v",
            "1",
            "-y",
            str(preview),
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except subprocess.SubprocessError:
        raise SceneQCError("scene frame extraction failed") from None
    if png.returncode != 0:
        raise SceneQCError("scene frame extraction failed")
    try:
        preview_snapshot = capture_regular_file(preview)
    except ArtifactError:
        raise SceneQCError("scene frame extraction failed") from None
    return frame, preview_snapshot, actual_timestamp


def frame_pixel_counts(
    frame: bytes, *, dark_threshold: int = 90
) -> tuple[int, int, int]:
    """Return the exact visibility counts persisted in scene-preview QC."""

    if (
        not isinstance(frame, bytes)
        or len(frame) != _FRAME_BYTES
        or dark_threshold not in {65, 90}
    ):
        raise SceneQCError("scene frame extraction failed")
    foreground = 0
    for index in range(0, len(frame), 3):
        if min(frame[index : index + 3]) < 245:
            foreground += 1
    safe_zone = frame[(1080 - 150) * 1920 * 3 :]
    dark = 0
    bright = 0
    for index in range(0, len(safe_zone), 3):
        pixel = safe_zone[index : index + 3]
        if max(pixel) <= dark_threshold:
            dark += 1
        if min(pixel) >= 185:
            bright += 1
    return foreground, dark, bright


def _evidence_dict(item: SceneQCEvidence) -> dict[str, object]:
    return {
        "scene_id": item.scene_id,
        "time_s": item.time_s,
        "kind": item.kind,
        "frame_sha256": item.frame_sha256,
        "preview_sha256": item.preview_sha256,
        "foreground_pixels": item.foreground_pixels,
        "dark_caption_pixels": item.dark_caption_pixels,
        "bright_caption_pixels": item.bright_caption_pixels,
    }


def render_scene_qc(
    *,
    final_mp4: Path,
    content_plan: Path,
    scene_timeline: Path,
    qc_root: Path,
) -> SceneQCResult:
    """Publish twelve hash-bound preview checks for a four-scene final video."""

    root = _project_root_from_formal(content_plan, name="content-plan.json")
    if _project_root_from_formal(scene_timeline, name="scene-timeline.json") != root:
        raise SceneQCError("scene QC inputs are invalid")
    final = Path(final_mp4).absolute()
    qc = Path(qc_root).absolute()
    if final.parent != root / "成片" or qc != root / "质检":
        raise SceneQCError("scene QC inputs are invalid")
    previews = qc / "scene-previews"
    qc_path = qc / "scene-preview-qc.json"
    if previews.exists() or qc_path.exists():
        raise SceneQCError("scene QC target is unavailable")
    try:
        plan_snapshot = load_content_plan_snapshot(project_root=root)
        timeline_value, timeline_snapshot = load_json_snapshot(
            Path(scene_timeline), within=root
        )
        timeline = validate_scene_timeline(timeline_value, project_root=root)
        final_snapshot = capture_regular_file(final, within=root)
        _captions_value, captions_snapshot = load_json_snapshot(
            root / "工程" / "media" / "captions" / "captions.json", within=root
        )
    except (ArtifactError, ContentPlanError, SceneTimelineError):
        raise SceneQCError("scene QC inputs are invalid") from None
    if Path(content_plan).absolute() != plan_snapshot.path:
        raise SceneQCError("scene QC inputs are invalid")
    cues = _caption_cues(captions_snapshot)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SceneQCError("scene frame extraction failed")
    _ensure_qc_root(root, qc)
    stage = qc / f".scene-previews-{uuid.uuid4().hex}.tmp"
    published_previews = False
    evidence: list[SceneQCEvidence] = []
    try:
        stage.mkdir()
        for scene in timeline.scenes:
            times = scene_sample_times(start=scene.start, end=scene.end)
            for kind, timestamp in zip(("settle", "midpoint", "late"), times):
                preview = stage / f"{scene.id}-{kind}.png"
                frame, preview_snapshot, actual_timestamp = _extract_frame(
                    ffmpeg=ffmpeg,
                    final_mp4=final,
                    timestamp=timestamp,
                    preview=preview,
                )
                foreground, dark, bright = frame_pixel_counts(frame)
                if foreground < 2_000:
                    raise SceneQCError("scene frame visibility failed")
                caption_active = any(
                    start <= actual_timestamp <= end for start, end in cues
                )
                if caption_active and (dark < 1_000 or bright < 100):
                    raise SceneQCError("scene caption visibility failed")
                evidence.append(
                    SceneQCEvidence(
                        scene_id=scene.id,
                        time_s=timestamp,
                        kind=kind,
                        frame_sha256=hashlib.sha256(frame).hexdigest(),
                        preview_sha256=preview_snapshot.sha256,
                        foreground_pixels=foreground,
                        dark_caption_pixels=dark,
                        bright_caption_pixels=bright,
                    )
                )
        evidence_tuple = tuple(evidence)
        _after_frames_extracted(evidence_tuple)
        inputs = (
            plan_snapshot.snapshot,
            plan_snapshot.handoff_snapshot,
            plan_snapshot.manifest_snapshot,
            plan_snapshot.narration_contract_snapshot,
            timeline_snapshot,
            final_snapshot,
            captions_snapshot,
        )
        if not all(snapshot_matches(snapshot) for snapshot in inputs):
            raise SceneQCError("scene QC input changed")
        os.rename(stage, previews)
        published_previews = True
        payload = {
            "schema_version": 1,
            "status": "pass",
            "final_video_sha256": final_snapshot.sha256,
            "content_plan_sha256": plan_snapshot.snapshot.sha256,
            "scene_timeline_sha256": timeline_snapshot.sha256,
            "evidence": [_evidence_dict(item) for item in evidence_tuple],
        }
        publish_bytes_no_clobber(
            qc_path, encode_canonical_json(payload), within=root
        )
    except SceneQCError:
        raise
    except (ArtifactError, OSError, subprocess.SubprocessError):
        raise SceneQCError("scene QC publication failed") from None
    finally:
        if stage.exists() and stage.parent == qc:
            shutil.rmtree(stage, ignore_errors=True)
        if published_previews and not qc_path.exists() and previews.exists():
            shutil.rmtree(previews, ignore_errors=True)
    return SceneQCResult(
        qc_path=qc_path,
        previews_dir=previews,
        evidence=tuple(evidence),
    )


def _load_timeline(path: Path):
    root = _project_root_from_formal(path, name="scene-timeline.json")
    try:
        value, snapshot = load_json_snapshot(path, within=root)
        timeline = validate_scene_timeline(value, project_root=root)
    except (ArtifactError, SceneTimelineError):
        raise SceneQCError("scene QC inputs are invalid") from None
    return root, timeline, snapshot


def create_scene_contact_sheet(
    *, final_mp4: Path, scene_timeline: Path, output: Path
) -> tuple[float, ...]:
    """Create one deterministic contact sheet covering every scene midpoint."""

    root, timeline, timeline_snapshot = _load_timeline(Path(scene_timeline))
    final = Path(final_mp4).absolute()
    target = Path(output).absolute()
    if final.parent != root / "成片" or target != root / "质检" / "contact-sheet.jpg" or target.exists():
        raise SceneQCError("scene QC target is unavailable")
    try:
        final_snapshot = capture_regular_file(final, within=root)
    except ArtifactError:
        raise SceneQCError("scene QC inputs are invalid") from None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SceneQCError("scene frame extraction failed")
    times = tuple(round((scene.start + scene.end) / 2.0, 6) for scene in timeline.scenes)
    columns = math.ceil(math.sqrt(len(times)))
    rows = math.ceil(len(times) / columns)
    _ensure_qc_root(root, target.parent)
    temporary = target.parent / f".{target.name}-{uuid.uuid4().hex}.tmp.jpg"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    for timestamp in times:
        command.extend(["-ss", f"{timestamp:.6f}", "-i", str(final)])
    filters = ";".join(
        f"[{index}:v]scale=480:270,setsar=1[f{index}]" for index in range(len(times))
    )
    layout = "|".join(
        f"{(index % columns) * 480}_{(index // columns) * 270}"
        for index in range(len(times))
    )
    filters += ";" + "".join(f"[f{index}]" for index in range(len(times)))
    filters += f"xstack=inputs={len(times)}:layout={layout}[sheet]"
    command.extend(
        ["-filter_complex", filters, "-map", "[sheet]", "-frames:v", "1", "-y", str(temporary)]
    )
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=60)
        if result.returncode != 0:
            raise SceneQCError("scene contact sheet failed")
        generated = capture_regular_file(temporary, within=root)
        if not snapshot_matches(final_snapshot) or not snapshot_matches(timeline_snapshot):
            raise SceneQCError("scene QC input changed")
        publish_bytes_no_clobber(target, generated.payload, within=root)
    except SceneQCError:
        raise
    except (ArtifactError, OSError, subprocess.SubprocessError):
        raise SceneQCError("scene contact sheet failed") from None
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return times


def scene_contact_sheet_qc(
    *, scene_timeline: Path, times: tuple[float, ...]
) -> dict[str, object]:
    """Return the deterministic all-scene midpoint coverage receipt payload."""

    _root, timeline, _snapshot = _load_timeline(Path(scene_timeline))
    expected = tuple(round((scene.start + scene.end) / 2.0, 6) for scene in timeline.scenes)
    if (
        not isinstance(times, tuple)
        or len(times) != len(expected)
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in times
        )
        or any(abs(float(value) - target) > 0.001 for value, target in zip(times, expected))
    ):
        raise SceneQCError("scene contact sheet coverage is invalid")
    columns = math.ceil(math.sqrt(len(expected)))
    rows = math.ceil(len(expected) / columns)
    return {
        "frame_count": len(expected),
        "times_s": list(expected),
        "layout": f"{columns}x{rows}",
        "coverage": "all-scene-midpoints",
    }

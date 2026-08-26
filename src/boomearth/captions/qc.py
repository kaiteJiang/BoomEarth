"""Write canonical final-audio caption artifacts and enforce their QC gate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from boomearth.providers.volcengine_asr import ASRTranscription, redact_sensitive_data

from .align import (
    CaptionAlignmentError,
    DISCOURSE_CONNECTORS,
    TIMING_SOURCE,
    Caption,
    align_display_script,
    group_caption_phrases,
    reading_units,
    words_with_gaps,
)


class CaptionArtifactError(RuntimeError):
    """Raised when canonical caption artifacts cannot be safely produced."""


class CaptionQCError(CaptionArtifactError):
    """Raised after a failed QC report blocks caption delivery."""


@dataclass(frozen=True, slots=True)
class CaptionArtifacts:
    paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _OriginalArtifactState:
    exists: bool
    contents: bytes | None


@dataclass(frozen=True, slots=True)
class _SourceMediaSnapshot:
    device: int
    inode: int
    size: int
    modified_ns: int
    sha256: str


_ARTIFACT_FILENAMES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_QC_MARKER_INDEX = _ARTIFACT_FILENAMES.index("caption-qc.json")
_NON_QC_ARTIFACT_INDEXES = tuple(
    index for index in range(len(_ARTIFACT_FILENAMES)) if index != _QC_MARKER_INDEX
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _capture_source_media_snapshot(path: Path) -> _SourceMediaSnapshot:
    try:
        before = path.lstat()
        _require_no_reparse_components(path, unavailable_message="final source media is unavailable")
        if not stat.S_ISREG(before.st_mode):
            raise OSError
        digest = _sha256(path)
        after = path.lstat()
        _require_no_reparse_components(path, unavailable_message="final source media is unavailable")
    except OSError:
        raise CaptionArtifactError("final source media is unavailable") from None
    if (
        not stat.S_ISREG(after.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise CaptionArtifactError("final source media is unavailable")
    return _SourceMediaSnapshot(
        device=before.st_dev,
        inode=before.st_ino,
        size=before.st_size,
        modified_ns=before.st_mtime_ns,
        sha256=digest,
    )


def _source_media_snapshot_matches(path: Path, snapshot: _SourceMediaSnapshot) -> bool:
    try:
        current = _capture_source_media_snapshot(path)
    except CaptionArtifactError:
        return False
    return current == snapshot


def _without_terminal_punctuation(text: str) -> str:
    return text.strip().rstrip("，、；：,;:。？！!?").rstrip()


def _split_connectors(captions: tuple[Caption, ...]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for index, caption in enumerate(captions):
        stripped = _without_terminal_punctuation(caption.text)
        for connector in DISCOURSE_CONNECTORS:
            if stripped == connector or stripped.endswith(connector):
                findings.append({"caption": index + 1, "phrase": connector})
                break
        if index + 1 >= len(captions):
            continue
        next_text = captions[index + 1].text.lstrip()
        for connector in DISCOURSE_CONNECTORS:
            if any(
                caption.text.rstrip().endswith(connector[:cut])
                and next_text.startswith(connector[cut:])
                for cut in range(1, len(connector))
            ):
                findings.append({"after_caption": index + 1, "phrase": connector})
    return findings


def evaluate_caption_qc(
    *,
    captions: tuple[Caption, ...],
    alignment_coverage: float,
    script_characters: int,
    matched_characters: int,
    asr_characters: int,
    word_units: int,
    source_media: str,
    resource_id: str,
    unmatched_substantive_characters: int = 0,
) -> dict[str, object]:
    """Return the renderer-facing caption QC report without logging script content."""

    errors: list[str] = []
    warnings: list[str] = []
    overlap_count = 0
    short_fragments: list[dict[str, object]] = []
    speeds: list[float] = []
    for index, caption in enumerate(captions):
        duration = caption.end - caption.start
        if duration <= 0:
            errors.append(f"caption {index + 1} has non-positive duration")
        elif duration < 0.5:
            errors.append(f"caption {index + 1} is shorter than 0.5s")
        units = reading_units(caption.text)
        speeds.append(units / max(duration, 0.001))
        if len(captions) > 1 and units <= 1.0:
            short_fragments.append(
                {
                    "caption": index + 1,
                    "text": caption.text,
                    "duration": round(duration, 3),
                }
            )
        if index and caption.start < captions[index - 1].end:
            overlap_count += 1

    connector_splits = _split_connectors(captions)
    if unmatched_substantive_characters:
        errors.append(
            f"{unmatched_substantive_characters} unmatched substantive display characters"
        )
    if alignment_coverage < 0.90:
        errors.append(f"alignment coverage {alignment_coverage:.4f} is below 0.9000")
    if overlap_count:
        errors.append(f"{overlap_count} caption overlaps detected")
    if short_fragments:
        errors.append(f"{len(short_fragments)} isolated short caption fragments detected")
    if connector_splits:
        errors.append(f"{len(connector_splits)} discourse connectors split across captions")
    maximum_speed = max(speeds, default=0.0)
    if maximum_speed > 12.0:
        errors.append(f"maximum reading speed {maximum_speed:.2f} units/s exceeds 12.0")
    elif maximum_speed > 9.0:
        warnings.append(
            f"maximum reading speed {maximum_speed:.2f} units/s exceeds preferred 9.0"
        )
    if not captions:
        errors.append("no captions generated")

    return {
        "status": "fail" if errors else "pass",
        "timing_source": TIMING_SOURCE,
        "alignment_coverage": round(alignment_coverage, 6),
        "minimum_coverage": 0.90,
        "script_characters": script_characters,
        "matched_characters": matched_characters,
        "asr_characters": asr_characters,
        "word_units": word_units,
        "caption_count": len(captions),
        "overlap_count": overlap_count,
        "max_reading_units_per_second": round(maximum_speed, 3),
        "short_fragments": short_fragments,
        "split_connectors": connector_splits,
        "source_media": source_media,
        "asr_resource_id": resource_id,
        "warnings": warnings,
        "errors": errors,
    }


def _timestamp(seconds: float, separator: str) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{separator}{milliseconds:03d}"


def _subtitle_text(captions: tuple[Caption, ...], *, is_vtt: bool) -> str:
    blocks: list[str] = []
    separator = "." if is_vtt else ","
    for index, caption in enumerate(captions, 1):
        timing = (
            f"{_timestamp(caption.start, separator)} --> "
            f"{_timestamp(caption.end, separator)}"
        )
        blocks.append(f"{timing}\n{caption.text}" if is_vtt else f"{index}\n{timing}\n{caption.text}")
    prefix = "WEBVTT\n\n" if is_vtt else ""
    return prefix + "\n\n".join(blocks) + "\n"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    return stat.S_ISLNK(stat_result.st_mode) or bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _absolute_path(path: Path) -> Path:
    return Path(path).absolute()


def _require_no_reparse_components(path: Path, *, unavailable_message: str) -> Path:
    """Reject every lexical component so a junction cannot redirect an artifact write."""

    absolute_path = _absolute_path(path)
    current = absolute_path
    while True:
        try:
            current_stat = current.lstat()
        except OSError as error:
            raise CaptionArtifactError(unavailable_message) from None
        if _is_reparse_point(current_stat):
            raise CaptionArtifactError(unavailable_message)
        if current == current.parent:
            return absolute_path
        current = current.parent


def _require_normal_directory(path: Path, *, unavailable_message: str) -> Path:
    absolute_path = _require_no_reparse_components(path, unavailable_message=unavailable_message)
    try:
        path_stat = absolute_path.lstat()
    except OSError as error:
        raise CaptionArtifactError(unavailable_message) from None
    if _is_reparse_point(path_stat) or not stat.S_ISDIR(path_stat.st_mode):
        raise CaptionArtifactError(unavailable_message)
    return absolute_path


def _require_normal_file(path: Path, *, unavailable_message: str) -> Path:
    absolute_path = _require_no_reparse_components(path, unavailable_message=unavailable_message)
    try:
        path_stat = absolute_path.lstat()
    except OSError as error:
        raise CaptionArtifactError(unavailable_message) from None
    if _is_reparse_point(path_stat) or not stat.S_ISREG(path_stat.st_mode):
        raise CaptionArtifactError(unavailable_message)
    return absolute_path


def _require_normal_artifact_target(path: Path) -> None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise CaptionArtifactError("caption artifact target is unavailable") from None
    if _is_reparse_point(path_stat) or not stat.S_ISREG(path_stat.st_mode):
        raise CaptionArtifactError("caption artifact target is unavailable")


def _read_artifact_state(path: Path, *, error_message: str) -> _OriginalArtifactState:
    """Read one normal artifact state without treating a missing file as an empty file."""

    try:
        before = path.lstat()
    except FileNotFoundError:
        return _OriginalArtifactState(exists=False, contents=None)
    except OSError as error:
        raise CaptionArtifactError(error_message) from None
    if _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
        raise CaptionArtifactError(error_message)
    try:
        contents = path.read_bytes()
        after = path.lstat()
    except OSError as error:
        raise CaptionArtifactError(error_message) from None
    if (
        _is_reparse_point(after)
        or not stat.S_ISREG(after.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(contents) != before.st_size
    ):
        raise CaptionArtifactError(error_message)
    return _OriginalArtifactState(exists=True, contents=contents)


def _artifact_matches_original_state(path: Path, original: _OriginalArtifactState) -> bool:
    return _read_artifact_state(
        path,
        error_message="caption artifact recovery failed",
    ) == original


def _write_original_backup(
    backup: Path,
    original: _OriginalArtifactState,
) -> None:
    """Persist a snapshot only for exceptional recovery of a still-live QC marker."""

    if not original.exists or original.contents is None:
        raise CaptionArtifactError("caption artifact recovery failed")
    backup_state = _read_artifact_state(
        backup,
        error_message="caption artifact recovery failed",
    )
    if backup_state.exists:
        if backup_state == original:
            return
        raise CaptionArtifactError("caption artifact recovery failed")
    try:
        backup.write_bytes(original.contents)
    except OSError as error:
        raise CaptionArtifactError("caption artifact recovery failed") from None
    if not _artifact_matches_original_state(backup, original):
        raise CaptionArtifactError("caption artifact recovery failed")


def _create_stage_workspace(active_media_dir: Path) -> Path:
    workspace = active_media_dir / f".caption-transaction-{uuid.uuid4().hex}"
    try:
        workspace.mkdir()
    except OSError as error:
        raise CaptionArtifactError("caption staging workspace is unavailable") from None
    if workspace.parent != active_media_dir:
        raise CaptionArtifactError("caption staging workspace is unavailable")
    try:
        workspace_stat = workspace.lstat()
    except OSError as error:
        raise CaptionArtifactError("caption staging workspace is unavailable") from None
    if _is_reparse_point(workspace_stat) or not stat.S_ISDIR(workspace_stat.st_mode):
        raise CaptionArtifactError("caption staging workspace is unavailable")
    return workspace


def _cleanup_stage_workspace(*, active_media_dir: Path, workspace: Path) -> None:
    """Remove only known regular stage files after revalidating the exact direct child."""

    if workspace.parent != active_media_dir:
        raise CaptionArtifactError("caption staging workspace is unavailable")
    try:
        workspace_stat = workspace.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise CaptionArtifactError("caption staging workspace is unavailable") from None
    if _is_reparse_point(workspace_stat) or not stat.S_ISDIR(workspace_stat.st_mode):
        raise CaptionArtifactError("caption staging workspace is unavailable")
    try:
        children = tuple(workspace.iterdir())
    except OSError as error:
        raise CaptionArtifactError("caption staging workspace is unavailable") from None
    for child in children:
        try:
            child_stat = child.lstat()
        except OSError as error:
            raise CaptionArtifactError("caption staging workspace is unavailable") from None
        if _is_reparse_point(child_stat) or not stat.S_ISREG(child_stat.st_mode):
            raise CaptionArtifactError("caption staging workspace is unavailable")
        try:
            child.unlink()
        except OSError as error:
            raise CaptionArtifactError("caption staging workspace is unavailable") from None
    try:
        workspace.rmdir()
    except OSError as error:
        raise CaptionArtifactError("caption staging workspace is unavailable") from None


def _publish_staged_artifact(staged: Path, target: Path) -> None:
    _require_normal_file(staged, unavailable_message="caption staging workspace is unavailable")
    _require_normal_artifact_target(target)
    try:
        os.replace(staged, target)
    except OSError as error:
        raise CaptionArtifactError("caption artifact publication failed") from None


def _move_existing_artifact_to_backup(target: Path, backup: Path) -> bool:
    """Atomically detach a normal target before the transaction changes any artifacts."""

    _require_normal_artifact_target(target)
    try:
        target.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise CaptionArtifactError("caption artifact backup failed") from None
    try:
        backup.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise CaptionArtifactError("caption artifact backup failed") from None
    else:
        raise CaptionArtifactError("caption artifact backup failed")
    try:
        os.replace(target, backup)
    except OSError as error:
        raise CaptionArtifactError("caption artifact backup failed") from None
    return True


def _restore_backup_artifact(backup: Path, target: Path) -> None:
    _require_normal_file(backup, unavailable_message="caption artifact recovery failed")
    _require_normal_artifact_target(target)
    try:
        os.replace(backup, target)
    except OSError as error:
        raise CaptionArtifactError("caption artifact recovery failed") from None


def _remove_normal_artifact(target: Path, *, error_message: str) -> None:
    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise CaptionArtifactError(error_message) from None
    if _is_reparse_point(target_stat) or not stat.S_ISREG(target_stat.st_mode):
        raise CaptionArtifactError(error_message)
    try:
        target.unlink()
    except OSError as error:
        raise CaptionArtifactError(error_message) from None


def _restore_artifact_to_original_state(
    *,
    target: Path,
    backup: Path,
    original: _OriginalArtifactState,
) -> None:
    target_matches = _artifact_matches_original_state(target, original)
    backup_state = _read_artifact_state(
        backup,
        error_message="caption artifact recovery failed",
    )
    backup_matches = backup_state == original

    if original.exists:
        if backup_matches:
            _restore_backup_artifact(backup, target)
        elif not target_matches:
            raise CaptionArtifactError("caption artifact recovery failed")
    else:
        if backup_state.exists:
            raise CaptionArtifactError("caption artifact recovery failed")
        if not target_matches:
            _remove_normal_artifact(
                target,
                error_message="caption artifact recovery failed",
            )

    if not _artifact_matches_original_state(target, original):
        raise CaptionArtifactError("caption artifact recovery failed")


def _all_artifacts_match_original_states(
    paths: tuple[Path, ...],
    originals: tuple[_OriginalArtifactState, ...],
) -> bool:
    return all(
        _artifact_matches_original_state(path, original)
        for path, original in zip(paths, originals)
    )


def _recover_original_artifacts(
    *,
    paths: tuple[Path, ...],
    backups: tuple[Path, ...],
    originals: tuple[_OriginalArtifactState, ...],
) -> None:
    """Recover from actual target/backup identities and restore the QC marker last."""

    if _all_artifacts_match_original_states(paths, originals):
        return

    qc_target = paths[_QC_MARKER_INDEX]
    qc_backup = backups[_QC_MARKER_INDEX]
    qc_original = originals[_QC_MARKER_INDEX]
    qc_backup_matches = _artifact_matches_original_state(qc_backup, qc_original)
    qc_target_matches = _artifact_matches_original_state(qc_target, qc_original)

    if qc_original.exists and not qc_backup_matches:
        if not qc_target_matches:
            _remove_normal_artifact(
                qc_target,
                error_message="caption artifact recovery failed",
            )
            raise CaptionArtifactError("caption artifact recovery failed")
        _write_original_backup(qc_backup, qc_original)
    _remove_normal_artifact(
        qc_target,
        error_message="caption artifact recovery failed",
    )

    for index in _NON_QC_ARTIFACT_INDEXES:
        _restore_artifact_to_original_state(
            target=paths[index],
            backup=backups[index],
            original=originals[index],
        )

    if not all(
        _artifact_matches_original_state(paths[index], originals[index])
        for index in _NON_QC_ARTIFACT_INDEXES
    ):
        raise CaptionArtifactError("caption artifact recovery failed")

    _restore_artifact_to_original_state(
        target=qc_target,
        backup=qc_backup,
        original=qc_original,
    )

    if not _all_artifacts_match_original_states(paths, originals):
        raise CaptionArtifactError("caption artifact recovery failed")


def write_caption_artifacts(
    *,
    media_dir: Path,
    source_media: Path,
    display_script: str,
    transcription: ASRTranscription,
) -> CaptionArtifacts:
    """Write the six canonical artifacts beside the exact final source media."""

    active_media_dir = _require_normal_directory(
        Path(media_dir),
        unavailable_message="active project media directory is unavailable",
    )
    final_media = _require_normal_file(
        Path(source_media),
        unavailable_message="final source media is unavailable",
    )
    if final_media.parent != active_media_dir:
        raise CaptionArtifactError("final source media must be in the active project media directory")
    source_snapshot = _capture_source_media_snapshot(final_media)

    alignment = align_display_script(display_script, transcription.words)
    try:
        captions = (
            group_caption_phrases(display_script, transcription.words, alignment)
            if not alignment.unmatched_substantive_characters
            else ()
        )
    except CaptionAlignmentError:
        captions = ()
    qc = evaluate_caption_qc(
        captions=captions,
        alignment_coverage=alignment.coverage,
        script_characters=alignment.script_characters,
        matched_characters=alignment.matched_characters,
        asr_characters=alignment.asr_characters,
        word_units=len(transcription.words),
        source_media=final_media.name,
        resource_id=transcription.resource_id,
        unmatched_substantive_characters=alignment.unmatched_substantive_characters,
    )
    qc["narration_sha256"] = source_snapshot.sha256
    paths = tuple(active_media_dir / name for name in _ARTIFACT_FILENAMES)
    for path in paths:
        _require_normal_artifact_target(path)
    originals = tuple(
        _read_artifact_state(path, error_message="caption artifact target is unavailable")
        for path in paths
    )

    workspace: Path | None = None
    try:
        workspace = _create_stage_workspace(active_media_dir)
        staged_paths = tuple(workspace / name for name in _ARTIFACT_FILENAMES)
        _write_json(staged_paths[0], redact_sensitive_data(transcription.raw_response))
        _write_json(staged_paths[1], words_with_gaps(transcription.words))
        _write_json(staged_paths[2], [caption.to_dict() for caption in captions])
        staged_paths[3].write_text(_subtitle_text(captions, is_vtt=False), encoding="utf-8")
        staged_paths[4].write_text(_subtitle_text(captions, is_vtt=True), encoding="utf-8")
        _write_json(staged_paths[5], qc)
    except (CaptionArtifactError, OSError):
        if workspace is not None:
            try:
                _cleanup_stage_workspace(
                    active_media_dir=active_media_dir,
                    workspace=workspace,
                )
            except CaptionArtifactError:
                pass
        raise CaptionArtifactError("caption artifacts could not be published") from None

    backups = tuple(
        workspace / f"backup-{index}-{name}"
        for index, name in enumerate(_ARTIFACT_FILENAMES)
    )
    try:
        _move_existing_artifact_to_backup(
            paths[_QC_MARKER_INDEX],
            backups[_QC_MARKER_INDEX],
        )
        for index in _NON_QC_ARTIFACT_INDEXES:
            _move_existing_artifact_to_backup(
                paths[index],
                backups[index],
            )
        for index in _NON_QC_ARTIFACT_INDEXES:
            _publish_staged_artifact(staged_paths[index], paths[index])
        if not _source_media_snapshot_matches(final_media, source_snapshot):
            raise CaptionArtifactError("final source media is unavailable")
        _publish_staged_artifact(
            staged_paths[_QC_MARKER_INDEX],
            paths[_QC_MARKER_INDEX],
        )
    except (CaptionArtifactError, OSError):
        try:
            _recover_original_artifacts(
                paths=paths,
                backups=backups,
                originals=originals,
            )
        except CaptionArtifactError:
            try:
                _remove_normal_artifact(
                    paths[_QC_MARKER_INDEX],
                    error_message="caption artifact recovery failed",
                )
            except CaptionArtifactError:
                pass
            raise CaptionArtifactError(
                "caption artifact recovery: manual recovery required"
            ) from None
        try:
            _cleanup_stage_workspace(active_media_dir=active_media_dir, workspace=workspace)
        except CaptionArtifactError:
            pass
        raise CaptionArtifactError("caption artifacts could not be published") from None

    try:
        _cleanup_stage_workspace(active_media_dir=active_media_dir, workspace=workspace)
    except CaptionArtifactError:
        pass
    if qc["status"] != "pass":
        raise CaptionQCError("caption QC failed")
    return CaptionArtifacts(paths=paths)


def generate_captions_from_locked_final_audio(
    *,
    narrator: object,
    asr_client: object,
    final_wav: Path,
    voice_manifest: Path,
    display_script: str,
    media_dir: Path,
) -> CaptionArtifacts:
    """Verify Task 6's manifest, then submit that same final WAV to one ASR boundary."""

    probe = getattr(narrator, "probe_committed_wav", None)
    transcribe = getattr(asr_client, "transcribe_file", None)
    if not callable(probe) or not callable(transcribe):
        raise CaptionArtifactError("locked final-audio dependencies are unavailable")
    committed_probe = probe(final_wav, voice_manifest)
    expected_sha256 = getattr(committed_probe, "sha256", None)
    transcription = transcribe(final_wav, expected_sha256=expected_sha256)
    if not isinstance(transcription, ASRTranscription):
        raise CaptionArtifactError("Volcengine/Doubao ASR result is unavailable")
    return write_caption_artifacts(
        media_dir=media_dir,
        source_media=final_wav,
        display_script=display_script,
        transcription=transcription,
    )

"""Prepare an offline V2 render snapshot; this command performs no network operations."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.audio import indextts2
from boomearth.audio.indextts2 import IndexTTS2Narrator, TTSRouting
from boomearth.captions.align import reading_units


CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)
PRODUCTION_MANIFEST_FIELDS = {
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
}
REPARSE_POINT = 0x0400
CANONICAL_QC_FIELDS = {
    "status",
    "timing_source",
    "alignment_coverage",
    "minimum_coverage",
    "script_characters",
    "matched_characters",
    "asr_characters",
    "word_units",
    "caption_count",
    "overlap_count",
    "max_reading_units_per_second",
    "short_fragments",
    "split_connectors",
    "narration_sha256",
    "source_media",
    "asr_resource_id",
    "warnings",
    "errors",
}


@dataclass(frozen=True, slots=True)
class PreparedProject:
    output_dir: Path
    duration_seconds: float


def _after_capture_read(source: Path) -> None:
    """Testing seam for source identity changes between capture reads."""


def _after_capture(stage: Path) -> None:
    """Testing seam after active artifacts have been captured."""


def _after_stage_assembled(stage: Path, destination: Path) -> None:
    """Testing seam before the private stage becomes renderer-visible."""


def _before_publish(destination: Path) -> None:
    """Testing seam immediately before the no-replace directory publication."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return bool(attributes & REPARSE_POINT)


def _has_reparse_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    return any(
        _is_reparse_point(candidate)
        for candidate in (absolute, *absolute.parents)
        if candidate.exists() or candidate.is_symlink()
    )


def _is_ordinary_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode) and not _has_reparse_component(path)
    except OSError:
        return False


def _is_ordinary_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode) and not _has_reparse_component(path)
    except OSError:
        return False


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("production render inputs are invalid") from exc


def _capture_file(source: Path, destination: Path) -> None:
    """Capture one stable normal file once, without a later active-path reopen."""
    try:
        before = source.lstat()
    except OSError as exc:
        raise ValueError("production render inputs are invalid") from exc
    if _is_reparse_point(source) or not stat.S_ISREG(before.st_mode):
        raise ValueError("production render inputs are invalid")
    try:
        with source.open("rb") as stream:
            contents = stream.read()
        _after_capture_read(source)
        after = source.lstat()
    except OSError as exc:
        raise ValueError("production render inputs are invalid") from exc
    if (
        _has_reparse_component(source)
        or not stat.S_ISREG(after.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(contents) != before.st_size
    ):
        raise ValueError("production render inputs are invalid")
    try:
        with destination.open("xb") as target:
            target.write(contents)
            target.flush()
            os.fsync(target.fileno())
    except OSError as exc:
        raise ValueError("production render snapshot could not be prepared") from exc


def _validate_manifest(manifest_path: Path, narration: Path, active_narration: Path) -> dict[str, Any]:
    try:
        manifest, _manifest_sha256 = indextts2._read_manifest_snapshot(manifest_path)
    except indextts2.IndexTTS2ValidationError as exc:
        raise ValueError("production render inputs are invalid") from exc
    if (
        set(manifest) != PRODUCTION_MANIFEST_FIELDS
        or manifest.get("provider") != "indextts2-local"
        or manifest.get("voice_id") != indextts2.CURRENT_VOICE_ID
        or manifest.get("model") != "IndexTTS2"
        or manifest.get("used_fallback") is not False
        or manifest.get("playback_speed") != 1.12
        or not _is_sha256(manifest.get("output_sha256"))
        or manifest["output_sha256"].casefold() != _sha256(narration)
        or not isinstance(manifest.get("output_path"), str)
        or Path(manifest["output_path"]).resolve(strict=False) != active_narration.resolve(strict=False)
        or not all(
            _is_sha256(manifest.get(field))
            for field in (
                "reference_audio_sha256",
                "segment_contract_sha256",
                "pronunciation_contract_sha256",
            )
        )
        or not isinstance(manifest.get("segment_count"), int)
        or isinstance(manifest.get("segment_count"), bool)
        or manifest["segment_count"] <= 0
    ):
        raise ValueError("production render inputs are invalid")
    return manifest


def _probe_captured_committed_wav(
    *,
    route: TTSRouting,
    narration: Path,
    manifest_path: Path,
    active_narration: Path,
) -> float:
    """Probe captured WAV bytes and verify their exact captured manifest/ledger binding."""
    narrator = IndexTTS2Narrator(route)
    try:
        probe = narrator.probe_existing_wav(narration)
        manifest, manifest_sha256 = indextts2._read_manifest_snapshot(manifest_path)
        issued_hashes, _references, bindings = indextts2._load_provenance_ledger_document(
            route.provenance_ledger_path
        )
    except indextts2.IndexTTS2ValidationError as exc:
        raise ValueError("production render inputs are invalid") from exc
    _validate_manifest(manifest_path, narration, active_narration)
    if (
        bindings is None
        or probe.sha256.casefold() not in issued_hashes
        or bindings.get(probe.sha256.casefold()) != manifest_sha256.casefold()
    ):
        raise ValueError("production render inputs are invalid")
    return probe.duration_seconds


def _caption_records(value: Any, duration: float) -> list[tuple[float, float, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("production render inputs are invalid")
    previous_end = 0.0
    records: list[tuple[float, float, str]] = []
    for item in value:
        if not isinstance(item, dict) or item.get("source") != "volcengine-word-timestamps":
            raise ValueError("production render inputs are invalid")
        text = item.get("text")
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("production render inputs are invalid") from exc
        if (
            not isinstance(text, str)
            or not text.strip()
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not (previous_end <= start < end <= duration + 0.001)
        ):
            raise ValueError("production render inputs are invalid")
        previous_end = end
        records.append((start, end, text))
    return records


def _word_records(value: Any, duration: float) -> list[tuple[float, float, str, bool]]:
    if not isinstance(value, list) or not value:
        raise ValueError("production render inputs are invalid")
    previous_end = 0.0
    spoken = False
    records: list[tuple[float, float, str, bool]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"text", "start", "end", "isGap"}:
            raise ValueError("production render inputs are invalid")
        text = item.get("text")
        is_gap = item.get("isGap")
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("production render inputs are invalid") from exc
        if (
            not isinstance(text, str)
            or not isinstance(is_gap, bool)
            or (not is_gap and not text.strip())
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not (previous_end <= start < end <= duration + 0.001)
        ):
            raise ValueError("production render inputs are invalid")
        previous_end = end
        spoken = spoken or not is_gap
        records.append((start, end, text, is_gap))
    if not spoken:
        raise ValueError("production render inputs are invalid")
    return records


def _cue_time(value: str) -> float:
    hours, minutes, seconds, milliseconds = re.split(r"[:.,]", value.strip())
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1_000


def _read_cues(path: Path, *, vtt: bool) -> list[tuple[float, float, str]]:
    try:
        lines = path.read_text(encoding="utf-8").replace("\r", "").split("\n")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("production render inputs are invalid") from exc
    if vtt:
        if not lines or lines[0].strip() != "WEBVTT":
            raise ValueError("production render inputs are invalid")
        lines = lines[1:]
    blocks = [block for block in "\n".join(lines).strip().split("\n\n") if block.strip()]
    cues: list[tuple[float, float, str]] = []
    for block in blocks:
        parts = [line.strip() for line in block.splitlines() if line.strip()]
        if not vtt and parts and parts[0].isdigit():
            parts = parts[1:]
        if len(parts) < 2 or " --> " not in parts[0]:
            raise ValueError("production render inputs are invalid")
        try:
            start, end = (_cue_time(value) for value in parts[0].split(" --> "))
        except (TypeError, ValueError) as exc:
            raise ValueError("production render inputs are invalid") from exc
        if end <= start:
            raise ValueError("production render inputs are invalid")
        cues.append((start, end, "\n".join(parts[1:])))
    if not cues:
        raise ValueError("production render inputs are invalid")
    return cues


def _same_cues(left: list[tuple[float, float, str]], right: list[tuple[float, float, str]]) -> bool:
    return len(left) == len(right) and all(
        abs(first[0] - second[0]) < 0.002
        and abs(first[1] - second[1]) < 0.002
        and first[2] == second[2]
        for first, second in zip(left, right)
    )


def _normalized_asr_text(value: str) -> str:
    return "".join(character.casefold() for character in value if character.isalnum())


def _validate_asr(
    value: Any,
    caption_words: list[tuple[float, float, str, bool]],
    duration: float,
) -> str:
    if not isinstance(value, dict) or "result" not in value:
        raise ValueError("production render inputs are invalid")
    result = value["result"]
    if not isinstance(result, dict) or not isinstance(result.get("text"), str) or not result["text"].strip():
        raise ValueError("production render inputs are invalid")
    utterances = result.get("utterances")
    if not isinstance(utterances, list) or not utterances:
        raise ValueError("production render inputs are invalid")
    expected_words = [(start, end, text) for start, end, text, is_gap in caption_words if not is_gap]
    raw_words: list[tuple[float, float, str]] = []
    previous_end = 0.0
    for utterance in utterances:
        words = utterance.get("words") if isinstance(utterance, dict) else None
        if not isinstance(words, list) or not words:
            raise ValueError("production render inputs are invalid")
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str) or not word["text"].strip():
                raise ValueError("production render inputs are invalid")
            try:
                start = float(word["start_time"])
                end = float(word["end_time"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("production render inputs are invalid") from exc
            start /= 1_000
            end /= 1_000
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or not (previous_end <= start < end <= duration + 0.001)
            ):
                raise ValueError("production render inputs are invalid")
            previous_end = end
            raw_words.append((start, end, word["text"]))
    if len(raw_words) != len(expected_words):
        raise ValueError("production render inputs are invalid")
    if any(
        raw_text != expected_text
        or abs(raw_start - expected_start) > 0.001
        or abs(raw_end - expected_end) > 0.001
        for (raw_start, raw_end, raw_text), (expected_start, expected_end, expected_text) in zip(
            raw_words, expected_words
        )
    ):
        raise ValueError("production render inputs are invalid")
    normalized_result = _normalized_asr_text(result["text"])
    if not normalized_result or normalized_result != _normalized_asr_text("".join(text for _, _, text in raw_words)):
        raise ValueError("production render inputs are invalid")
    return normalized_result


def _validate_captions(captions_dir: Path, narration: Path, duration: float) -> None:
    files = tuple(captions_dir / name for name in CAPTION_FILES)
    if not _is_ordinary_directory(captions_dir) or any(not _is_ordinary_file(path) for path in files):
        raise ValueError("production render inputs are invalid")
    words = _word_records(_read_json(captions_dir / "captions_words.json"), duration)
    spoken_words = sum(1 for _start, _end, _text, is_gap in words if not is_gap)
    asr_text = _validate_asr(_read_json(captions_dir / "asr-result.json"), words, duration)
    phrases = _caption_records(_read_json(captions_dir / "captions.json"), duration)
    if not _same_cues(phrases, _read_cues(captions_dir / "captions.srt", vtt=False)) or not _same_cues(
        phrases, _read_cues(captions_dir / "captions.vtt", vtt=True)
    ):
        raise ValueError("production render inputs are invalid")
    qc = _read_json(captions_dir / "caption-qc.json")
    numeric_character_fields = ("script_characters", "matched_characters", "asr_characters")
    maximum_reading_speed = max(reading_units(text) / (end - start) for start, end, text in phrases)
    expected_warnings = (
        [f"maximum reading speed {maximum_reading_speed:.2f} units/s exceeds preferred 9.0"]
        if 9.0 < maximum_reading_speed <= 12.0
        else []
    )
    if (
        not isinstance(qc, dict)
        or set(qc) != CANONICAL_QC_FIELDS
        or qc.get("status") != "pass"
        or qc.get("timing_source") != "volcengine-word-timestamps"
        or qc.get("narration_sha256") != _sha256(narration)
        or qc.get("source_media") != narration.name
        or qc.get("minimum_coverage") != 0.90
        or not isinstance(qc.get("asr_resource_id"), str)
        or not qc["asr_resource_id"].strip()
        or qc.get("caption_count") != len(phrases)
        or qc.get("word_units") != spoken_words
        or qc.get("overlap_count") != 0
        or qc.get("errors") != []
        or not isinstance(qc.get("warnings"), list)
        or not isinstance(qc.get("short_fragments"), list)
        or not isinstance(qc.get("split_connectors"), list)
        or not isinstance(qc.get("alignment_coverage"), (int, float))
        or isinstance(qc.get("alignment_coverage"), bool)
        or not math.isfinite(float(qc["alignment_coverage"]))
        or not 0.90 <= float(qc["alignment_coverage"]) <= 1.0
        or any(
            not isinstance(qc.get(field), int) or isinstance(qc.get(field), bool) or qc[field] < 0
            for field in numeric_character_fields
        )
        or qc["script_characters"] == 0
        or qc["matched_characters"] > qc["script_characters"]
        or qc["matched_characters"] > qc["asr_characters"]
        or qc["asr_characters"] != len(asr_text)
        or abs(float(qc["alignment_coverage"]) - qc["matched_characters"] / qc["script_characters"]) > 0.000001
        or not isinstance(qc.get("max_reading_units_per_second"), (int, float))
        or isinstance(qc.get("max_reading_units_per_second"), bool)
        or not math.isfinite(float(qc["max_reading_units_per_second"]))
        or float(qc["max_reading_units_per_second"]) < 0
        or qc["max_reading_units_per_second"] != round(maximum_reading_speed, 3)
        or maximum_reading_speed > 12.0
        or qc["warnings"] != expected_warnings
    ):
        raise ValueError("production render inputs are invalid")


def _ensure_output_parent(workspace_root: Path, output_parent: Path) -> None:
    if not _is_within(output_parent, workspace_root):
        raise ValueError("production render inputs are invalid")
    relative = output_parent.resolve(strict=False).relative_to(workspace_root.resolve(strict=True))
    current = workspace_root
    for component in relative.parts:
        current = current / component
        if current.exists():
            if not _is_ordinary_directory(current):
                raise ValueError("production render inputs are invalid")
            continue
        try:
            current.mkdir()
        except FileExistsError:
            if not _is_ordinary_directory(current):
                raise ValueError("production render inputs are invalid") from None
        except OSError as exc:
            raise ValueError("production render inputs are invalid") from exc


def _create_private_stage(workspace_root: Path, output_name: str) -> Path:
    parent = workspace_root / "runtime" / "v2-production-stages"
    _ensure_output_parent(workspace_root, parent)
    try:
        path = Path(tempfile.mkdtemp(prefix=f".{output_name}.stage-", dir=parent))
    except OSError as exc:
        raise ValueError("production render snapshot could not be prepared") from exc
    if not _is_ordinary_directory(path):
        raise ValueError("production render snapshot could not be prepared")
    return path


def _publish_stage_no_replace(stage: Path, destination: Path) -> None:
    if os.name == "nt":
        move_file = ctypes.windll.kernel32.MoveFileW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        move_file.restype = ctypes.c_int
        if not move_file(str(stage), str(destination)):
            raise FileExistsError("production render destination is unavailable")
        return
    if destination.exists():
        raise FileExistsError("production render destination is unavailable")
    os.rename(stage, destination)


def _render_index(template: Path, *, duration: float) -> str:
    scene_split = duration / 2
    try:
        rendered = template.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("production render template is unavailable") from exc
    replacements = {
        "__DURATION_SECONDS__": f"{duration:.6f}",
        "__SCENE_SPLIT_SECONDS__": f"{scene_split:.6f}",
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    if any(placeholder in rendered for placeholder in replacements):
        raise ValueError("production render template is unavailable")
    return rendered


def prepare_project(
    *,
    narration: Path,
    manifest: Path,
    captions_dir: Path,
    output_dir: Path,
    workspace_root: Path,
) -> PreparedProject:
    """Capture, validate, and atomically publish an offline renderer snapshot."""
    workspace_root = Path(workspace_root)
    narration = Path(narration)
    manifest = Path(manifest)
    captions_dir = Path(captions_dir)
    output_dir = Path(output_dir)
    if (
        not _is_ordinary_directory(workspace_root)
        or any(not _is_within(path, workspace_root) for path in (narration, manifest, captions_dir, output_dir))
        or not _is_ordinary_file(narration)
        or not _is_ordinary_file(manifest)
        or narration.name != "narration.wav"
        or manifest.name != "voice_manifest.json"
        or narration.parent != manifest.parent
        or captions_dir != narration.parent / "captions"
        or output_dir.exists()
    ):
        raise ValueError("production render inputs are invalid")
    _ensure_output_parent(workspace_root, output_dir.parent)
    stage: Path | None = None
    try:
        stage = _create_private_stage(workspace_root, output_dir.name)
        media_dir = stage / "media"
        media_dir.mkdir()
        for source in (narration, manifest, *(captions_dir / name for name in CAPTION_FILES)):
            _capture_file(source, media_dir / source.name)
        _after_capture(stage)

        staged_narration = media_dir / "narration.wav"
        staged_manifest = media_dir / "voice_manifest.json"
        route = TTSRouting.load(workspace_root / "automation" / "config" / "tts-routing.json")
        duration = _probe_captured_committed_wav(
            route=route,
            narration=staged_narration,
            manifest_path=staged_manifest,
            active_narration=narration,
        )
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("production render inputs are invalid")
        _validate_captions(media_dir, staged_narration, duration)
        rendered_html = _render_index(
            ROOT / "video-production-sample" / "index.template.html",
            duration=duration,
        )
        (stage / "index.html").write_text(rendered_html, encoding="utf-8", newline="\n")
        dependency = ROOT / "node_modules" / "gsap" / "dist" / "gsap.min.js"
        dependency_target = stage / "node_modules" / "gsap" / "dist" / "gsap.min.js"
        dependency_target.parent.mkdir(parents=True)
        _capture_file(dependency, dependency_target)
        _after_stage_assembled(stage, output_dir)
        _before_publish(output_dir)
        _publish_stage_no_replace(stage, output_dir)
    except (OSError, ValueError, indextts2.IndexTTS2ValidationError) as exc:
        raise ValueError("production render snapshot could not be prepared") from exc
    return PreparedProject(output_dir=output_dir, duration_seconds=duration)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare an offline V2 render directory; no network operations are performed."
    )
    parser.add_argument("--workspace-root", type=Path, default=ROOT, metavar="ROOT")
    parser.add_argument("--narration", required=True, type=Path, metavar="WAV")
    parser.add_argument("--manifest", required=True, type=Path, metavar="MANIFEST")
    parser.add_argument("--captions-dir", required=True, type=Path, metavar="DIRECTORY")
    parser.add_argument("--output-dir", required=True, type=Path, metavar="DIRECTORY")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        prepare_project(
            narration=args.narration,
            manifest=args.manifest,
            captions_dir=args.captions_dir,
            output_dir=args.output_dir,
            workspace_root=args.workspace_root,
        )
    except ValueError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

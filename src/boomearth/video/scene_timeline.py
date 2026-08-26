"""Derive continuous scene timing only from final-audio word timestamps."""

from __future__ import annotations

import io
import json
import math
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boomearth.captions.align import AlignmentResult, align_display_script
from boomearth.providers.volcengine_asr import ASRWord
from boomearth.video.artifacts import (
    ArtifactError,
    FileSnapshot,
    capture_regular_file,
    encode_canonical_json,
    publish_bytes_no_clobber,
    snapshot_matches,
)
from boomearth.video.content_plan import (
    ContentPlanError,
    ContentPlanSnapshot,
    load_content_plan_snapshot,
)


_TIMELINE_FIELDS = {
    "schema_version",
    "narration_sha256",
    "captions_words_sha256",
    "content_plan_sha256",
    "duration_seconds",
    "alignment_coverage",
    "scenes",
}
_SCENE_FIELDS = {
    "id",
    "start",
    "end",
    "matched_units",
    "segment_ids",
    "alignment_coverage",
}
_WORD_FIELDS = {"text", "start", "end", "isGap"}
_MAX_UNMAPPED_NOISE_WORDS = 6
_MAX_UNMAPPED_NOISE_UNITS_PER_WORD = 1
_CHINESE_DIGITS = "零一二三四五六七八九"
_SINGLE_CHARACTER_HOMOPHONES = (frozenset("他她它"), frozenset("的地得"))


class SceneTimelineError(RuntimeError):
    """A fixed, redacted final-audio timeline failure."""


@dataclass(frozen=True, slots=True)
class SceneTiming:
    id: str
    start: float
    end: float
    matched_units: int
    segment_ids: tuple[str, ...]
    alignment_coverage: float


@dataclass(frozen=True, slots=True)
class SceneTimeline:
    schema_version: int
    narration_sha256: str
    captions_words_sha256: str
    content_plan_sha256: str
    duration_seconds: float
    alignment_coverage: float
    scenes: tuple[SceneTiming, ...]


@dataclass(frozen=True, slots=True)
class SceneTimelineSnapshot:
    path: Path
    timeline: SceneTimeline
    snapshot: FileSnapshot
    content_plan_snapshot: FileSnapshot
    narration_snapshot: FileSnapshot
    captions_words_snapshot: FileSnapshot
    caption_qc_snapshot: FileSnapshot


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneTimelineError("caption words are invalid")
    result = float(value)
    if not math.isfinite(result):
        raise SceneTimelineError("caption words are invalid")
    return result


def _decode_json(snapshot: FileSnapshot, *, error_message: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SceneTimelineError(error_message)
            result[key] = value
        return result

    try:
        return json.loads(
            snapshot.payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except SceneTimelineError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SceneTimelineError(error_message) from None


def _wav_duration(snapshot: FileSnapshot) -> float:
    try:
        with wave.open(io.BytesIO(snapshot.payload), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            frame_rate = source.getframerate()
            frame_count = source.getnframes()
            compression = source.getcomptype()
    except (EOFError, wave.Error):
        raise SceneTimelineError("final narration is invalid") from None
    if (
        channels not in {1, 2}
        or sample_width not in {1, 2, 3, 4}
        or frame_rate <= 0
        or frame_count <= 0
        or compression != "NONE"
    ):
        raise SceneTimelineError("final narration is invalid")
    duration = frame_count / frame_rate
    if not math.isfinite(duration) or duration < 1.0:
        raise SceneTimelineError("final narration is invalid")
    return duration


def _parse_manifest_output_hash(plan_snapshot: ContentPlanSnapshot) -> str:
    try:
        value = json.loads(plan_snapshot.manifest_snapshot.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SceneTimelineError("final narration is invalid") from None
    expected = value.get("output_sha256") if isinstance(value, dict) else None
    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise SceneTimelineError("final narration is invalid")
    return expected


def _load_words(snapshot: FileSnapshot, *, duration: float) -> tuple[ASRWord, ...]:
    value = _decode_json(snapshot, error_message="caption words are invalid")
    if not isinstance(value, list) or not value:
        raise SceneTimelineError("caption words are invalid")
    words: list[ASRWord] = []
    previous_end = 0.0
    for item in value:
        if not isinstance(item, dict) or set(item) != _WORD_FIELDS:
            raise SceneTimelineError("caption words are invalid")
        text = item["text"]
        is_gap = item["isGap"]
        start = _number(item["start"])
        end = _number(item["end"])
        if (
            not isinstance(text, str)
            or not isinstance(is_gap, bool)
            or start < 0.0
            or end <= start
            or start < previous_end - 1e-9
            or end > duration + 0.05
            or (is_gap and text != "")
            or (not is_gap and not text.strip())
        ):
            raise SceneTimelineError("caption words are invalid")
        previous_end = end
        if not is_gap:
            words.append(ASRWord(text=text, start=start, end=end))
    if not words:
        raise SceneTimelineError("caption words are invalid")
    return tuple(words)


def _validate_caption_qc(snapshot: FileSnapshot, *, narration_sha256: str) -> None:
    value = _decode_json(snapshot, error_message="caption QC is invalid")
    if not isinstance(value, dict):
        raise SceneTimelineError("caption QC is invalid")
    coverage = value.get("alignment_coverage")
    if (
        value.get("status") != "pass"
        or value.get("timing_source") != "volcengine-word-timestamps"
        or value.get("narration_sha256") != narration_sha256
        or isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not math.isfinite(float(coverage))
        or float(coverage) < 0.90
        or float(coverage) > 1.0
    ):
        raise SceneTimelineError("caption QC is invalid")


def _segment_texts(plan_snapshot: ContentPlanSnapshot) -> tuple[str, ...]:
    try:
        lines = plan_snapshot.narration_contract_snapshot.payload.decode("utf-8").splitlines()
        values = tuple(json.loads(line) for line in lines)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SceneTimelineError("narration contract is invalid") from None
    texts = tuple(value.get("text") if isinstance(value, dict) else None for value in values)
    if not texts or not all(isinstance(text, str) and text for text in texts):
        raise SceneTimelineError("narration contract is invalid")
    return tuple(str(text).replace("\r\n", "\n").replace("\r", "\n").strip() for text in texts)


def _chinese_integer(value: int) -> str | None:
    if not 0 <= value < 100:
        return None
    if value < 10:
        return _CHINESE_DIGITS[value]
    tens, ones = divmod(value, 10)
    prefix = "" if tens == 1 else _CHINESE_DIGITS[tens]
    suffix = "" if ones == 0 else _CHINESE_DIGITS[ones]
    return f"{prefix}十{suffix}"


def _is_equivalent_number_normalization(
    *,
    word_index: int,
    words: tuple[ASRWord, ...],
    alignment: AlignmentResult,
    script: str,
) -> bool:
    token = words[word_index].text
    if not token.isascii():
        return False
    previous_positions = [
        script_index
        for script_index, mapped_index in zip(
            alignment.script_indices, alignment.word_indices
        )
        if mapped_index == word_index - 1
    ]
    next_positions = [
        script_index
        for script_index, mapped_index in zip(
            alignment.script_indices, alignment.word_indices
        )
        if mapped_index == word_index + 1
    ]
    if not previous_positions or not next_positions:
        return False
    gap = "".join(
        character
        for character in script[max(previous_positions) + 1 : min(next_positions)]
        if character.isalnum()
    )
    if token.isdigit() and 1 <= len(token) <= 2:
        return gap == _chinese_integer(int(token))
    match = re.fullmatch(r"(\d{1,2})[~-](\d{1,2})", token)
    if match is None:
        return False
    start = _chinese_integer(int(match.group(1)))
    end = _chinese_integer(int(match.group(2)))
    if start is None or end is None:
        return False
    return gap in {f"{start}到{end}", f"{start}至{end}"}


def _is_equivalent_single_character_homophone(
    *,
    word_index: int,
    words: tuple[ASRWord, ...],
    alignment: AlignmentResult,
    script: str,
) -> bool:
    token = words[word_index].text
    if len(token) != 1:
        return False
    previous_positions = [
        script_index
        for script_index, mapped_index in zip(
            alignment.script_indices, alignment.word_indices
        )
        if mapped_index == word_index - 1
    ]
    next_positions = [
        script_index
        for script_index, mapped_index in zip(
            alignment.script_indices, alignment.word_indices
        )
        if mapped_index == word_index + 1
    ]
    if not previous_positions or not next_positions:
        return False
    gap = "".join(
        character
        for character in script[max(previous_positions) + 1 : min(next_positions)]
        if character.isalnum()
    )
    return len(gap) == 1 and any(
        token in group and gap in group for group in _SINGLE_CHARACTER_HOMOPHONES
    )


def _is_short_ascii_prefix_noise(
    *,
    word_index: int,
    words: tuple[ASRWord, ...],
    mapped_word_indexes: set[int],
) -> bool:
    word = words[word_index]
    if (
        word_index == 0
        or word_index + 1 >= len(words)
        or word_index - 1 not in mapped_word_indexes
        or word_index + 1 not in mapped_word_indexes
        or not word.text.isascii()
        or not word.text.isalpha()
        or not 1 <= len(word.text) <= 4
        or word.end - word.start > 0.25
    ):
        return False
    previous = words[word_index - 1]
    following = words[word_index + 1]
    return (
        following.text.isascii()
        and following.text.isalpha()
        and 0 <= word.start - previous.end <= 0.30
        and 0 <= following.start - word.end <= 0.30
    )


def _alignment_scenes(
    *,
    plan_snapshot: ContentPlanSnapshot,
    words: tuple[ASRWord, ...],
    duration: float,
) -> tuple[float, tuple[SceneTiming, ...]]:
    texts = _segment_texts(plan_snapshot)
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for text in texts:
        offsets.append((cursor, cursor + len(text)))
        cursor += len(text)
    full_script = "".join(texts)
    try:
        alignment = align_display_script(full_script, words)
    except Exception:
        raise SceneTimelineError("word alignment is invalid") from None
    if not math.isfinite(alignment.coverage) or alignment.coverage < 0.90:
        raise SceneTimelineError("word alignment is invalid")
    mapped_word_indexes = {
        index for index in alignment.word_indices if index is not None
    }
    substantive_word_indexes = {
        index for index, word in enumerate(words) if any(character.isalnum() for character in word.text)
    }
    unmapped_word_indexes = {
        index
        for index in substantive_word_indexes - mapped_word_indexes
        if not _is_equivalent_number_normalization(
            word_index=index,
            words=words,
            alignment=alignment,
            script=full_script,
        )
        and not _is_equivalent_single_character_homophone(
            word_index=index,
            words=words,
            alignment=alignment,
            script=full_script,
        )
        and not _is_short_ascii_prefix_noise(
            word_index=index,
            words=words,
            mapped_word_indexes=mapped_word_indexes,
        )
    }
    unmapped_units = tuple(
        sum(character.isalnum() for character in words[index].text)
        for index in unmapped_word_indexes
    )
    if unmapped_word_indexes and (
        len(unmapped_word_indexes) > _MAX_UNMAPPED_NOISE_WORDS
        or any(
            units > _MAX_UNMAPPED_NOISE_UNITS_PER_WORD
            for units in unmapped_units
        )
    ):
        raise SceneTimelineError("word alignment is invalid")

    per_segment: dict[str, tuple[int, int, set[int]]] = {}
    for index, (start, end) in enumerate(offsets, 1):
        pairs = [
            (script_index, word_index)
            for script_index, word_index in zip(
                alignment.script_indices, alignment.word_indices
            )
            if start <= script_index < end
        ]
        matched = sum(word_index is not None for _, word_index in pairs)
        word_indexes = {word_index for _, word_index in pairs if word_index is not None}
        if not pairs or matched / len(pairs) < 0.80 or not word_indexes:
            raise SceneTimelineError("scene alignment is invalid")
        per_segment[f"segment-{index:03d}"] = (len(pairs), matched, word_indexes)

    pending: list[tuple[object, int, int, set[int]]] = []
    for scene in plan_snapshot.plan.scenes:
        total = 0
        matched = 0
        indexes: set[int] = set()
        for segment_id in scene.narration_segment_ids:
            segment_total, segment_matched, segment_indexes = per_segment[segment_id]
            total += segment_total
            matched += segment_matched
            indexes.update(segment_indexes)
        coverage = matched / total if total else 0.0
        if coverage < 0.80 or not indexes:
            raise SceneTimelineError("scene alignment is invalid")
        pending.append((scene, matched, total, indexes))

    starts = [0.0]
    starts.extend(words[min(indexes)].start for _, _, _, indexes in pending[1:])
    ends = [*starts[1:], duration]
    result: list[SceneTiming] = []
    for (scene, matched, total, _indexes), start, end in zip(pending, starts, ends):
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0.0
            or end - start < 1.0
            or end > duration + 0.05
        ):
            raise SceneTimelineError("scene timing is invalid")
        result.append(
            SceneTiming(
                id=scene.id,
                start=round(start, 6),
                end=round(end, 6),
                matched_units=matched,
                segment_ids=scene.narration_segment_ids,
                alignment_coverage=round(matched / total, 6),
            )
        )
    return round(alignment.coverage, 6), tuple(result)


def _timeline_dict(timeline: SceneTimeline) -> dict[str, object]:
    return {
        "schema_version": timeline.schema_version,
        "narration_sha256": timeline.narration_sha256,
        "captions_words_sha256": timeline.captions_words_sha256,
        "content_plan_sha256": timeline.content_plan_sha256,
        "duration_seconds": timeline.duration_seconds,
        "alignment_coverage": timeline.alignment_coverage,
        "scenes": [
            {
                "id": scene.id,
                "start": scene.start,
                "end": scene.end,
                "matched_units": scene.matched_units,
                "segment_ids": list(scene.segment_ids),
                "alignment_coverage": scene.alignment_coverage,
            }
            for scene in timeline.scenes
        ],
    }


def validate_scene_timeline(
    value: object,
    *,
    project_root: Path,
    prearchive_project_root: Path | None = None,
) -> SceneTimeline:
    """Revalidate a formal timeline against current local upstream artifacts."""

    root = Path(project_root).absolute()
    try:
        plan_snapshot = load_content_plan_snapshot(
            project_root=root,
            prearchive_project_root=prearchive_project_root,
        )
        narration_snapshot = capture_regular_file(
            root / "工程" / "media" / "narration.wav", within=root
        )
        words_snapshot = capture_regular_file(
            root / "工程" / "media" / "captions" / "captions_words.json", within=root
        )
    except (ArtifactError, ContentPlanError):
        raise SceneTimelineError("timeline input is invalid") from None
    duration = _wav_duration(narration_snapshot)
    if _parse_manifest_output_hash(plan_snapshot) != narration_snapshot.sha256:
        raise SceneTimelineError("timeline provenance is invalid")
    words = _load_words(words_snapshot, duration=duration)
    expected_coverage, expected_scenes = _alignment_scenes(
        plan_snapshot=plan_snapshot,
        words=words,
        duration=duration,
    )
    if not isinstance(value, dict) or set(value) != _TIMELINE_FIELDS:
        raise SceneTimelineError("scene timeline schema is invalid")
    raw_scenes = value["scenes"]
    if not isinstance(raw_scenes, list) or len(raw_scenes) != len(plan_snapshot.plan.scenes):
        raise SceneTimelineError("scene timeline schema is invalid")
    if (
        value["schema_version"] != 1
        or isinstance(value["schema_version"], bool)
        or value["narration_sha256"] != narration_snapshot.sha256
        or value["captions_words_sha256"] != words_snapshot.sha256
        or value["content_plan_sha256"] != plan_snapshot.snapshot.sha256
    ):
        raise SceneTimelineError("timeline provenance is invalid")
    try:
        declared_duration = float(value["duration_seconds"])
        overall_coverage = float(value["alignment_coverage"])
    except (TypeError, ValueError):
        raise SceneTimelineError("scene timing is invalid") from None
    if (
        isinstance(value["duration_seconds"], bool)
        or isinstance(value["alignment_coverage"], bool)
        or not math.isfinite(declared_duration)
        or not math.isfinite(overall_coverage)
        or abs(declared_duration - duration) > 0.05
        or not 0.90 <= overall_coverage <= 1.0
        or abs(overall_coverage - expected_coverage) > 1e-6
    ):
        raise SceneTimelineError("scene timing is invalid")
    scenes: list[SceneTiming] = []
    previous_end: float | None = None
    for plan_scene, expected, raw in zip(
        plan_snapshot.plan.scenes, expected_scenes, raw_scenes
    ):
        if not isinstance(raw, dict) or set(raw) != _SCENE_FIELDS:
            raise SceneTimelineError("scene timeline schema is invalid")
        try:
            start = float(raw["start"])
            end = float(raw["end"])
            coverage = float(raw["alignment_coverage"])
        except (TypeError, ValueError):
            raise SceneTimelineError("scene timing is invalid") from None
        matched = raw["matched_units"]
        if (
            raw["id"] != plan_scene.id
            or raw["segment_ids"] != list(plan_scene.narration_segment_ids)
            or isinstance(raw["start"], bool)
            or isinstance(raw["end"], bool)
            or isinstance(raw["alignment_coverage"], bool)
            or not isinstance(matched, int)
            or isinstance(matched, bool)
            or matched <= 0
            or not all(math.isfinite(item) for item in (start, end, coverage))
            or end - start < 1.0
            or not 0.80 <= coverage <= 1.0
            or abs(start - expected.start) > 1e-6
            or abs(end - expected.end) > 1e-6
            or matched != expected.matched_units
            or abs(coverage - expected.alignment_coverage) > 1e-6
            or (previous_end is None and abs(start) > 1e-6)
            or (previous_end is not None and abs(start - previous_end) > 1e-6)
        ):
            raise SceneTimelineError("scene timing is invalid")
        scenes.append(
            SceneTiming(
                id=plan_scene.id,
                start=start,
                end=end,
                matched_units=matched,
                segment_ids=plan_scene.narration_segment_ids,
                alignment_coverage=coverage,
            )
        )
        previous_end = end
    if previous_end is None or abs(previous_end - duration) > 0.05:
        raise SceneTimelineError("scene timing is invalid")
    snapshots = (
        plan_snapshot.snapshot,
        plan_snapshot.handoff_snapshot,
        plan_snapshot.manifest_snapshot,
        plan_snapshot.narration_contract_snapshot,
        narration_snapshot,
        words_snapshot,
    )
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        raise SceneTimelineError("timeline input changed")
    return SceneTimeline(
        schema_version=1,
        narration_sha256=narration_snapshot.sha256,
        captions_words_sha256=words_snapshot.sha256,
        content_plan_sha256=plan_snapshot.snapshot.sha256,
        duration_seconds=declared_duration,
        alignment_coverage=overall_coverage,
        scenes=tuple(scenes),
    )


def build_scene_timeline(*, project_root: Path) -> SceneTimelineSnapshot:
    """Build and no-clobber publish a final-WAV-bound scene timeline."""

    root = Path(project_root).absolute()
    try:
        plan_snapshot = load_content_plan_snapshot(project_root=root)
        narration_snapshot = capture_regular_file(
            root / "工程" / "media" / "narration.wav", within=root
        )
        words_snapshot = capture_regular_file(
            root / "工程" / "media" / "captions" / "captions_words.json",
            within=root,
        )
        qc_snapshot = capture_regular_file(
            root / "工程" / "media" / "captions" / "caption-qc.json",
            within=root,
        )
    except (ArtifactError, ContentPlanError):
        raise SceneTimelineError("timeline input is invalid") from None
    duration = _wav_duration(narration_snapshot)
    if _parse_manifest_output_hash(plan_snapshot) != narration_snapshot.sha256:
        raise SceneTimelineError("final narration is invalid")
    _validate_caption_qc(qc_snapshot, narration_sha256=narration_snapshot.sha256)
    words = _load_words(words_snapshot, duration=duration)
    alignment_coverage, scenes = _alignment_scenes(
        plan_snapshot=plan_snapshot,
        words=words,
        duration=duration,
    )
    timeline = SceneTimeline(
        schema_version=1,
        narration_sha256=narration_snapshot.sha256,
        captions_words_sha256=words_snapshot.sha256,
        content_plan_sha256=plan_snapshot.snapshot.sha256,
        duration_seconds=round(duration, 6),
        alignment_coverage=alignment_coverage,
        scenes=scenes,
    )
    snapshots = (
        plan_snapshot.snapshot,
        plan_snapshot.handoff_snapshot,
        plan_snapshot.manifest_snapshot,
        plan_snapshot.narration_contract_snapshot,
        narration_snapshot,
        words_snapshot,
        qc_snapshot,
    )
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        raise SceneTimelineError("timeline input changed")
    target = root / "工程" / "scene-timeline.json"
    try:
        published = publish_bytes_no_clobber(
            target, encode_canonical_json(_timeline_dict(timeline)), within=root
        )
    except ArtifactError:
        raise SceneTimelineError("timeline publication failed") from None
    if not all(snapshot_matches(snapshot) for snapshot in snapshots):
        try:
            if snapshot_matches(published):
                published.path.unlink()
        except OSError:
            pass
        raise SceneTimelineError("timeline input changed")
    return SceneTimelineSnapshot(
        path=target,
        timeline=timeline,
        snapshot=published,
        content_plan_snapshot=plan_snapshot.snapshot,
        narration_snapshot=narration_snapshot,
        captions_words_snapshot=words_snapshot,
        caption_qc_snapshot=qc_snapshot,
    )

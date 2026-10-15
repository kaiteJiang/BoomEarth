"""Validate explicit painting regions and resolve complete speech cue phrases."""
from __future__ import annotations

import math
import re
import difflib
import json
from pathlib import Path

from boomearth.providers.volcengine_asr import ASRWord
from boomearth.video.artifacts import ArtifactError, FileSnapshot, capture_regular_file


class SpongeComponentError(ValueError):
    pass


class SpongeCueReviewRequired(SpongeComponentError):
    """A whole-caption cue needs its existing, hash-bound lexical review."""


def _normalize(text: str) -> str:
    return "".join(c.casefold() for c in text if c.isalnum())


def load_reviewed_cue_captions(
    project_root: Path, *, caption_snapshots: dict[str, FileSnapshot],
    narration_sha256: str, captions: tuple[dict, ...], words: tuple[ASRWord, ...],
) -> tuple[frozenset[tuple[str, str, float, float]], tuple[FileSnapshot, ...]]:
    """Read the existing caption review chain; never manufacture a lexical exception."""
    error = "sponge cue lexical review is invalid"
    try:
        qc = json.loads(caption_snapshots["caption-qc.json"].payload)
        relative = qc["semantic_review_path"]
        if (qc.get("manual_semantic_review") is not True or not isinstance(relative, str)
                or Path(relative).is_absolute() or ".." in Path(relative).parts):
            raise ValueError
        review_snapshot = capture_regular_file(project_root / relative, within=project_root)
        review = json.loads(review_snapshot.payload)
        if (review_snapshot.sha256 != qc["semantic_review_sha256"]
                or review.get("status") != "pass"
                or review.get("narration_sha256") != narration_sha256
                or any(review.get(flag) is not True for flag in (
                    "actual_asr_lexical_differences_reviewed", "actual_word_boundaries_reviewed",
                    "source_words_preserved", "words_numbers_proper_names_negations_reviewed"))
                or review.get("text_or_timing_changes_after_asr") is not False):
            raise ValueError
        audit_name = "caption-final-mapping-audit-v01.json"
        audit_snapshot = capture_regular_file(review_snapshot.path.parent / audit_name, within=project_root)
        audit = json.loads(audit_snapshot.payload)
        if (review["evidence_sha256"][audit_name] != audit_snapshot.sha256
                or audit.get("narration_sha256") != narration_sha256
                or audit.get("all_actual_word_edges_exact") is not True):
            raise ValueError
        for name in ("captions.json", "captions_words.json"):
            if (review["source_artifact_sha256"][name] != caption_snapshots[name].sha256
                    or audit["candidate_artifact_sha256"][name] != caption_snapshots[name].sha256):
                raise ValueError
        rows = audit["rows"]
        if not isinstance(rows, list) or len(rows) != len(captions):
            raise ValueError
        approved = set()
        previous_word = -1
        for index, (row, caption) in enumerate(zip(rows, captions, strict=True), 1):
            first, last = row["first_word"], row["last_word"]
            if (type(first) is not int or type(last) is not int
                    or first != previous_word + 1 or not first <= last < len(words)
                    or row["caption"] != index or row["text"] != caption["text"]
                    or row["start"] != caption["start"] or row["end"] != caption["end"]
                    or row["start"] != words[first].start or row["end"] != words[last].end
                    or row.get("first_start_exact") is not True or row.get("last_end_exact") is not True
                    or _normalize(row["asr_span"]) != _normalize("".join(w.text for w in words[first:last + 1]))):
                raise ValueError
            previous_word = last
            approved.add((row["text"], _normalize(row["asr_span"]), row["start"], row["end"]))
        if previous_word != len(words) - 1:
            raise ValueError
        return frozenset(approved), (review_snapshot, audit_snapshot)
    except (ArtifactError, ValueError, TypeError, KeyError, AttributeError):
        raise SpongeComponentError(error) from None


def _number(value: object, low: float, high: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def validate_drawing_plan(value: object, plan, project_root: Path) -> dict:
    error = "sponge drawing plan is invalid"
    if (not isinstance(value, dict) or set(value) != {"schema_version", "scenes"}
            or value["schema_version"] != 2 or not isinstance(value["scenes"], dict)
            or set(value["scenes"]) != {scene.id for scene in plan.scenes}):
        raise SpongeComponentError(error)
    for scene in plan.scenes:
        entry = value["scenes"][scene.id]
        if (not isinstance(entry, dict) or set(entry) != {"source_sha256", "parts"}
                or not scene.visual_asset
                or entry["source_sha256"] != capture_regular_file(project_root / scene.visual_asset, within=project_root).sha256
                or not isinstance(entry["parts"], list) or not 3 <= len(entry["parts"]) <= 24):
            raise SpongeComponentError(error)
        seen = set(); text_started = False
        for index, part in enumerate(entry["parts"]):
            if (not isinstance(part, dict) or set(part) != {"id", "kind", "polygons", "weight"}
                    or not isinstance(part["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", part["id"])
                    or part["id"] in seen or part["kind"] not in {"character", "object", "motion", "text"}
                    or (index == 0) != (part["kind"] == "character")
                    or not _number(part["weight"], .1, 12)
                    or not isinstance(part["polygons"], list) or not 1 <= len(part["polygons"]) <= 20):
                raise SpongeComponentError(error)
            if text_started and part["kind"] != "text":
                raise SpongeComponentError(error)
            text_started |= part["kind"] == "text"
            seen.add(part["id"])
            for poly in part["polygons"]:
                if (not isinstance(poly, list) or not 3 <= len(poly) <= 32
                        or any(not isinstance(p, list) or len(p) != 2 or not all(_number(x, 0, 1) for x in p) for p in poly)):
                    raise SpongeComponentError(error)
                area = abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))) / 2
                if area < .00001:
                    raise SpongeComponentError(error)
        if sum(part["kind"] == "text" for part in entry["parts"]) != len(scene.overlay_labels):
            raise SpongeComponentError(error)
    return value["scenes"]


def resolve_cue_start(
    phrase: str,
    captions: list[dict],
    after: float = -1,
    *,
    words: tuple[ASRWord, ...],
    reviewed_captions: frozenset[tuple[str, str, float, float]] = frozenset(),
) -> float:
    """Bind an exact display phrase to its first real ASR token, never a caption start."""
    error = "sponge text cue has no complete speech match"
    epsilon = 1e-6
    if not words or not _number(after, -math.inf, math.inf):
        raise SpongeComponentError(error)
    previous_end = 0.0
    for word in words:
        if (not isinstance(word, ASRWord) or not isinstance(word.text, str)
                or not word.text.strip() or not _number(word.start, 0, math.inf)
                or not _number(word.end, 0, math.inf) or word.end <= word.start
                or word.start < previous_end - epsilon):
            raise SpongeComponentError(error)
        previous_end = word.end
    target = _normalize(phrase)
    characters: list[str] = []
    spans = []
    previous_end = 0.0
    for caption in captions:
        if not isinstance(caption, dict):
            raise SpongeComponentError(error)
        text, start, end = caption.get("text"), caption.get("start"), caption.get("end")
        if (not isinstance(text, str) or not _normalize(text)
                or not _number(start, 0, math.inf) or not _number(end, 0, math.inf)
                or end <= start or start < previous_end - epsilon):
            raise SpongeComponentError(error)
        previous_end = end
        own_words = tuple(word for word in words
                          if word.end > start + epsilon and word.start < end - epsilon)
        if (not own_words or abs(own_words[0].start - start) > epsilon
                or abs(own_words[-1].end - end) > epsilon
                or any(word.start < start - epsilon or word.end > end + epsilon
                       for word in own_words)):
            raise SpongeComponentError(error)
        display = _normalize(text)
        raw = "".join(_normalize(word.text) for word in own_words)
        raw_times = [word.start for word in own_words for _ in _normalize(word.text)]
        mapping: list[int | None] = [None] * len(display)
        opcodes = difflib.SequenceMatcher(None, display, raw, autojunk=False).get_opcodes()
        for kind, a, b, c, d in opcodes:
            if kind == "equal":
                mapping[a:b] = range(c, d)
        # A displaced exact block can otherwise bind an early instruction to a
        # later repetition. Insert+delete is not evidence of word correspondence.
        relocated = {item[0] for item in opcodes}.issuperset({"insert", "delete"})
        spans.append((len(characters), len(characters) + len(display), display, raw,
                      mapping, raw_times, (text, raw, start, end), relocated))
        characters.extend(display)
    source = "".join(characters)
    index = source.find(target) if target else -1
    while index >= 0:
        at = None
        review_needed = False
        for low, high, display, raw, mapping, raw_times, key, relocated in spans:
            left, right = max(index, low) - low, min(index + len(target), high) - low
            if left >= right:
                continue
            mapped = mapping[left:right]
            exact = (not relocated and all(position is not None for position in mapped)
                     and mapped == list(range(mapped[0], mapped[0] + len(mapped))))
            if exact:
                # The nearest mapped context (or caption edge) must not hide
                # skipped ASR words. This also rejects a matching later action.
                before = next((p for p in range(left - 1, -1, -1) if mapping[p] is not None), -1)
                after_index = next((p for p in range(right, len(display)) if mapping[p] is not None), len(display))
                before_raw = mapping[before] if before >= 0 else -1
                after_raw = mapping[after_index] if after_index < len(display) else len(raw)
                exact = (mapped[0] - before_raw == left - before
                         and after_raw - mapped[-1] == after_index - right + 1)
            if exact:
                onset = raw_times[mapped[0]]
            elif left == 0 and right == len(display) and not relocated:
                # A reviewed whole caption has an unambiguous first real word.
                # Partial aliases are rejected; no fuzzy or sub-token timing.
                if key not in reviewed_captions:
                    review_needed = True
                onset = key[2]
            else:
                raise SpongeComponentError(error)
            if at is None:
                at = onset
        if at is None:
            raise SpongeComponentError(error)
        if at > after:
            if review_needed:
                raise SpongeCueReviewRequired(error)
            return at
        index = source.find(target, index + 1)
    raise SpongeComponentError(error)

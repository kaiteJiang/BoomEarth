#!/usr/bin/env python3
"""Generate subtitle artifacts from final media using Volcengine word timestamps."""

from __future__ import annotations

import argparse
import ctypes
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
DEFAULT_RESOURCE_ID = "volc.bigasr.auc_turbo"
CAPTION_CONNECTORS = (
    "比如说", "第一个", "第二个", "第三个", "首先", "其次", "然后",
    "而且", "但是", "只是", "所以", "如果", "其实", "就是", "这个", "某个",
)
SCRIPT_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
ARTIFACT_FILENAMES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)
SUPERSEDED_RECEIPT_KEYS = frozenset(
    {
        "old_asr_plan_sha256",
        "old_content_plan_sha256",
        "old_narration_sha256",
        "schema_version",
        "status",
        "v1_project_id",
        "v2_project_id",
        "v2_publication_revision_sha256",
    }
)
LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value
    return values


def env_candidates(explicit: str | None) -> list[Path]:
    found: list[Path] = []
    if explicit:
        found.append(Path(explicit).expanduser())
    for origin in (Path.cwd(), Path(__file__).resolve()):
        current = origin if origin.is_dir() else origin.parent
        for parent in (current, *current.parents):
            candidate = parent / ".env"
            if candidate not in found:
                found.append(candidate)
    return found


def load_config(explicit_env: str | None) -> tuple[dict[str, str], Path | None]:
    config = dict(os.environ)
    used: Path | None = None
    for candidate in env_candidates(explicit_env):
        values = parse_env_file(candidate)
        if values:
            for key, value in values.items():
                config.setdefault(key, value)
            if "VOLCENGINE_API_KEY" in values and used is None:
                used = candidate
    return config, used


def normalize_chars(text: str) -> list[str]:
    return [char.casefold() for char in text if char.isalnum()]


def extract_markdown_narration(text: str) -> str | None:
    lines = []
    for raw in text.splitlines():
        match = re.match(r"^\s*-\s*口播[：:]\s*(.+?)\s*$", raw)
        if match:
            lines.append(match.group(1))
    return "\n".join(lines) if lines else None


def collect_narration(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, list):
        for item in value:
            output.extend(collect_narration(item))
    elif isinstance(value, dict):
        direct = value.get("narration")
        if isinstance(direct, str) and direct.strip():
            output.append(direct.strip())
        else:
            for key in ("segments", "scenes", "items"):
                if key in value:
                    output.extend(collect_narration(value[key]))
    return output


def _after_script_snapshot_read(path: Path) -> None:
    """Test seam after the open handle has been read; production is a no-op."""


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_reparse_point(path: Path) -> bool:
    if os.name == "nt":
        return bool(
            path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
    return path.is_symlink()


def _has_reparse_component(path: Path) -> bool:
    current = path
    while True:
        try:
            if _is_reparse_point(current):
                return True
        except FileNotFoundError:
            pass
        if current.parent == current:
            return False
        current = current.parent


def _capture_media_snapshot(path: Path) -> tuple[int, int, int, int, str]:
    """Hash one stable ordinary media file without following a reparse input."""

    media = _absolute_without_resolving(path)
    try:
        path_before = media.lstat()
    except OSError:
        raise RuntimeError("Final media must be an ordinary non-reparse file") from None
    if _has_reparse_component(media) or not stat.S_ISREG(path_before.st_mode):
        raise RuntimeError("Final media must be an ordinary non-reparse file")
    digest = hashlib.sha256()
    try:
        with media.open("rb") as handle:
            handle_before = os.fstat(handle.fileno())
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            handle_after = os.fstat(handle.fileno())
        path_after = media.lstat()
    except OSError:
        raise RuntimeError("Final media changed during subtitle generation") from None
    before = (
        path_before.st_dev,
        path_before.st_ino,
        path_before.st_size,
        path_before.st_mtime_ns,
    )
    if (
        before
        != (
            handle_before.st_dev,
            handle_before.st_ino,
            handle_before.st_size,
            handle_before.st_mtime_ns,
        )
        or before
        != (
            handle_after.st_dev,
            handle_after.st_ino,
            handle_after.st_size,
            handle_after.st_mtime_ns,
        )
        or before
        != (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_size,
            path_after.st_mtime_ns,
        )
        or _has_reparse_component(media)
    ):
        raise RuntimeError("Final media changed during subtitle generation")
    return (*before, digest.hexdigest())


def _reject_superseded_final_audio(media: Path) -> None:
    """Reject a V1 final WAV superseded by an immutable local revision receipt."""

    media = _absolute_without_resolving(media)
    if media.parent.name != "media" or media.parent.parent.name != "工程":
        return
    project = media.parent.parent.parent
    receipt = project / "工程" / "qc" / "superseded-by-v2.json"
    if not receipt.exists() and not receipt.is_symlink():
        return
    try:
        receipt_stat = receipt.lstat()
        if _has_reparse_component(receipt) or not stat.S_ISREG(receipt_stat.st_mode):
            raise ValueError
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != set(SUPERSEDED_RECEIPT_KEYS):
            raise ValueError
        digests = (
            value["old_asr_plan_sha256"],
            value["old_content_plan_sha256"],
            value["old_narration_sha256"],
            value["v2_publication_revision_sha256"],
        )
        if not (
            value["schema_version"] == 1
            and value["status"] == "superseded-before-network"
            and value["v1_project_id"] == project.name
            and value["v2_project_id"] == f"{project.name}-v2"
            and all(
                isinstance(digest, str) and LOWER_SHA256.fullmatch(digest)
                for digest in digests
            )
            and value["old_narration_sha256"] == _capture_media_snapshot(media)[-1]
        ):
            raise ValueError
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise RuntimeError("SUPERSEDED_RECEIPT_INVALID") from None
    raise RuntimeError("FINAL_AUDIO_SUPERSEDED")


def _locked_script_bytes(path: Path, expected_sha256: str) -> bytes:
    """Read one ordinary script snapshot and verify identity plus upstream SHA."""

    if not isinstance(expected_sha256, str) or not SCRIPT_SHA256.fullmatch(
        expected_sha256
    ):
        raise RuntimeError("a valid upstream script SHA-256 is required")
    script_path = _absolute_without_resolving(path)
    try:
        path_stat = script_path.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Narration script must be an ordinary file: {script_path}") from exc
    if _has_reparse_component(script_path) or not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeError(f"Narration script must be an ordinary file: {script_path}")

    with script_path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"Narration script must be an ordinary file: {script_path}")
        contents = handle.read()
        _after_script_snapshot_read(script_path)
        after = os.fstat(handle.fileno())

    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(contents) != before.st_size:
        raise RuntimeError(f"Narration script changed during read: {script_path}")
    if not contents:
        raise RuntimeError(f"Narration script is empty: {script_path}")
    actual_sha256 = hashlib.sha256(contents).hexdigest()
    if actual_sha256.casefold() != expected_sha256.casefold():
        raise RuntimeError("Narration script SHA-256 does not match the upstream manifest")
    return contents


def _parse_script_snapshot(script_path: Path, contents: bytes) -> str:
    try:
        raw = contents.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"Narration script is not valid UTF-8: {script_path}") from exc
    if script_path.suffix.lower() == ".jsonl":
        rows: list[str] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid narration JSONL row {line_number} in {script_path}"
                ) from exc
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("text"), str)
                or not value["text"].strip()
            ):
                raise RuntimeError(
                    f"Invalid narration JSONL text at row {line_number} in {script_path}"
                )
            rows.append(value["text"].strip())
        if not rows:
            raise RuntimeError(f"No narration text found in {script_path}")
        return "\n".join(rows)
    if script_path.suffix.lower() == ".json":
        segments = collect_narration(json.loads(raw))
        if not segments:
            raise RuntimeError(f"No narration fields found in {script_path}")
        return "\n".join(segments)
    if script_path.suffix.lower() in {".md", ".markdown"}:
        extracted = extract_markdown_narration(raw)
        if extracted:
            return extracted
    return raw.strip()


def load_script(
    path: str | None,
    inline: str | None,
    *,
    expected_sha256: str | None = None,
    require_locked_file: bool = False,
) -> str | None:
    if require_locked_file:
        if inline is not None:
            raise RuntimeError("Locked narration requires a script file, not inline text")
        if not path:
            raise RuntimeError("Locked narration requires a script file")
        script_path = _absolute_without_resolving(Path(path))
        contents = _locked_script_bytes(script_path, expected_sha256 or "")
        return _parse_script_snapshot(script_path, contents)
    if inline:
        return inline.strip()
    if not path:
        return None
    script_path = _absolute_without_resolving(Path(path))
    return _parse_script_snapshot(script_path, script_path.read_bytes())


def safe_caption_cut(text: str, max_chars: int) -> int:
    """Choose a balanced cut without breaking terms or discourse connectors."""
    if _caption_fits(text, max_chars):
        return len(text)
    protected = [match.span() for match in re.finditer(r"[A-Za-z0-9.+/\-]+(?: [A-Za-z0-9.+/\-]+)*", text)]
    for phrase in CAPTION_CONNECTORS:
        protected.extend((match.start(), min(len(text), match.end() + 1)) for match in re.finditer(re.escape(phrase), text))
    for match in re.finditer(r"没有|不会|不能|不是|不再|并非|未能|无法|无需|别再|不要|不|没|未|别|无|非", text):
        protected.append((match.start(), min(len(text), match.end() + 1)))

    total = reading_units(_display_caption_text(text))
    target = total / max(2, (total + max_chars - 1) // max_chars)
    candidates: list[tuple[float, int]] = []
    for cut in range(1, len(text)):
        if any(start < cut < end for start, end in protected):
            continue
        left = reading_units(_display_caption_text(text[:cut]))
        right = reading_units(_display_caption_text(text[cut:]))
        if left < 4 or right < 4 or left > max_chars + 2:
            continue
        priority = 3.0
        if text[cut - 1] in "，、；：,;:。？！!?":
            priority = 0.0
        elif any(text.startswith(phrase, cut) for phrase in CAPTION_CONNECTORS):
            priority = 0.5
        elif bool(re.match(r"[A-Za-z0-9]", text[cut:cut + 1])) != bool(re.match(r"[A-Za-z0-9]", text[cut - 1:cut])):
            priority = 1.5
        if text[cut - 1] in "的地得把被向给和与或在从对让用":
            priority += 2.5
        candidates.append((abs(left - target) + priority * 1.5, cut))
    if candidates:
        return min(candidates)[1]
    return min(max_chars, len(text))


def join_caption_chunks(left: str, right: str) -> str:
    separator = " " if re.search(r"[A-Za-z0-9]$", left) and re.match(r"^[A-Za-z0-9]", right) else ""
    return left.rstrip() + separator + right.lstrip()


def merge_short_chunks(chunks: list[str], max_chars: int) -> list[str]:
    merged = list(chunks)
    index = 0
    while index < len(merged):
        chunk = merged[index]
        length = reading_units(chunk)
        starts_forward = chunk.startswith(CAPTION_CONNECTORS)
        starts_ascii = bool(re.match(r"^[A-Za-z0-9]", chunk))
        needs_merge = length < 6 or (starts_forward and length <= 8) or (starts_ascii and length <= 6)
        if not needs_merge or len(merged) == 1:
            index += 1
            continue
        options: dict[int, str] = {}
        if index > 0:
            combined = join_caption_chunks(merged[index - 1], chunk)
            if reading_units(combined) <= max_chars:
                options[index - 1] = combined
        if index + 1 < len(merged):
            combined = join_caption_chunks(chunk, merged[index + 1])
            if reading_units(combined) <= max_chars:
                options[index] = combined
        if not options:
            index += 1
            continue
        prefer_next = starts_forward or chunk in {"它和", "它做调研"}
        target = index if prefer_next and index in options else index - 1
        if target not in options:
            target = index
        combined = options[target]
        if target == index - 1:
            merged[index - 1:index + 1] = [combined]
            index = max(0, index - 1)
        else:
            merged[index:index + 2] = [combined]
    return merged


def split_connector_segments(text: str) -> list[str]:
    cuts = {0, len(text)}
    for phrase in CAPTION_CONNECTORS:
        for match in re.finditer(re.escape(phrase), text):
            if match.start() > 0 and len(normalize_chars(text[:match.start()])) >= 4:
                cuts.add(match.start())
    points = sorted(cuts)
    return [text[points[index]:points[index + 1]] for index in range(len(points) - 1) if text[points[index]:points[index + 1]]]


DISPLAY_PUNCTUATION = "，、；：,;:。？！!?"
SEMANTIC_CLAUSE_PUNCTUATION = "，、,；;：:。？！!?"


def _display_caption_text(text: str) -> str:
    normalized = text.replace(".", " ")
    return "".join(character for character in normalized if character not in DISPLAY_PUNCTUATION).strip()


def _caption_fits(text: str, max_chars: int) -> bool:
    """Allow one intact ASCII name when the surrounding CJK stays in bounds."""

    displayed = _display_caption_text(text)
    if reading_units(displayed) <= max_chars:
        return True
    has_ascii_name = bool(re.search(r"[A-Za-z0-9.+/\-]+", displayed))
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", displayed))
    return has_ascii_name and cjk_count <= max_chars


def _split_long_caption(text: str, max_chars: int) -> list[str]:
    """Apply the oral-linebreak hard limit at safe semantic cuts."""

    pending = text.strip()
    chunks: list[str] = []
    while not _caption_fits(pending, max_chars):
        cut = safe_caption_cut(pending, max_chars)
        if cut <= 0 or cut >= len(pending):
            break
        left = _display_caption_text(pending[:cut])
        if not left:
            break
        chunks.append(left)
        pending = pending[cut:].lstrip()
    final = _display_caption_text(pending)
    if final:
        chunks.append(final)
    return chunks


def split_caption_text(text: str, max_chars: int) -> list[str]:
    """Group semantic clauses into one readable caption without crossing sentences."""

    if max_chars <= 0:
        raise RuntimeError("Caption grouping limit must be positive")
    normalized = re.sub(r"[\r\n]+", "", text.strip())
    if not normalized:
        return []
    sentence_boundaries = [match.end() for match in re.finditer(r"[。？！!?]", normalized)]
    if not sentence_boundaries or sentence_boundaries[-1] != len(normalized):
        sentence_boundaries.append(len(normalized))

    captions: list[str] = []
    sentence_start = 0
    for sentence_end in sentence_boundaries:
        sentence = normalized[sentence_start:sentence_end]
        sentence_start = sentence_end
        clauses = re.split(f"[{SEMANTIC_CLAUSE_PUNCTUATION}]", sentence)
        bounded: list[str] = []
        for clause in clauses:
            displayed = _display_caption_text(clause)
            if displayed:
                bounded.extend(_split_long_caption(displayed, max_chars))
        captions.extend(merge_short_chunks(bounded, max_chars))
    return captions


def reading_units(text: str) -> float:
    units = 0.0
    for token in re.findall(r"[A-Za-z0-9.+/\-]+(?: [A-Za-z0-9.+/\-]+)*|[\u3400-\u9fff]|[^\s]", text):
        if re.fullmatch(r"[A-Za-z0-9.+/\-]+(?: [A-Za-z0-9.+/\-]+)*", token):
            units += 1.5
        elif re.fullmatch(r"[\u3400-\u9fff]", token):
            units += 1.0
        elif token not in "，、；：,;:。？！!?":
            units += 0.5
    return units


def media_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return None


def prepare_audio(media: Path, temp_dir: Path) -> Path:
    if media.suffix.lower() == ".wav":
        return media
    output = temp_dir / "final-audio.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)],
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to extract final audio") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffmpeg could not extract audio from {media}") from exc
    return output


def transcribe(
    audio: Path,
    api_key: str,
    resource_id: str,
    *,
    expected_sha256: str | None,
) -> Any:
    """Delegate locked final WAV ASR to the canonical turbo provider boundary."""

    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise RuntimeError("expected_sha256 from the upstream locked-WAV manifest is required")
    workspace_src = Path(__file__).resolve().parents[4] / "src"
    if str(workspace_src) not in sys.path:
        sys.path.insert(0, str(workspace_src))
    from boomearth.providers.volcengine_asr import VolcengineASRClient

    client = VolcengineASRClient(
        api_key=api_key,
        resource_id=resource_id,
    )
    try:
        return client.transcribe_file(audio, expected_sha256=expected_sha256)
    finally:
        client.close()


def milliseconds(value: Any) -> float:
    number = float(value)
    return number / 1000.0


def extract_word_units(result: dict[str, Any]) -> list[dict[str, Any]]:
    root = result.get("result", result)
    utterances = root.get("utterances") if isinstance(root, dict) else None
    if not isinstance(utterances, list):
        raise RuntimeError("Volcengine response has no result.utterances")
    words: list[dict[str, Any]] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        for word in utterance.get("words") or []:
            if not isinstance(word, dict):
                continue
            text = str(word.get("text", "")).strip()
            start_raw = word.get("start_time")
            end_raw = word.get("end_time")
            if not text or start_raw is None or end_raw is None:
                continue
            if float(start_raw) < 0 or float(end_raw) < 0:
                continue
            start, end = milliseconds(start_raw), milliseconds(end_raw)
            if end < start:
                continue
            words.append({"text": text, "start": round(start, 3), "end": round(end, 3), "isGap": False})
    words.sort(key=lambda item: (item["start"], item["end"]))
    if not words:
        raise RuntimeError("Volcengine response has no usable word timestamps")
    return words


def words_with_gaps(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    last_end = 0.0
    for word in words:
        if word["start"] - last_end >= 0.2:
            output.append({"text": "", "start": round(last_end, 3), "end": word["start"], "isGap": True})
        output.append(word)
        last_end = max(last_end, word["end"])
    return output


def expand_asr_characters(words: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, float]]]:
    characters: list[str] = []
    timings: list[dict[str, float]] = []
    for word in words:
        normalized = normalize_chars(word["text"])
        if not normalized:
            continue
        duration = max(0.001, word["end"] - word["start"])
        for index, char in enumerate(normalized):
            start = word["start"] + duration * index / len(normalized)
            end = word["start"] + duration * (index + 1) / len(normalized)
            characters.append(char)
            timings.append({"start": start, "end": end})
    if not characters:
        raise RuntimeError("ASR words could not be normalized into timing characters")
    return characters, timings


def align_script(script_chars: list[str], asr_chars: list[str]) -> tuple[list[int], int]:
    matcher = difflib.SequenceMatcher(a=script_chars, b=asr_chars, autojunk=False)
    mapping: list[int | None] = [None] * len(script_chars)
    matched = 0
    for block in matcher.get_matching_blocks():
        if block.size == 0:
            continue
        matched += block.size
        for offset in range(block.size):
            mapping[block.a + offset] = block.b + offset

    anchors = [index for index, value in enumerate(mapping) if value is not None]
    if not anchors:
        raise RuntimeError("Original script and ASR text have no alignable characters")
    for index, value in enumerate(mapping):
        if value is not None:
            continue
        left = next((pos for pos in reversed(anchors) if pos < index), None)
        right = next((pos for pos in anchors if pos > index), None)
        if left is None:
            right_value = mapping[right] if right is not None else 0
            predicted = max(0, int(right_value) - (right - index if right is not None else 0))
        elif right is None:
            predicted = min(len(asr_chars) - 1, int(mapping[left]) + index - left)
        else:
            left_value, right_value = int(mapping[left]), int(mapping[right])
            fraction = (index - left) / (right - left)
            predicted = round(left_value + (right_value - left_value) * fraction)
        mapping[index] = max(0, min(len(asr_chars) - 1, predicted))

    resolved = [int(value) for value in mapping]
    for index in range(1, len(resolved)):
        if resolved[index] < resolved[index - 1]:
            resolved[index] = resolved[index - 1]
    return resolved, matched


def build_captions(script: str, mapping: list[int], asr_timings: list[dict[str, float]], max_chars: int) -> list[dict[str, Any]]:
    chunks = split_caption_text(script, max_chars)
    script_count = len(normalize_chars(script))
    chunk_count = sum(len(normalize_chars(chunk)) for chunk in chunks)
    if chunk_count != script_count:
        raise RuntimeError(f"Caption splitting lost script characters: script={script_count}, chunks={chunk_count}")

    captions: list[dict[str, Any]] = []
    cursor = 0
    for chunk in chunks:
        length = len(normalize_chars(chunk))
        if length == 0:
            continue
        start_index = mapping[cursor]
        end_index = mapping[cursor + length - 1]
        start = asr_timings[start_index]["start"]
        end = asr_timings[end_index]["end"]
        captions.append({
            "start": round(start, 3),
            "end": round(max(start + 0.05, end), 3),
            "text": chunk,
            "source": "volcengine-word-timestamps",
        })
        cursor += length

    frame_gap = 1 / 30
    for index, caption in enumerate(captions):
        next_start = captions[index + 1]["start"] if index + 1 < len(captions) else None
        desired = max(caption["end"] + 0.12, caption["start"] + 0.6)
        if next_start is not None:
            caption["end"] = math.floor(
                min(desired, next_start - frame_gap) * 1000
            ) / 1000
        else:
            caption["end"] = round(desired, 3)
    return captions


def timestamp(seconds: float, separator: str) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def subtitle_text(captions: list[dict[str, Any]], vtt: bool) -> str:
    blocks = []
    for index, caption in enumerate(captions, 1):
        line = f"{timestamp(caption['start'], '.' if vtt else ',')} --> {timestamp(caption['end'], '.' if vtt else ',')}\n{caption['text']}"
        blocks.append(line if vtt else f"{index}\n{line}")
    prefix = "WEBVTT\n\n" if vtt else ""
    return prefix + "\n\n".join(blocks) + "\n"


def split_connector_boundaries(captions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for index in range(len(captions) - 1):
        left, right = captions[index]["text"], captions[index + 1]["text"]
        for phrase in CAPTION_CONNECTORS:
            if any(left.endswith(phrase[:cut]) and right.startswith(phrase[cut:]) for cut in range(1, len(phrase))):
                findings.append({"after_caption": index + 1, "phrase": phrase})
    return findings


def build_qc(
    script_chars: list[str],
    asr_chars: list[str],
    matched: int,
    words: list[dict[str, Any]],
    captions: list[dict[str, Any]],
    media: Path,
    resource_id: str,
    min_coverage: float,
    duration: float | None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    coverage = matched / len(script_chars) if script_chars else 0.0
    overlap_count = 0
    reading_speeds = []
    short_fragments = []
    for index, caption in enumerate(captions):
        caption_duration = caption["end"] - caption["start"]
        if caption["end"] <= caption["start"]:
            errors.append(f"caption {index + 1} has non-positive duration")
        if 0 < caption_duration < 0.5:
            errors.append(f"caption {index + 1} is shorter than 0.5s")
        units = reading_units(caption["text"])
        speed = units / max(caption_duration, 0.001)
        reading_speeds.append(speed)
        if units <= 1.5 and caption_duration < 0.8:
            short_fragments.append({"caption": index + 1, "text": caption["text"], "duration": round(caption_duration, 3)})
        if index and caption["start"] < captions[index - 1]["end"] - 0.001:
            overlap_count += 1
    connector_splits = split_connector_boundaries(captions)
    if coverage < min_coverage:
        errors.append(f"alignment coverage {coverage:.4f} is below {min_coverage:.4f}")
    if overlap_count:
        errors.append(f"{overlap_count} caption overlaps detected")
    if short_fragments:
        errors.append(f"{len(short_fragments)} isolated short caption fragments detected")
    if connector_splits:
        errors.append(f"{len(connector_splits)} discourse connectors split across captions")
    if reading_speeds and max(reading_speeds) > 12.0:
        errors.append(f"maximum reading speed {max(reading_speeds):.2f} units/s exceeds 12.0")
    elif reading_speeds and max(reading_speeds) > 9.0:
        warnings.append(f"maximum reading speed {max(reading_speeds):.2f} units/s exceeds preferred 9.0")
    if duration is not None and captions and captions[-1]["end"] > duration + 0.5:
        errors.append(f"last caption ends after media duration: {captions[-1]['end']:.3f}s > {duration:.3f}s")
    if not captions:
        errors.append("no captions generated")
    return {
        "status": "fail" if errors else "pass",
        "timing_source": "volcengine-word-timestamps",
        "alignment_coverage": round(coverage, 6),
        "minimum_coverage": min_coverage,
        "script_characters": len(script_chars),
        "matched_characters": matched,
        "asr_characters": len(asr_chars),
        "word_units": len(words),
        "caption_count": len(captions),
        "overlap_count": overlap_count,
        "max_reading_units_per_second": round(max(reading_speeds), 3) if reading_speeds else 0.0,
        "short_fragments": short_fragments,
        "split_connectors": connector_splits,
        "source_media": str(media),
        "asr_resource_id": resource_id,
        "warnings": warnings,
        "errors": errors,
    }


def artifact_payloads(
    *,
    result: dict[str, Any],
    raw_words: list[dict[str, Any]],
    captions: list[dict[str, Any]],
    qc: dict[str, Any],
) -> dict[str, bytes]:
    return {
        "asr-result.json": json.dumps(
            result, ensure_ascii=False, indent=2
        ).encode("utf-8"),
        "captions_words.json": json.dumps(
            words_with_gaps(raw_words), ensure_ascii=False, indent=2
        ).encode("utf-8"),
        "captions.json": json.dumps(
            captions, ensure_ascii=False, indent=2
        ).encode("utf-8"),
        "captions.srt": subtitle_text(captions, vtt=False).encode("utf-8"),
        "captions.vtt": subtitle_text(captions, vtt=True).encode("utf-8"),
        "caption-qc.json": json.dumps(qc, ensure_ascii=False, indent=2).encode(
            "utf-8"
        ),
    }


def _write_stage_artifact(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)


def _before_artifact_publish(stage: Path, destination: Path) -> None:
    """Test seam immediately before no-replace directory publication."""


def _after_artifact_parent_check(parent: Path) -> None:
    """Test seam after parent validation and before private stage creation."""


def _directory_token(path: Path) -> tuple[int, int]:
    details = path.lstat()
    if _is_reparse_point(path) or not stat.S_ISDIR(details.st_mode):
        raise RuntimeError("subtitle artifact stage is unsafe")
    return details.st_dev, details.st_ino


def _publish_stage_no_replace(stage: Path, destination: Path) -> None:
    if os.name == "nt":
        move_file = ctypes.windll.kernel32.MoveFileW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
        move_file.restype = ctypes.c_int
        if not move_file(str(stage), str(destination)):
            raise FileExistsError("subtitle artifact destination is unavailable")
        return
    if destination.exists():
        raise FileExistsError("subtitle artifact destination is unavailable")
    os.rename(stage, destination)


def _remove_owned_stage(stage: Path, token: tuple[int, int]) -> None:
    try:
        current_token = _directory_token(stage)
    except (FileNotFoundError, RuntimeError):
        return
    if current_token == token:
        shutil.rmtree(stage)


def publish_artifact_directory(
    out_dir: Path,
    artifacts: dict[str, bytes],
) -> None:
    if tuple(artifacts) != ARTIFACT_FILENAMES:
        raise RuntimeError("subtitle artifact set is incomplete or out of order")

    destination = _absolute_without_resolving(out_dir)
    parent = destination.parent
    try:
        parent_details = parent.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Subtitle artifact parent must be an ordinary directory: {parent}"
        ) from exc
    if _has_reparse_component(parent) or not stat.S_ISDIR(parent_details.st_mode):
        raise RuntimeError(
            f"Subtitle artifact parent must be an ordinary directory: {parent}"
        )
    parent_token = _directory_token(parent)
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError("subtitle artifact destination is unavailable")

    _after_artifact_parent_check(parent)
    if _directory_token(parent) != parent_token:
        raise RuntimeError("subtitle artifact parent identity changed")
    stage = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.stage-", dir=parent)
    )
    stage_token = _directory_token(stage)
    try:
        if _directory_token(parent) != parent_token:
            raise RuntimeError("subtitle artifact parent identity changed")
        for name, contents in artifacts.items():
            _write_stage_artifact(stage / name, contents)

        entries = list(stage.iterdir())
        if (
            len(entries) != len(ARTIFACT_FILENAMES)
            or {entry.name for entry in entries} != set(ARTIFACT_FILENAMES)
        ):
            raise RuntimeError("subtitle artifact stage is incomplete")
        for entry in entries:
            details = entry.lstat()
            if _is_reparse_point(entry) or not stat.S_ISREG(details.st_mode):
                raise RuntimeError("subtitle artifact stage contains an unsafe entry")
        if _directory_token(stage) != stage_token:
            raise RuntimeError("subtitle artifact stage identity changed")

        _before_artifact_publish(stage, destination)
        if _directory_token(stage) != stage_token:
            raise RuntimeError("subtitle artifact stage identity changed")
        _publish_stage_no_replace(stage, destination)
    finally:
        _remove_owned_stage(stage, stage_token)


def doctor(args: argparse.Namespace) -> int:
    config, env_file = load_config(args.env_file)
    resource_id = config.get("VOLCENGINE_RESOURCE_ID", DEFAULT_RESOURCE_ID)
    checks = {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "ffprobe": shutil.which("ffprobe") is not None,
        "volcengine_api_key": bool(config.get("VOLCENGINE_API_KEY")),
        "resource_id": resource_id,
        "env_file": str(env_file) if env_file else None,
    }
    checks["ready"] = bool(
        checks["ffmpeg"]
        and checks["ffprobe"]
        and checks["volcengine_api_key"]
        and resource_id == DEFAULT_RESOURCE_ID
    )
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["ready"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", nargs="?", help="final audio or final merged video")
    parser.add_argument("--script", help="narration JSON, Markdown handoff, or plain text file")
    parser.add_argument("--script-text", help="inline narration text")
    parser.add_argument("--out-dir", default="media/captions")
    parser.add_argument("--asr-result", help="reuse an existing Volcengine result instead of calling the API")
    parser.add_argument(
        "--expected-sha256",
        help="upstream locked-WAV manifest SHA-256 required for a live ASR submit",
    )
    parser.add_argument(
        "--expected-script-sha256",
        help="upstream manifest SHA-256 for the exact narration script file",
    )
    parser.add_argument("--env-file")
    parser.add_argument("--resource-id")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=14,
        help="hard review limit for one semantic single-line caption",
    )
    parser.add_argument("--min-coverage", type=float, default=0.90)
    parser.add_argument("--doctor", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.doctor:
        return doctor(args)
    if not args.media:
        raise RuntimeError("media is required unless --doctor is used")
    media = _absolute_without_resolving(Path(args.media))
    media_snapshot = _capture_media_snapshot(media)
    if not 0 < args.min_coverage <= 1:
        raise RuntimeError("--min-coverage must be between 0 and 1")
    live = not args.asr_result
    if live and media.suffix.lower() != ".wav":
        raise RuntimeError("live ASR requires an upstream-locked final WAV; use --asr-result for offline media reuse")
    if live and args.script_text is not None:
        raise RuntimeError(
            "live ASR requires a locked narration script file; --script-text is offline-only"
        )
    if live and not args.script:
        raise RuntimeError("--script is required for live ASR captions")
    if live and not args.expected_script_sha256:
        raise RuntimeError("--expected-script-sha256 is required for live ASR captions")
    if live and not args.expected_sha256:
        raise RuntimeError("--expected-sha256 is required for a live ASR submit")
    if live and media_snapshot[-1].casefold() != args.expected_sha256.casefold():
        raise RuntimeError("Final media SHA-256 does not match the upstream manifest")
    if live:
        _reject_superseded_final_audio(media)

    out_dir = _absolute_without_resolving(Path(args.out_dir))
    if live:
        out_parent = out_dir.parent
        try:
            parent_stat = out_parent.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Live subtitle output parent must be an ordinary directory: {out_parent}"
            ) from exc
        if _has_reparse_component(out_parent) or not stat.S_ISDIR(parent_stat.st_mode):
            raise RuntimeError(
                f"Live subtitle output parent must be an ordinary directory: {out_parent}"
            )
        if out_dir.exists() or out_dir.is_symlink():
            raise RuntimeError(f"Live subtitle output must not already exist: {out_dir}")

    config: dict[str, str] | None = None
    if live:
        config, _ = load_config(args.env_file)
        resource_id = (
            args.resource_id
            if args.resource_id is not None
            else config.get("VOLCENGINE_RESOURCE_ID", DEFAULT_RESOURCE_ID)
        )
        if resource_id != DEFAULT_RESOURCE_ID:
            raise RuntimeError(f"live ASR requires resource ID {DEFAULT_RESOURCE_ID}")
        script = load_script(
            args.script,
            None,
            expected_sha256=args.expected_script_sha256,
            require_locked_file=True,
        )
    else:
        script = load_script(args.script, args.script_text)

    if args.asr_result:
        result_path = Path(args.asr_result).expanduser().resolve()
        result = json.loads(result_path.read_text(encoding="utf-8"))
        raw_words = extract_word_units(result)
        resource_id = args.resource_id or DEFAULT_RESOURCE_ID
    else:
        api_key = config.get("VOLCENGINE_API_KEY") if config is not None else None
        if not api_key:
            raise RuntimeError("VOLCENGINE_API_KEY is missing from the environment or workspace .env")
        transcription = transcribe(
            media,
            api_key,
            resource_id,
            expected_sha256=args.expected_sha256,
        )
        result = transcription.raw_response
        raw_words = [word.to_dict() for word in transcription.words]
    asr_chars, asr_timings = expand_asr_characters(raw_words)
    if args.asr_result and not script:
        root = result.get("result", result)
        script = str(root.get("text", "")).strip() if isinstance(root, dict) else ""
    script_chars = normalize_chars(script or "")
    if not script_chars:
        raise RuntimeError("No usable narration script was provided or returned by ASR")
    mapping, matched = align_script(script_chars, asr_chars)
    captions = build_captions(script or "", mapping, asr_timings, args.max_chars)
    duration = media_duration(media)
    qc = build_qc(script_chars, asr_chars, matched, raw_words, captions, media, resource_id, args.min_coverage, duration)
    qc["source_media"] = media.name
    qc["narration_sha256"] = media_snapshot[-1]

    if not live:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
    payloads = artifact_payloads(
        result=result,
        raw_words=raw_words,
        captions=captions,
        qc=qc,
    )
    if _capture_media_snapshot(media) != media_snapshot:
        raise RuntimeError("Final media changed during subtitle generation")
    publish_artifact_directory(out_dir, payloads)

    print(json.dumps({
        "status": qc["status"],
        "out_dir": str(out_dir),
        "alignment_coverage": qc["alignment_coverage"],
        "caption_count": qc["caption_count"],
        "word_units": qc["word_units"],
        "errors": qc["errors"],
    }, ensure_ascii=False, indent=2))
    return 0 if qc["status"] == "pass" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

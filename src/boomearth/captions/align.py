"""Align display script phrases to real Volcengine/Doubao token boundaries."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from boomearth.providers.volcengine_asr import ASRWord


TIMING_SOURCE = "volcengine-word-timestamps"
DISCOURSE_CONNECTORS = (
    "比如说",
    "首先",
    "其次",
    "然后",
    "而且",
    "但是",
    "只是",
    "所以",
    "如果",
    "其实",
    "就是",
)
_PUNCTUATION = frozenset("，、；：,;:。？！!?")
_TERMINAL_PUNCTUATION = frozenset("。！？!?")
_SEMANTIC_CLAUSE_PUNCTUATION = frozenset("，、,；;：:")
_ENGLISH_TERM = re.compile(r"[A-Za-z0-9.+/\-]+(?: [A-Za-z0-9.+/\-]+)*")


class CaptionAlignmentError(RuntimeError):
    """Raised when display text cannot be aligned to real ASR token timings."""


@dataclass(frozen=True, slots=True)
class Caption:
    start: float
    end: float
    text: str
    source: str = TIMING_SOURCE

    def to_dict(self) -> dict[str, object]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    script_characters: int
    asr_characters: int
    matched_characters: int
    script_indices: tuple[int, ...]
    word_indices: tuple[int | None, ...]

    @property
    def coverage(self) -> float:
        if not self.script_characters:
            return 0.0
        return self.matched_characters / self.script_characters

    @property
    def unmatched_substantive_characters(self) -> int:
        """Count display-script units that cannot be placed on a real ASR token."""

        return sum(word_index is None for word_index in self.word_indices)


def _normalized_positions(text: str) -> list[tuple[int, str]]:
    return [
        (index, character.casefold())
        for index, character in enumerate(text)
        if character.isalnum()
    ]


def align_display_script(script: str, words: tuple[ASRWord, ...]) -> AlignmentResult:
    """Map script characters to ASR token indexes without inventing sub-token times."""

    script_positions = _normalized_positions(script)
    if not script_positions:
        raise CaptionAlignmentError("display script has no alignable characters")

    asr_positions: list[tuple[int, str]] = []
    for word_index, word in enumerate(words):
        asr_positions.extend((word_index, character) for _, character in _normalized_positions(word.text))
    if not asr_positions:
        raise CaptionAlignmentError("ASR tokens have no alignable characters")

    matcher = difflib.SequenceMatcher(
        a=[character for _, character in script_positions],
        b=[character for _, character in asr_positions],
        autojunk=False,
    )
    word_indices: list[int | None] = [None] * len(script_positions)
    matched = 0
    for block in matcher.get_matching_blocks():
        if not block.size:
            continue
        matched += block.size
        for offset in range(block.size):
            word_indices[block.a + offset] = asr_positions[block.b + offset][0]

    return AlignmentResult(
        script_characters=len(script_positions),
        asr_characters=len(asr_positions),
        matched_characters=matched,
        script_indices=tuple(index for index, _ in script_positions),
        word_indices=tuple(word_indices),
    )


def reading_units(text: str) -> float:
    """Count Chinese characters and whole English product/model terms as semantic units."""

    units = 0.0
    for token in re.findall(r"[A-Za-z0-9.+/\-]+(?: [A-Za-z0-9.+/\-]+)*|[\u3400-\u9fff]|[^\s]", text):
        if _ENGLISH_TERM.fullmatch(token):
            units += 1.0
        elif "\u3400" <= token <= "\u9fff":
            units += 1.0
        elif token not in _PUNCTUATION:
            units += 0.5
    return units


def _protected_cut_positions(script: str) -> set[int]:
    protected: set[int] = set()
    for match in _ENGLISH_TERM.finditer(script):
        protected.update(range(match.start() + 1, match.end()))
    for connector in DISCOURSE_CONNECTORS:
        for match in re.finditer(re.escape(connector), script):
            protected.update(range(match.start() + 1, match.end()))
    return protected


def _word_boundary_at(alignment: AlignmentResult, position: int) -> tuple[int, int] | None:
    left: int | None = None
    right: int | None = None
    for script_index, word_index in zip(alignment.script_indices, alignment.word_indices):
        if script_index < position and word_index is not None:
            left = word_index
        elif script_index >= position and word_index is not None and right is None:
            right = word_index
    if left is None or right is None or left >= right:
        return None
    return left, right


def _caption_word_indexes(
    alignment: AlignmentResult,
    *,
    start: int,
    end: int,
) -> tuple[int, ...]:
    return tuple(
        word_index
        for script_index, word_index in zip(alignment.script_indices, alignment.word_indices)
        if start <= script_index < end and word_index is not None
    )


def _display_text(text: str) -> str:
    """Keep approved words while removing punctuation from on-screen captions."""

    return "".join(character for character in text if character not in _PUNCTUATION).strip()


def _semantic_caption_ranges(
    script: str,
    alignment: AlignmentResult,
    *,
    max_units: float,
) -> tuple[tuple[int, int], ...]:
    """Group semantic clauses into one readable caption without crossing sentences."""

    ends = [
        position
        for position in range(1, len(script))
        if script[position - 1]
        in (_TERMINAL_PUNCTUATION | _SEMANTIC_CLAUSE_PUNCTUATION)
        and _word_boundary_at(alignment, position) is not None
    ]
    if not ends or ends[-1] != len(script):
        ends.append(len(script))

    ranges: list[tuple[int, int]] = []
    start = 0
    current = 0
    minimum_units = 6.0
    for end in ends:
        candidate = script[current:end]
        candidate_units = reading_units(_display_text(candidate))
        current_units = reading_units(_display_text(script[start:current]))
        terminal = script[end - 1] in _TERMINAL_PUNCTUATION
        if (
            current > start
            and current_units >= minimum_units
            and (current_units + candidate_units >= max_units or terminal)
        ):
            ranges.append((start, current))
            start = current
        current = end
        if terminal:
            ranges.append((start, current))
            start = current
    if current > start:
        ranges.append((start, current))
    return tuple(item for item in ranges if _display_text(script[item[0] : item[1]]))


def group_caption_phrases(
    script: str,
    words: tuple[ASRWord, ...],
    alignment: AlignmentResult,
    *,
    max_units: float = 20.0,
) -> tuple[Caption, ...]:
    """Use real timing while displaying one punctuation-free semantic line."""

    if max_units <= 0:
        raise CaptionAlignmentError("caption grouping limit is invalid")
    captions: list[Caption] = []
    for start, end in _semantic_caption_ranges(
        script, alignment, max_units=max_units
    ):
        word_indexes = _caption_word_indexes(alignment, start=start, end=end)
        if not word_indexes:
            continue
        captions.append(
            Caption(
                start=words[min(word_indexes)].start,
                end=words[max(word_indexes)].end,
                text=_display_text(script[start:end]),
            )
        )
    if not captions:
        raise CaptionAlignmentError("display script could not be placed on ASR token boundaries")
    return tuple(captions)


def words_with_gaps(words: tuple[ASRWord, ...]) -> list[dict[str, object]]:
    """Keep provider token units intact and represent only material pauses."""

    units: list[dict[str, object]] = []
    previous_end = 0.0
    for word in words:
        if word.start - previous_end >= 0.2:
            units.append(
                {
                    "text": "",
                    "start": previous_end,
                    "end": word.start,
                    "isGap": True,
                }
            )
        units.append(word.to_dict())
        previous_end = max(previous_end, word.end)
    return units

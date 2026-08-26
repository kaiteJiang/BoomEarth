from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


_URL_SCHEME = "URL scheme"
_SOURCE_METADATA_KEY = "source metadata key"
_INTERNAL_PATH = ".internal path"
_TRANSCRIPT_PATH_MARKER = "transcript path marker"
_SOURCE_PHRASE = "source phrase"

_URL_SCHEME_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])(?:https?|ftp|file):",
    re.IGNORECASE,
)
_PROTOCOL_RELATIVE_URL_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])//[^/\s]+(?:/[^\s]*)?",
)
_SOURCE_METADATA_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:source_url|source_title|source_link|source_transcript)"
    r"(?![A-Za-z0-9_])"
    r"|(?:来源链接|原视频链接|源视频链接|原标题|来源标题|源标题)",
    re.IGNORECASE,
)
_TRANSCRIPT_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:transcript|逐字稿)(?=$|[\\/._-])",
    re.IGNORECASE,
)

_CATEGORIES = (
    _URL_SCHEME,
    _SOURCE_METADATA_KEY,
    _INTERNAL_PATH,
    _TRANSCRIPT_PATH_MARKER,
    _SOURCE_PHRASE,
)


def _normalize_without_cf(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )


def _normalize_source_phrase(value: str) -> str:
    return "".join(
        character
        for character in _normalize_without_cf(value)
        if not character.isspace()
    )


def _normalized_source_text(text: str) -> tuple[str, list[int]]:
    normalized_lines: list[str] = []
    line_numbers: list[int] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        normalized_line = _normalize_source_phrase(line)
        normalized_lines.append(normalized_line)
        line_numbers.extend([line_number] * len(normalized_line))

    return "".join(normalized_lines), line_numbers


def validate_public_handoff(
    text: str,
    source_phrases: Iterable[str] = (),
) -> list[str]:
    """Return redacted, line-oriented violations in a public handoff."""
    if not isinstance(text, str):
        raise TypeError("text must be str")

    normalized_phrases: list[str] = []
    for phrase in source_phrases:
        if not isinstance(phrase, str):
            raise TypeError("source_phrases must contain only str")
        normalized = _normalize_source_phrase(phrase)
        if normalized and normalized not in normalized_phrases:
            normalized_phrases.append(normalized)

    violations: list[tuple[int, int]] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        normalized_line = _normalize_without_cf(line)
        categories = (
            (
                _URL_SCHEME,
                (
                    _URL_SCHEME_RE.search(normalized_line) is not None
                    or _PROTOCOL_RELATIVE_URL_RE.search(normalized_line)
                    is not None
                ),
            ),
            (
                _SOURCE_METADATA_KEY,
                _SOURCE_METADATA_RE.search(normalized_line) is not None,
            ),
            (_INTERNAL_PATH, ".internal" in normalized_line),
            (
                _TRANSCRIPT_PATH_MARKER,
                _TRANSCRIPT_PATH_RE.search(normalized_line) is not None,
            ),
        )

        for category_rank, (_, matched) in enumerate(categories):
            if matched:
                violations.append((line_number, category_rank))

    normalized_text, line_numbers = _normalized_source_text(text)
    for phrase in normalized_phrases:
        start = 0
        while True:
            match_start = normalized_text.find(phrase, start)
            if match_start < 0:
                break
            violations.append((line_numbers[match_start], 4))
            start = match_start + 1

    return [
        f"line {line_number}: {_CATEGORIES[category_rank]}"
        for line_number, category_rank in sorted(set(violations))
    ]

"""Punctuation policy for on-screen captions; narration text stays unchanged."""

from __future__ import annotations

import re


_TRAILING_PUNCTUATION = frozenset(
    "，,。.!！?？、；;：:…—-·~～\"'‘’“”「」『』（）()[]【】《》〈〉"
)


def format_caption_text(text: str) -> str:
    """Use spaces for internal commas and keep only a final question mark.

    This runs after semantic grouping, so a comma at a cue boundary disappears;
    a comma inside one cue becomes one visible space. Numeric and model-name
    periods are kept intact.
    """

    normalized = re.sub(
        r"\s+", " ", text.replace("，", " ").replace(",", " ").replace("、", " ")
    ).strip()
    end = len(normalized)
    while end and normalized[end - 1] in _TRAILING_PUNCTUATION:
        end -= 1
    suffix = normalized[end:]
    question = next((char for char in reversed(suffix) if char in "？?"), "")
    return normalized[:end].rstrip() + question

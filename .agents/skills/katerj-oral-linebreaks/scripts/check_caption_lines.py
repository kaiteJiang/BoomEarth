#!/usr/bin/env python3
"""Validate closed, readable Chinese oral-copy and caption lines."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
ENGLISH_NAME = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-_./+][A-Za-z0-9]+)*")
DANGLING_END = re.compile(
    r"(?:的|地|得|把|被|给|对|从|和|与|跟|为|因为|如果|虽然|只要|不仅|一旦|为了|直到|通过|关于|是否|能不能|会不会|有没有)$"
)
ORPHAN_START = re.compile(r"^(?:是|把|被|而是|才|才能|就|就会|就能|所以|因此)")
TERMINAL = "。！？!?"
TRAILING_MARKS = "，、：；,;:"


def _english_only(line: str) -> bool:
    return bool(ENGLISH_NAME.search(line)) and not bool(CJK.search(line))


def analyze_text(text: str, *, max_cjk: int = 14) -> list[dict[str, object]]:
    """Return deterministic line-level violations without rewriting input."""

    issues: list[dict[str, object]] = []
    lines: list[tuple[int, str]] = []
    previous_dangling = False

    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            previous_dangling = False
            continue
        lines.append((number, line))

        length = len(CJK.findall(line))
        if length > max_cjk:
            issues.append(
                {"line": number, "code": "over_limit", "cjk": length, "text": line}
            )

        lexical = line.rstrip(TRAILING_MARKS).rstrip()
        dangling = not line.endswith(tuple(TERMINAL)) and bool(
            DANGLING_END.search(lexical)
        )
        if dangling:
            issues.append(
                {"line": number, "code": "dangling_end", "cjk": length, "text": line}
            )
        if previous_dangling and ORPHAN_START.search(line):
            issues.append(
                {"line": number, "code": "orphan_start", "cjk": length, "text": line}
            )
        previous_dangling = dangling

    for (number, line), (_, following) in zip(lines, lines[1:]):
        if _english_only(line) and _english_only(following) and not line.endswith(
            tuple(TERMINAL)
        ):
            issues.append(
                {
                    "line": number,
                    "code": "split_english_sentence",
                    "cjk": 0,
                    "text": line,
                }
            )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="UTF-8 text file to inspect")
    parser.add_argument("--max-cjk", type=int, default=14)
    args = parser.parse_args()
    text = Path(args.file).read_text(encoding="utf-8")
    issues = analyze_text(text, max_cjk=args.max_cjk)
    json.dump({"status": "pass" if not issues else "fail", "issues": issues}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())

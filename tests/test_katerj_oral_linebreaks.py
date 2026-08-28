"""Behavioral tests for the public KaterJ oral-linebreak checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / ".agents"
    / "skills"
    / "katerj-oral-linebreaks"
    / "scripts"
    / "check_caption_lines.py"
)


def _load_checker():
    spec = importlib.util.spec_from_file_location("katerj_oral_linebreaks", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checker_accepts_closed_short_subtitle_lines() -> None:
    checker = _load_checker()

    assert checker.analyze_text("先把主题讲清楚\n再给出解决办法", max_cjk=14) == []


def test_checker_reports_reading_and_closure_failures() -> None:
    checker = _load_checker()

    issues = checker.analyze_text(
        "真正重要的，\n是把每一步都验证清楚\n这是一条明显超过十四个汉字并且难以阅读的字幕",
        max_cjk=14,
    )

    assert {issue["code"] for issue in issues} == {
        "dangling_end",
        "orphan_start",
        "over_limit",
    }


def test_checker_protects_complete_english_sentences() -> None:
    checker = _load_checker()

    issues = checker.analyze_text("Ship the smallest\nchange that works.", max_cjk=14)

    assert [issue["code"] for issue in issues] == ["split_english_sentence"]

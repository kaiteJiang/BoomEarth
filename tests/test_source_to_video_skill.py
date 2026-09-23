from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / ".agents" / "skills" / "ra-source-to-video"


def _text() -> str:
    return (SKILL / "SKILL.md").read_text(encoding="utf-8")


def test_source_to_video_skill_routes_current_intakes_and_sponge_default() -> None:
    text = _text()
    metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert text.startswith("---\nname: ra-source-to-video\n")
    for value in (
        "katerj-video-wash",
        "ra-x-article-import",
        "ra-github-skill-import",
        "katerj-video-director",
        "sponge-host-handdrawn-v1",
        "profiled-illustration-v4",
        "ra-video-illustrations",
        "IndexTTS2",
        "katerj-audio-subtitles",
        "jl-oral-linebreaks",
        "1920×1080",
    ):
        assert value in text
    assert "$ra-source-to-video" in metadata
    assert "allow_implicit_invocation: true" in metadata


def test_source_to_video_skill_preserves_resume_and_finalization_gates() -> None:
    text = _text()

    for value in (
        "VIDEO-PRODUCTION-RUNBOOK.md",
        "VIDEO-CONTINUOUS-ORCHESTRATION.md",
        "production note",
        "实际文件和哈希",
        "已批准全文不追补、不擅改",
        "每 30 分钟检查一次",
        "完整 WAV/manifest 验证后",
        "canonical finalizer",
        "逐场景 QC",
        "check_delivery",
        "统一归档",
        "ra-video-cover",
        "缺文件、缺实际多模态QC或仅样片PASS时不称推荐最终成片",
    ):
        assert value in text


def test_source_to_video_skill_repairs_failed_intake_without_repeating_valid_media() -> None:
    text = _text()

    for value in (
        "音频/字幕/插画证据有效则复用",
        "来源采集失败按连续制作编排分类、修复代码、离线回归并通过新事务继续",
        "旧计划和回执不可原地改",
        "每次完整采集成功后把无隐私的修复方法写入来源修复账本",
        "完成作业缺文件时进入取回而非再生成",
    ):
        assert value in text

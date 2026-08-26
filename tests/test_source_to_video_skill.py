from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / ".agents" / "skills" / "ra-source-to-video"


def test_source_to_video_skill_declares_all_supported_intakes_and_production_defaults() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert text.startswith("---\nname: ra-source-to-video\n")
    for value in (
        "katerj-video-wash",
        "ra-x-article-import",
        "ra-github-skill-import",
        "katerj-video-director",
        "xiaohei-white-first-v1",
        "katerj-xiaohei-illustrations",
        "user-indextts2-black-gold-v3",
        "headroom_08-circle-lower-left",
        "anchor-dark",
        "katerj-audio-subtitles",
        "one cue per frame",
        "1920×1080",
        "compile_source_handoff.py",
    ):
        assert value in text
    assert "$ra-source-to-video" in metadata
    assert 'allow_implicit_invocation: true' in metadata


def test_source_to_video_skill_preserves_private_and_provider_approval_boundaries() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    for value in (
        "视频工作台/.internal/",
        "SHA-256",
        "no-retry/no-fallback",
        "Do not disclose credentials",
        "do not silently switch provider",
        "never overwrite an approved final",
    ):
        assert value in text


def test_source_to_video_skill_routes_resumable_finalization_and_cover_handoffs() -> None:
    """Would fail if the intake Skill regenerates or manually composes recovered media."""
    text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())

    for value in (
        "immutable local receipts and actual files",
        "status/read-plus-one-download retrieval plan",
        "never back to generation",
        "exact retrieval authorization",
        "signed URLs and private IDs",
        "ignored private input/state",
        "stdin-based local helpers",
        "command-line arguments",
        "public reports",
        "canonical content finalizer with optional avatar input",
        "sample/manual composition",
        "final archive already passes delivery",
        "post-success reporting error",
        "archive verification",
        "covers: punk-cover-giant-title-3x4-v1",
        "ra-video-cover",
        "punk-cover",
        "giant-perspective-chinese-title",
        "封面/作品封面-1080x1440.png",
        "质检/punk-cover-qc.json",
        "separate exact ImageGen approval",
    ):
        assert value in text


def test_source_to_video_skill_does_not_bypass_retrieval_archive_or_cover_gates() -> None:
    """Would fail if urgency rerenders a delivery or starts covers before final QC passes."""
    text = " ".join((SKILL / "SKILL.md").read_text(encoding="utf-8").split())

    assert (
        'Speed, deadline, sunk cost, subscription entitlement, or "直接出片" '
        "never authorizes retrieval"
    ) in text
    assert "A passing archive must be verified and reported, never rerendered." in text
    assert (
        "After final QC and archive verification have passed, route the "
        "post-delivery cover to `ra-video-cover`"
    ) in text

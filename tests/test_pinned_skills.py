"""Minimal regression tests for the locally patched pinned transcript skill."""

from __future__ import annotations

import ast
from pathlib import Path


TRANSCRIPT_PATH = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "ra-逐字稿提取skill"
    / "scripts"
    / "transcript.py"
)
SKILLS_ROOT = Path(__file__).parents[1] / ".agents" / "skills"


def _extract_function(name: str):
    """Extract one pure helper without importing transcript.py or loading .env."""

    tree = ast.parse(TRANSCRIPT_PATH.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace: dict[str, object] = {}
    exec(compile(ast.fix_missing_locations(module), str(TRANSCRIPT_PATH), "exec"), namespace)
    return namespace[name]


def test_credential_status_never_exposes_secret_fragments() -> None:
    mask = _extract_function("mask")
    dummy_secret = "dummy-secret-value-never-real"

    configured = mask(dummy_secret)
    missing = mask("")

    assert configured == "SET"
    assert missing == "UNSET"
    assert configured in {"SET", "UNSET"}
    assert missing in {"SET", "UNSET"}
    assert dummy_secret not in configured
    assert dummy_secret[:6] not in configured
    assert dummy_secret[-4:] not in configured


def test_p2_skill_docs_use_shipped_windows_adapters_and_private_gates() -> None:
    download = (SKILLS_ROOT / "katerj-source-acquisition" / "SKILL.md").read_text("utf-8")
    transcript = (SKILLS_ROOT / "katerj-source-transcription" / "SKILL.md").read_text("utf-8")
    pipeline = (SKILLS_ROOT / "katerj-video-wash" / "SKILL.md").read_text("utf-8")
    rewrite = (SKILLS_ROOT / "katerj-script-rewrite" / "SKILL.md").read_text("utf-8")
    combined = "\n".join((download, transcript, pipeline, rewrite))

    required_commands = (
        "automation/scripts/source_intake.py",
        "automation/scripts/acquire_source.py",
        "automation/scripts/normalize_source_audio.py",
        "automation/scripts/transcribe_source.py",
        "automation/scripts/prepare_rewrite.py",
        "automation/scripts/compile_source_handoff.py",
    )
    assert all(command in combined for command in required_commands)
    assert "--input-file" in download
    assert "--provider yt-dlp|tikhub" in pipeline
    assert "--approval" in combined
    assert "rewrite-candidate.json" in rewrite
    assert "rewrite-review.json" in rewrite
    assert "publication-receipt.json" in pipeline

    forbidden = (
        "/opt/homebrew/bin/python3",
        "QUSHUIYIN_API_KEY",
        "PARAFORMER_MODEL=paraformer-v2",
        'transcript.py" "<url>"',
        "--internal",
    )
    assert all(value not in combined for value in forbidden)


def test_p2_skills_do_not_author_public_handoff_without_compiler() -> None:
    pipeline = (SKILLS_ROOT / "katerj-video-wash" / "SKILL.md").read_text("utf-8")
    rewrite = (SKILLS_ROOT / "katerj-script-rewrite" / "SKILL.md").read_text("utf-8")
    for text in (pipeline, rewrite):
        assert "compile_source_handoff.py compile" in text
        assert "不得直接写入 `待制作`" in text


def test_rewrite_flow_binds_three_second_hook_to_multiplatform_title_evidence() -> None:
    rewrite = (SKILLS_ROOT / "katerj-script-rewrite" / "SKILL.md").read_text("utf-8")
    hook = (SKILLS_ROOT / "katerj-video-hook" / "SKILL.md").read_text("utf-8")
    title = (SKILLS_ROOT / "katerj-video-titles" / "SKILL.md").read_text("utf-8")

    for token in (
        "opening_contract",
        "hook_3s",
        "audience_pain",
        "value_promise",
        "cta",
        "bridge",
        "jl-multiplatform-titles",
    ):
        assert token in rewrite
    assert rewrite.index("`katerj-video-hook`") < rewrite.index("`jl-multiplatform-titles`")
    assert rewrite.index("`jl-multiplatform-titles`") < rewrite.index("`katerj-hook-review`")
    assert "前 3 秒" in hook
    assert "标题、封面字、开场钩子" in hook
    assert "jl-multiplatform-titles" in title

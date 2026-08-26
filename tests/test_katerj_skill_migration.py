"""Regression contract for the KaterJ skill namespace migration."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / ".agents" / "skills"
MAPPING_PATH = SKILLS / "katerj-skill-map.json"

EXPECTED = {
    "ra-选题": "katerj-topic-planning",
    "ra-hook": "katerj-video-hook",
    "ra-video-wash-pipeline": "katerj-video-wash",
    "ra-video-download": "katerj-source-acquisition",
    "ra-逐字稿提取skill": "katerj-source-transcription",
    "ra-洗稿": "katerj-script-rewrite",
    "ra-人话": "katerj-human-writing",
    "ra-video-title": "katerj-video-titles",
    "dbs-ai-check": "katerj-ai-writing-review",
    "dbs-hook": "katerj-hook-review",
    "dbs-resonate": "katerj-resonance-review",
    "ra-video-production-director": "katerj-video-director",
    "tts-skill": "katerj-local-tts",
    "ra-audio-to-subtitles": "katerj-audio-subtitles",
    "skill-captions": "katerj-caption-rendering",
    "rn-motion-director": "katerj-motion-director",
    "rn-replica-qc": "katerj-replica-qc",
    "ian-xiaohei-illustrations": "katerj-xiaohei-illustrations",
}


def _frontmatter_name(text: str) -> str:
    match = re.search(r"(?m)^name:\s*([^\n]+)$", text)
    assert match, "missing frontmatter name"
    return match.group(1).strip()


def test_katerj_map_declares_all_legacy_compatibility_names() -> None:
    payload = json.loads(MAPPING_PATH.read_text("utf-8"))

    assert payload["schema_version"] == 1
    assert payload["canonical_prefix"] == "katerj-"
    assert payload["mappings"] == EXPECTED


def test_each_katerj_skill_is_canonical_and_each_old_name_is_a_thin_bridge() -> None:
    for legacy, canonical in EXPECTED.items():
        canonical_path = SKILLS / canonical / "SKILL.md"
        legacy_path = SKILLS / legacy / "SKILL.md"

        canonical_text = canonical_path.read_text("utf-8")
        legacy_text = legacy_path.read_text("utf-8")

        assert _frontmatter_name(canonical_text) == canonical
        assert len(canonical_text.splitlines()) >= 24
        assert "## Outcome" in canonical_text
        assert "## Workflow" in canonical_text
        assert "## Acceptance" in canonical_text

        assert _frontmatter_name(legacy_text) == legacy
        assert canonical in legacy_text
        assert "Compatibility bridge" in legacy_text
        assert len(legacy_text.splitlines()) <= 22


def test_canonical_skill_entrypoints_do_not_claim_vendored_material_as_original() -> None:
    forbidden_brand_tokens = (
        "dontbesilent",
        "Pluviobyte",
        "@Pluvio9yte",
        "rnskill",
    )

    for canonical in EXPECTED.values():
        text = (SKILLS / canonical / "SKILL.md").read_text("utf-8")
        assert all(token not in text for token in forbidden_brand_tokens)

    notice = (SKILLS / "KATERJ-MIGRATION.md").read_text("utf-8")
    assert "vendored" in notice
    assert "LICENSE-NOTES.md" in notice
    assert "不改变第三方脚本、素材或字体的原许可证" in notice


def test_new_skill_names_follow_runtime_safe_kebab_case() -> None:
    for canonical in EXPECTED.values():
        assert re.fullmatch(r"[a-z0-9-]+", canonical)
        assert len(canonical) < 64

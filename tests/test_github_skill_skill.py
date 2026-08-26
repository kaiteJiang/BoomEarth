from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / ".agents" / "skills" / "ra-github-skill-import"


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    raw, _body = text[4:].split("\n---\n", 1)
    values: dict[str, str] = {}
    for line in raw.splitlines():
        key, value = line.split(":", 1)
        assert key not in values
        values[key] = value.strip()
    return values


def test_github_skill_import_is_a_discoverable_project_skill() -> None:
    text = (SKILL / "SKILL.md").read_text("utf-8")
    metadata = _frontmatter(text)

    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "ra-github-skill-import"
    assert metadata["description"].startswith("Use when ")
    for trigger in ("GitHub Skill URL", "解读", "洗稿", "制作视频"):
        assert trigger in metadata["description"]


def test_github_skill_import_routes_only_through_the_bounded_private_lane() -> None:
    text = (SKILL / "SKILL.md").read_text("utf-8")
    commands = re.findall(r"^uv run python ([^\n]+)$", text, re.MULTILINE)

    assert commands == [
        'automation/scripts/source_intake.py github-skill --input-file "<PRIVATE_URL_FILE>" --authorized',
        "automation/scripts/acquire_github_skill.py plan <work_id>",
        'automation/scripts/acquire_github_skill.py run <work_id> --approval "<PRIVATE_APPROVAL_JSON>"',
        "automation/scripts/prepare_rewrite.py <work_id> --platform <platform> --duration-target-s <seconds> --archive-slug <slug>",
    ]
    for required_boundary in (
        "不安装仓库 Skill",
        "不执行仓库代码",
        "不读取 Cookie",
        "不使用 GitHub Token",
        "不跟随重定向",
        "不重试",
        "不切换 Provider",
        "不调用 Paraformer",
        "不调用火山/豆包 ASR",
        "github_skill_ready",
        "UNDECLARED",
        "UNKNOWN",
        "一次真实采集",
        "ra-洗稿",
    ):
        assert required_boundary in text


def test_github_skill_import_metadata_and_attribution_are_source_safe() -> None:
    metadata = (SKILL / "agents" / "openai.yaml").read_text("utf-8")
    attribution = (
        SKILL / "references" / "upstream-attribution.md"
    ).read_text("utf-8")

    assert '$ra-github-skill-import' in metadata
    assert "private" in metadata.lower()
    assert "chujianyun/awesome-gpt-image2-ppt-skills" in attribution
    assert "UNDECLARED" in attribution
    assert "未安装" in attribution
    assert "未执行" in attribution


def test_project_docs_register_the_new_skill_without_claiming_live_acceptance() -> None:
    installed = (ROOT / ".agents" / "skills" / "INSTALLED.md").read_text("utf-8")
    readme = (ROOT / "README.md").read_text("utf-8")

    assert "ra-github-skill-import" in installed
    assert "ra-github-skill-import" in readme
    assert "REAL_ACCEPTANCE_PASS" in installed
    assert "GitHub Skill URL" in readme

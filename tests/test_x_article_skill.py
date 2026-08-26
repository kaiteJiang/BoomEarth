from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILL = ROOT / ".agents" / "skills" / "ra-x-article-import"


def test_x_article_skill_has_safe_routing_workflow_and_attribution() -> None:
    skill = (SKILL / "SKILL.md").read_text("utf-8")
    metadata = (SKILL / "agents" / "openai.yaml").read_text("utf-8")
    attribution = (SKILL / "references" / "upstream-attribution.md").read_text(
        "utf-8"
    )

    assert skill.startswith("---\nname: ra-x-article-import\n")
    for phrase in (
        "X Article",
        "图文帖子",
        "source_intake.py x-article",
        "acquire_x_article.py plan",
        "acquire_x_article.py run",
        "PRIVATE_URL_FILE",
        "article_ready",
        "不读取 Cookie",
        "不重试",
        "不自动降级",
        "ra-video-download",
        "高媒体恢复档位",
        "x-article-request-budget-exceeded",
        "--profile high-media",
        "request_count=61",
        "新的 work_id",
        "不得修改或复用",
        "正式 article/manifest",
        "source_registered",
    ):
        assert phrase in skill
    assert "$ra-x-article-import" in metadata
    assert "kaiteJiang/content-repub" in attribution
    assert "references/parse_tweet.md" in attribution
    assert "MIT" in attribution


def test_video_download_routes_x_articles_to_dedicated_skill() -> None:
    download = (ROOT / ".agents" / "skills" / "katerj-source-acquisition" / "SKILL.md").read_text(
        "utf-8"
    )
    assert "X Article/图文帖子" in download
    assert "ra-x-article-import" in download
    assert "X 原生媒体" in download

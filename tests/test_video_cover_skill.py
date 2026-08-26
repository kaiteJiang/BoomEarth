from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "ra-video-cover"


def test_video_cover_skill_uses_the_approved_single_punk_cover_default() -> None:
    text = " ".join((SKILL / "SKILL.md").read_text("utf-8").split())

    for value in (
        "punk-cover-giant-title-3x4-v1",
        "punk-cover",
        "giant-perspective-chinese-title",
        "3:4",
        "1080×1440",
        "封面/作品封面-1080x1440.png",
        "质检/punk-cover-qc.json",
        "不生成主页预览",
        "一次精确 ImageGen 批准",
        "platform-defaults-v1",
        "历史合同",
    ):
        assert value in text


def test_video_cover_skill_ui_describes_one_3x4_work_cover() -> None:
    text = " ".join((SKILL / "agents" / "openai.yaml").read_text("utf-8").split())

    assert "单张 3:4 巨型透视作品封面" in text
    assert "抖音主页与视频号封面" not in text

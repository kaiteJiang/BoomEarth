from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
DIRECTOR = ROOT / ".agents" / "skills" / "katerj-video-director"


def _director_text() -> str:
    return "\n".join(
        (
            (DIRECTOR / "SKILL.md").read_text(encoding="utf-8"),
            (DIRECTOR / "references" / "routing.md").read_text(encoding="utf-8"),
            (DIRECTOR / "references" / "delivery-gates.md").read_text(
                encoding="utf-8"
            ),
        )
    )


def test_director_uses_canonical_private_heygen_paths_and_terminal_receipt() -> None:
    """Would fail if recovery writes provider state beside the repository or reports a transient receipt."""

    text = " ".join(_director_text().split())

    assert "01-内容生产/视频工作台/.internal/heygen/retrieval-url.txt" in text
    assert "01-内容生产/视频工作台/.internal/heygen/retrieval-receipt.json" in text
    assert "工程/delivery-report.json" in text
    assert "transient" in text
    assert "deleted before archive" in text
    assert "status == \"pass\"" in text
    assert "status is PASS" not in text
    assert ".internal\\heygen" not in text


def test_director_preserves_private_master_before_public_archive() -> None:
    """Would fail if canonical finalization can publish or discard the provider master."""

    text = " ".join(_director_text().split())

    for value in (
        "archived-masters/<project-name>/<master-sha256>.mp4",
        "hard link",
        "remove the active-project master",
        "recoverable active source",
        "final archive contains no provider master",
        "no provider receipt, signed URL, or private ID",
        "never refreshes or reselects the group/look",
        "overrides the generic Skill's ID-bearing CLI download",
    ):
        assert value.casefold() in text.casefold()


def test_director_routes_the_default_post_delivery_punk_cover() -> None:
    text = " ".join(_director_text().split())

    for value in (
        "punk-cover-giant-title-3x4-v1",
        "giant-perspective-chinese-title",
        "封面/作品封面-1080x1440.png",
        "质检/punk-cover-qc.json",
        "after core delivery and archive verification",
        "platform-defaults-v1",
        "historical",
    ):
        assert value in text

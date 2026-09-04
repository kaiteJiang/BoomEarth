from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from boomearth.workbench.rewrite_package import (
    REQUIRED_REVIEWS,
    RewritePackageError,
    find_source_overlap_lines,
    load_rewrite_candidate,
    load_rewrite_review,
    normalized_reading_units,
    prepare_rewrite_brief,
    reading_unit_count,
)
from boomearth.workbench.source_artifacts import publish_json_exclusive, sha256_file
from boomearth.workbench.source_intake import create_url_intake, create_x_article_intake
from boomearth.workbench.source_ledger import (
    StageEvent,
    WashEventLedger,
    event_sha256,
)


WORK_ID = "00000000-0000-4000-8000-000000000006"
REGISTERED_VISUAL_TARGETS = (
    "xiaohei-white-first-v1",
    "editorial-motion-v2",
    "semantic-handdrawn-v3",
    "semantic-handdrawn-v3/type-led",
    "vivid-comic-explainer",
    "engineering-sketch-explainer",
    "four-panel-comic-explainer",
    "blue-black-whiteboard-explainer",
    "xiaohuang-warm-first-v1",
)


def _work_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def _candidate_value(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "archive_slug": "first-principles-rewrite",
        "duration_target_s": 60,
        "illustration_skill": "ra-video-illustrations",
        "platform": "douyin",
        "ratio": "9:16",
        "schema_version": 1,
        "segments": [
            {
                "segment_id": "segment-001",
                "text": "真正重要的不是记住答案，而是学会从事实重新搭建问题。",
                "visual_intent": "人物拆解复杂积木",
            },
            {
                "segment_id": "segment-002",
                "text": "先拿掉习惯性的结论，再确认哪些条件真的不能省略。",
                "visual_intent": "逐层移除标签",
            },
        ],
        "title_candidates": ["别急着找答案，先把问题拆到底"],
        "visual": "default",
        "work_id": WORK_ID,
    }
    value.update(changes)
    return value


def _write_candidate(root: Path, **changes: object) -> Path:
    path = root / "rewrite-candidate.json"
    path.write_text(
        json.dumps(_candidate_value(**changes), ensure_ascii=False), encoding="utf-8"
    )
    return path


def _write_review(root: Path, candidate_sha256: str, **changes: object) -> Path:
    value: dict[str, object] = {
        "candidate_sha256": candidate_sha256,
        "reviewed_at": "2026-08-13T08:09:10Z",
        "reviews": {name: "pass" for name in REQUIRED_REVIEWS},
        "schema_version": 1,
    }
    value.update(changes)
    path = root / "rewrite-review.json"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def _opening_contract() -> dict[str, object]:
    return {
        "audience_pain": "如果你刚开始用智能助手，很容易不知道边界该放在哪里。",
        "bridge": "下面先从工作区开始。",
        "cover_hook": "先把边界搞清楚",
        "cta": "先点赞收藏，真正用到时可以直接照着做。",
        "hook_3s": "智能助手最容易踩坑的不是提示词。",
        "hook_type": "pain-point",
        "proof_segment_ids": ["segment-001", "segment-002"],
        "schema_version": 1,
        "title_formula": "reversal",
        "title_skill": "jl-multiplatform-titles",
        "value_promise": "这条视频会帮你把工作区和权限一次讲明白。",
    }


def _v2_segments() -> list[dict[str, str]]:
    return [
        {
            "segment_id": "segment-001",
            "text": (
                "智能助手最容易踩坑的不是提示词。"
                "如果你刚开始用智能助手，很容易不知道边界该放在哪里。"
            ),
            "visual_intent": "新手站在权限边界前判断范围",
        },
        {
            "segment_id": "segment-002",
            "text": (
                "这条视频会帮你把工作区和权限一次讲明白。"
                "先点赞收藏，真正用到时可以直接照着做。"
                "下面先从工作区开始。"
            ),
            "visual_intent": "清晰路线连接工作区权限和验收",
        },
    ]


def _transcript_with(text: str) -> dict[str, object]:
    return {
        "duration": 1.0,
        "language": "zh-CN",
        "sentences": [{"text": text}],
        "source_id": "safe-id",
    }


def test_candidate_title_limit_stays_five_by_default(tmp_path: Path) -> None:
    path = _write_candidate(
        tmp_path,
        title_candidates=[f"候选标题第{index}条，保留悬念" for index in range(1, 7)],
    )

    with pytest.raises(RewritePackageError, match="^rewrite-candidate-invalid$"):
        load_rewrite_candidate(path)


def test_production_revision_can_request_twelve_title_slots(tmp_path: Path) -> None:
    titles = [f"手机发出任务第{index}次，电脑继续工作" for index in range(1, 9)]
    path = _write_candidate(tmp_path, title_candidates=titles)

    candidate = load_rewrite_candidate(path, maximum_title_candidates=12)

    assert candidate.title_candidates == tuple(titles)


def test_v2_candidate_binds_three_second_hook_title_skill_and_opening_prefix(
    tmp_path: Path,
) -> None:
    candidate = load_rewrite_candidate(
        _write_candidate(
            tmp_path,
            schema_version=2,
            opening_contract=_opening_contract(),
            segments=_v2_segments(),
        )
    )

    assert candidate.schema_version == 2
    assert candidate.opening_contract is not None
    assert candidate.opening_contract.hook_3s == "智能助手最容易踩坑的不是提示词。"
    assert candidate.opening_contract.title_skill == "jl-multiplatform-titles"
    assert candidate.opening_contract.proof_segment_ids == (
        "segment-001",
        "segment-002",
    )


@pytest.mark.parametrize(
    "opening_changes",
    [
        {"title_skill": "ra-video-title"},
        {"hook_3s": ""},
        {"cta": "下面直接开始。"},
        {"proof_segment_ids": ["segment-002"]},
        {"cover_hook": "别急着找答案，先把问题拆到底"},
    ],
)
def test_v2_candidate_rejects_unbound_or_unfulfilled_opening_contract(
    tmp_path: Path, opening_changes: dict[str, object]
) -> None:
    opening = _opening_contract()
    opening.update(opening_changes)
    path = _write_candidate(
        tmp_path,
        schema_version=2,
        opening_contract=opening,
        segments=_v2_segments(),
    )

    with pytest.raises(RewritePackageError, match="^rewrite-candidate-invalid$"):
        load_rewrite_candidate(path)


def test_v2_candidate_rejects_opening_copy_that_is_not_the_spoken_prefix(
    tmp_path: Path,
) -> None:
    opening = _opening_contract()
    opening["value_promise"] = "这条视频会告诉你一个完全不同的承诺。"
    path = _write_candidate(
        tmp_path,
        schema_version=2,
        opening_contract=opening,
        segments=_v2_segments(),
    )

    with pytest.raises(RewritePackageError, match="^rewrite-candidate-invalid$"):
        load_rewrite_candidate(path)


def test_review_stack_orders_hook_selection_before_title_assets_and_hook_qc() -> None:
    assert REQUIRED_REVIEWS.index("ra-hook") < REQUIRED_REVIEWS.index(
        "jl-multiplatform-titles"
    )
    assert REQUIRED_REVIEWS.index("jl-multiplatform-titles") < REQUIRED_REVIEWS.index(
        "dbs-hook"
    )


def test_exact_overlap_is_nfkc_punctuation_and_space_insensitive(
    tmp_path: Path,
) -> None:
    candidate = load_rewrite_candidate(
        _write_candidate(
            tmp_path,
            segments=[
                {
                    "segment_id": "segment-001",
                    "text": "第一性原理，不是口号，而是把问题拆回最基本的事实。",
                    "visual_intent": "抽象拆解",
                }
            ],
        )
    )

    assert find_source_overlap_lines(
        _transcript_with("第一性原理不是口号而是把问题拆回最基本的事实"),
        candidate,
        window_units=20,
    ) == (1,)


def test_short_source_sentence_uses_human_review_not_mechanical_failure(
    tmp_path: Path,
) -> None:
    candidate = load_rewrite_candidate(
        _write_candidate(
            tmp_path,
            segments=[
                {
                    "segment_id": "segment-001",
                    "text": "短句",
                    "visual_intent": "短画面",
                }
            ],
        )
    )
    assert find_source_overlap_lines(_transcript_with("短句"), candidate) == ()


def test_normalized_reading_units_remove_nfkc_space_punctuation_and_controls() -> None:
    assert normalized_reading_units("Ａ B，c\u200b！") == "abc"
    assert reading_unit_count("Ａ B，c\u200b！") == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": True},
        {"archive_slug": "../escape"},
        {"title_candidates": ["duplicate", "duplicate"]},
        {"duration_target_s": True},
        {
            "segments": [
                {
                    "segment_id": "segment-002",
                    "text": "valid text",
                    "visual_intent": "valid visual",
                }
            ]
        },
    ],
)
def test_candidate_rejects_unknown_traversal_duplicate_and_schema_errors(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    path = _write_candidate(tmp_path, **changes)
    with pytest.raises(RewritePackageError, match="^rewrite-candidate-invalid$"):
        load_rewrite_candidate(path)


@pytest.mark.parametrize("visual", ("default", *REGISTERED_VISUAL_TARGETS))
def test_candidate_accepts_only_default_or_registered_visual_targets(
    tmp_path: Path, visual: str
) -> None:
    assert load_rewrite_candidate(_write_candidate(tmp_path, visual=visual)).visual == visual


def test_candidate_rejects_unregistered_visual_target(tmp_path: Path) -> None:
    with pytest.raises(RewritePackageError, match="^rewrite-candidate-invalid$"):
        load_rewrite_candidate(_write_candidate(tmp_path, visual="cinematic-food"))


def test_review_requires_exact_skills_statuses_and_candidate_hash(tmp_path: Path) -> None:
    candidate_path = _write_candidate(tmp_path)
    candidate_sha256 = sha256_file(candidate_path)
    review = load_rewrite_review(
        _write_review(tmp_path, candidate_sha256), candidate_sha256
    )
    assert review.all_passed is True
    assert "private" not in repr(review)

    missing = {name: "pass" for name in REQUIRED_REVIEWS[:-1]}
    bad_path = _write_review(tmp_path, candidate_sha256, reviews=missing)
    with pytest.raises(RewritePackageError, match="^rewrite-review-invalid$"):
        load_rewrite_review(bad_path, candidate_sha256)

    bad_path.write_text(
        json.dumps(
            {
                "candidate_sha256": candidate_sha256,
                "reviewed_at": "2026-08-13T08:09:10Z",
                "reviews": {name: "approved" for name in REQUIRED_REVIEWS},
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RewritePackageError, match="^rewrite-review-invalid$"):
        load_rewrite_review(bad_path, candidate_sha256)

    good_path = _write_review(tmp_path, "0" * 64)
    with pytest.raises(RewritePackageError, match="^rewrite-review-candidate-mismatch$"):
        load_rewrite_review(good_path, candidate_sha256)


def test_review_can_load_revise_but_is_not_all_passed(tmp_path: Path) -> None:
    candidate_path = _write_candidate(tmp_path)
    digest = sha256_file(candidate_path)
    reviews = {name: "pass" for name in REQUIRED_REVIEWS}
    reviews["dbs-ai-check"] = "revise"
    review = load_rewrite_review(
        _write_review(tmp_path, digest, reviews=reviews), digest
    )
    assert review.all_passed is False


def test_prepare_rewrite_brief_binds_transcript_without_copying_text_to_summary(
    tmp_path: Path,
) -> None:
    url_file = tmp_path / "url.txt"
    url_file.write_text("https://example.invalid/item\n", encoding="utf-8")
    order = create_url_intake(
        tmp_path,
        url_file,
        authorized=True,
        now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        uuid_factory=lambda: __import__("uuid").UUID(WORK_ID),
    )
    transcript = _transcript_with("private source sentence must stay private")
    transcript_path = order.private_root / "transcript.json"
    transcript_sha = publish_json_exclusive(order.private_root, transcript_path, transcript)
    manifest_path = order.private_root / "transcript-manifest.json"
    manifest_sha = publish_json_exclusive(
        order.private_root,
        manifest_path,
        {
            "artifact": {
                "relative_path": "transcript.json",
                "sha256": transcript_sha,
                "size_bytes": transcript_path.stat().st_size,
            },
            "request_id": "safe-id",
            "schema_version": 1,
            "summary": {"duration": 1.0, "language": "zh-CN", "segment_count": 1},
            "upstream_sha256": "a" * 64,
            "work_id": WORK_ID,
        },
    )
    ledger = WashEventLedger(tmp_path)
    previous = ledger.current(WORK_ID)
    for stage in ("media_ready", "audio_ready"):
        event = StageEvent(
            2,
            WORK_ID,
            previous.source_id,
            previous.source_kind,
            stage,
            "ok",
            f"{stage}-manifest",
            "b" * 64,
            "2026-08-13T08:09:10Z",
            event_sha256(previous),
        )
        ledger.append(event)
        previous = event
    ledger.append(
        StageEvent(
            2,
            WORK_ID,
            previous.source_id,
            previous.source_kind,
            "transcript_ready",
            "ok",
            "transcript-manifest",
            manifest_sha,
            "2026-08-13T08:09:10Z",
            event_sha256(previous),
        )
    )

    result = prepare_rewrite_brief(
        tmp_path,
        WORK_ID,
        platform="douyin",
        duration_target_s=60,
        archive_slug="first-principles-rewrite",
    )

    brief = json.loads(result.path.read_text("utf-8"))
    assert brief["ratio"] == "16:9"
    assert brief["transcript_sha256"] == transcript_sha
    assert tuple(brief["required_reviews"]) == REQUIRED_REVIEWS
    assert "private source sentence" not in repr(result)
    assert "private source sentence" not in json.dumps(
        {key: value for key, value in brief.items() if key != "transcript_artifact"}
    )


def _article_ready(root: Path):
    url_file = root / "x-url.txt"
    url_file.write_text(
        "https://x.com/fixture_author/status/1234567890123456789\n",
        encoding="utf-8",
    )
    order = create_x_article_intake(
        root,
        url_file,
        authorized=True,
        now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        uuid_factory=lambda: __import__("uuid").UUID(WORK_ID),
    )
    article_root = order.private_root / "article-source"
    article_root.mkdir()
    article_json = article_root / "article.json"
    article_json_sha = publish_json_exclusive(
        order.private_root,
        article_json,
        {
            "author_handle": "@safe",
            "blocks": [{"text": "private article body", "type": "paragraph"}],
            "provider": "x-public-relay-html",
            "schema_version": 1,
            "title": "private title",
        },
    )
    article_md = article_root / "article.md"
    article_md.write_text("# private title\n\nprivate article body\n", encoding="utf-8")
    manifest = order.private_root / "x-article-manifest.json"
    manifest_sha = publish_json_exclusive(
        order.private_root,
        manifest,
        {
            "article_json_sha256": article_json_sha,
            "article_markdown_sha256": sha256_file(article_md),
            "assets": [],
            "provider": "x-public-relay-html",
            "schema_version": 1,
            "source_input_sha256": order.source_input_sha256,
            "work_id": WORK_ID,
        },
    )
    previous = WashEventLedger(root).current(WORK_ID)
    WashEventLedger(root).append(
        StageEvent(
            2,
            WORK_ID,
            previous.source_id,
            "x-article",
            "article_ready",
            "ok",
            "x-article-manifest",
            manifest_sha,
            "2026-08-13T08:09:10Z",
            event_sha256(previous),
        )
    )
    return order, article_md


def test_prepare_rewrite_brief_accepts_hash_bound_article_without_fake_asr_fields(
    tmp_path: Path,
) -> None:
    order, article_md = _article_ready(tmp_path)

    result = prepare_rewrite_brief(
        tmp_path,
        WORK_ID,
        platform="douyin",
        duration_target_s=60,
        archive_slug="article-rewrite",
    )

    brief = json.loads(result.path.read_text("utf-8"))
    assert brief["schema_version"] == 2
    assert brief["source_kind"] == "x-article"
    assert brief["source_artifact"] == "article-source/article.md"
    assert brief["source_sha256"] == sha256_file(article_md)
    assert brief["source_manifest_sha256"] == sha256_file(
        order.private_root / "x-article-manifest.json"
    )
    assert not ({"request_id", "transcript_artifact", "transcript_sha256"} & set(brief))


def test_prepare_rewrite_brief_rejects_replaced_article_markdown(tmp_path: Path) -> None:
    _, article_md = _article_ready(tmp_path)
    article_md.write_text("replaced", encoding="utf-8")

    with pytest.raises(RewritePackageError, match="^rewrite-article-invalid$"):
        prepare_rewrite_brief(
            tmp_path,
            WORK_ID,
            platform="douyin",
            duration_target_s=60,
            archive_slug="article-rewrite",
        )


def test_legacy_candidate_ratio_remains_readable(tmp_path: Path) -> None:
    candidate = load_rewrite_candidate(_write_candidate(tmp_path, ratio="9:16"))

    assert candidate.ratio == "9:16"

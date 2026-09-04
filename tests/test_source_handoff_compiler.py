from __future__ import annotations

import json
import importlib.util
import wave
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.video.content_plan import parse_handoff_segments
from boomearth.workbench.handoff import validate_public_handoff
from boomearth.workbench import handoff_compiler
from boomearth.workbench.handoff_compiler import (
    HandoffCompilerError,
    compile_source_handoff,
    recover_handoff_receipt,
    render_source_free_handoff,
)
from boomearth.workbench.rewrite_package import (
    REQUIRED_REVIEWS,
    load_rewrite_candidate,
    prepare_rewrite_brief,
)
from boomearth.workbench.source_artifacts import publish_json_exclusive, sha256_file
from boomearth.workbench.source_intake import create_local_intake, create_x_article_intake
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.production_policy import apply_horizontal_production_policy
from boomearth.workbench.source_ledger import (
    StageEvent,
    WashEventLedger,
    event_sha256,
)


WORK_ID = "00000000-0000-4000-8000-000000000007"
FIXED_UUID = UUID(WORK_ID)
REGISTERED_HANDOFF_TARGETS = (
    ("xiaohei-white-first-v1", "katerj-xiaohei-illustrations"),
    ("editorial-motion-v2", "ra-video-illustrations"),
    ("semantic-handdrawn-v3", "ra-video-illustrations"),
    ("semantic-handdrawn-v3/type-led", "ra-video-illustrations"),
    ("vivid-comic-explainer", "ra-video-illustrations"),
    ("engineering-sketch-explainer", "ra-video-illustrations"),
    ("four-panel-comic-explainer", "ra-video-illustrations"),
    ("blue-black-whiteboard-explainer", "ra-video-illustrations"),
    ("xiaohuang-warm-first-v1", "ra-video-illustrations"),
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


def _publish_stage(
    root: Path,
    previous: StageEvent,
    stage: str,
    label: str,
    digest: str,
) -> StageEvent:
    event = StageEvent(
        2,
        WORK_ID,
        previous.source_id,
        previous.source_kind,
        stage,
        "ok",
        label,
        digest,
        "2026-08-13T09:10:11Z",
        event_sha256(previous),
    )
    WashEventLedger(root).append(event)
    return event


def _complete_rewrite_work(
    root: Path,
    *,
    source_text: str = "来源内容从最基本事实重新拆解复杂问题并判断哪些条件不能省略",
    candidate_text: str = "先别急着接受现成答案，我们可以换个角度逐步确认真正重要的条件。",
    review_status: str = "pass",
    ratio: str = "16:9",
) -> tuple[Path, Path]:
    source = root / "authorized" / "source.wav"
    source.parent.mkdir(parents=True)
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\0\0" * 16_000)
    order = create_local_intake(
        root,
        source,
        authorized=True,
        now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        uuid_factory=lambda: FIXED_UUID,
    )
    ledger = WashEventLedger(root)
    previous = ledger.current(WORK_ID)

    media = order.private_root / "source-media" / "original.wav"
    media.parent.mkdir()
    media.write_bytes(source.read_bytes())
    media_manifest = order.private_root / "source-media-manifest.json"
    media_manifest_sha = publish_json_exclusive(
        order.private_root,
        media_manifest,
        {
            "artifact": {
                "relative_path": "source-media/original.wav",
                "sha256": sha256_file(media),
                "size_bytes": media.stat().st_size,
            },
            "metadata": {
                "audio_streams": 1,
                "container": "wav",
                "duration_s": 1.0,
                "video_streams": 0,
            },
            "schema_version": 1,
            "source_input_sha256": order.source_input_sha256,
            "work_id": WORK_ID,
        },
    )
    previous = _publish_stage(
        root, previous, "media_ready", "source-media-manifest", media_manifest_sha
    )

    audio = order.private_root / "source-audio-16k-mono.wav"
    audio.write_bytes(source.read_bytes())
    audio_manifest = order.private_root / "source-audio-manifest.json"
    audio_manifest_sha = publish_json_exclusive(
        order.private_root,
        audio_manifest,
        {
            "artifact": {
                "relative_path": "source-audio-16k-mono.wav",
                "sha256": sha256_file(audio),
                "size_bytes": audio.stat().st_size,
            },
            "format": {
                "bits_per_sample": 16,
                "channels": 1,
                "duration_s": 1.0,
                "sample_rate": 16000,
            },
            "schema_version": 1,
            "upstream_sha256": media_manifest_sha,
            "work_id": WORK_ID,
        },
    )
    previous = _publish_stage(
        root, previous, "audio_ready", "source-audio-manifest", audio_manifest_sha
    )

    transcript = order.private_root / "transcript.json"
    transcript_sha = publish_json_exclusive(
        order.private_root,
        transcript,
        {
            "duration": 1.0,
            "language": "zh-CN",
            "sentences": [{"text": source_text}],
            "source_id": "safe-id",
        },
    )
    transcript_manifest = order.private_root / "transcript-manifest.json"
    transcript_manifest_sha = publish_json_exclusive(
        order.private_root,
        transcript_manifest,
        {
            "artifact": {
                "relative_path": "transcript.json",
                "sha256": transcript_sha,
                "size_bytes": transcript.stat().st_size,
            },
            "request_id": "safe-id",
            "schema_version": 1,
            "summary": {"duration": 1.0, "language": "zh-CN", "segment_count": 1},
            "upstream_sha256": audio_manifest_sha,
            "work_id": WORK_ID,
        },
    )
    _publish_stage(
        root,
        previous,
        "transcript_ready",
        "transcript-manifest",
        transcript_manifest_sha,
    )
    prepare_rewrite_brief(
        root,
        WORK_ID,
        platform="douyin",
        duration_target_s=60,
        archive_slug="source-free-project",
    )
    if ratio != "16:9":
        brief_path = order.private_root / "rewrite-brief.json"
        brief = json.loads(brief_path.read_text("utf-8"))
        brief["ratio"] = ratio
        brief_path.write_text(
            json.dumps(brief, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
    candidate = order.private_root / "rewrite-candidate.json"
    publish_json_exclusive(
        order.private_root,
        candidate,
        {
            "archive_slug": "source-free-project",
            "duration_target_s": 60,
            "illustration_skill": "katerj-xiaohei-illustrations",
            "platform": "douyin",
            "ratio": ratio,
            "schema_version": 1,
            "segments": [
                {
                    "segment_id": "segment-001",
                    "text": candidate_text,
                    "visual_intent": "人物逐层拆开复杂结构",
                },
                {
                    "segment_id": "segment-002",
                    "text": "确认事实以后，再用这些事实重新拼出自己的判断。",
                    "visual_intent": "人物重新组合清晰积木",
                },
            ],
            "title_candidates": ["别急着找答案，先确认什么是真的"],
            "visual": "default",
            "work_id": WORK_ID,
        },
    )
    review = order.private_root / "rewrite-review.json"
    publish_json_exclusive(
        order.private_root,
        review,
        {
            "candidate_sha256": sha256_file(candidate),
            "reviewed_at": "2026-08-13T09:10:11Z",
            "reviews": {name: review_status for name in REQUIRED_REVIEWS},
            "schema_version": 1,
        },
    )
    return candidate, review


def _complete_article_rewrite_work(root: Path) -> tuple[Path, Path]:
    url_file = root / "private-x.txt"
    url_file.write_text(
        "https://x.com/fixture_author/status/1234567890123456789\n",
        encoding="utf-8",
    )
    order = create_x_article_intake(
        root,
        url_file,
        authorized=True,
        now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        uuid_factory=lambda: FIXED_UUID,
    )
    article_root = order.private_root / "article-source"
    article_root.mkdir()
    article_json = article_root / "article.json"
    article_json_sha = publish_json_exclusive(
        order.private_root,
        article_json,
        {
            "author_handle": "@safe",
            "blocks": [{"text": "原文讨论一种完全不同的抽象问题", "type": "paragraph"}],
            "provider": "x-public-relay-html",
            "schema_version": 1,
            "title": "安全标题",
        },
    )
    article_md = article_root / "article.md"
    article_md.write_text("# 安全标题\n\n原文讨论一种完全不同的抽象问题\n", encoding="utf-8")
    manifest_path = order.private_root / "x-article-manifest.json"
    manifest_sha = publish_json_exclusive(
        order.private_root,
        manifest_path,
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
    _publish_stage(root, previous, "article_ready", "x-article-manifest", manifest_sha)
    prepare_rewrite_brief(
        root,
        WORK_ID,
        platform="douyin",
        duration_target_s=60,
        archive_slug="article-source-free-project",
    )
    candidate = order.private_root / "rewrite-candidate.json"
    publish_json_exclusive(
        order.private_root,
        candidate,
        {
            "archive_slug": "article-source-free-project",
            "duration_target_s": 60,
            "illustration_skill": "ra-video-illustrations",
            "platform": "douyin",
            "ratio": "16:9",
            "schema_version": 1,
            "segments": [
                {
                    "segment_id": "segment-001",
                    "text": "先从观众真正遇到的困难出发，再重新组织一套可以落地的判断。",
                    "visual_intent": "清晰路径逐步展开",
                }
            ],
            "title_candidates": ["换一个起点，结论会更清楚"],
            "visual": "editorial-motion-v2",
            "work_id": WORK_ID,
        },
    )
    review = order.private_root / "rewrite-review.json"
    publish_json_exclusive(
        order.private_root,
        review,
        {
            "candidate_sha256": sha256_file(candidate),
            "reviewed_at": "2026-08-13T09:10:11Z",
            "reviews": {name: "pass" for name in REQUIRED_REVIEWS},
            "schema_version": 1,
        },
    )
    return candidate, review


def _upgrade_rewrite_to_opening_contract_v2(
    candidate: Path,
    review: Path,
    *,
    review_schema_version: int,
) -> None:
    candidate_value = json.loads(candidate.read_text("utf-8"))
    candidate_value["schema_version"] = 2
    candidate_value["segments"] = [
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
    candidate_value["opening_contract"] = {
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
    candidate.write_text(
        json.dumps(candidate_value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    review_value = json.loads(review.read_text("utf-8"))
    review_value["candidate_sha256"] = sha256_file(candidate)
    review_value["schema_version"] = review_schema_version
    review.write_text(
        json.dumps(review_value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _complete_manual_text_rewrite_work(root: Path) -> tuple[Path, Path]:
    source = root / "authorized" / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_text(
        "这是一份由用户直接粘贴的教程正文，界面图片没有随正文一起复制。",
        encoding="utf-8",
    )
    order = create_local_intake(
        root,
        source,
        authorized=True,
        now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        uuid_factory=lambda: FIXED_UUID,
    )
    private_root = order.private_root
    private_source = private_root / "source-text.txt"
    private_source.write_bytes(source.read_bytes())
    source_sha = sha256_file(private_source)
    previous = WashEventLedger(root).current(WORK_ID)

    media_manifest = private_root / "media-manifest.json"
    media_manifest_sha = publish_json_exclusive(
        private_root,
        media_manifest,
        {
            "artifact": "source-text.txt",
            "mode": "manual-text-source",
            "schema_version": 1,
            "source_sha256": source_sha,
        },
    )
    previous = _publish_stage(
        root, previous, "media_ready", "manual-text-source", media_manifest_sha
    )

    audio_manifest = private_root / "audio-manifest.json"
    audio_manifest_sha = publish_json_exclusive(
        private_root,
        audio_manifest,
        {
            "mode": "manual-text-no-audio",
            "schema_version": 1,
            "source_sha256": source_sha,
            "transcription_required": False,
        },
    )
    previous = _publish_stage(
        root, previous, "audio_ready", "manual-text-no-audio", audio_manifest_sha
    )

    transcript = private_root / "transcript.json"
    transcript_sha = publish_json_exclusive(
        private_root,
        transcript,
        {
            "duration": 0.0,
            "language": "zh-CN",
            "sentences": [{"text": private_source.read_text("utf-8")}],
            "source_id": source_sha,
        },
    )
    transcript_manifest = private_root / "transcript-manifest.json"
    transcript_manifest_sha = publish_json_exclusive(
        private_root,
        transcript_manifest,
        {
            "artifact": {
                "relative_path": "transcript.json",
                "sha256": transcript_sha,
                "size_bytes": transcript.stat().st_size,
            },
            "request_id": "manual-user-paste",
            "schema_version": 1,
            "summary": {"duration": 0.0, "language": "zh-CN", "segment_count": 1},
            "upstream_sha256": source_sha,
            "work_id": WORK_ID,
        },
    )
    _publish_stage(
        root,
        previous,
        "transcript_ready",
        "transcript-manifest",
        transcript_manifest_sha,
    )
    prepare_rewrite_brief(
        root,
        WORK_ID,
        platform="douyin",
        duration_target_s=60,
        archive_slug="manual-text-source-free-project",
    )
    candidate = private_root / "rewrite-candidate.json"
    publish_json_exclusive(
        private_root,
        candidate,
        {
            "archive_slug": "manual-text-source-free-project",
            "duration_target_s": 60,
            "illustration_skill": "katerj-xiaohei-illustrations",
            "platform": "douyin",
            "ratio": "16:9",
            "schema_version": 1,
            "segments": [
                {
                    "segment_id": "segment-001",
                    "text": "先认清工作区和权限，再决定要不要增加复杂扩展。",
                    "visual_intent": "小黑人物先圈定工作边界再打开工具箱",
                }
            ],
            "title_candidates": ["先把基础边界认清，再谈高级功能"],
            "visual": "default",
            "work_id": WORK_ID,
        },
    )
    review = private_root / "rewrite-review.json"
    publish_json_exclusive(
        private_root,
        review,
        {
            "candidate_sha256": sha256_file(candidate),
            "reviewed_at": "2026-08-13T09:10:11Z",
            "reviews": {name: "pass" for name in REQUIRED_REVIEWS},
            "schema_version": 1,
        },
    )
    return candidate, review


def test_article_ready_chain_compiles_without_fake_transcript_provenance(
    tmp_path: Path,
) -> None:
    candidate, review = _complete_article_rewrite_work(tmp_path)

    publication = compile_source_handoff(
        tmp_path, WORK_ID, candidate, review
    )

    receipt = json.loads((_work_root(tmp_path) / "publication-receipt.json").read_text("utf-8"))
    assert publication.relative_path.endswith("交接稿.md")
    assert receipt["schema_version"] == 2
    assert receipt["source_kind"] == "x-article"
    assert "transcript_manifest_sha256" not in receipt
    assert "source_audio_manifest_sha256" not in receipt
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_manual_text_chain_compiles_without_fake_media_or_audio(
    tmp_path: Path,
) -> None:
    candidate, review = _complete_manual_text_rewrite_work(tmp_path)

    publication = compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "manual-text-source-free-project"
        / "交接稿.md"
    )
    assert publication.handoff_sha256 == sha256_file(handoff)
    assert validate_public_handoff(handoff.read_text("utf-8")) == []
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_compiler_accepts_ten_reviewed_title_candidates(tmp_path: Path) -> None:
    candidate, review = _complete_manual_text_rewrite_work(tmp_path)
    candidate_value = json.loads(candidate.read_text("utf-8"))
    candidate_value["title_candidates"] = [
        f"第{index}个合规候选标题，讲清工具选择"
        for index in range(1, 11)
    ]
    candidate.write_text(
        json.dumps(candidate_value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    review_value = json.loads(review.read_text("utf-8"))
    review_value["candidate_sha256"] = sha256_file(candidate)
    review.write_text(
        json.dumps(review_value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    publication = compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "manual-text-source-free-project"
        / "交接稿.md"
    )
    assert "第10个合规候选标题，讲清工具选择" in handoff.read_text("utf-8")


def test_compiler_publishes_p1_compatible_source_free_handoff(tmp_path: Path) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)

    publication = compile_source_handoff(
        tmp_path, WORK_ID, candidate, review
    )

    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "source-free-project"
        / "交接稿.md"
    )
    text = handoff.read_text("utf-8")
    assert validate_public_handoff(text) == []
    assert [segment.id for segment in parse_handoff_segments(text)] == [
        "segment-001",
        "segment-002",
    ]
    assert publication.handoff_sha256 == sha256_file(handoff)
    assert publication.relative_path == "待制作/source-free-project/交接稿.md"
    assert 'ratio: "16:9"' in text
    assert 'voice: "user-indextts2-black-gold-v3"' in text
    assert 'covers: "punk-cover-giant-title-3x4-v1"' in text
    assert "来源内容从最基本事实" not in text
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_compiler_requires_schema_two_review_for_opening_contract_v2(
    tmp_path: Path,
) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    _upgrade_rewrite_to_opening_contract_v2(
        candidate,
        review,
        review_schema_version=1,
    )

    with pytest.raises(
        HandoffCompilerError, match="^rewrite-review-not-passed$"
    ):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)


def test_compiler_publishes_opening_contract_v2_without_private_metadata(
    tmp_path: Path,
) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    _upgrade_rewrite_to_opening_contract_v2(
        candidate,
        review,
        review_schema_version=2,
    )

    compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "source-free-project"
        / "交接稿.md"
    ).read_text("utf-8")
    assert "智能助手最容易踩坑的不是提示词" in handoff
    assert "opening_contract" not in handoff
    assert "jl-multiplatform-titles" not in handoff


def test_compiler_resolves_default_visual_to_xiaohei(tmp_path: Path) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)

    compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "source-free-project"
        / "交接稿.md"
    ).read_text("utf-8")
    assert 'visual: "xiaohei-white-first-v1"' in handoff
    assert 'illustration_skill: "katerj-xiaohei-illustrations"' in handoff
    assert 'visual: "default"' not in handoff


def test_handoff_renderer_rejects_unregistered_visual(tmp_path: Path) -> None:
    candidate_path, _review = _complete_rewrite_work(tmp_path)
    candidate = load_rewrite_candidate(candidate_path)
    with pytest.raises(HandoffCompilerError, match="^rewrite-visual-invalid$"):
        render_source_free_handoff(replace(candidate, visual="cinematic-food"))


@pytest.mark.parametrize(("visual", "skill"), REGISTERED_HANDOFF_TARGETS)
def test_handoff_renderer_preserves_registered_visual_and_canonical_skill(
    tmp_path: Path, visual: str, skill: str
) -> None:
    candidate_path, _review = _complete_rewrite_work(tmp_path)
    candidate = load_rewrite_candidate(candidate_path)

    handoff = render_source_free_handoff(
        replace(candidate, visual=visual, illustration_skill="stale-caller-value")
    )

    assert f'visual: "{visual}"' in handoff
    assert f'illustration_skill: "{skill}"' in handoff


def test_compiler_rejects_legacy_ratio_before_publication(tmp_path: Path) -> None:
    candidate, review = _complete_rewrite_work(tmp_path, ratio="9:16")

    with pytest.raises(
        HandoffCompilerError, match="^rewrite-production-ratio-invalid$"
    ):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    assert not (WorkbenchPaths(tmp_path).pending / "source-free-project").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "transcript_ready"


def test_compiler_rejects_source_overlap_with_redacted_line_only(tmp_path: Path) -> None:
    source = "第一性原理不是口号而是把问题拆回最基本的事实"
    candidate, review = _complete_rewrite_work(
        tmp_path,
        source_text=source,
        candidate_text="第一性原理，不是口号，而是把问题拆回最基本的事实。",
    )

    with pytest.raises(HandoffCompilerError) as raised:
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    assert str(raised.value) == "line 1: source overlap"
    assert source not in repr(raised.value)


def test_compiler_requires_every_review_to_pass(tmp_path: Path) -> None:
    candidate, review = _complete_rewrite_work(tmp_path, review_status="revise")
    with pytest.raises(HandoffCompilerError, match="^rewrite-review-not-passed$"):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)


def test_compiler_applies_source_overlap_gate_to_public_titles(tmp_path: Path) -> None:
    source = "第一性原理不是口号而是把问题拆回最基本的事实"
    candidate, review = _complete_rewrite_work(tmp_path, source_text=source)
    value = json.loads(candidate.read_text("utf-8"))
    value["title_candidates"] = [
        "第一性原理，不是口号，而是把问题拆回最基本的事实。"
    ]
    candidate.unlink()
    publish_json_exclusive(_work_root(tmp_path), candidate, value)
    review_value = json.loads(review.read_text("utf-8"))
    review_value["candidate_sha256"] = sha256_file(candidate)
    review.unlink()
    publish_json_exclusive(_work_root(tmp_path), review, review_value)

    with pytest.raises(HandoffCompilerError, match="^line 1: source overlap$"):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)


def test_compiler_never_overwrites_existing_public_project(tmp_path: Path) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    target = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "source-free-project"
    )
    target.mkdir(parents=True)
    foreign = target / "foreign.txt"
    foreign.write_bytes(b"foreign")

    with pytest.raises(HandoffCompilerError, match="^handoff-target-exists$"):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)
    assert foreign.read_bytes() == b"foreign"


def test_recovery_writes_receipt_only_for_exact_existing_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    publish = handoff_compiler.publish_json_exclusive

    def fail_receipt(root: Path, target: Path, value: object) -> str:
        if target.name == "publication-receipt.json":
            raise handoff_compiler.SourceContractError("artifact-unavailable")
        return publish(root, target, value)

    monkeypatch.setattr(handoff_compiler, "publish_json_exclusive", fail_receipt)
    with pytest.raises(HandoffCompilerError, match="^handoff-receipt-unavailable$"):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "source-free-project"
        / "交接稿.md"
    )
    original = handoff.read_bytes()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "rewrite_ready"

    monkeypatch.setattr(handoff_compiler, "publish_json_exclusive", publish)
    publication = recover_handoff_receipt(tmp_path, WORK_ID)

    assert handoff.read_bytes() == original
    assert publication.handoff_sha256 == sha256_file(handoff)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_recovery_rejects_changed_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    publish = handoff_compiler.publish_json_exclusive

    def fail_receipt(root: Path, target: Path, value: object) -> str:
        if target.name == "publication-receipt.json":
            raise handoff_compiler.SourceContractError("artifact-unavailable")
        return publish(root, target, value)

    monkeypatch.setattr(handoff_compiler, "publish_json_exclusive", fail_receipt)
    with pytest.raises(HandoffCompilerError):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)
    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "source-free-project"
        / "交接稿.md"
    )
    handoff.write_text(handoff.read_text("utf-8") + "\nchanged\n", encoding="utf-8")
    monkeypatch.setattr(handoff_compiler, "publish_json_exclusive", publish)

    with pytest.raises(HandoffCompilerError, match="^handoff-recovery-invalid$"):
        recover_handoff_receipt(tmp_path, WORK_ID)


def test_compiler_rejects_upstream_manifest_mutation(tmp_path: Path) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    manifest = _work_root(tmp_path) / "source-audio-manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")

    with pytest.raises(HandoffCompilerError, match="^handoff-chain-invalid$"):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)


def test_recovery_can_append_missing_ledger_event_for_existing_valid_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    append = handoff_compiler._append_handoff_ready

    def fail_append(*_args, **_kwargs):
        raise HandoffCompilerError("handoff-ledger-invalid")

    monkeypatch.setattr(handoff_compiler, "_append_handoff_ready", fail_append)
    with pytest.raises(HandoffCompilerError, match="^handoff-ledger-invalid$"):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)
    assert (_work_root(tmp_path) / "publication-receipt.json").is_file()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "rewrite_ready"

    monkeypatch.setattr(handoff_compiler, "_append_handoff_ready", append)
    recover_handoff_receipt(tmp_path, WORK_ID)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_compile_source_handoff_cli_reports_only_safe_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    script_path = (
        Path(__file__).parents[1]
        / "automation"
        / "scripts"
        / "compile_source_handoff.py"
    )
    spec = importlib.util.spec_from_file_location("compile_source_handoff_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(
        [
            "compile",
            WORK_ID,
            "--candidate",
            str(candidate),
            "--review",
            str(review),
        ],
        root=tmp_path,
    ) == 0

    captured = capsys.readouterr()
    assert "stage=handoff_ready status=created" in captured.out
    assert "来源内容" not in captured.out
    assert captured.err == ""


def test_legacy_compile_cannot_cross_production_started_into_a_revision(
    tmp_path: Path,
) -> None:
    candidate, review = _complete_rewrite_work(tmp_path)
    compile_source_handoff(tmp_path, WORK_ID, candidate, review)
    paths = WorkbenchPaths(tmp_path)
    paths.active.mkdir(parents=True)
    pending = paths.pending / "source-free-project"
    active = paths.active / "source-free-project"
    pending.rename(active)
    handoff = active / "交接稿.md"
    handoff.write_text(
        handoff.read_text("utf-8").replace(
            'status: "待制作"', 'status: "制作中"', 1
        ),
        encoding="utf-8",
        newline="",
    )
    apply_horizontal_production_policy(
        tmp_path, WORK_ID, "source-free-project"
    )

    with pytest.raises(HandoffCompilerError, match="^handoff-chain-invalid$"):
        compile_source_handoff(tmp_path, WORK_ID, candidate, review)

    private_root = _work_root(tmp_path)
    assert not (private_root / "rewrite-brief-v2.json").exists()
    assert not (private_root / "publication-revision-v2.json").exists()

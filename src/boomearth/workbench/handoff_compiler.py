"""Deterministic source-free handoff publication and receipt recovery."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from boomearth.audio.indextts2 import CURRENT_VOICE_ID
from boomearth.video.content_plan import ContentPlanError, parse_handoff_segments
from boomearth.video.cover_contracts import DEFAULT_PUNK_COVER_CONTRACT
from boomearth.video.illustration_themes import (
    IllustrationThemeError,
    resolve_visual_style,
)
from boomearth.workbench.handoff import validate_public_handoff
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.rewrite_package import (
    LEGACY_REQUIRED_REVIEWS,
    PRODUCTION_RATIO,
    REQUIRED_REVIEWS,
    RewriteCandidate,
    RewritePackageError,
    RewriteSegment,
    _verified_rewrite_source,
    find_source_overlap_lines,
    load_rewrite_candidate,
    load_rewrite_review,
    reading_unit_count,
)
from boomearth.workbench.source_artifacts import (
    SourceContractError,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import (
    SourceLedgerError,
    StageEvent,
    WashEventLedger,
    event_sha256,
)


_MEDIA_MANIFEST_KEYS = frozenset(
    {"artifact", "metadata", "schema_version", "source_input_sha256", "work_id"}
)
_AUDIO_MANIFEST_KEYS = frozenset(
    {"artifact", "format", "schema_version", "upstream_sha256", "work_id"}
)
_MANUAL_MEDIA_MANIFEST_KEYS = frozenset(
    {"artifact", "mode", "schema_version", "source_sha256"}
)
_MANUAL_AUDIO_MANIFEST_KEYS = frozenset(
    {"mode", "schema_version", "source_sha256", "transcription_required"}
)
_TRANSCRIPT_MANIFEST_KEYS = frozenset(
    {"artifact", "request_id", "schema_version", "summary", "upstream_sha256", "work_id"}
)
_BRIEF_KEYS = frozenset(
    {
        "archive_slug",
        "content_goal",
        "duration_target_s",
        "platform",
        "ratio",
        "required_reviews",
        "schema_version",
        "transcript_artifact",
        "transcript_manifest_sha256",
        "transcript_sha256",
        "work_id",
    }
)
_ARTICLE_BRIEF_KEYS = frozenset(
    {
        "archive_slug",
        "content_goal",
        "duration_target_s",
        "platform",
        "ratio",
        "required_reviews",
        "schema_version",
        "source_artifact",
        "source_kind",
        "source_manifest_sha256",
        "source_sha256",
        "work_id",
    }
)
_TRANSCRIPT_KEYS = frozenset({"duration", "language", "sentences", "source_id"})
_RECEIPT_KEYS = frozenset(
    {
        "archive_slug",
        "candidate_sha256",
        "handoff",
        "review_sha256",
        "rewrite_brief_sha256",
        "schema_version",
        "source_audio_manifest_sha256",
        "source_media_manifest_sha256",
        "transcript_manifest_sha256",
        "work_id",
    }
)
_ARTICLE_RECEIPT_KEYS = frozenset(
    {
        "archive_slug",
        "candidate_sha256",
        "handoff",
        "review_sha256",
        "rewrite_brief_sha256",
        "schema_version",
        "source_artifact_sha256",
        "source_kind",
        "source_manifest_sha256",
        "work_id",
    }
)


class HandoffCompilerError(RuntimeError):
    """A source-redacted handoff compilation failure."""

    def __repr__(self) -> str:
        return "HandoffCompilerError(<redacted>)"


def _valid_required_reviews(value: object) -> bool:
    return isinstance(value, list) and tuple(value) in {
        tuple(LEGACY_REQUIRED_REVIEWS),
        tuple(REQUIRED_REVIEWS),
    }


@dataclass(frozen=True, slots=True, repr=False)
class HandoffPublication:
    work_id: str
    archive_slug: str
    handoff_sha256: str
    relative_path: str

    def __repr__(self) -> str:
        return "HandoffPublication(<redacted>)"


@dataclass(frozen=True, slots=True)
class _Chain:
    private_root: Path
    source_kind: str
    source_media_manifest_sha256: str
    source_audio_manifest_sha256: str
    transcript_manifest_sha256: str
    transcript_sha256: str
    rewrite_brief_sha256: str
    transcript: dict[str, object]
    brief: dict[str, object]
    current: StageEvent


def _artifact(
    private_root: Path,
    value: object,
    *,
    expected_relative: str,
) -> Path:
    if not (
        isinstance(value, dict)
        and set(value) == {"relative_path", "sha256", "size_bytes"}
        and value["relative_path"] == expected_relative
        and isinstance(value["sha256"], str)
        and len(value["sha256"]) == 64
        and type(value["size_bytes"]) is int
        and value["size_bytes"] > 0
    ):
        raise ValueError
    path = verify_private_relative(private_root, expected_relative)
    if sha256_file(path) != value["sha256"] or path.stat().st_size != value["size_bytes"]:
        raise ValueError
    return path


def _verified_manual_text_chain(
    order,
    current: StageEvent,
    work_id: str,
) -> _Chain:
    private_root = order.private_root
    media_path = verify_private_relative(private_root, "media-manifest.json")
    audio_path = verify_private_relative(private_root, "audio-manifest.json")
    transcript_manifest_path = verify_private_relative(
        private_root, "transcript-manifest.json"
    )
    brief_path = verify_private_relative(private_root, "rewrite-brief.json")
    media_sha = sha256_file(media_path)
    audio_sha = sha256_file(audio_path)
    transcript_manifest_sha = sha256_file(transcript_manifest_path)
    brief_sha = sha256_file(brief_path)
    media = load_exact_json(media_path, _MANUAL_MEDIA_MANIFEST_KEYS)
    audio = load_exact_json(audio_path, _MANUAL_AUDIO_MANIFEST_KEYS)
    transcript_manifest = load_exact_json(
        transcript_manifest_path, _TRANSCRIPT_MANIFEST_KEYS
    )
    brief = load_exact_json(brief_path, _BRIEF_KEYS)
    source_path = verify_private_relative(private_root, "source-text.txt")
    source_sha = sha256_file(source_path)
    transcript_summary = transcript_manifest["summary"]
    if not (
        order.source_kind == "local"
        and current.source_kind == "local"
        and media["schema_version"] == 1
        and media["artifact"] == "source-text.txt"
        and media["mode"] == "manual-text-source"
        and media["source_sha256"] == source_sha
        and source_sha == order.source_input_sha256
        and audio["schema_version"] == 1
        and audio["mode"] == "manual-text-no-audio"
        and audio["source_sha256"] == source_sha
        and audio["transcription_required"] is False
        and transcript_manifest["schema_version"] == 1
        and transcript_manifest["work_id"] == work_id
        and transcript_manifest["request_id"] == "manual-user-paste"
        and transcript_manifest["upstream_sha256"] == source_sha
        and brief["schema_version"] == 1
        and brief["work_id"] == work_id
        and brief["transcript_artifact"] == "transcript.json"
        and brief["transcript_manifest_sha256"] == transcript_manifest_sha
        and (
            current.stage != "transcript_ready"
            or current.artifact_sha256 == transcript_manifest_sha
        )
        and _valid_required_reviews(brief["required_reviews"])
        and brief["content_goal"]
        == "create a source-free original production script"
        and isinstance(transcript_summary, dict)
        and set(transcript_summary) == {"duration", "language", "segment_count"}
        and transcript_summary["duration"] == 0.0
        and transcript_summary["language"] == "zh-CN"
        and type(transcript_summary["segment_count"]) is int
        and transcript_summary["segment_count"] > 0
    ):
        raise ValueError
    transcript_path = _artifact(
        private_root,
        transcript_manifest["artifact"],
        expected_relative="transcript.json",
    )
    transcript_sha = sha256_file(transcript_path)
    if brief["transcript_sha256"] != transcript_sha:
        raise ValueError
    transcript = load_exact_json(transcript_path, _TRANSCRIPT_KEYS)
    if not (
        transcript["duration"] == 0.0
        and transcript["language"] == "zh-CN"
        and transcript["source_id"] == source_sha
        and isinstance(transcript["sentences"], list)
        and len(transcript["sentences"]) == transcript_summary["segment_count"]
        and all(
            isinstance(item, dict)
            and set(item) == {"text"}
            and isinstance(item["text"], str)
            and bool(item["text"].strip())
            for item in transcript["sentences"]
        )
    ):
        raise ValueError
    return _Chain(
        private_root,
        "transcript",
        media_sha,
        audio_sha,
        transcript_manifest_sha,
        transcript_sha,
        brief_sha,
        transcript,
        brief,
        current,
    )


def _verified_transcript_chain(root: Path, work_id: str) -> _Chain:
    try:
        order = load_work_order(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if current.stage not in {"transcript_ready", "rewrite_ready", "handoff_ready"}:
            raise ValueError
        private_root = order.private_root
        if (private_root / "media-manifest.json").exists():
            return _verified_manual_text_chain(order, current, work_id)
        media_path = verify_private_relative(private_root, "source-media-manifest.json")
        audio_path = verify_private_relative(private_root, "source-audio-manifest.json")
        transcript_manifest_path = verify_private_relative(
            private_root, "transcript-manifest.json"
        )
        brief_path = verify_private_relative(private_root, "rewrite-brief.json")
        media_sha = sha256_file(media_path)
        audio_sha = sha256_file(audio_path)
        transcript_manifest_sha = sha256_file(transcript_manifest_path)
        brief_sha = sha256_file(brief_path)
        media = load_exact_json(media_path, _MEDIA_MANIFEST_KEYS)
        audio = load_exact_json(audio_path, _AUDIO_MANIFEST_KEYS)
        transcript_manifest = load_exact_json(
            transcript_manifest_path, _TRANSCRIPT_MANIFEST_KEYS
        )
        brief = load_exact_json(brief_path, _BRIEF_KEYS)
        media_metadata = media["metadata"]
        audio_format = audio["format"]
        transcript_summary = transcript_manifest["summary"]
        if not (
            media["schema_version"] == 1
            and media["work_id"] == work_id
            and media["source_input_sha256"] == order.source_input_sha256
            and audio["schema_version"] == 1
            and audio["work_id"] == work_id
            and audio["upstream_sha256"] == media_sha
            and transcript_manifest["schema_version"] == 1
            and transcript_manifest["work_id"] == work_id
            and transcript_manifest["upstream_sha256"] == audio_sha
            and brief["schema_version"] == 1
            and brief["work_id"] == work_id
            and brief["transcript_artifact"] == "transcript.json"
            and brief["transcript_manifest_sha256"] == transcript_manifest_sha
            and (
                current.stage != "transcript_ready"
                or current.artifact_sha256 == transcript_manifest_sha
            )
            and _valid_required_reviews(brief["required_reviews"])
            and brief["content_goal"]
            == "create a source-free original production script"
            and isinstance(media_metadata, dict)
            and set(media_metadata)
            == {"audio_streams", "container", "duration_s", "video_streams"}
            and type(media_metadata["audio_streams"]) is int
            and media_metadata["audio_streams"] >= 1
            and isinstance(media_metadata["container"], str)
            and type(media_metadata["duration_s"]) in {int, float}
            and media_metadata["duration_s"] > 0
            and type(media_metadata["video_streams"]) is int
            and media_metadata["video_streams"] >= 0
            and isinstance(audio_format, dict)
            and set(audio_format)
            == {"bits_per_sample", "channels", "duration_s", "sample_rate"}
            and audio_format["bits_per_sample"] == 16
            and audio_format["channels"] == 1
            and audio_format["sample_rate"] == 16000
            and type(audio_format["duration_s"]) in {int, float}
            and audio_format["duration_s"] > 0
            and isinstance(transcript_manifest["request_id"], str)
            and bool(transcript_manifest["request_id"])
            and isinstance(transcript_summary, dict)
            and set(transcript_summary)
            == {"duration", "language", "segment_count"}
            and type(transcript_summary["duration"]) in {int, float}
            and transcript_summary["duration"] >= 0
            and isinstance(transcript_summary["language"], str)
            and bool(transcript_summary["language"])
            and type(transcript_summary["segment_count"]) is int
            and transcript_summary["segment_count"] >= 0
        ):
            raise ValueError
        media_artifact = media["artifact"]
        if not isinstance(media_artifact, dict) or not isinstance(
            media_artifact.get("relative_path"), str
        ):
            raise ValueError
        media_relative = media_artifact["relative_path"]
        if not media_relative.startswith("source-media/original."):
            raise ValueError
        _artifact(private_root, media_artifact, expected_relative=media_relative)
        _artifact(
            private_root,
            audio["artifact"],
            expected_relative="source-audio-16k-mono.wav",
        )
        transcript_path = _artifact(
            private_root,
            transcript_manifest["artifact"],
            expected_relative="transcript.json",
        )
        transcript_sha = sha256_file(transcript_path)
        if brief["transcript_sha256"] != transcript_sha:
            raise ValueError
        transcript = load_exact_json(transcript_path, _TRANSCRIPT_KEYS)
        summary = transcript_summary
        if not (
            isinstance(summary, dict)
            and set(summary) == {"duration", "language", "segment_count"}
            and isinstance(transcript["sentences"], list)
            and all(isinstance(item, dict) for item in transcript["sentences"])
            and type(transcript["duration"]) in {int, float}
            and transcript["duration"] >= 0
            and isinstance(transcript["language"], str)
            and bool(transcript["language"])
            and isinstance(transcript["source_id"], str)
            and bool(transcript["source_id"])
            and transcript["duration"] == summary["duration"]
            and transcript["language"] == summary["language"]
            and len(transcript["sentences"]) == summary["segment_count"]
        ):
            raise ValueError
        return _Chain(
            private_root,
            "transcript",
            media_sha,
            audio_sha,
            transcript_manifest_sha,
            transcript_sha,
            brief_sha,
            transcript,
            brief,
            current,
        )
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        raise HandoffCompilerError("handoff-chain-invalid") from None


def _verified_document_chain(root: Path, work_id: str) -> _Chain:
    try:
        source = _verified_rewrite_source(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if source.kind not in {"x-article", "github-skill"}:
            raise ValueError
        source_artifact = {
            "x-article": "article-source/article.md",
            "github-skill": "github-skill-source/repository.md",
        }[source.kind]
        brief_path = verify_private_relative(source.private_root, "rewrite-brief.json")
        brief_sha = sha256_file(brief_path)
        brief = load_exact_json(brief_path, _ARTICLE_BRIEF_KEYS)
        if not (
            brief["schema_version"] == 2
            and brief["work_id"] == work_id
            and brief["source_kind"] == source.kind
            and brief["source_artifact"] == source_artifact
            and brief["source_sha256"] == source.artifact_sha256
            and brief["source_manifest_sha256"] == source.manifest_sha256
            and _valid_required_reviews(brief["required_reviews"])
            and brief["content_goal"]
            == "create a source-free original production script"
        ):
            raise ValueError
        source_text = source.path.read_text(encoding="utf-8")
        transcript_like = {
            "duration": 0,
            "language": "und",
            "sentences": [{"text": source_text}],
            "source_id": source.kind,
        }
        return _Chain(
            source.private_root,
            source.kind,
            "",
            "",
            source.manifest_sha256,
            source.artifact_sha256,
            brief_sha,
            transcript_like,
            brief,
            current,
        )
    except (
        RewritePackageError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise HandoffCompilerError("handoff-chain-invalid") from None


def _verified_chain(root: Path, work_id: str) -> _Chain:
    try:
        current = WashEventLedger(root).current(work_id)
    except SourceLedgerError:
        raise HandoffCompilerError("handoff-chain-invalid") from None
    if current.source_kind in {"x-article", "github-skill"}:
        return _verified_document_chain(root, work_id)
    return _verified_transcript_chain(root, work_id)


def _exact_private_input(private_root: Path, supplied: Path, name: str) -> Path:
    try:
        expected = verify_private_relative(private_root, name)
        actual = Path(os.path.abspath(os.fspath(supplied)))
        if actual != expected:
            raise ValueError
        return expected
    except (SourceContractError, OSError, TypeError, ValueError):
        raise HandoffCompilerError("rewrite-input-invalid") from None


def _validated_rewrite(
    chain: _Chain,
    work_id: str,
    candidate_path: Path,
    review_path: Path,
) -> tuple[RewriteCandidate, str, str]:
    candidate_file = _exact_private_input(
        chain.private_root, candidate_path, "rewrite-candidate.json"
    )
    review_file = _exact_private_input(
        chain.private_root, review_path, "rewrite-review.json"
    )
    try:
        candidate_sha = sha256_file(candidate_file)
        candidate = load_rewrite_candidate(candidate_file, maximum_title_candidates=12)
        review_sha = sha256_file(review_file)
        review = load_rewrite_review(review_file, candidate_sha)
    except (SourceContractError, RewritePackageError):
        raise HandoffCompilerError("rewrite-input-invalid") from None
    brief = chain.brief
    if brief["ratio"] != PRODUCTION_RATIO or candidate.ratio != PRODUCTION_RATIO:
        raise HandoffCompilerError("rewrite-production-ratio-invalid")
    if not review.all_passed:
        raise HandoffCompilerError("rewrite-review-not-passed")
    if candidate.schema_version == 2 and (
        review.schema_version != 2
        or tuple(name for name, _status in review.reviews) != REQUIRED_REVIEWS
    ):
        raise HandoffCompilerError("rewrite-review-not-passed")
    if not (
        candidate.work_id == work_id
        and candidate.archive_slug == brief["archive_slug"]
        and candidate.platform == brief["platform"]
        and candidate.ratio == brief["ratio"]
        and candidate.duration_target_s == brief["duration_target_s"]
    ):
        raise HandoffCompilerError("rewrite-input-invalid")
    for visible in (
        *candidate.title_candidates,
        candidate.visual,
        candidate.illustration_skill,
        *(segment.text for segment in candidate.segments),
        *(segment.visual_intent for segment in candidate.segments),
    ):
        if validate_public_handoff(visible):
            raise HandoffCompilerError("handoff-public-content-invalid")
        if any(
            line.startswith(("## ", "### ")) or line.strip() == "---"
            for line in visible.splitlines()
        ):
            raise HandoffCompilerError("handoff-public-content-invalid")
    overlaps = find_source_overlap_lines(chain.transcript, candidate)
    if overlaps:
        raise HandoffCompilerError(f"line {overlaps[0]}: source overlap")
    other_visible = (
        *candidate.title_candidates,
        candidate.visual,
        candidate.illustration_skill,
        *(segment.visual_intent for segment in candidate.segments),
    )
    visible_candidate = RewriteCandidate(
        work_id=candidate.work_id,
        archive_slug=candidate.archive_slug,
        platform=candidate.platform,
        ratio=candidate.ratio,
        duration_target_s=candidate.duration_target_s,
        title_candidates=candidate.title_candidates,
        segments=tuple(
            RewriteSegment(f"segment-{index:03d}", text, "safe")
            for index, text in enumerate(other_visible, start=1)
        ),
        visual=candidate.visual,
        illustration_skill=candidate.illustration_skill,
        schema_version=1,
        opening_contract=None,
    )
    visible_overlaps = find_source_overlap_lines(chain.transcript, visible_candidate)
    if visible_overlaps:
        raise HandoffCompilerError(
            f"line {visible_overlaps[0]}: source overlap"
        )
    return candidate, candidate_sha, review_sha


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_source_free_handoff(candidate: RewriteCandidate) -> str:
    if not isinstance(candidate, RewriteCandidate):
        raise HandoffCompilerError("rewrite-input-invalid")
    try:
        resolved_visual = resolve_visual_style(candidate.visual)
    except IllustrationThemeError:
        raise HandoffCompilerError("rewrite-visual-invalid") from None
    frontmatter = (
        ("status", "待制作"),
        ("platform", candidate.platform),
        ("ratio", candidate.ratio),
        ("duration_target_s", candidate.duration_target_s),
        ("word_count", reading_unit_count(candidate.spoken_text)),
        ("voice", CURRENT_VOICE_ID),
        ("voice_provider", "indextts2-local"),
        ("captions", "asr-word-timestamps"),
        ("caption_style", "anchor-dark"),
        ("visual", resolved_visual.target),
        ("illustration_skill", resolved_visual.illustration_skill),
        ("covers", DEFAULT_PUNK_COVER_CONTRACT),
        ("archive_slug", candidate.archive_slug),
    )
    lines = ["---"]
    for key, value in frontmatter:
        lines.append(f"{key}: {value if isinstance(value, int) else _yaml_string(value)}")
    lines.extend(["---", "", "## 标题候选", ""])
    lines.extend(f"- {title}" for title in candidate.title_candidates)
    lines.extend(["", "## 新稿分段", ""])
    for segment in candidate.segments:
        lines.extend([f"### {segment.segment_id}", "", segment.text, ""])
    lines.extend(["## 分段视觉意图", ""])
    lines.extend(
        f"- 第 {index} 段：{segment.visual_intent}"
        for index, segment in enumerate(candidate.segments, start=1)
    )
    lines.extend(
        [
            "",
            "## 内容画面计划",
            "",
            "- 审核候选：`工程/content-plan.candidate.json`",
            "- 正式计划由本地编译器校验后生成。",
            "",
            "## 制作回执",
            "",
            "- 制作状态：待填写",
            "- 输出文件：待填写",
            "- 音频状态：待填写",
            "- 字幕状态：待填写",
            "",
            "## QC结果",
            "",
            "- 画面：待检查",
            "- 音频：待检查",
            "- 字幕：待检查",
            "- 成片状态：待检查",
            "",
        ]
    )
    return "\n".join(lines)


def _publish_bytes_exclusive(root: Path, target: Path, payload: bytes) -> str:
    try:
        relative = Path(os.path.abspath(os.fspath(target))).relative_to(
            Path(os.path.abspath(os.fspath(root)))
        )
        formal = verify_private_relative(root, relative.as_posix())
        if formal.exists() or formal.is_symlink():
            raise HandoffCompilerError("handoff-target-exists")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".handoff.", suffix=".tmp", dir=formal.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, formal)
            except FileExistsError:
                raise HandoffCompilerError("handoff-target-exists") from None
        finally:
            temporary.unlink(missing_ok=True)
        return sha256_file(formal)
    except HandoffCompilerError:
        raise
    except (SourceContractError, OSError, TypeError, ValueError):
        raise HandoffCompilerError("handoff-publication-failed") from None


def _receipt_value(
    chain: _Chain,
    candidate: RewriteCandidate,
    candidate_sha: str,
    review_sha: str,
    handoff: Path,
    handoff_sha: str,
) -> dict[str, object]:
    common = {
        "archive_slug": candidate.archive_slug,
        "candidate_sha256": candidate_sha,
        "handoff": {
            "relative_path": f"待制作/{candidate.archive_slug}/交接稿.md",
            "sha256": handoff_sha,
            "size_bytes": handoff.stat().st_size,
        },
        "review_sha256": review_sha,
        "rewrite_brief_sha256": chain.rewrite_brief_sha256,
        "work_id": candidate.work_id,
    }
    if chain.source_kind in {"x-article", "github-skill"}:
        return {
            **common,
            "schema_version": 2,
            "source_kind": chain.source_kind,
            "source_artifact_sha256": chain.transcript_sha256,
            "source_manifest_sha256": chain.transcript_manifest_sha256,
        }
    return {
        **common,
        "schema_version": 1,
        "source_audio_manifest_sha256": chain.source_audio_manifest_sha256,
        "source_media_manifest_sha256": chain.source_media_manifest_sha256,
        "transcript_manifest_sha256": chain.transcript_manifest_sha256,
    }


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _append_rewrite_ready(root: Path, chain: _Chain, review_sha: str) -> StageEvent:
    if chain.current.stage == "rewrite_ready":
        if chain.current.artifact_sha256 != review_sha:
            raise HandoffCompilerError("rewrite-input-invalid")
        return chain.current
    if chain.current.stage not in {
        "transcript_ready",
        "article_ready",
        "github_skill_ready",
    }:
        raise HandoffCompilerError("handoff-chain-invalid")
    event = StageEvent(
        2,
        chain.current.work_id,
        chain.current.source_id,
        chain.current.source_kind,
        "rewrite_ready",
        "ok",
        "rewrite-review",
        review_sha,
        _timestamp(),
        event_sha256(chain.current),
    )
    try:
        WashEventLedger(root).append(event)
    except SourceLedgerError:
        raise HandoffCompilerError("handoff-ledger-invalid") from None
    return event


def _append_handoff_ready(
    root: Path, previous: StageEvent, receipt_sha: str
) -> None:
    try:
        WashEventLedger(root).append(
            StageEvent(
                2,
                previous.work_id,
                previous.source_id,
                previous.source_kind,
                "handoff_ready",
                "ok",
                "publication-receipt",
                receipt_sha,
                _timestamp(),
                event_sha256(previous),
            )
        )
    except SourceLedgerError:
        raise HandoffCompilerError("handoff-ledger-invalid") from None


def compile_source_handoff(
    root: Path,
    work_id: str,
    candidate_path: Path,
    review_path: Path,
) -> HandoffPublication:
    chain = _verified_chain(Path(root), work_id)
    candidate, candidate_sha, review_sha = _validated_rewrite(
        chain, work_id, Path(candidate_path), Path(review_path)
    )
    rendered = render_source_free_handoff(candidate)
    if validate_public_handoff(rendered):
        raise HandoffCompilerError("handoff-public-content-invalid")
    try:
        parse_handoff_segments(rendered)
    except ContentPlanError:
        raise HandoffCompilerError("handoff-public-content-invalid") from None
    previous = _append_rewrite_ready(root, chain, review_sha)
    paths = WorkbenchPaths(root)
    project_dir = paths.pending / candidate.archive_slug
    try:
        paths.pending.mkdir(parents=True, exist_ok=True)
        verify_private_relative(paths.pending, candidate.archive_slug)
        project_dir.mkdir(parents=False, exist_ok=False)
        verify_private_relative(paths.pending, candidate.archive_slug)
    except FileExistsError:
        raise HandoffCompilerError("handoff-target-exists") from None
    except (SourceContractError, OSError):
        raise HandoffCompilerError("handoff-publication-failed") from None
    handoff = project_dir / "交接稿.md"
    handoff_sha = _publish_bytes_exclusive(
        project_dir, handoff, rendered.encode("utf-8")
    )
    receipt_path = chain.private_root / "publication-receipt.json"
    receipt = _receipt_value(
        chain, candidate, candidate_sha, review_sha, handoff, handoff_sha
    )
    try:
        receipt_sha = publish_json_exclusive(
            chain.private_root, receipt_path, receipt
        )
    except SourceContractError:
        raise HandoffCompilerError("handoff-receipt-unavailable") from None
    _append_handoff_ready(root, previous, receipt_sha)
    return HandoffPublication(
        work_id,
        candidate.archive_slug,
        handoff_sha,
        receipt["handoff"]["relative_path"],  # type: ignore[index]
    )


def recover_handoff_receipt(root: Path, work_id: str) -> HandoffPublication:
    chain = _verified_chain(Path(root), work_id)
    candidate_path = chain.private_root / "rewrite-candidate.json"
    review_path = chain.private_root / "rewrite-review.json"
    candidate, candidate_sha, review_sha = _validated_rewrite(
        chain, work_id, candidate_path, review_path
    )
    paths = WorkbenchPaths(root)
    try:
        handoff = verify_private_relative(
            paths.pending, f"{candidate.archive_slug}/交接稿.md"
        )
    except SourceContractError:
        raise HandoffCompilerError("handoff-recovery-invalid") from None
    expected_text = render_source_free_handoff(candidate)
    try:
        if handoff.read_bytes() != expected_text.encode("utf-8"):
            raise ValueError
        handoff_sha = sha256_file(handoff)
    except (SourceContractError, OSError, ValueError):
        raise HandoffCompilerError("handoff-recovery-invalid") from None
    expected = _receipt_value(
        chain, candidate, candidate_sha, review_sha, handoff, handoff_sha
    )
    receipt_path = chain.private_root / "publication-receipt.json"
    if receipt_path.exists():
        try:
            receipt_keys = (
                _ARTICLE_RECEIPT_KEYS
                if chain.source_kind in {"x-article", "github-skill"}
                else _RECEIPT_KEYS
            )
            actual = load_exact_json(receipt_path, receipt_keys)
            receipt_sha = sha256_file(receipt_path)
            if actual != expected:
                raise ValueError
            if chain.current.stage == "handoff_ready":
                if receipt_sha != chain.current.artifact_sha256:
                    raise ValueError
            elif (
                chain.current.stage == "rewrite_ready"
                and chain.current.artifact_sha256 == review_sha
            ):
                _append_handoff_ready(root, chain.current, receipt_sha)
            else:
                raise ValueError
        except (SourceContractError, OSError, TypeError, ValueError):
            raise HandoffCompilerError("handoff-recovery-invalid") from None
    else:
        if chain.current.stage != "rewrite_ready" or chain.current.artifact_sha256 != review_sha:
            raise HandoffCompilerError("handoff-recovery-invalid")
        try:
            receipt_sha = publish_json_exclusive(
                chain.private_root, receipt_path, expected
            )
        except SourceContractError:
            raise HandoffCompilerError("handoff-receipt-unavailable") from None
        _append_handoff_ready(root, chain.current, receipt_sha)
    return HandoffPublication(
        work_id,
        candidate.archive_slug,
        handoff_sha,
        expected["handoff"]["relative_path"],  # type: ignore[index]
    )


__all__ = [
    "HandoffCompilerError",
    "HandoffPublication",
    "compile_source_handoff",
    "recover_handoff_receipt",
    "render_source_free_handoff",
]

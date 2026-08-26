"""Fail-closed production revisions for an already-started private source chain."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from boomearth.video.content_plan import ContentPlanError, parse_handoff_segments
from boomearth.workbench.handoff import validate_public_handoff
from boomearth.workbench.handoff_compiler import (
    HandoffCompilerError,
    render_source_free_handoff,
)
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.production_policy import (
    ProductionPolicyError,
    verify_horizontal_production_policy,
)
from boomearth.workbench.rewrite_package import (
    PRODUCTION_RATIO,
    REQUIRED_REVIEWS,
    RewritePackageError,
    find_source_overlap_lines,
    load_rewrite_candidate,
    load_rewrite_review,
    reading_unit_count,
)
from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import SourceLedgerError, WashEventLedger


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_KEYS = frozenset(
    {"artifact", "metadata", "schema_version", "source_input_sha256", "work_id"}
)
_AUDIO_KEYS = frozenset(
    {"artifact", "format", "schema_version", "upstream_sha256", "work_id"}
)
_TRANSCRIPT_MANIFEST_KEYS = frozenset(
    {"artifact", "request_id", "schema_version", "summary", "upstream_sha256", "work_id"}
)
_TRANSCRIPT_KEYS = frozenset({"duration", "language", "sentences", "source_id"})
_PUBLICATION_KEYS = frozenset(
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
_POLICY_KEYS = frozenset(
    {
        "handoff_after_sha256",
        "handoff_before_sha256",
        "handoff_relative_path",
        "original_publication_receipt_sha256",
        "policy",
        "production_height",
        "production_ratio",
        "production_width",
        "schema_version",
        "work_id",
    }
)
_ASR_PLAN_KEYS = frozenset(
    {
        "created_at",
        "failure_policy",
        "forbidden_providers",
        "input",
        "operation",
        "output",
        "project_id",
        "provider",
        "schema_version",
        "script_contract",
        "status",
    }
)
_BRIEF_V2_KEYS = frozenset(
    {
        "archive_slug",
        "content_goal",
        "duration_target_s",
        "original_active_handoff_sha256",
        "original_production_policy_receipt_sha256",
        "original_publication_receipt_sha256",
        "platform",
        "ratio",
        "required_reviews",
        "revision",
        "schema_version",
        "transcript_artifact",
        "transcript_manifest_sha256",
        "transcript_sha256",
        "work_id",
    }
)


class ProductionRevisionError(ValueError):
    """A fixed-message production-revision failure."""

    def __repr__(self) -> str:
        return "ProductionRevisionError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _OriginalChain:
    private_root: Path
    active: Path
    transcript: dict[str, object]
    transcript_sha256: str
    transcript_manifest_sha256: str
    publication_sha256: str
    policy_sha256: str
    active_handoff_sha256: str


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _artifact(private_root: Path, value: object, relative: str) -> Path:
    if not (
        isinstance(value, dict)
        and set(value) == {"relative_path", "sha256", "size_bytes"}
        and value["relative_path"] == relative
        and _digest(value["sha256"])
        and type(value["size_bytes"]) is int
        and value["size_bytes"] > 0
    ):
        raise ValueError
    path = verify_private_relative(private_root, relative)
    if sha256_file(path) != value["sha256"] or path.stat().st_size != value["size_bytes"]:
        raise ValueError
    return path


def _load_source_chain(
    root: Path, work_id: str, private_root: Path
) -> tuple[str, str, str, str, dict[str, object]]:
    order = load_work_order(root, work_id)
    media_path = verify_private_relative(private_root, "source-media-manifest.json")
    audio_path = verify_private_relative(private_root, "source-audio-manifest.json")
    transcript_manifest_path = verify_private_relative(private_root, "transcript-manifest.json")
    media = load_exact_json(media_path, _MEDIA_KEYS)
    audio = load_exact_json(audio_path, _AUDIO_KEYS)
    transcript_manifest = load_exact_json(transcript_manifest_path, _TRANSCRIPT_MANIFEST_KEYS)
    media_sha = sha256_file(media_path)
    audio_sha = sha256_file(audio_path)
    transcript_manifest_sha = sha256_file(transcript_manifest_path)
    media_artifact = media["artifact"]
    if not isinstance(media_artifact, dict) or not isinstance(media_artifact.get("relative_path"), str):
        raise ValueError
    media_relative = media_artifact["relative_path"]
    if not media_relative.startswith("source-media/original."):
        raise ValueError
    _artifact(private_root, media_artifact, media_relative)
    _artifact(private_root, audio["artifact"], "source-audio-16k-mono.wav")
    transcript_path = _artifact(private_root, transcript_manifest["artifact"], "transcript.json")
    transcript_sha = sha256_file(transcript_path)
    transcript = load_exact_json(transcript_path, _TRANSCRIPT_KEYS)
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
        and isinstance(transcript["sentences"], list)
    ):
        raise ValueError
    return media_sha, audio_sha, transcript_manifest_sha, transcript_sha, transcript


def _load_publication_chain(
    private_root: Path,
    work_id: str,
    original_active_project: str,
    source_hashes: tuple[str, str, str, str, dict[str, object]],
) -> tuple[str, str]:
    (
        media_sha,
        audio_sha,
        transcript_manifest_sha,
        _transcript_sha,
        _transcript,
    ) = source_hashes
    publication_path = verify_private_relative(private_root, "publication-receipt.json")
    publication = load_exact_json(publication_path, _PUBLICATION_KEYS)
    publication_sha = sha256_file(publication_path)
    handoff_value = publication["handoff"]
    if not (
        publication["schema_version"] == 1
        and publication["work_id"] == work_id
        and publication["archive_slug"] == original_active_project
        and publication["source_media_manifest_sha256"] == media_sha
        and publication["source_audio_manifest_sha256"] == audio_sha
        and publication["transcript_manifest_sha256"] == transcript_manifest_sha
        and isinstance(handoff_value, dict)
        and set(handoff_value) == {"relative_path", "sha256", "size_bytes"}
        and handoff_value["relative_path"] == f"待制作/{original_active_project}/交接稿.md"
        and _digest(handoff_value["sha256"])
        and type(handoff_value["size_bytes"]) is int
        and handoff_value["size_bytes"] > 0
    ):
        raise ValueError
    for name, expected in (
        ("rewrite-brief.json", publication["rewrite_brief_sha256"]),
        ("rewrite-candidate.json", publication["candidate_sha256"]),
        ("rewrite-review.json", publication["review_sha256"]),
    ):
        if not _digest(expected) or sha256_file(verify_private_relative(private_root, name)) != expected:
            raise ValueError
    policy_path = verify_private_relative(private_root, "production-policy-receipt.json")
    policy = load_exact_json(policy_path, _POLICY_KEYS)
    policy_sha = sha256_file(policy_path)
    if not (
        policy["schema_version"] == 1
        and policy["work_id"] == work_id
        and policy["original_publication_receipt_sha256"] == publication_sha
        and policy["production_ratio"] == PRODUCTION_RATIO
        and policy["production_width"] == 1920
        and policy["production_height"] == 1080
        and policy["handoff_relative_path"] == f"制作中/{original_active_project}/交接稿.md"
    ):
        raise ValueError
    return publication_sha, policy_sha


def _validate_v1_asr_plan(active: Path, original_active_project: str) -> None:
    narration = active / "工程" / "media" / "narration.wav"
    segments = active / "工程" / "tts-segments.jsonl"
    content_plan = active / "工程" / "content-plan.json"
    plan_path = active / "工程" / "qc" / "final-audio-asr-plan.json"
    plan = load_exact_json(plan_path, _ASR_PLAN_KEYS)
    provider = plan["provider"]
    input_value = plan["input"]
    script = plan["script_contract"]
    output = plan["output"]
    if not (
        narration.is_file()
        and segments.is_file()
        and content_plan.is_file()
        and plan["schema_version"] == 1
        and plan["status"] == "awaiting-explicit-approval"
        and plan["project_id"] == original_active_project
        and isinstance(input_value, dict)
        and set(input_value) == {"encoding", "path", "sha256"}
        and input_value["path"] == str(narration)
        and input_value["sha256"] == sha256_file(narration)
        and isinstance(script, dict)
        and set(script) == {"path", "segment_count", "sha256"}
        and script["path"] == str(segments)
        and script["sha256"] == sha256_file(segments)
        and isinstance(provider, dict)
        and provider.get("request_count") == 1
        and provider.get("retry_count") == 0
        and provider.get("follow_redirects") is False
        and provider.get("resource_id") == "volc.bigasr.auc_turbo"
        and isinstance(output, dict)
        and output.get("directory") == str(active / "工程" / "media" / "captions")
        and not (active / "工程" / "media" / "captions").exists()
    ):
        raise ValueError


def _load_original_chain(root: Path, work_id: str, original_active_project: str) -> _OriginalChain:
    try:
        order = load_work_order(root, work_id)
        if order.source_kind not in {"local", "url"}:
            raise ValueError
        paths = WorkbenchPaths(root)
        active = paths.public_project("active", original_active_project)
        current = WashEventLedger(root).current(work_id)
        if not (
            current.stage == "production_started"
            and current.artifact_label == "production-policy-receipt"
        ):
            raise ValueError
        source_hashes = _load_source_chain(root, work_id, order.private_root)
        publication_sha, policy_sha = _load_publication_chain(
            order.private_root, work_id, original_active_project, source_hashes
        )
        if current.artifact_sha256 != policy_sha:
            raise ValueError
        policy_result = verify_horizontal_production_policy(
            root, work_id, original_active_project
        )
        _validate_v1_asr_plan(active, original_active_project)
        return _OriginalChain(
            order.private_root,
            active,
            source_hashes[4],
            source_hashes[3],
            source_hashes[2],
            publication_sha,
            policy_sha,
            policy_result.handoff_sha256,
        )
    except (
        OSError,
        ProductionPolicyError,
        SourceContractError,
        SourceLedgerError,
        TypeError,
        ValueError,
    ):
        raise ProductionRevisionError("production-revision-chain-invalid") from None


def prepare_production_revision(
    root: Path,
    work_id: str,
    *,
    original_active_project: str,
    archive_slug: str,
    duration_target_s: int,
) -> ArtifactRecord:
    """Validate the V1 chain and exclusively publish the V2 brief."""

    if not (
        isinstance(original_active_project, str)
        and original_active_project
        and archive_slug == f"{original_active_project}-v2"
        and duration_target_s == 165
    ):
        raise ProductionRevisionError("production-revision-input-invalid")
    chain = _load_original_chain(Path(root), work_id, original_active_project)
    value = {
        "archive_slug": archive_slug,
        "content_goal": "create a source-free original production script",
        "duration_target_s": duration_target_s,
        "original_active_handoff_sha256": chain.active_handoff_sha256,
        "original_production_policy_receipt_sha256": chain.policy_sha256,
        "original_publication_receipt_sha256": chain.publication_sha256,
        "platform": "douyin",
        "ratio": PRODUCTION_RATIO,
        "required_reviews": list(REQUIRED_REVIEWS),
        "revision": 2,
        "schema_version": 1,
        "transcript_artifact": "transcript.json",
        "transcript_manifest_sha256": chain.transcript_manifest_sha256,
        "transcript_sha256": chain.transcript_sha256,
        "work_id": work_id,
    }
    if set(value) != set(_BRIEF_V2_KEYS):
        raise ProductionRevisionError("production-revision-input-invalid")
    path = chain.private_root / "rewrite-brief-v2.json"
    try:
        digest = publish_json_exclusive(chain.private_root, path, value)
        return ArtifactRecord(
            relative_path="rewrite-brief-v2.json",
            sha256=digest,
            size_bytes=path.stat().st_size,
            path=path,
        )
    except SourceContractError as exc:
        if str(exc) == "artifact-exists":
            raise ProductionRevisionError("production-revision-target-exists") from None
        raise ProductionRevisionError("production-revision-publication-failed") from None
    except OSError:
        raise ProductionRevisionError("production-revision-publication-failed") from None


@dataclass(frozen=True, slots=True, repr=False)
class ProductionRevision:
    work_id: str
    revision: int
    archive_slug: str
    handoff_sha256: str
    revision_receipt_sha256: str
    active_relative_path: str

    def __repr__(self) -> str:
        return "ProductionRevision(<redacted>)"


def _exact_v2_input(private_root: Path, supplied: Path, name: str) -> Path:
    try:
        expected = verify_private_relative(private_root, name)
        actual = Path(os.path.abspath(os.fspath(supplied)))
        if actual != expected:
            raise ValueError
        return expected
    except (OSError, SourceContractError, TypeError, ValueError):
        raise ProductionRevisionError("production-revision-rewrite-invalid") from None


def _load_v2_rewrite(
    chain: _OriginalChain,
    work_id: str,
    candidate_path: Path,
    review_path: Path,
):
    try:
        brief_path = verify_private_relative(chain.private_root, "rewrite-brief-v2.json")
        brief = load_exact_json(brief_path, _BRIEF_V2_KEYS)
        candidate_file = _exact_v2_input(
            chain.private_root, candidate_path, "rewrite-candidate-v2.json"
        )
        review_file = _exact_v2_input(
            chain.private_root, review_path, "rewrite-review-v2.json"
        )
        candidate_sha = sha256_file(candidate_file)
        candidate = load_rewrite_candidate(
            candidate_file, maximum_title_candidates=12
        )
        review_sha = sha256_file(review_file)
        review = load_rewrite_review(review_file, candidate_sha)
        units = reading_unit_count(candidate.spoken_text)
        if not (
            brief["schema_version"] == 1
            and brief["revision"] == 2
            and brief["work_id"] == work_id
            and brief["transcript_sha256"] == chain.transcript_sha256
            and brief["transcript_manifest_sha256"] == chain.transcript_manifest_sha256
            and brief["original_publication_receipt_sha256"] == chain.publication_sha256
            and brief["original_production_policy_receipt_sha256"] == chain.policy_sha256
            and brief["original_active_handoff_sha256"] == chain.active_handoff_sha256
            and candidate.work_id == work_id
            and candidate.archive_slug == brief["archive_slug"]
            and candidate.platform == brief["platform"]
            and candidate.ratio == brief["ratio"] == PRODUCTION_RATIO
            and candidate.duration_target_s == brief["duration_target_s"] == 165
            and candidate.visual == "vivid-comic-explainer"
            and candidate.illustration_skill == "ra-video-illustrations"
            and 8 <= len(candidate.title_candidates) <= 12
            and review.all_passed
            and len(candidate.segments) == 10
            and 750 <= units <= 850
        ):
            raise ValueError
        if find_source_overlap_lines(chain.transcript, candidate):
            raise ValueError
        rendered = render_source_free_handoff(candidate)
        expected_status = 'status: "待制作"'
        if rendered.count(expected_status) != 1:
            raise ValueError
        rendered = rendered.replace(expected_status, 'status: "制作中"', 1)
        if validate_public_handoff(rendered) or len(parse_handoff_segments(rendered)) != 10:
            raise ValueError
        return brief_path, candidate_sha, review_sha, candidate, rendered
    except ProductionRevisionError:
        raise
    except (
        ContentPlanError,
        HandoffCompilerError,
        OSError,
        RewritePackageError,
        SourceContractError,
        TypeError,
        ValueError,
    ):
        raise ProductionRevisionError("production-revision-rewrite-invalid") from None


def _write_handoff_stage(stage: Path, rendered: str) -> tuple[Path, str]:
    handoff = stage / "交接稿.md"
    try:
        stage.mkdir(parents=False, exist_ok=False)
        with handoff.open("xb") as stream:
            stream.write(rendered.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        return handoff, sha256_file(handoff)
    except (FileExistsError, OSError, SourceContractError):
        try:
            handoff.unlink(missing_ok=True)
            stage.rmdir()
        except OSError:
            pass
        raise ProductionRevisionError("production-revision-publication-failed") from None


def compile_production_revision(
    root: Path,
    work_id: str,
    candidate_path: Path,
    review_path: Path,
    *,
    original_active_project: str,
) -> ProductionRevision:
    """Compile and exclusively publish a V2 active production revision."""

    root = Path(root)
    chain = _load_original_chain(root, work_id, original_active_project)
    brief_path, candidate_sha, review_sha, candidate, rendered = _load_v2_rewrite(
        chain, work_id, Path(candidate_path), Path(review_path)
    )
    paths = WorkbenchPaths(root)
    target = paths.public_project("active", candidate.archive_slug)
    publication_path = chain.private_root / "publication-revision-v2.json"
    production_path = chain.private_root / "production-revision-v2.json"
    superseded_path = chain.active / "工程" / "qc" / "superseded-by-v2.json"
    if any(
        path.exists() or path.is_symlink()
        for path in (target, publication_path, production_path, superseded_path)
    ):
        raise ProductionRevisionError("production-revision-target-exists")
    paths.active.mkdir(parents=True, exist_ok=True)
    stage = paths.active / f".revision-{uuid4().hex}"
    staged_handoff, handoff_sha = _write_handoff_stage(stage, rendered)
    try:
        os.rename(stage, target)
    except OSError:
        try:
            staged_handoff.unlink(missing_ok=True)
            stage.rmdir()
        except OSError:
            pass
        raise ProductionRevisionError("production-revision-publication-failed") from None
    handoff = target / "交接稿.md"
    publication_value = {
        "archive_slug": candidate.archive_slug,
        "candidate_sha256": candidate_sha,
        "handoff": {
            "relative_path": f"制作中/{candidate.archive_slug}/交接稿.md",
            "sha256": handoff_sha,
            "size_bytes": handoff.stat().st_size,
        },
        "original_publication_receipt_sha256": chain.publication_sha256,
        "review_sha256": review_sha,
        "revision": 2,
        "rewrite_brief_sha256": sha256_file(brief_path),
        "schema_version": 1,
        "work_id": work_id,
    }
    try:
        publication_sha = publish_json_exclusive(
            chain.private_root, publication_path, publication_value
        )
        narration = chain.active / "工程" / "media" / "narration.wav"
        content_plan = chain.active / "工程" / "content-plan.json"
        asr_plan = chain.active / "工程" / "qc" / "final-audio-asr-plan.json"
        superseded_value = {
            "old_asr_plan_sha256": sha256_file(asr_plan),
            "old_content_plan_sha256": sha256_file(content_plan),
            "old_narration_sha256": sha256_file(narration),
            "schema_version": 1,
            "status": "superseded-before-network",
            "v1_project_id": original_active_project,
            "v2_project_id": candidate.archive_slug,
            "v2_publication_revision_sha256": publication_sha,
        }
        superseded_sha = publish_json_exclusive(
            chain.active, superseded_path, superseded_value
        )
        production_value = {
            "active_relative_path": f"制作中/{candidate.archive_slug}",
            "archive_slug": candidate.archive_slug,
            "handoff_sha256": handoff_sha,
            "production_ratio": PRODUCTION_RATIO,
            "publication_revision_sha256": publication_sha,
            "revision": 2,
            "schema_version": 1,
            "superseded_receipt_sha256": superseded_sha,
            "work_id": work_id,
        }
        production_sha = publish_json_exclusive(
            chain.private_root, production_path, production_value
        )
        verified = _load_original_chain(root, work_id, original_active_project)
        if (
            verified.active_handoff_sha256 != chain.active_handoff_sha256
            or sha256_file(handoff) != handoff_sha
        ):
            raise ValueError
    except (OSError, SourceContractError, ValueError):
        raise ProductionRevisionError("production-revision-publication-failed") from None
    return ProductionRevision(
        work_id,
        2,
        candidate.archive_slug,
        handoff_sha,
        production_sha,
        f"制作中/{candidate.archive_slug}",
    )


__all__ = [
    "ProductionRevision",
    "ProductionRevisionError",
    "compile_production_revision",
    "prepare_production_revision",
]

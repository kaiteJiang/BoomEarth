"""Strict private rewrite brief, candidate, review, and overlap contracts."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Final

from boomearth.providers.github_skill import (
    GitHubSkillProviderError,
    parse_github_skill_url,
)
from boomearth.video.illustration_themes import resolve_visual_style
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


LEGACY_REQUIRED_REVIEWS = (
    "ra-洗稿",
    "ra-人话",
    "dbs-ai-check",
    "dbs-hook",
    "dbs-resonate",
    "ra-video-title",
)
REQUIRED_REVIEWS = (
    "ra-洗稿",
    "ra-人话",
    "dbs-ai-check",
    "ra-hook",
    "jl-multiplatform-titles",
    "dbs-hook",
    "dbs-resonate",
    "ra-video-title",
)
PRODUCTION_RATIO: Final[str] = "16:9"
_CANDIDATE_V1_KEYS = frozenset(
    {
        "archive_slug",
        "duration_target_s",
        "illustration_skill",
        "platform",
        "ratio",
        "schema_version",
        "segments",
        "title_candidates",
        "visual",
        "work_id",
    }
)
_CANDIDATE_V2_KEYS = _CANDIDATE_V1_KEYS | {"opening_contract"}
_SEGMENT_KEYS = frozenset({"segment_id", "text", "visual_intent"})
_OPENING_KEYS = frozenset(
    {
        "audience_pain",
        "bridge",
        "cover_hook",
        "cta",
        "hook_3s",
        "hook_type",
        "proof_segment_ids",
        "schema_version",
        "title_formula",
        "title_skill",
        "value_promise",
    }
)
_REVIEW_KEYS = frozenset(
    {"candidate_sha256", "reviewed_at", "reviews", "schema_version"}
)
_TRANSCRIPT_KEYS = frozenset({"duration", "language", "sentences", "source_id"})
_TRANSCRIPT_MANIFEST_KEYS = frozenset(
    {"artifact", "request_id", "schema_version", "summary", "upstream_sha256", "work_id"}
)
_ARTICLE_MANIFEST_KEYS = frozenset(
    {
        "article_json_sha256",
        "article_markdown_sha256",
        "assets",
        "provider",
        "schema_version",
        "source_input_sha256",
        "work_id",
    }
)
_GITHUB_SKILL_MANIFEST_KEYS = frozenset(
    {
        "created_at",
        "default_branch",
        "files",
        "license_status",
        "repository",
        "repository_markdown_path",
        "repository_markdown_sha256",
        "request_count",
        "requested_path",
        "requested_scope",
        "resolved_commit",
        "schema_version",
        "source_input_sha256",
        "source_kind",
        "total_text_bytes",
        "tree_sha256",
        "work_id",
    }
)
_GITHUB_SKILL_FILE_KEYS = frozenset(
    {"path", "blob_sha", "local_path", "sha256", "bytes", "role"}
)
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SEGMENT_RE = re.compile(r"^segment-[0-9]{3}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SPDX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_RATIOS = frozenset({"16:9", "9:16", "1:1"})
_REVIEW_STATUSES = frozenset({"pass", "revise", "not_applicable"})
_HOOK_TYPES = frozenset(
    {
        "content-preview",
        "value-packaging",
        "phenomenon-first",
        "pain-point",
        "audience-callout",
        "event-opening",
        "topic-opening",
    }
)
_TITLE_FORMULAS = frozenset(
    {
        "number-list",
        "cost-and-gain",
        "reversal",
        "tutorial",
        "social-proof",
        "comparison",
        "emotional-short",
    }
)


class RewritePackageError(ValueError):
    """A fixed-message private rewrite package failure."""

    def __repr__(self) -> str:
        return "RewritePackageError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RewriteBrief:
    work_id: str
    platform: str
    ratio: str
    duration_target_s: int
    archive_slug: str
    transcript_artifact: str
    transcript_sha256: str
    transcript_manifest_sha256: str
    required_reviews: tuple[str, ...]

    def __repr__(self) -> str:
        return "RewriteBrief(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RewriteSource:
    kind: str
    path: Path
    artifact_sha256: str
    manifest_sha256: str
    private_root: Path

    def __repr__(self) -> str:
        return "RewriteSource(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RewriteSegment:
    segment_id: str
    text: str
    visual_intent: str

    def __repr__(self) -> str:
        return "RewriteSegment(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RewriteOpeningContract:
    hook_type: str
    hook_3s: str
    audience_pain: str
    value_promise: str
    cta: str
    bridge: str
    title_skill: str
    title_formula: str
    cover_hook: str
    proof_segment_ids: tuple[str, ...]

    def __repr__(self) -> str:
        return "RewriteOpeningContract(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RewriteCandidate:
    work_id: str
    archive_slug: str
    platform: str
    ratio: str
    duration_target_s: int
    title_candidates: tuple[str, ...]
    segments: tuple[RewriteSegment, ...]
    visual: str
    illustration_skill: str
    schema_version: int = 1
    opening_contract: RewriteOpeningContract | None = None

    @property
    def spoken_text(self) -> str:
        return "\n".join(segment.text for segment in self.segments)

    def __repr__(self) -> str:
        return "RewriteCandidate(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class RewriteReview:
    candidate_sha256: str
    reviewed_at: str
    reviews: tuple[tuple[str, str], ...]
    schema_version: int = 1

    @property
    def all_passed(self) -> bool:
        return all(status == "pass" for _, status in self.reviews)

    def __repr__(self) -> str:
        return "RewriteReview(<redacted>)"


def normalized_reading_units(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be str")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith(("P", "C"))
    )


def reading_unit_count(text: str) -> int:
    return len(normalized_reading_units(text))


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _text(value: object, *, maximum_units: int) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 0 < reading_unit_count(value) <= maximum_units
    )


def _common_values(
    platform: object,
    ratio: object,
    duration_target_s: object,
    archive_slug: object,
) -> bool:
    return (
        isinstance(platform, str)
        and _TOKEN_RE.fullmatch(platform) is not None
        and ratio in _RATIOS
        and type(duration_target_s) is int
        and 5 <= duration_target_s <= 3600
        and isinstance(archive_slug, str)
        and _SLUG_RE.fullmatch(archive_slug) is not None
    )


def _load_opening_contract(
    value: object,
    *,
    segments: tuple[RewriteSegment, ...],
    title_candidates: tuple[str, ...],
) -> RewriteOpeningContract:
    if not isinstance(value, dict) or set(value) != set(_OPENING_KEYS):
        raise ValueError
    proof_ids = value["proof_segment_ids"]
    if not (
        value["schema_version"] == 1
        and value["hook_type"] in _HOOK_TYPES
        and value["title_skill"] == "jl-multiplatform-titles"
        and value["title_formula"] in _TITLE_FORMULAS
        and _text(value["hook_3s"], maximum_units=40)
        and 6 <= reading_unit_count(value["hook_3s"]) <= 40
        and _text(value["audience_pain"], maximum_units=120)
        and _text(value["value_promise"], maximum_units=120)
        and _text(value["cta"], maximum_units=80)
        and "收藏" in value["cta"]
        and ("点赞" in value["cta"] or "关注" in value["cta"])
        and _text(value["bridge"], maximum_units=80)
        and _text(value["cover_hook"], maximum_units=40)
        and isinstance(proof_ids, list)
        and 1 <= len(proof_ids) <= 3
        and proof_ids == [f"segment-{index:03d}" for index in range(1, len(proof_ids) + 1)]
    ):
        raise ValueError
    if len(segments) < len(proof_ids):
        raise ValueError
    spoken_prefix = normalized_reading_units(
        "".join(segment.text for segment in segments[: len(proof_ids)])
    )
    contracted_prefix = normalized_reading_units(
        "".join(
            (
                value["hook_3s"],
                value["audience_pain"],
                value["value_promise"],
                value["cta"],
                value["bridge"],
            )
        )
    )
    if not spoken_prefix.startswith(contracted_prefix):
        raise ValueError
    normalized_cover = normalized_reading_units(value["cover_hook"])
    if normalized_cover in {
        normalized_reading_units(title) for title in title_candidates
    }:
        raise ValueError
    return RewriteOpeningContract(
        hook_type=value["hook_type"],
        hook_3s=value["hook_3s"],
        audience_pain=value["audience_pain"],
        value_promise=value["value_promise"],
        cta=value["cta"],
        bridge=value["bridge"],
        title_skill=value["title_skill"],
        title_formula=value["title_formula"],
        cover_hook=value["cover_hook"],
        proof_segment_ids=tuple(proof_ids),
    )


def load_rewrite_candidate(
    path: Path,
    *,
    maximum_title_candidates: int = 5,
) -> RewriteCandidate:
    try:
        if (
            type(maximum_title_candidates) is not int
            or not 1 <= maximum_title_candidates <= 12
        ):
            raise ValueError
        sha256_file(path)
        try:
            value = load_exact_json(path, _CANDIDATE_V2_KEYS)
        except SourceContractError:
            value = load_exact_json(path, _CANDIDATE_V1_KEYS)
        resolve_visual_style(value["visual"])
        if not (
            value["schema_version"] in {1, 2}
            and (
                value["schema_version"] == 2
                if "opening_contract" in value
                else value["schema_version"] == 1
            )
            and isinstance(value["work_id"], str)
            and _TOKEN_RE.fullmatch(value["work_id"]) is not None
            and _common_values(
                value["platform"],
                value["ratio"],
                value["duration_target_s"],
                value["archive_slug"],
            )
            and isinstance(value["title_candidates"], list)
            and 1
            <= len(value["title_candidates"])
            <= maximum_title_candidates
            and all(_text(title, maximum_units=120) for title in value["title_candidates"])
            and isinstance(value["segments"], list)
            and 1 <= len(value["segments"]) <= 100
            and isinstance(value["illustration_skill"], str)
            and _TOKEN_RE.fullmatch(value["illustration_skill"]) is not None
        ):
            raise ValueError
        normalized_titles = [
            normalized_reading_units(title) for title in value["title_candidates"]
        ]
        if len(set(normalized_titles)) != len(normalized_titles):
            raise ValueError
        segments: list[RewriteSegment] = []
        for index, item in enumerate(value["segments"], start=1):
            if not (
                isinstance(item, dict)
                and set(item) == set(_SEGMENT_KEYS)
                and item["segment_id"] == f"segment-{index:03d}"
                and _SEGMENT_RE.fullmatch(item["segment_id"]) is not None
                and _text(item["text"], maximum_units=1200)
                and _text(item["visual_intent"], maximum_units=240)
            ):
                raise ValueError
            segments.append(
                RewriteSegment(
                    item["segment_id"], item["text"], item["visual_intent"]
                )
            )
        segment_values = tuple(segments)
        title_values = tuple(value["title_candidates"])
        opening_contract = (
            _load_opening_contract(
                value["opening_contract"],
                segments=segment_values,
                title_candidates=title_values,
            )
            if value["schema_version"] == 2
            else None
        )
        return RewriteCandidate(
            work_id=value["work_id"],
            archive_slug=value["archive_slug"],
            platform=value["platform"],
            ratio=value["ratio"],
            duration_target_s=value["duration_target_s"],
            title_candidates=title_values,
            segments=segment_values,
            visual=value["visual"],
            illustration_skill=value["illustration_skill"],
            schema_version=value["schema_version"],
            opening_contract=opening_contract,
        )
    except (SourceContractError, TypeError, ValueError):
        raise RewritePackageError("rewrite-candidate-invalid") from None


def load_rewrite_review(path: Path, candidate_sha256: str) -> RewriteReview:
    try:
        if not _digest(candidate_sha256):
            raise ValueError
        sha256_file(path)
        value = load_exact_json(path, _REVIEW_KEYS)
        review_names = tuple(value["reviews"]) if isinstance(value["reviews"], dict) else ()
        allowed_review_names = {
            frozenset(LEGACY_REQUIRED_REVIEWS),
            frozenset(REQUIRED_REVIEWS),
        }
        if not (
            value["schema_version"] in {1, 2}
            and _digest(value["candidate_sha256"])
            and _timestamp(value["reviewed_at"])
            and isinstance(value["reviews"], dict)
            and frozenset(review_names) in allowed_review_names
            and all(status in _REVIEW_STATUSES for status in value["reviews"].values())
        ):
            raise ValueError
        if value["candidate_sha256"] != candidate_sha256:
            raise RewritePackageError("rewrite-review-candidate-mismatch")
        review_order = (
            REQUIRED_REVIEWS
            if frozenset(review_names) == frozenset(REQUIRED_REVIEWS)
            else LEGACY_REQUIRED_REVIEWS
        )
        return RewriteReview(
            candidate_sha256=value["candidate_sha256"],
            reviewed_at=value["reviewed_at"],
            reviews=tuple((name, value["reviews"][name]) for name in review_order),
            schema_version=value["schema_version"],
        )
    except RewritePackageError:
        raise
    except (SourceContractError, TypeError, ValueError):
        raise RewritePackageError("rewrite-review-invalid") from None


def _sentence_texts(transcript: Mapping[str, object]) -> tuple[str, ...]:
    try:
        sentences = transcript["sentences"]
        if not isinstance(sentences, list):
            raise ValueError
        values: list[str] = []
        for sentence in sentences:
            if not isinstance(sentence, Mapping):
                raise ValueError
            text = sentence.get("text")
            if not isinstance(text, str):
                raise ValueError
            values.append(text)
        return tuple(values)
    except (KeyError, TypeError, ValueError):
        raise RewritePackageError("rewrite-transcript-invalid") from None


def find_source_overlap_lines(
    transcript: Mapping[str, object],
    candidate: RewriteCandidate,
    *,
    window_units: int = 20,
) -> tuple[int, ...]:
    if type(window_units) is not int or window_units <= 0:
        raise RewritePackageError("rewrite-overlap-window-invalid")
    if not isinstance(candidate, RewriteCandidate):
        raise RewritePackageError("rewrite-candidate-invalid")
    source_windows: set[bytes] = set()
    for sentence in _sentence_texts(transcript):
        normalized = normalized_reading_units(sentence)
        if len(normalized) < window_units:
            continue
        for start in range(len(normalized) - window_units + 1):
            source_windows.add(
                hashlib.sha256(
                    normalized[start : start + window_units].encode("utf-8")
                ).digest()
            )
    matched: list[int] = []
    for line_number, line in enumerate(candidate.spoken_text.splitlines(), start=1):
        normalized = normalized_reading_units(line)
        for start in range(max(0, len(normalized) - window_units + 1)):
            digest = hashlib.sha256(
                normalized[start : start + window_units].encode("utf-8")
            ).digest()
            if digest in source_windows:
                matched.append(line_number)
                break
    return tuple(matched)


def _verified_transcript(root: Path, work_id: str) -> tuple[Path, str, str, Path]:
    try:
        order = load_work_order(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if current.stage != "transcript_ready":
            raise ValueError
        manifest_path = verify_private_relative(
            order.private_root, "transcript-manifest.json"
        )
        manifest_sha256 = sha256_file(manifest_path)
        if manifest_sha256 != current.artifact_sha256:
            raise ValueError
        manifest = load_exact_json(manifest_path, _TRANSCRIPT_MANIFEST_KEYS)
        artifact = manifest["artifact"]
        summary = manifest["summary"]
        if not (
            manifest["schema_version"] == 1
            and manifest["work_id"] == work_id
            and _digest(manifest["upstream_sha256"])
            and isinstance(artifact, dict)
            and set(artifact) == {"relative_path", "sha256", "size_bytes"}
            and artifact["relative_path"] == "transcript.json"
            and _digest(artifact["sha256"])
            and type(artifact["size_bytes"]) is int
            and artifact["size_bytes"] > 0
            and isinstance(manifest["request_id"], str)
            and bool(manifest["request_id"])
            and isinstance(summary, dict)
            and set(summary) == {"duration", "language", "segment_count"}
            and type(summary["duration"]) in {int, float}
            and math.isfinite(float(summary["duration"]))
            and float(summary["duration"]) >= 0
            and isinstance(summary["language"], str)
            and bool(summary["language"])
            and type(summary["segment_count"]) is int
            and summary["segment_count"] >= 0
        ):
            raise ValueError
        transcript_path = verify_private_relative(order.private_root, "transcript.json")
        if (
            sha256_file(transcript_path) != artifact["sha256"]
            or transcript_path.stat().st_size != artifact["size_bytes"]
        ):
            raise ValueError
        transcript = load_exact_json(transcript_path, _TRANSCRIPT_KEYS)
        if not (
            type(transcript["duration"]) in {int, float}
            and math.isfinite(float(transcript["duration"]))
            and float(transcript["duration"]) >= 0
            and isinstance(transcript["language"], str)
            and bool(transcript["language"])
            and isinstance(transcript["source_id"], str)
            and bool(transcript["source_id"])
            and isinstance(transcript["sentences"], list)
            and all(isinstance(item, dict) for item in transcript["sentences"])
            and float(transcript["duration"]) == float(summary["duration"])
            and transcript["language"] == summary["language"]
            and len(transcript["sentences"]) == summary["segment_count"]
        ):
            raise ValueError
        return transcript_path, artifact["sha256"], manifest_sha256, order.private_root
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        raise RewritePackageError("rewrite-transcript-invalid") from None


def _verified_article(root: Path, work_id: str) -> RewriteSource:
    try:
        order = load_work_order(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if (
            order.source_kind != "x-article"
            or current.source_kind != "x-article"
            or current.stage not in {"article_ready", "rewrite_ready", "handoff_ready"}
            or (
                current.stage == "article_ready"
                and current.artifact_label != "x-article-manifest"
            )
        ):
            raise ValueError
        manifest_path = verify_private_relative(
            order.private_root, "x-article-manifest.json"
        )
        manifest_sha = sha256_file(manifest_path)
        if current.stage == "article_ready" and manifest_sha != current.artifact_sha256:
            raise ValueError
        manifest = load_exact_json(manifest_path, _ARTICLE_MANIFEST_KEYS)
        if not (
            manifest["schema_version"] == 1
            and manifest["work_id"] == work_id
            and manifest["provider"] == "x-public-relay-html"
            and manifest["source_input_sha256"] == order.source_input_sha256
            and _digest(manifest["article_json_sha256"])
            and _digest(manifest["article_markdown_sha256"])
            and isinstance(manifest["assets"], list)
        ):
            raise ValueError
        article_json_path = verify_private_relative(
            order.private_root, "article-source/article.json"
        )
        article_path = verify_private_relative(
            order.private_root, "article-source/article.md"
        )
        if (
            sha256_file(article_json_path) != manifest["article_json_sha256"]
            or sha256_file(article_path) != manifest["article_markdown_sha256"]
        ):
            raise ValueError
        for asset in manifest["assets"]:
            if not (
                isinstance(asset, dict)
                and set(asset)
                == {
                    "asset_id",
                    "relative_path",
                    "sha256",
                    "bytes",
                    "width",
                    "height",
                    "format",
                }
                and isinstance(asset["asset_id"], str)
                and isinstance(asset["relative_path"], str)
                and asset["relative_path"].startswith("article-source/images/")
                and _digest(asset["sha256"])
                and type(asset["bytes"]) is int
                and asset["bytes"] > 0
                and type(asset["width"]) is int
                and asset["width"] > 0
                and type(asset["height"]) is int
                and asset["height"] > 0
                and asset["format"] in {"jpeg", "png", "webp"}
            ):
                raise ValueError
            path = verify_private_relative(order.private_root, asset["relative_path"])
            if sha256_file(path) != asset["sha256"] or path.stat().st_size != asset["bytes"]:
                raise ValueError
        return RewriteSource(
            kind="x-article",
            path=article_path,
            artifact_sha256=manifest["article_markdown_sha256"],  # type: ignore[arg-type]
            manifest_sha256=manifest_sha,
            private_root=order.private_root,
        )
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        raise RewritePackageError("rewrite-article-invalid") from None


def _verified_github_skill(root: Path, work_id: str) -> RewriteSource:
    try:
        order = load_work_order(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if (
            order.source_kind != "github-skill"
            or current.source_kind != "github-skill"
            or current.stage
            not in {"github_skill_ready", "rewrite_ready", "handoff_ready"}
            or (
                current.stage == "github_skill_ready"
                and current.artifact_label != "github-skill-manifest"
            )
        ):
            raise ValueError
        manifest_path = verify_private_relative(
            order.private_root, "github-skill-manifest.json"
        )
        manifest_sha = sha256_file(manifest_path)
        if (
            current.stage == "github_skill_ready"
            and manifest_sha != current.artifact_sha256
        ):
            raise ValueError
        manifest = load_exact_json(
            manifest_path, _GITHUB_SKILL_MANIFEST_KEYS
        )
        source_input = verify_private_relative(
            order.private_root, "source-input.txt"
        )
        source_lines = source_input.read_text("utf-8").splitlines()
        if len(source_lines) != 1:
            raise ValueError
        target = parse_github_skill_url(source_lines[0])
        files = manifest["files"]
        repository = manifest["repository"]
        if not (
            manifest["schema_version"] == 1
            and manifest["work_id"] == work_id
            and manifest["source_kind"] == "github-skill"
            and manifest["source_input_sha256"] == order.source_input_sha256
            and isinstance(repository, str)
            and repository.casefold()
            == f"{target.owner}/{target.repo}".casefold()
            and manifest["requested_scope"] == target.scope
            and manifest["requested_path"] == target.requested_path
            and _SHA1_RE.fullmatch(manifest["resolved_commit"] or "")
            is not None
            and _REF_RE.fullmatch(manifest["default_branch"] or "")
            is not None
            and isinstance(manifest["license_status"], str)
            and (
                manifest["license_status"] in {"UNDECLARED", "UNKNOWN"}
                or _SPDX_RE.fullmatch(manifest["license_status"]) is not None
            )
            and _digest(manifest["tree_sha256"])
            and manifest["repository_markdown_path"]
            == "github-skill-source/repository.md"
            and _digest(manifest["repository_markdown_sha256"])
            and type(manifest["request_count"]) is int
            and 1 <= manifest["request_count"] <= 48
            and type(manifest["total_text_bytes"]) is int
            and 0 < manifest["total_text_bytes"] <= 4 * 1024 * 1024
            and isinstance(files, list)
            and 1 <= len(files) <= 44
            and _timestamp(manifest["created_at"])
        ):
            raise ValueError
        repository_path = verify_private_relative(
            order.private_root, "github-skill-source/repository.md"
        )
        if sha256_file(repository_path) != manifest["repository_markdown_sha256"]:
            raise ValueError
        seen: set[str] = set()
        total_bytes = 0
        for item in files:
            if not isinstance(item, dict) or set(item) != set(
                _GITHUB_SKILL_FILE_KEYS
            ):
                raise ValueError
            source_path = item["path"]
            local_path = item["local_path"]
            if not isinstance(source_path, str) or not isinstance(local_path, str):
                raise ValueError
            pure = PurePosixPath(source_path)
            if not (
                not pure.is_absolute()
                and ".." not in pure.parts
                and "\\" not in source_path
                and 1 <= len(pure.parts) <= 12
                and pure.suffix.lower() == ".md"
                and source_path.casefold() not in seen
                and local_path
                == f"github-skill-source/selected-files/{source_path}"
                and _SHA1_RE.fullmatch(item["blob_sha"] or "") is not None
                and _digest(item["sha256"])
                and type(item["bytes"]) is int
                and 0 < item["bytes"] <= 256 * 1024
                and item["role"] in {"root-readme", "skill", "reference"}
            ):
                raise ValueError
            selected = verify_private_relative(order.private_root, local_path)
            if (
                selected.stat().st_size != item["bytes"]
                or sha256_file(selected) != item["sha256"]
            ):
                raise ValueError
            seen.add(source_path.casefold())
            total_bytes += item["bytes"]
        if total_bytes != manifest["total_text_bytes"]:
            raise ValueError
        return RewriteSource(
            kind="github-skill",
            path=repository_path,
            artifact_sha256=manifest["repository_markdown_sha256"],
            manifest_sha256=manifest_sha,
            private_root=order.private_root,
        )
    except (
        KeyError,
        GitHubSkillProviderError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise RewritePackageError("rewrite-github-skill-invalid") from None


def _verified_rewrite_source(root: Path, work_id: str) -> RewriteSource:
    """Resolve and hash-verify the formal transcript or article rewrite source."""

    try:
        current = WashEventLedger(root).current(work_id)
    except SourceLedgerError:
        raise RewritePackageError("rewrite-source-invalid") from None
    if current.source_kind == "x-article":
        return _verified_article(root, work_id)
    if current.source_kind == "github-skill":
        return _verified_github_skill(root, work_id)
    path, artifact_sha, manifest_sha, private_root = _verified_transcript(root, work_id)
    return RewriteSource(
        kind="transcript",
        path=path,
        artifact_sha256=artifact_sha,
        manifest_sha256=manifest_sha,
        private_root=private_root,
    )


def prepare_rewrite_brief(
    root: Path,
    work_id: str,
    *,
    platform: str,
    duration_target_s: int,
    archive_slug: str,
) -> ArtifactRecord:
    if not _common_values(
        platform, PRODUCTION_RATIO, duration_target_s, archive_slug
    ):
        raise RewritePackageError("rewrite-brief-invalid")
    source = _verified_rewrite_source(Path(root), work_id)
    common = {
        "archive_slug": archive_slug,
        "content_goal": "create a source-free original production script",
        "duration_target_s": duration_target_s,
        "platform": platform,
        "ratio": PRODUCTION_RATIO,
        "required_reviews": list(REQUIRED_REVIEWS),
        "work_id": work_id,
    }
    if source.kind == "transcript":
        value = {
            **common,
            "schema_version": 1,
            "transcript_artifact": "transcript.json",
            "transcript_manifest_sha256": source.manifest_sha256,
            "transcript_sha256": source.artifact_sha256,
        }
    else:
        source_artifact = {
            "x-article": "article-source/article.md",
            "github-skill": "github-skill-source/repository.md",
        }.get(source.kind)
        if source_artifact is None:
            raise RewritePackageError("rewrite-source-invalid")
        value = {
            **common,
            "schema_version": 2,
            "source_kind": source.kind,
            "source_artifact": source_artifact,
            "source_manifest_sha256": source.manifest_sha256,
            "source_sha256": source.artifact_sha256,
        }
    try:
        path = verify_private_relative(source.private_root, "rewrite-brief.json")
        digest = publish_json_exclusive(source.private_root, path, value)
        return ArtifactRecord(
            relative_path="rewrite-brief.json",
            sha256=digest,
            size_bytes=path.stat().st_size,
            path=path,
        )
    except (SourceContractError, OSError):
        raise RewritePackageError("rewrite-brief-unavailable") from None


__all__ = [
    "PRODUCTION_RATIO",
    "REQUIRED_REVIEWS",
    "LEGACY_REQUIRED_REVIEWS",
    "RewriteBrief",
    "RewriteCandidate",
    "RewriteOpeningContract",
    "RewritePackageError",
    "RewriteReview",
    "RewriteSource",
    "_verified_rewrite_source",
    "RewriteSegment",
    "find_source_overlap_lines",
    "load_rewrite_candidate",
    "load_rewrite_review",
    "normalized_reading_units",
    "prepare_rewrite_brief",
    "reading_unit_count",
]

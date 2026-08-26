"""Audited correction from a legacy public ratio to horizontal production."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from boomearth.video.artifacts import (
    ArtifactError,
    capture_regular_file,
    snapshot_matches,
)
from boomearth.video.content_plan import ContentPlanError, parse_handoff_segments
from boomearth.workbench.handoff import validate_public_handoff
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.rewrite_package import PRODUCTION_RATIO
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


POLICY = "source-independent-horizontal-v1"
PRODUCTION_WIDTH = 1920
PRODUCTION_HEIGHT = 1080
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
_ARTICLE_PUBLICATION_KEYS = frozenset(
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


class ProductionPolicyError(ValueError):
    """A fixed-message production-policy failure."""

    def __repr__(self) -> str:
        return "ProductionPolicyError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ProductionPolicyResult:
    work_id: str
    project_slug: str
    handoff_sha256: str
    receipt_sha256: str
    status: str

    def __repr__(self) -> str:
        return "ProductionPolicyResult(<redacted>)"


def _after_handoff_captured(_snapshot) -> None:
    """Testing seam after the active handoff is captured."""


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _strict_json(path: Path, keys: frozenset[str]) -> dict[str, Any]:
    try:
        value = load_exact_json(path, keys)
    except (SourceContractError, OSError):
        raise ProductionPolicyError("production-policy-invalid") from None
    return value


def _active_project_matches_slug(active_project: str, publication_slug: str) -> bool:
    if active_project == publication_slug:
        return True
    suffix = f"-{publication_slug}"
    if not active_project.endswith(suffix):
        return False
    prefix = active_project[: -len(suffix)]
    try:
        return date.fromisoformat(prefix).isoformat() == prefix
    except ValueError:
        return False


def _context(root: Path, work_id: str, active_project: str):
    try:
        order = load_work_order(root, work_id)
        paths = WorkbenchPaths(root)
        project = paths.public_project("active", active_project)
        handoff = project / "交接稿.md"
        publication_path = verify_private_relative(
            order.private_root, "publication-receipt.json"
        )
        publication = _strict_json(
            publication_path,
            _ARTICLE_PUBLICATION_KEYS
            if order.source_kind == "x-article"
            else _PUBLICATION_KEYS,
        )
        if order.source_kind == "x-article":
            source_contract_valid = (
                publication["schema_version"] == 2
                and publication["source_kind"] == "x-article"
                and isinstance(publication["source_artifact_sha256"], str)
                and isinstance(publication["source_manifest_sha256"], str)
            )
        else:
            source_contract_valid = publication["schema_version"] == 1
        if not (
            source_contract_valid
            and publication["work_id"] == work_id
            and _active_project_matches_slug(
                active_project, publication["archive_slug"]
            )
            and isinstance(publication["handoff"], dict)
        ):
            raise ValueError
        publication_sha = sha256_file(publication_path)
        handoff_snapshot = capture_regular_file(handoff, within=paths.workbench_root)
        current = WashEventLedger(root).current(work_id)
    except (
        ArtifactError,
        ProductionPolicyError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise ProductionPolicyError("production-policy-invalid") from None
    return order, paths, handoff, handoff_snapshot, publication_sha, current


def _replace_ratio(payload: bytes) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ProductionPolicyError("production-handoff-invalid") from None
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    delimiters = [index for index, line in enumerate(lines) if line == "---"]
    if len(delimiters) < 2 or delimiters[0] != 0:
        raise ProductionPolicyError("production-handoff-invalid")
    frontmatter = lines[1 : delimiters[1]]
    ratio_indexes = [
        index for index, line in enumerate(frontmatter) if line.startswith("ratio:")
    ]
    if len(ratio_indexes) != 1:
        raise ProductionPolicyError("production-handoff-invalid")
    ratio_line = frontmatter[ratio_indexes[0]]
    if ratio_line not in {'ratio: "9:16"', f'ratio: "{PRODUCTION_RATIO}"'}:
        raise ProductionPolicyError("production-handoff-invalid")
    if 'status: "制作中"' not in frontmatter:
        raise ProductionPolicyError("production-handoff-invalid")
    absolute_index = ratio_indexes[0] + 1
    lines[absolute_index] = f'ratio: "{PRODUCTION_RATIO}"'
    rendered = "\n".join(lines)
    if validate_public_handoff(rendered):
        raise ProductionPolicyError("production-handoff-invalid")
    try:
        parse_handoff_segments(rendered)
    except ContentPlanError:
        raise ProductionPolicyError("production-handoff-invalid") from None
    if ratio_line == f'ratio: "{PRODUCTION_RATIO}"':
        return payload
    return rendered.encode("utf-8")


def _policy_value(
    *,
    work_id: str,
    active_project: str,
    publication_sha: str,
    before_sha: str,
    after_sha: str,
) -> dict[str, object]:
    return {
        "handoff_after_sha256": after_sha,
        "handoff_before_sha256": before_sha,
        "handoff_relative_path": f"制作中/{active_project}/交接稿.md",
        "original_publication_receipt_sha256": publication_sha,
        "policy": POLICY,
        "production_height": PRODUCTION_HEIGHT,
        "production_ratio": PRODUCTION_RATIO,
        "production_width": PRODUCTION_WIDTH,
        "schema_version": 1,
        "work_id": work_id,
    }


def _write_replacement(path: Path, payload: bytes, original) -> None:
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".handoff-policy-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if not snapshot_matches(original):
            raise ProductionPolicyError("production-handoff-changed")
        os.replace(temporary, path)
        temporary = None
        if sha256_file(path) != hashlib.sha256(payload).hexdigest():
            raise ProductionPolicyError("production-policy-invalid")
    except ProductionPolicyError:
        raise
    except (OSError, SourceContractError):
        raise ProductionPolicyError("production-policy-invalid") from None
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _result(
    work_id: str,
    active_project: str,
    handoff_sha: str,
    receipt_sha: str,
    status: str,
) -> ProductionPolicyResult:
    return ProductionPolicyResult(
        work_id, active_project, handoff_sha, receipt_sha, status
    )


def verify_horizontal_production_policy(
    root: Path, work_id: str, active_project: str
) -> ProductionPolicyResult:
    order, _paths, _handoff, handoff_snapshot, publication_sha, current = _context(
        Path(root), work_id, active_project
    )
    receipt_path = order.private_root / "production-policy-receipt.json"
    receipt = _strict_json(receipt_path, _POLICY_KEYS)
    try:
        receipt_sha = sha256_file(receipt_path)
        if not (
            receipt
            == _policy_value(
                work_id=work_id,
                active_project=active_project,
                publication_sha=publication_sha,
                before_sha=str(receipt["handoff_before_sha256"]),
                after_sha=handoff_snapshot.sha256,
            )
            and current.stage in {"production_started", "production_archived"}
            and (
                current.stage == "production_archived"
                or (
                    current.artifact_label == "production-policy-receipt"
                    and current.artifact_sha256 == receipt_sha
                )
            )
        ):
            raise ValueError
    except (SourceContractError, OSError, TypeError, ValueError):
        raise ProductionPolicyError("production-policy-invalid") from None
    return _result(
        work_id, active_project, handoff_snapshot.sha256, receipt_sha, "verified"
    )


def apply_horizontal_production_policy(
    root: Path, work_id: str, active_project: str
) -> ProductionPolicyResult:
    root = Path(root)
    order, _paths, handoff, handoff_snapshot, publication_sha, current = _context(
        root, work_id, active_project
    )
    _after_handoff_captured(handoff_snapshot)
    if current.stage == "production_started":
        return verify_horizontal_production_policy(root, work_id, active_project)
    if current.stage != "handoff_ready" or current.artifact_sha256 != publication_sha:
        raise ProductionPolicyError("production-policy-invalid")
    receipt_path = order.private_root / "production-policy-receipt.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ProductionPolicyError("production-policy-invalid")
    corrected = _replace_ratio(handoff_snapshot.payload)
    handoff_changed = corrected != handoff_snapshot.payload
    after_sha = hashlib.sha256(corrected).hexdigest()
    receipt_value = _policy_value(
        work_id=work_id,
        active_project=active_project,
        publication_sha=publication_sha,
        before_sha=handoff_snapshot.sha256,
        after_sha=after_sha,
    )
    try:
        receipt_sha = publish_json_exclusive(
            order.private_root, receipt_path, receipt_value
        )
        if handoff_changed:
            _write_replacement(handoff, corrected, handoff_snapshot)
        event = StageEvent(
            2,
            work_id,
            current.source_id,
            current.source_kind,
            "production_started",
            "ok",
            "production-policy-receipt",
            receipt_sha,
            _timestamp(),
            event_sha256(current),
        )
        WashEventLedger(root).append(event)
    except (ProductionPolicyError, SourceContractError, SourceLedgerError):
        try:
            if (
                handoff_changed
                and handoff.exists()
                and sha256_file(handoff) == after_sha
            ):
                _write_replacement(
                    handoff,
                    handoff_snapshot.payload,
                    capture_regular_file(handoff),
                )
            if receipt_path.exists() and sha256_file(receipt_path) == locals().get(
                "receipt_sha", ""
            ):
                receipt_path.unlink()
        except (ArtifactError, OSError, ProductionPolicyError, SourceContractError):
            pass
        raise ProductionPolicyError("production-policy-invalid") from None
    return _result(work_id, active_project, after_sha, receipt_sha, "applied")


__all__ = [
    "POLICY",
    "PRODUCTION_HEIGHT",
    "PRODUCTION_WIDTH",
    "ProductionPolicyError",
    "ProductionPolicyResult",
    "apply_horizontal_production_policy",
    "verify_horizontal_production_policy",
]

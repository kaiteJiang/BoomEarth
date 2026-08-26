"""Bind one verified P1 archive receipt to the terminal P2 ledger stage."""

from __future__ import annotations

import json
import importlib.util
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from boomearth.video.artifacts import ArtifactError, capture_regular_file, snapshot_matches
from boomearth.workbench.paths import WorkbenchPaths
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
_COMPLETION_KEYS = frozenset(
    {
        "archive_relative_path",
        "delivery_report_sha256",
        "final_video_sha256",
        "original_publication_receipt_sha256",
        "policy_receipt_sha256",
        "publication_manifest_sha256",
        "schema_version",
        "work_id",
    }
)


class ProductionArchiveError(ValueError):
    """A fixed-message production archive failure."""

    def __repr__(self) -> str:
        return "ProductionArchiveError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ProductionArchiveResult:
    work_id: str
    archive_relative: str
    delivery_report_sha256: str
    final_video_sha256: str
    status: str

    def __repr__(self) -> str:
        return "ProductionArchiveResult(<redacted>)"


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _before_terminal_append() -> None:
    """Testing seam before the final evidence recheck and ledger append."""


def _project_name_matches_slug(project_name: str, publication_slug: str) -> bool:
    if project_name == publication_slug:
        return True
    suffix = f"-{publication_slug}"
    if not project_name.endswith(suffix):
        return False
    prefix = project_name[: -len(suffix)]
    try:
        return date.fromisoformat(prefix).isoformat() == prefix
    except ValueError:
        return False


def _historical_delivery_pass(root: Path, archive: Path, final: Path) -> bool:
    script = Path(__file__).resolve().parents[3] / "automation" / "scripts" / "check_delivery.py"
    spec = importlib.util.spec_from_file_location("boomearth_terminal_delivery_check", script)
    if spec is None or spec.loader is None:
        return False
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        return False
    result = module.check_delivery(
        archive / "交接稿.md", archive, final, sample_mode=False
    )
    return result.exit_code == 0 and result.diagnostics == (
        "status=pass mode=historical",
    )


def _archive_evidence(
    root: Path,
    work_id: str,
    archive_relative: str,
):
    try:
        order = load_work_order(root, work_id)
        paths = WorkbenchPaths(root)
        archive = paths.public_project("archived", archive_relative)
        publication_path = order.private_root / "publication-receipt.json"
        publication = json.loads(publication_path.read_text("utf-8"))
        if not isinstance(publication, dict):
            raise ValueError
        publication_slug = publication.get("archive_slug")
        if not (
            publication.get("work_id") == work_id
            and isinstance(publication_slug, str)
            and _project_name_matches_slug(archive.name, publication_slug)
        ):
            raise ValueError
        policy_path = order.private_root / "production-policy-receipt.json"
        policy = load_exact_json(policy_path, _POLICY_KEYS)
        valid_handoff_paths = {
            f"制作中/{archive.name}/交接稿.md",
            f"制作中/{publication_slug}/交接稿.md",
        }
        if not (
            policy["schema_version"] == 1
            and policy["work_id"] == work_id
            and policy["policy"] == "source-independent-horizontal-v1"
            and policy["production_ratio"] == "16:9"
            and policy["production_width"] == 1920
            and policy["production_height"] == 1080
            and policy["original_publication_receipt_sha256"]
            == sha256_file(publication_path)
            and policy["handoff_relative_path"] in valid_handoff_paths
        ):
            raise ValueError
        policy_sha = sha256_file(policy_path)
        report_path = verify_private_relative(archive, "工程/delivery-report.json")
        report_snapshot = capture_regular_file(report_path, within=paths.workbench_root)
        manifest_snapshot = capture_regular_file(
            verify_private_relative(archive, "工程/publication-manifest.json"),
            within=paths.workbench_root,
        )
        report = json.loads(report_snapshot.payload.decode("utf-8"))
        if not isinstance(report, dict):
            raise ValueError
        media = report.get("media")
        artifacts = report.get("artifacts")
        artifact_sha256 = report.get("artifact_sha256")
        if not (
            report.get("status") == "pass"
            and report.get("mode") == "production"
            and isinstance(media, dict)
            and media.get("width") == 1920
            and media.get("height") == 1080
            and media.get("video_codec") == "h264"
            and media.get("audio_codec") == "aac"
            and isinstance(artifacts, dict)
            and isinstance(artifacts.get("final"), str)
            and isinstance(artifact_sha256, dict)
        ):
            raise ValueError
        final_root = artifacts["final"]
        final_candidates = [
            relative
            for relative in artifact_sha256
            if isinstance(relative, str)
            and relative.startswith(final_root)
            and Path(relative).suffix.casefold() == ".mp4"
        ]
        if final_root != "成片/" or len(final_candidates) != 1:
            raise ValueError
        final_relative = final_candidates[0]
        final_path = verify_private_relative(archive, final_relative)
        final_snapshot = capture_regular_file(final_path, within=archive)
        if artifact_sha256.get(final_relative) != final_snapshot.sha256:
            raise ValueError
        if not _historical_delivery_pass(root, archive, final_path):
            raise ValueError
        if not all(
            snapshot_matches(item)
            for item in (report_snapshot, manifest_snapshot, final_snapshot)
        ):
            raise ValueError
        current = WashEventLedger(root).current(work_id)
    except (
        ArtifactError,
        OSError,
        SourceContractError,
        SourceLedgerError,
        TypeError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise ProductionArchiveError("production-archive-invalid") from None
    return (
        order,
        report_snapshot,
        manifest_snapshot,
        final_snapshot,
        policy_sha,
        sha256_file(publication_path),
        current,
    )


def _completion_value(
    *,
    work_id: str,
    archive_relative: str,
    report_sha: str,
    manifest_sha: str,
    final_sha: str,
    policy_sha: str,
    publication_sha: str,
) -> dict[str, object]:
    return {
        "archive_relative_path": archive_relative,
        "delivery_report_sha256": report_sha,
        "final_video_sha256": final_sha,
        "original_publication_receipt_sha256": publication_sha,
        "policy_receipt_sha256": policy_sha,
        "publication_manifest_sha256": manifest_sha,
        "schema_version": 1,
        "work_id": work_id,
    }


def verify_production_archive(
    root: Path,
    work_id: str,
    archive_relative: str,
) -> ProductionArchiveResult:
    root = Path(root)
    order, report, manifest, final, policy_sha, publication_sha, current = _archive_evidence(
        root, work_id, archive_relative
    )
    valid_terminal = (
        current.stage == "production_archived"
        and current.artifact_label == "delivery-report"
        and current.artifact_sha256 == report.sha256
    )
    if current.stage == "production_archived" and current.artifact_label == "archive-completion-receipt":
        try:
            receipt_path = order.private_root / "archive-completion-receipt.json"
            receipt = load_exact_json(receipt_path, _COMPLETION_KEYS)
            valid_terminal = (
                receipt
                == _completion_value(
                    work_id=work_id,
                    archive_relative=archive_relative,
                    report_sha=report.sha256,
                    manifest_sha=manifest.sha256,
                    final_sha=final.sha256,
                    policy_sha=policy_sha,
                    publication_sha=publication_sha,
                )
                and current.artifact_sha256 == sha256_file(receipt_path)
            )
        except (OSError, SourceContractError):
            valid_terminal = False
    if not valid_terminal:
        raise ProductionArchiveError("production-archive-invalid")
    return ProductionArchiveResult(
        work_id, archive_relative, report.sha256, final.sha256, "verified"
    )


def complete_production_archive(
    root: Path,
    work_id: str,
    archive_relative: str,
) -> ProductionArchiveResult:
    root = Path(root)
    order, report, manifest, final, policy_sha, publication_sha, current = _archive_evidence(
        root, work_id, archive_relative
    )
    if current.stage == "production_archived":
        return verify_production_archive(root, work_id, archive_relative)
    if not (
        current.stage == "production_started"
        and current.artifact_label == "production-policy-receipt"
        and current.artifact_sha256 == policy_sha
    ):
        raise ProductionArchiveError("production-archive-invalid")
    _before_terminal_append()
    rechecked_order, rechecked_report, rechecked_manifest, rechecked_final, rechecked_policy, rechecked_publication, rechecked_current = _archive_evidence(
        root, work_id, archive_relative
    )
    if not (
        rechecked_report.sha256 == report.sha256
        and rechecked_final.sha256 == final.sha256
        and rechecked_policy == policy_sha
        and rechecked_publication == publication_sha
        and rechecked_current == current
    ):
        raise ProductionArchiveError("production-archive-invalid")
    receipt_path = order.private_root / "archive-completion-receipt.json"
    receipt_value = _completion_value(
        work_id=work_id,
        archive_relative=archive_relative,
        report_sha=rechecked_report.sha256,
        manifest_sha=rechecked_manifest.sha256,
        final_sha=rechecked_final.sha256,
        policy_sha=rechecked_policy,
        publication_sha=rechecked_publication,
    )
    try:
        if receipt_path.exists():
            receipt = load_exact_json(receipt_path, _COMPLETION_KEYS)
            if receipt != receipt_value:
                raise SourceContractError("artifact-exists")
            receipt_sha = sha256_file(receipt_path)
        else:
            receipt_sha = publish_json_exclusive(
                rechecked_order.private_root, receipt_path, receipt_value
            )
    except (OSError, SourceContractError):
        raise ProductionArchiveError("production-archive-invalid") from None
    event = StageEvent(
        2,
        work_id,
        current.source_id,
        current.source_kind,
        "production_archived",
        "ok",
        "archive-completion-receipt",
        receipt_sha,
        _timestamp(),
        event_sha256(current),
    )
    try:
        appended = WashEventLedger(root).append(event)
    except SourceLedgerError:
        try:
            return verify_production_archive(root, work_id, archive_relative)
        except ProductionArchiveError:
            raise ProductionArchiveError("production-archive-invalid") from None
    if not appended:
        return verify_production_archive(root, work_id, archive_relative)
    verified = verify_production_archive(root, work_id, archive_relative)
    return ProductionArchiveResult(
        work_id,
        archive_relative,
        verified.delivery_report_sha256,
        verified.final_video_sha256,
        "applied",
    )


__all__ = [
    "ProductionArchiveError",
    "ProductionArchiveResult",
    "complete_production_archive",
    "verify_production_archive",
]

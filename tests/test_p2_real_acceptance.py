from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.handoff_compiler import recover_handoff_receipt
from boomearth.workbench.production_archive import verify_production_archive
from boomearth.workbench.source_artifacts import sha256_file
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import WashEventLedger


REAL_ACCEPTANCE_ENABLED = os.environ.get("BOOMEARTH_P2_REAL") == "1"


def _resolve_physical_project(parent: Path, slug: str, *, nested: bool = False) -> Path:
    prefix = "*/" if nested else ""
    matches = {
        path.resolve()
        for pattern in (f"{prefix}{slug}", f"{prefix}????-??-??-{slug}")
        for path in parent.glob(pattern)
        if path.is_dir()
    }
    assert len(matches) == 1
    return matches.pop()


@pytest.mark.skipif(
    not REAL_ACCEPTANCE_ENABLED,
    reason="explicit P2 real acceptance required",
)
def test_real_p2_artifact_chain_from_prepared_runtime() -> None:
    root_value = os.environ.get("BOOMEARTH_ROOT")
    work_id = os.environ.get("BOOMEARTH_P2_WORK_ID")
    assert root_value and work_id

    root = Path(root_value)
    assert root.is_absolute() and root.is_dir()
    order = load_work_order(root, work_id)
    assert order.work_id == work_id

    current = WashEventLedger(root).current(work_id)
    assert current.stage in {
        "handoff_ready",
        "production_started",
        "production_archived",
    }

    receipt_path = order.private_root / "publication-receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    assert receipt["schema_version"] in {1, 2}
    assert receipt["work_id"] == work_id
    assert receipt["archive_slug"]
    if receipt["schema_version"] == 2:
        assert receipt["source_kind"] == "x-article"
        for key in ("source_artifact_sha256", "source_manifest_sha256"):
            assert isinstance(receipt[key], str) and len(receipt[key]) == 64

    if current.stage == "handoff_ready":
        handoff = receipt["handoff"]
        assert set(handoff) == {"relative_path", "sha256", "size_bytes"}
        handoff_path = WorkbenchPaths(root).workbench_root / handoff["relative_path"]
        assert handoff_path.is_file()
        assert handoff_path.stat().st_size == handoff["size_bytes"]
        assert sha256_file(handoff_path) == handoff["sha256"]
        assert sha256_file(receipt_path) == current.artifact_sha256
        publication = recover_handoff_receipt(root, work_id)
        assert publication.handoff_sha256 == handoff["sha256"]
    elif current.stage == "production_started":
        policy_path = order.private_root / "production-policy-receipt.json"
        policy = json.loads(policy_path.read_text("utf-8"))
        assert policy["schema_version"] == 1
        assert policy["work_id"] == work_id
        assert policy["production_ratio"] == "16:9"
        assert (policy["production_width"], policy["production_height"]) == (
            1920,
            1080,
        )
        assert policy["original_publication_receipt_sha256"] == sha256_file(
            receipt_path
        )
        active = _resolve_physical_project(
            WorkbenchPaths(root).active, receipt["archive_slug"]
        )
        handoff_path = active / "交接稿.md"
        assert handoff_path.is_file()
        assert sha256_file(handoff_path) == policy["handoff_after_sha256"]
        assert 'ratio: "16:9"' in handoff_path.read_text("utf-8")
        assert current.artifact_label == "production-policy-receipt"
        assert current.artifact_sha256 == sha256_file(policy_path)
    else:
        paths = WorkbenchPaths(root)
        archive = _resolve_physical_project(
            paths.archived, receipt["archive_slug"], nested=True
        )
        archive_relative = archive.relative_to(paths.archived).as_posix()
        verified = verify_production_archive(root, work_id, archive_relative)
        assert verified.status == "verified"
        report = archive / "工程" / "delivery-report.json"
        final = archive / "成片" / "boomearth-v2-production.mp4"
        assert verified.delivery_report_sha256 == sha256_file(report)
        assert verified.final_video_sha256 == sha256_file(final)
        if current.artifact_label == "delivery-report":
            assert current.artifact_sha256 == sha256_file(report)
        else:
            assert current.artifact_label == "archive-completion-receipt"
            completion = order.private_root / "archive-completion-receipt.json"
            value = json.loads(completion.read_text("utf-8"))
            assert set(value) == {
                "archive_relative_path",
                "delivery_report_sha256",
                "final_video_sha256",
                "original_publication_receipt_sha256",
                "policy_receipt_sha256",
                "publication_manifest_sha256",
                "schema_version",
                "work_id",
            }
            assert value["work_id"] == work_id
            assert value["archive_relative_path"] == archive_relative
            assert value["delivery_report_sha256"] == sha256_file(report)
            assert value["final_video_sha256"] == sha256_file(final)
            assert current.artifact_sha256 == sha256_file(completion)
        policy_path = order.private_root / "production-policy-receipt.json"
        policy = json.loads(policy_path.read_text("utf-8"))
        assert policy["production_ratio"] == "16:9"
        assert (policy["production_width"], policy["production_height"]) == (
            1920,
            1080,
        )

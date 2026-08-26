from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench import production_archive
from boomearth.workbench.production_archive import (
    ProductionArchiveError,
    complete_production_archive,
    verify_production_archive,
)
from boomearth.workbench.handoff_compiler import compile_source_handoff
from boomearth.workbench.production_policy import apply_horizontal_production_policy
from boomearth.workbench.source_artifacts import canonical_json_bytes, sha256_file
from boomearth.workbench.source_ledger import WashEventLedger
from test_production_policy import (
    ARTICLE_SLUG,
    DATED_ARTICLE_PROJECT,
    SLUG,
    _legacy_active_project,
)
from test_source_handoff_compiler import (
    WORK_ID,
    _complete_article_rewrite_work,
    _work_root,
)


ARCHIVE_RELATIVE = f"8月上旬/{SLUG}"


def _archived_project(root: Path) -> tuple[Path, Path]:
    active, _publication, _before = _legacy_active_project(root)
    apply_horizontal_production_policy(root, WORK_ID, SLUG)
    archive = WorkbenchPaths(root).archived / Path(ARCHIVE_RELATIVE)
    archive.parent.mkdir(parents=True)
    os.rename(active, archive)
    final = archive / "成片" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"final-video")
    report = archive / "工程" / "delivery-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    (report.parent / "publication-manifest.json").write_text("{}\n", encoding="utf-8")
    report.write_bytes(
        canonical_json_bytes(
            {
                "artifact_sha256": {"成片/final.mp4": sha256_file(final)},
                "artifacts": {"final": "成片/"},
                "media": {
                    "audio_codec": "aac",
                    "height": 1080,
                    "video_codec": "h264",
                    "width": 1920,
                },
                "mode": "production",
                "status": "pass",
            }
        )
    )
    return archive, report


def _dated_article_archive_with_undated_policy(root: Path) -> tuple[Path, Path]:
    candidate, review = _complete_article_rewrite_work(root)
    compile_source_handoff(root, WORK_ID, candidate, review)
    paths = WorkbenchPaths(root)
    pending = paths.pending / ARTICLE_SLUG
    active = paths.active / ARTICLE_SLUG
    paths.active.mkdir(parents=True, exist_ok=True)
    os.rename(pending, active)
    handoff = active / "交接稿.md"
    handoff.write_text(
        handoff.read_text("utf-8").replace(
            'status: "待制作"', 'status: "制作中"', 1
        ),
        encoding="utf-8",
        newline="",
    )
    apply_horizontal_production_policy(root, WORK_ID, ARTICLE_SLUG)

    archive = paths.archived / "8月上旬" / DATED_ARTICLE_PROJECT
    archive.parent.mkdir(parents=True)
    os.rename(active, archive)
    final = archive / "成片" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"final-video")
    report = archive / "工程" / "delivery-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    (report.parent / "publication-manifest.json").write_text(
        "{}\n", encoding="utf-8"
    )
    report.write_bytes(
        canonical_json_bytes(
            {
                "artifact_sha256": {"成片/final.mp4": sha256_file(final)},
                "artifacts": {"final": "成片/"},
                "media": {
                    "audio_codec": "aac",
                    "height": 1080,
                    "video_codec": "h264",
                    "width": 1920,
                },
                "mode": "production",
                "status": "pass",
            }
        )
    )
    return archive, report


def test_complete_archive_binds_exact_delivery_report_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _archive, report = _archived_project(tmp_path)
    monkeypatch.setattr(production_archive, "_historical_delivery_pass", lambda *_: True)

    applied = complete_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)

    assert applied.status == "applied"
    assert applied.delivery_report_sha256 == sha256_file(report)
    current = WashEventLedger(tmp_path).current(WORK_ID)
    assert current.stage == "production_archived"
    assert current.artifact_label == "archive-completion-receipt"
    ledger_before = WashEventLedger(tmp_path).ledger_path.read_bytes()

    verified = complete_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)

    assert verified.status == "verified"
    assert verify_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE) == verified
    assert WashEventLedger(tmp_path).ledger_path.read_bytes() == ledger_before


def test_complete_archive_accepts_dated_article_project_with_undated_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _archive, report = _dated_article_archive_with_undated_policy(tmp_path)
    monkeypatch.setattr(
        production_archive, "_historical_delivery_pass", lambda *_: True
    )
    archive_relative = f"8月上旬/{DATED_ARTICLE_PROJECT}"

    applied = complete_production_archive(tmp_path, WORK_ID, archive_relative)

    assert applied.status == "applied"
    assert applied.delivery_report_sha256 == sha256_file(report)
    current = WashEventLedger(tmp_path).current(WORK_ID)
    assert current.stage == "production_archived"
    assert current.artifact_label == "archive-completion-receipt"


def test_archive_verification_rejects_changed_delivery_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _archive, report = _archived_project(tmp_path)
    monkeypatch.setattr(production_archive, "_historical_delivery_pass", lambda *_: True)
    complete_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)
    value = json.loads(report.read_text("utf-8"))
    value["media"]["width"] = 1280
    report.write_bytes(canonical_json_bytes(value))

    with pytest.raises(ProductionArchiveError, match="^production-archive-invalid$"):
        verify_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)


def test_archive_completion_rejects_unbound_policy_stage(tmp_path: Path) -> None:
    active, _publication, _before = _legacy_active_project(tmp_path)
    archive = WorkbenchPaths(tmp_path).archived / Path(ARCHIVE_RELATIVE)
    archive.parent.mkdir(parents=True)
    os.rename(active, archive)

    with pytest.raises(ProductionArchiveError, match="^production-archive-invalid$"):
        complete_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)

    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"
    assert not (_work_root(tmp_path) / "production-policy-receipt.json").exists()


def test_archive_completion_rejects_invalid_mp4_without_historical_pass(
    tmp_path: Path,
) -> None:
    _archived_project(tmp_path)

    with pytest.raises(ProductionArchiveError, match="^production-archive-invalid$"):
        complete_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)

    assert WashEventLedger(tmp_path).status(WORK_ID) == "production_started"


def test_archive_completion_rejects_changed_original_publication_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _archived_project(tmp_path)
    monkeypatch.setattr(production_archive, "_historical_delivery_pass", lambda *_: True)
    publication = _work_root(tmp_path) / "publication-receipt.json"
    value = json.loads(publication.read_text("utf-8"))
    value["archive_slug"] = "other-project"
    publication.write_bytes(canonical_json_bytes(value))

    with pytest.raises(ProductionArchiveError, match="^production-archive-invalid$"):
        complete_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)


def test_archive_mutation_before_terminal_recheck_never_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _archive, report = _archived_project(tmp_path)
    monkeypatch.setattr(production_archive, "_historical_delivery_pass", lambda *_: True)

    def mutate() -> None:
        report.write_bytes(report.read_bytes() + b"changed")

    monkeypatch.setattr(production_archive, "_before_terminal_append", mutate)

    with pytest.raises(ProductionArchiveError, match="^production-archive-invalid$"):
        complete_production_archive(tmp_path, WORK_ID, ARCHIVE_RELATIVE)

    assert WashEventLedger(tmp_path).status(WORK_ID) == "production_started"

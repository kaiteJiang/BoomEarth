from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from boomearth.workbench import production_policy
from boomearth.workbench.handoff_compiler import compile_source_handoff
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.production_policy import (
    ProductionPolicyError,
    apply_horizontal_production_policy,
    verify_horizontal_production_policy,
)
from boomearth.workbench.rewrite_package import prepare_rewrite_brief
from boomearth.workbench.source_artifacts import (
    SourceContractError,
    canonical_json_bytes,
    sha256_file,
)
from boomearth.workbench.source_ledger import SourceLedgerError, WashEventLedger
from test_source_handoff_compiler import (
    WORK_ID,
    _complete_article_rewrite_work,
    _complete_rewrite_work,
    _work_root,
)
from test_github_skill_rewrite import (
    WORK_ID as GITHUB_SKILL_WORK_ID,
    _candidate_and_review as _github_skill_candidate_and_review,
    _github_ready,
)


SLUG = "source-free-project"
ARTICLE_SLUG = "article-source-free-project"
DATED_ARTICLE_PROJECT = f"2026-08-14-{ARTICLE_SLUG}"
GITHUB_SKILL_SLUG = "github-skill-source-free"
RECEIPT_KEYS = {
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


def _legacy_active_project(
    root: Path, *, legacy_ratio: bool = True
) -> tuple[Path, Path, str]:
    candidate, review = _complete_rewrite_work(root)
    compile_source_handoff(root, WORK_ID, candidate, review)
    paths = WorkbenchPaths(root)
    pending = paths.pending / SLUG
    handoff = pending / "交接稿.md"
    if legacy_ratio:
        legacy = handoff.read_text("utf-8").replace(
            'ratio: "16:9"', 'ratio: "9:16"', 1
        )
        handoff.write_text(legacy, encoding="utf-8", newline="")

    publication_path = _work_root(root) / "publication-receipt.json"
    publication = json.loads(publication_path.read_text("utf-8"))
    publication["handoff"]["sha256"] = sha256_file(handoff)
    publication["handoff"]["size_bytes"] = handoff.stat().st_size
    publication_path.write_bytes(canonical_json_bytes(publication))
    publication_sha = sha256_file(publication_path)

    ledger_path = WashEventLedger(root).ledger_path
    rows = [json.loads(line) for line in ledger_path.read_text("utf-8").splitlines()]
    rows[-1]["artifact_sha256"] = publication_sha
    ledger_path.write_bytes(b"".join(canonical_json_bytes(row) for row in rows))

    active = paths.active / SLUG
    paths.active.mkdir(parents=True, exist_ok=True)
    os.rename(pending, active)
    handoff = active / "交接稿.md"
    active_text = handoff.read_text("utf-8").replace(
        'status: "待制作"', 'status: "制作中"', 1
    )
    handoff.write_text(active_text, encoding="utf-8", newline="")
    return active, publication_path, sha256_file(handoff)


def test_apply_binds_native_horizontal_handoff_without_changing_bytes(
    tmp_path: Path,
) -> None:
    active, publication_path, before_sha = _legacy_active_project(
        tmp_path, legacy_ratio=False
    )
    handoff = active / "交接稿.md"
    before = handoff.read_bytes()

    result = apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    assert handoff.read_bytes() == before
    assert result.handoff_sha256 == before_sha
    assert result.status == "applied"
    receipt_path = _work_root(tmp_path) / "production-policy-receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    assert receipt["handoff_before_sha256"] == before_sha
    assert receipt["handoff_after_sha256"] == before_sha
    assert receipt["original_publication_receipt_sha256"] == sha256_file(
        publication_path
    )
    assert WashEventLedger(tmp_path).status(WORK_ID) == "production_started"


def test_apply_accepts_x_article_publication_receipt(tmp_path: Path) -> None:
    candidate, review = _complete_article_rewrite_work(tmp_path)
    compile_source_handoff(tmp_path, WORK_ID, candidate, review)
    paths = WorkbenchPaths(tmp_path)
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

    result = apply_horizontal_production_policy(tmp_path, WORK_ID, ARTICLE_SLUG)

    assert result.status == "applied"
    assert result.handoff_sha256 == sha256_file(handoff)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "production_started"


def test_apply_accepts_github_skill_publication_receipt(tmp_path: Path) -> None:
    private_root = _github_ready(tmp_path)
    prepare_rewrite_brief(
        tmp_path,
        GITHUB_SKILL_WORK_ID,
        platform="douyin",
        duration_target_s=75,
        archive_slug=GITHUB_SKILL_SLUG,
    )
    candidate, review = _github_skill_candidate_and_review(private_root)
    compile_source_handoff(tmp_path, GITHUB_SKILL_WORK_ID, candidate, review)
    paths = WorkbenchPaths(tmp_path)
    pending = paths.pending / GITHUB_SKILL_SLUG
    active = paths.active / GITHUB_SKILL_SLUG
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

    result = apply_horizontal_production_policy(
        tmp_path, GITHUB_SKILL_WORK_ID, GITHUB_SKILL_SLUG
    )

    assert result.status == "applied"
    assert result.handoff_sha256 == sha256_file(handoff)
    assert (
        WashEventLedger(tmp_path).status(GITHUB_SKILL_WORK_ID)
        == "production_started"
    )


def test_apply_accepts_dated_active_project_for_undated_article_slug(
    tmp_path: Path,
) -> None:
    candidate, review = _complete_article_rewrite_work(tmp_path)
    compile_source_handoff(tmp_path, WORK_ID, candidate, review)
    paths = WorkbenchPaths(tmp_path)
    pending = paths.pending / ARTICLE_SLUG
    active = paths.active / DATED_ARTICLE_PROJECT
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

    result = apply_horizontal_production_policy(
        tmp_path, WORK_ID, DATED_ARTICLE_PROJECT
    )

    assert result.status == "applied"
    receipt = json.loads(
        (_work_root(tmp_path) / "production-policy-receipt.json").read_text(
            "utf-8"
        )
    )
    assert receipt["handoff_relative_path"] == (
        f"制作中/{DATED_ARTICLE_PROJECT}/交接稿.md"
    )
    assert WashEventLedger(tmp_path).status(WORK_ID) == "production_started"


def test_apply_corrects_only_ratio_and_binds_receipt_and_ledger(tmp_path: Path) -> None:
    active, publication_path, before_sha = _legacy_active_project(tmp_path)
    handoff = active / "交接稿.md"
    before = handoff.read_text("utf-8")

    result = apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    after = handoff.read_text("utf-8")
    assert after == before.replace('ratio: "9:16"', 'ratio: "16:9"', 1)
    assert result.handoff_sha256 == sha256_file(handoff)
    assert result.status == "applied"
    assert "source-free-project" not in repr(result)

    receipt_path = _work_root(tmp_path) / "production-policy-receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    assert set(receipt) == RECEIPT_KEYS
    assert receipt == {
        "handoff_after_sha256": sha256_file(handoff),
        "handoff_before_sha256": before_sha,
        "handoff_relative_path": f"制作中/{SLUG}/交接稿.md",
        "original_publication_receipt_sha256": sha256_file(publication_path),
        "policy": "source-independent-horizontal-v1",
        "production_height": 1080,
        "production_ratio": "16:9",
        "production_width": 1920,
        "schema_version": 1,
        "work_id": WORK_ID,
    }
    current = WashEventLedger(tmp_path).current(WORK_ID)
    assert current.stage == "production_started"
    assert current.artifact_label == "production-policy-receipt"
    assert current.artifact_sha256 == sha256_file(receipt_path)


def test_second_apply_is_a_verification_without_second_event(tmp_path: Path) -> None:
    _legacy_active_project(tmp_path)
    apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)
    ledger = WashEventLedger(tmp_path).ledger_path
    rows = ledger.read_bytes()

    result = apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    assert result.status == "verified"
    assert ledger.read_bytes() == rows
    assert verify_horizontal_production_policy(tmp_path, WORK_ID, SLUG) == result


def test_apply_rejects_unexpected_ratio_without_artifacts(tmp_path: Path) -> None:
    active, _publication, _before = _legacy_active_project(tmp_path)
    handoff = active / "交接稿.md"
    handoff.write_text(
        handoff.read_text("utf-8").replace('ratio: "9:16"', 'ratio: "1:1"'),
        encoding="utf-8",
        newline="",
    )

    with pytest.raises(ProductionPolicyError, match="^production-handoff-invalid$"):
        apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    assert not (_work_root(tmp_path) / "production-policy-receipt.json").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_verify_rejects_changed_corrected_handoff(tmp_path: Path) -> None:
    active, _publication, _before = _legacy_active_project(tmp_path)
    apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)
    handoff = active / "交接稿.md"
    handoff.write_bytes(handoff.read_bytes() + b"\nchanged\n")

    with pytest.raises(ProductionPolicyError, match="^production-policy-invalid$"):
        verify_horizontal_production_policy(tmp_path, WORK_ID, SLUG)


def test_conflicting_existing_policy_receipt_fails_without_replacing_handoff(
    tmp_path: Path,
) -> None:
    active, _publication, before_sha = _legacy_active_project(tmp_path)
    receipt = _work_root(tmp_path) / "production-policy-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    conflicting = receipt.read_bytes()

    with pytest.raises(ProductionPolicyError, match="^production-policy-invalid$"):
        apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    assert sha256_file(active / "交接稿.md") == before_sha
    assert receipt.read_bytes() == conflicting
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_handoff_changed_after_capture_fails_without_policy_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active, _publication, _before = _legacy_active_project(tmp_path)
    handoff = active / "交接稿.md"

    def mutate(_snapshot) -> None:
        handoff.write_bytes(handoff.read_bytes() + b"\nchanged\n")

    monkeypatch.setattr(
        production_policy, "_after_handoff_captured", mutate, raising=False
    )

    with pytest.raises(
        ProductionPolicyError, match="^production-policy-invalid$"
    ):
        apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    assert not (_work_root(tmp_path) / "production-policy-receipt.json").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"


def test_receipt_publication_failure_keeps_original_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active, _publication, before_sha = _legacy_active_project(tmp_path)

    def fail_publish(*_args, **_kwargs):
        raise SourceContractError("artifact-unavailable")

    monkeypatch.setattr(production_policy, "publish_json_exclusive", fail_publish)

    with pytest.raises(ProductionPolicyError, match="^production-policy-invalid$"):
        apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    assert sha256_file(active / "交接稿.md") == before_sha
    assert not (_work_root(tmp_path) / "production-policy-receipt.json").exists()


def test_ledger_failure_rolls_back_exact_handoff_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active, _publication, before_sha = _legacy_active_project(tmp_path)
    original_append = WashEventLedger.append

    def fail_production_started(self, event):
        if event.stage == "production_started":
            raise SourceLedgerError("stage-event-conflict")
        return original_append(self, event)

    monkeypatch.setattr(WashEventLedger, "append", fail_production_started)

    with pytest.raises(ProductionPolicyError, match="^production-policy-invalid$"):
        apply_horizontal_production_policy(tmp_path, WORK_ID, SLUG)

    assert sha256_file(active / "交接稿.md") == before_sha
    assert not (_work_root(tmp_path) / "production-policy-receipt.json").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"

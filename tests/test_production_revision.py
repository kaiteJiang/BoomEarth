from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from boomearth.workbench.production_policy import apply_horizontal_production_policy
from boomearth.workbench.production_revision import (
    compile_production_revision,
    ProductionRevisionError,
    prepare_production_revision,
)
from boomearth.video.content_plan import parse_handoff_segments
from boomearth.workbench.rewrite_package import REQUIRED_REVIEWS, reading_unit_count
from boomearth.workbench.handoff_compiler import compile_source_handoff
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.source_artifacts import (
    canonical_json_bytes,
    publish_json_exclusive,
    sha256_file,
)
from test_source_handoff_compiler import (
    WORK_ID,
    _complete_rewrite_work,
    _work_root,
)


V1_SLUG = "2026-08-16-workbuddy-remote-assistant"
V2_SLUG = "2026-08-16-workbuddy-remote-assistant-v2"


@dataclass(frozen=True)
class ProductionStartedFixture:
    root: Path
    active: Path
    private_root: Path
    transcript_sha256: str
    transcript_manifest_sha256: str
    publication_sha256: str
    handoff_sha256: str

    def mutate_one_byte(self, artifact: str) -> None:
        paths = {
            "transcript": self.private_root / "transcript.json",
            "original_handoff": self.active / "交接稿.md",
            "publication_receipt": self.private_root / "publication-receipt.json",
        }
        target = paths[artifact]
        target.write_bytes(target.read_bytes() + b" ")


def _rewrite_slug(candidate: Path, review: Path, private_root: Path) -> None:
    brief_path = private_root / "rewrite-brief.json"
    brief = json.loads(brief_path.read_text("utf-8"))
    brief["archive_slug"] = V1_SLUG
    brief_path.write_bytes(canonical_json_bytes(brief))

    candidate_value = json.loads(candidate.read_text("utf-8"))
    candidate_value["archive_slug"] = V1_SLUG
    candidate.write_bytes(canonical_json_bytes(candidate_value))

    review_value = json.loads(review.read_text("utf-8"))
    review_value["candidate_sha256"] = sha256_file(candidate)
    review.write_bytes(canonical_json_bytes(review_value))


def _create_pending_handoff(root: Path) -> tuple[Path, Path]:
    candidate, review = _complete_rewrite_work(root)
    private_root = _work_root(root)
    _rewrite_slug(candidate, review, private_root)
    compile_source_handoff(root, WORK_ID, candidate, review)
    return private_root, WorkbenchPaths(root).pending / V1_SLUG


def _create_v1_runtime(active: Path) -> None:
    media = active / "工程" / "media"
    qc = active / "工程" / "qc"
    media.mkdir(parents=True)
    qc.mkdir(parents=True)
    narration = media / "narration.wav"
    narration.write_bytes(b"RIFF-production-revision-fixture")
    segments = active / "工程" / "tts-segments.jsonl"
    segments.write_text(
        json.dumps({"segment_id": "segment-001", "text": "安全测试旁白"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
        newline="",
    )
    content_plan = active / "工程" / "content-plan.json"
    content_plan.write_bytes(canonical_json_bytes({"schema_version": 4}))
    captions = media / "captions"
    plan = {
        "created_at": "2026-08-16T10:41:16Z",
        "failure_policy": "stop-without-retry-or-provider-fallback",
        "forbidden_providers": [
            "TOS",
            "TikHub",
            "Paraformer",
            "Qushuiyin",
            "HeyGen",
            "IndexTTS2",
            "ImageGen",
        ],
        "input": {
            "encoding": "original-wav-bytes-as-base64",
            "path": str(narration),
            "sha256": sha256_file(narration),
        },
        "operation": "volcengine-recording-file-turbo-caption-timing",
        "output": {
            "directory": str(captions),
            "minimum_alignment_coverage": 0.9,
            "publication": "atomic-no-clobber",
            "required_timing_source": "volcengine-word-timestamps",
        },
        "project_id": V1_SLUG,
        "provider": {
            "endpoint": "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash",
            "follow_redirects": False,
            "name": "volcengine-doubao-asr",
            "object_storage": False,
            "request_count": 1,
            "resource_id": "volc.bigasr.auc_turbo",
            "retry_count": 0,
            "submit_query_polling": False,
        },
        "schema_version": 1,
        "script_contract": {
            "path": str(segments),
            "segment_count": 1,
            "sha256": sha256_file(segments),
        },
        "status": "awaiting-explicit-approval",
    }
    publish_json_exclusive(active, qc / "final-audio-asr-plan.json", plan)


@pytest.fixture
def production_started_fixture(tmp_path: Path) -> ProductionStartedFixture:
    private_root, pending = _create_pending_handoff(tmp_path)
    paths = WorkbenchPaths(tmp_path)
    paths.active.mkdir(parents=True)
    active = paths.active / V1_SLUG
    os.rename(pending, active)
    handoff = active / "交接稿.md"
    handoff.write_text(
        handoff.read_text("utf-8").replace('status: "待制作"', 'status: "制作中"', 1),
        encoding="utf-8",
        newline="",
    )
    apply_horizontal_production_policy(tmp_path, WORK_ID, V1_SLUG)
    _create_v1_runtime(active)
    transcript = private_root / "transcript.json"
    transcript_manifest = private_root / "transcript-manifest.json"
    publication = private_root / "publication-receipt.json"
    return ProductionStartedFixture(
        root=tmp_path,
        active=active,
        private_root=private_root,
        transcript_sha256=sha256_file(transcript),
        transcript_manifest_sha256=sha256_file(transcript_manifest),
        publication_sha256=sha256_file(publication),
        handoff_sha256=sha256_file(handoff),
    )


def test_revision_prepares_v2_brief_from_verified_production_started_chain(
    production_started_fixture: ProductionStartedFixture,
) -> None:
    fixture = production_started_fixture

    record = prepare_production_revision(
        fixture.root,
        WORK_ID,
        original_active_project=V1_SLUG,
        archive_slug=V2_SLUG,
        duration_target_s=165,
    )

    value = json.loads(record.path.read_text("utf-8"))
    assert value["schema_version"] == 1
    assert value["revision"] == 2
    assert value["duration_target_s"] == 165
    assert value["ratio"] == "16:9"
    assert value["transcript_sha256"] == fixture.transcript_sha256
    assert value["transcript_manifest_sha256"] == fixture.transcript_manifest_sha256
    assert value["original_publication_receipt_sha256"] == fixture.publication_sha256
    assert value["original_active_handoff_sha256"] == fixture.handoff_sha256


@pytest.mark.parametrize(
    "mutated_artifact",
    ["transcript", "original_handoff", "publication_receipt"],
)
def test_revision_rejects_changed_chain(
    production_started_fixture: ProductionStartedFixture,
    mutated_artifact: str,
) -> None:
    production_started_fixture.mutate_one_byte(mutated_artifact)

    with pytest.raises(
        ProductionRevisionError, match="production-revision-chain-invalid"
    ):
        prepare_production_revision(
            production_started_fixture.root,
            WORK_ID,
            original_active_project=V1_SLUG,
            archive_slug=V2_SLUG,
            duration_target_s=165,
        )


def test_revision_rejects_non_production_started_ledger(tmp_path: Path) -> None:
    _create_pending_handoff(tmp_path)

    with pytest.raises(
        ProductionRevisionError, match="production-revision-chain-invalid"
    ):
        prepare_production_revision(
            tmp_path,
            WORK_ID,
            original_active_project=V1_SLUG,
            archive_slug=V2_SLUG,
            duration_target_s=165,
        )

    assert not (_work_root(tmp_path) / "rewrite-brief-v2.json").exists()


def test_revision_is_no_clobber(
    production_started_fixture: ProductionStartedFixture,
) -> None:
    fixture = production_started_fixture
    first = prepare_production_revision(
        fixture.root,
        WORK_ID,
        original_active_project=V1_SLUG,
        archive_slug=V2_SLUG,
        duration_target_s=165,
    )
    before = first.path.read_bytes()

    with pytest.raises(
        ProductionRevisionError, match="production-revision-target-exists"
    ):
        prepare_production_revision(
            fixture.root,
            WORK_ID,
            original_active_project=V1_SLUG,
            archive_slug=V2_SLUG,
            duration_target_s=165,
        )

    assert first.path.read_bytes() == before


def test_revision_diagnostics_never_expose_source_text_paths_or_hashes(
    production_started_fixture: ProductionStartedFixture,
) -> None:
    fixture = production_started_fixture
    fixture.mutate_one_byte("transcript")

    with pytest.raises(ProductionRevisionError) as captured:
        prepare_production_revision(
            fixture.root,
            WORK_ID,
            original_active_project=V1_SLUG,
            archive_slug=V2_SLUG,
            duration_target_s=165,
        )

    rendered = repr(captured.value)
    assert rendered == "ProductionRevisionError(<redacted>)"
    assert str(fixture.root) not in rendered
    assert fixture.transcript_sha256 not in rendered
    assert "来源内容" not in rendered


def _units(total: int, index: int) -> str:
    share, remainder = divmod(total, 10)
    count = share + (1 if index <= remainder else 0)
    return "协" * count


def _publish_v2_inputs(
    fixture: ProductionStartedFixture,
    *,
    segment_count: int = 10,
    total_units: int = 800,
    extra_candidate_field: bool = False,
) -> tuple[Path, Path]:
    prepare_production_revision(
        fixture.root,
        WORK_ID,
        original_active_project=V1_SLUG,
        archive_slug=V2_SLUG,
        duration_target_s=165,
    )
    candidate = {
        "archive_slug": V2_SLUG,
        "duration_target_s": 165,
        "illustration_skill": "ra-video-illustrations",
        "platform": "douyin",
        "ratio": "16:9",
        "schema_version": 1,
        "segments": [
            {
                "segment_id": f"segment-{index:03d}",
                "text": _units(total_units, index),
                "visual_intent": f"第{index}段人物行动与可见结果",
            }
            for index in range(1, segment_count + 1)
        ],
        "title_candidates": [
            f"手机远程指挥第{index}次，电脑继续干活"
            for index in range(1, 9)
        ],
        "visual": "vivid-comic-explainer",
        "work_id": WORK_ID,
    }
    if extra_candidate_field:
        candidate["unexpected"] = True
    candidate_path = fixture.private_root / "rewrite-candidate-v2.json"
    candidate_path.write_bytes(canonical_json_bytes(candidate))
    review_path = fixture.private_root / "rewrite-review-v2.json"
    review_path.write_bytes(
        canonical_json_bytes(
            {
                "candidate_sha256": sha256_file(candidate_path),
                "reviewed_at": "2026-08-16T11:00:00Z",
                "reviews": {name: "pass" for name in REQUIRED_REVIEWS},
                "schema_version": 1,
            }
        )
    )
    return candidate_path, review_path


def test_revision_compiles_ten_segment_active_handoff_and_supersedes_v1(
    production_started_fixture: ProductionStartedFixture,
) -> None:
    fixture = production_started_fixture
    candidate, review = _publish_v2_inputs(fixture)
    v1_plan = fixture.active / "工程" / "qc" / "final-audio-asr-plan.json"
    original_plan = v1_plan.read_bytes()

    result = compile_production_revision(
        fixture.root,
        WORK_ID,
        candidate,
        review,
        original_active_project=V1_SLUG,
    )

    handoff = WorkbenchPaths(fixture.root).active / V2_SLUG / "交接稿.md"
    handoff_text = handoff.read_text("utf-8")
    assert result.revision == 2
    assert result.archive_slug == V2_SLUG
    assert result.handoff_sha256 == sha256_file(handoff)
    assert 'status: "制作中"' in handoff_text
    assert "duration_target_s: 165" in handoff_text
    assert "word_count: 800" in handoff_text
    assert len(parse_handoff_segments(handoff_text)) == 10
    assert reading_unit_count("".join(_units(800, index) for index in range(1, 11))) == 800
    assert v1_plan.read_bytes() == original_plan
    superseded = json.loads(
        (fixture.active / "工程" / "qc" / "superseded-by-v2.json").read_text("utf-8")
    )
    assert superseded["status"] == "superseded-before-network"
    assert superseded["old_narration_sha256"] == sha256_file(
        fixture.active / "工程" / "media" / "narration.wav"
    )
    assert (fixture.private_root / "publication-revision-v2.json").is_file()
    assert (fixture.private_root / "production-revision-v2.json").is_file()


@pytest.mark.parametrize(
    ("segment_count", "total_units"),
    [(9, 800), (11, 800), (10, 749), (10, 851)],
)
def test_revision_rejects_wrong_segment_count_or_reading_units(
    production_started_fixture: ProductionStartedFixture,
    segment_count: int,
    total_units: int,
) -> None:
    candidate, review = _publish_v2_inputs(
        production_started_fixture,
        segment_count=segment_count,
        total_units=total_units,
    )

    with pytest.raises(ProductionRevisionError, match="production-revision-rewrite-invalid"):
        compile_production_revision(
            production_started_fixture.root,
            WORK_ID,
            candidate,
            review,
            original_active_project=V1_SLUG,
        )

    assert not (WorkbenchPaths(production_started_fixture.root).active / V2_SLUG).exists()


def test_revision_rejects_unknown_candidate_field(
    production_started_fixture: ProductionStartedFixture,
) -> None:
    candidate, review = _publish_v2_inputs(
        production_started_fixture, extra_candidate_field=True
    )

    with pytest.raises(ProductionRevisionError, match="production-revision-rewrite-invalid"):
        compile_production_revision(
            production_started_fixture.root,
            WORK_ID,
            candidate,
            review,
            original_active_project=V1_SLUG,
        )


def test_revision_never_overwrites_existing_v2_target(
    production_started_fixture: ProductionStartedFixture,
) -> None:
    candidate, review = _publish_v2_inputs(production_started_fixture)
    target = WorkbenchPaths(production_started_fixture.root).active / V2_SLUG
    target.mkdir(parents=True)
    foreign = target / "foreign.bin"
    foreign.write_bytes(b"preserve")

    with pytest.raises(ProductionRevisionError, match="production-revision-target-exists"):
        compile_production_revision(
            production_started_fixture.root,
            WORK_ID,
            candidate,
            review,
            original_active_project=V1_SLUG,
        )

    assert foreign.read_bytes() == b"preserve"

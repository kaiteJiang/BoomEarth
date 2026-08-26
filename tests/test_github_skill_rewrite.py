from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.workbench.github_skill_acquisition import (
    plan_github_skill_acquisition,
)
from boomearth.workbench.handoff_compiler import compile_source_handoff
from boomearth.workbench.rewrite_package import (
    REQUIRED_REVIEWS,
    RewritePackageError,
    prepare_rewrite_brief,
)
from boomearth.workbench.source_artifacts import (
    publish_json_exclusive,
    sha256_file,
)
from boomearth.workbench.source_intake import create_github_skill_intake
from boomearth.workbench.source_ledger import (
    StageEvent,
    WashEventLedger,
    event_sha256,
)


WORK_ID = "33333333-3333-4333-8333-333333333333"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
COMMIT_SHA = "a" * 40
GITHUB_URL = "https://github.com/acme/skills"
PRIVATE_SOURCE_TEXT = (
    "github.com/acme/skills owner/repo SKILL.md resolved_commit .internal "
    "这份私有资料只用于解释工具仓库的设计结构"
)


def _private_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def _github_ready(root: Path) -> Path:
    url_file = root / "github-url.txt"
    url_file.write_text(GITHUB_URL + "\n", encoding="utf-8")
    order = create_github_skill_intake(
        root,
        url_file,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    plan_github_skill_acquisition(root, WORK_ID)
    source_root = order.private_root / "github-skill-source"
    selected = source_root / "selected-files" / "comic" / "SKILL.md"
    selected.parent.mkdir(parents=True)
    selected.write_text(
        "---\nname: comic\ndescription: Explain ideas\n---\n",
        encoding="utf-8",
    )
    repository = source_root / "repository.md"
    repository.write_text(PRIVATE_SOURCE_TEXT + "\n", encoding="utf-8")
    manifest = order.private_root / "github-skill-manifest.json"
    manifest_sha = publish_json_exclusive(
        order.private_root,
        manifest,
        {
            "created_at": "2026-08-15T09:01:00Z",
            "default_branch": "main",
            "files": [
                {
                    "blob_sha": "b" * 40,
                    "bytes": selected.stat().st_size,
                    "local_path": "github-skill-source/selected-files/comic/SKILL.md",
                    "path": "comic/SKILL.md",
                    "role": "skill",
                    "sha256": sha256_file(selected),
                }
            ],
            "license_status": "UNDECLARED",
            "repository": "acme/skills",
            "repository_markdown_path": "github-skill-source/repository.md",
            "repository_markdown_sha256": sha256_file(repository),
            "request_count": 5,
            "requested_path": "",
            "requested_scope": "repository",
            "resolved_commit": COMMIT_SHA,
            "schema_version": 1,
            "source_input_sha256": order.source_input_sha256,
            "source_kind": "github-skill",
            "total_text_bytes": selected.stat().st_size,
            "tree_sha256": "c" * 64,
            "work_id": WORK_ID,
        },
    )
    previous = WashEventLedger(root).current(WORK_ID)
    WashEventLedger(root).append(
        StageEvent(
            schema_version=2,
            work_id=WORK_ID,
            source_id=order.source_input_sha256,
            source_kind="github-skill",
            stage="github_skill_ready",
            result="ok",
            artifact_label="github-skill-manifest",
            artifact_sha256=manifest_sha,
            timestamp="2026-08-15T09:01:01Z",
            previous_event_sha256=event_sha256(previous),
        )
    )
    return order.private_root


def test_prepare_rewrite_brief_accepts_hash_bound_github_skill(
    tmp_path: Path,
) -> None:
    private_root = _github_ready(tmp_path)

    record = prepare_rewrite_brief(
        tmp_path,
        WORK_ID,
        platform="douyin",
        duration_target_s=75,
        archive_slug="github-skill-overview",
    )

    value = json.loads(record.path.read_text("utf-8"))
    assert value["schema_version"] == 2
    assert value["source_kind"] == "github-skill"
    assert value["source_artifact"] == "github-skill-source/repository.md"
    assert value["source_sha256"] == sha256_file(
        private_root / "github-skill-source" / "repository.md"
    )
    assert value["source_manifest_sha256"] == sha256_file(
        private_root / "github-skill-manifest.json"
    )
    assert not ({"request_id", "transcript_artifact", "transcript_sha256"} & set(value))


@pytest.mark.parametrize("target", ["repository", "selected", "manifest"])
def test_github_skill_rewrite_rejects_any_hash_bound_source_tamper(
    tmp_path: Path, target: str
) -> None:
    private_root = _github_ready(tmp_path)
    paths = {
        "repository": private_root / "github-skill-source" / "repository.md",
        "selected": private_root
        / "github-skill-source"
        / "selected-files"
        / "comic"
        / "SKILL.md",
        "manifest": private_root / "github-skill-manifest.json",
    }
    paths[target].write_bytes(paths[target].read_bytes() + b"tampered")

    with pytest.raises(
        RewritePackageError, match="^rewrite-github-skill-invalid$"
    ):
        prepare_rewrite_brief(
            tmp_path,
            WORK_ID,
            platform="douyin",
            duration_target_s=75,
            archive_slug="github-skill-overview",
        )


def _candidate_and_review(private_root: Path) -> tuple[Path, Path]:
    candidate = private_root / "rewrite-candidate.json"
    publish_json_exclusive(
        private_root,
        candidate,
        {
            "archive_slug": "github-skill-source-free",
            "duration_target_s": 75,
            "illustration_skill": "ra-video-illustrations",
            "platform": "douyin",
            "ratio": "16:9",
            "schema_version": 1,
            "segments": [
                {
                    "segment_id": "segment-001",
                    "text": "很多工具真正值得学的，不是表面的按钮，而是它怎样把复杂任务拆成稳定步骤。",
                    "visual_intent": "人物把散乱流程整理成清晰路线",
                },
                {
                    "segment_id": "segment-002",
                    "text": "看懂输入、约束和验收标准，才有机会把别人的经验变成自己的工作方法。",
                    "visual_intent": "输入约束和验收形成闭环",
                },
            ],
            "title_candidates": ["别只抄工具，先看懂它怎么拆任务"],
            "visual": "editorial-motion-v2",
            "work_id": WORK_ID,
        },
    )
    review = private_root / "rewrite-review.json"
    publish_json_exclusive(
        private_root,
        review,
        {
            "candidate_sha256": sha256_file(candidate),
            "reviewed_at": "2026-08-15T09:02:00Z",
            "reviews": {name: "pass" for name in REQUIRED_REVIEWS},
            "schema_version": 1,
        },
    )
    return candidate, review


def test_github_skill_compiles_source_free_public_handoff(tmp_path: Path) -> None:
    private_root = _github_ready(tmp_path)
    prepare_rewrite_brief(
        tmp_path,
        WORK_ID,
        platform="douyin",
        duration_target_s=75,
        archive_slug="github-skill-source-free",
    )
    candidate, review = _candidate_and_review(private_root)

    publication = compile_source_handoff(
        tmp_path, WORK_ID, candidate, review
    )

    handoff = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "github-skill-source-free"
        / "交接稿.md"
    )
    public_text = handoff.read_text("utf-8")
    receipt_text = (private_root / "publication-receipt.json").read_text("utf-8")
    assert publication.handoff_sha256 == sha256_file(handoff)
    for private_value in (
        "github.com",
        "acme/skills",
        "owner/repo",
        "SKILL.md",
        "resolved_commit",
        ".internal",
        COMMIT_SHA,
    ):
        assert private_value not in public_text
        assert private_value not in receipt_text
    assert WashEventLedger(tmp_path).status(WORK_ID) == "handoff_ready"

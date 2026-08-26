from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

from boomearth.video.avatar_defaults import load_default_avatar_config


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation" / "scripts" / "run_heygen_direct_avatar.py"
GROUP_ID = "a" * 32
PREFERRED_ID = "b" * 32
FALLBACK_ID = "e" * 32
PLAN_SHA256 = "7f8518f7db5e9a55049f49c4ea6d6e8f509695231e60cbd607bcb36c88a75a14"
WORK_ID = "35d0e1ad-d511-451f-af90-919879cbd9c1"
ATTEMPT_TWO_PLAN_SHA256 = "ba9e7dcf0e836a7d8a50179c6473e15795ce66668cbfd222bf95a3fc1b3d9089"
ATTEMPT_FOUR_PLAN_SHA256 = "22be1bad47c1522ded4df5c73cc5227dc21aa7dcb3b7e30b7cc192c2c528a9fa"
FAILED_LOOK_PAYLOAD_SHA256 = "842d2efddd88d7262b582202ca85d90a3230d5f5a9217b6fdd172bdced1a49d1"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_config(path: Path) -> Path:
    from boomearth.video.avatar_defaults import config_payload_sha256

    document: dict[str, object] = {
        "schema_version": 2,
        "owner": "user",
        "display_name": "User",
        "group_id": GROUP_ID,
        "preferred_look_id": PREFERRED_ID,
        "preferred_engine": "avatar_iii",
        "composite_profile": "headroom_08-circle-lower-left",
    }
    document["payload_sha256"] = config_payload_sha256(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_approved_project_binding_uses_requested_project_and_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("heygen_direct_project_binding")
    repo = tmp_path / "BoomEarth"
    project = (
        repo
        / "01-内容生产"
        / "视频工作台"
        / "制作中"
        / "2026-08-21-claude-academy-ai-learning-standard"
    )
    approval = project / "工程" / "media" / "heygen-approval-plan.md"
    approval.parent.mkdir(parents=True)
    approval.write_bytes(b"approved\n")
    monkeypatch.setattr(module, "ROOT", repo)

    configure = getattr(module, "_configure_approved_project", None)
    assert callable(configure)
    configure(
        project_root=project,
        approval_plan=approval,
        approved_plan_sha256=PLAN_SHA256,
        work_id=WORK_ID,
    )

    assert module.PROJECT == project.absolute()
    assert module.AUDIO == project.absolute() / "工程" / "media" / "narration.wav"
    assert module.MASTER == (
        project.absolute() / "工程" / "media" / "heygen-user-avatar-master-v3.mp4"
    )
    assert module.RUNTIME == (
        repo
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "heygen"
        / WORK_ID
    )
    assert module.APPROVED_PLAN_SHA256 == PLAN_SHA256


def test_refreshed_look_selection_uses_ready_preferred_same_group(tmp_path: Path) -> None:
    module = _load_script("heygen_direct_look_selection")
    config = load_default_avatar_config(_write_config(tmp_path / "default-avatar.json"))
    payload = {
        "data": {
            "items": [
                {
                    "id": "c" * 32,
                    "name": "Other group",
                    "avatar_type": "photo_avatar",
                    "group_id": "d" * 32,
                    "image_width": 1080,
                    "image_height": 1920,
                    "preferred_orientation": "portrait",
                    "preview_image_url": "https://example.invalid/other.png",
                    "preview_video_url": None,
                    "status": "completed",
                    "supported_api_engines": ["avatar_iii"],
                    "tags": [],
                    "default_voice_id": None,
                    "gender": None,
                    "error": None,
                },
                {
                    "id": PREFERRED_ID,
                    "name": "Preferred",
                    "avatar_type": "photo_avatar",
                    "group_id": GROUP_ID,
                    "image_width": 1080,
                    "image_height": 1920,
                    "preferred_orientation": "portrait",
                    "preview_image_url": "https://example.invalid/preferred.png",
                    "preview_video_url": None,
                    "status": "completed",
                    "supported_api_engines": ["avatar_iii"],
                    "tags": [],
                    "default_voice_id": None,
                    "gender": None,
                    "error": None,
                },
            ],
            "has_more": False,
            "next_token": None,
        }
    }

    select = getattr(module, "_select_refreshed_look", None)
    assert callable(select)
    selection, look = select(config, payload, engine="avatar_iii")

    assert selection.reason == "preferred"
    assert selection.candidate_count == 1
    assert selection.look_id == PREFERRED_ID
    assert look["id"] == PREFERRED_ID


def test_attempt_two_binding_versions_runtime_and_public_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("heygen_direct_attempt_two_binding")
    repo = tmp_path / "BoomEarth"
    project = (
        repo
        / "01-内容生产"
        / "视频工作台"
        / "制作中"
        / "2026-08-21-claude-academy-ai-learning-standard"
    )
    approval = project / "工程" / "media" / "heygen-attempt-2-approval-plan.md"
    approval.parent.mkdir(parents=True)
    approval.write_bytes(b"attempt-two\n")
    monkeypatch.setattr(module, "ROOT", repo)

    module._configure_approved_project(
        project_root=project,
        approval_plan=approval,
        approved_plan_sha256=ATTEMPT_TWO_PLAN_SHA256,
        work_id=WORK_ID,
        attempt=2,
    )

    assert module.RUNTIME == (
        repo
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "heygen"
        / WORK_ID
        / "attempt-2"
    )
    assert module.PUBLIC_PLAN.name == "heygen-direct-video-v3-attempt-2-plan.json"
    assert module.PUBLIC_APPROVAL.name == "heygen-direct-video-v3-attempt-2-approval.json"
    assert module.PUBLIC_RESULT.name == "heygen-direct-video-v3-attempt-2-result.json"
    assert module.MASTER.name == "heygen-user-avatar-master-v3.mp4"


def test_preparation_failure_writes_redacted_attempt_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("heygen_direct_attempt_two_failure")
    repo = tmp_path / "BoomEarth"
    project = (
        repo
        / "01-内容生产"
        / "视频工作台"
        / "制作中"
        / "2026-08-21-claude-academy-ai-learning-standard"
    )
    approval = project / "工程" / "media" / "heygen-attempt-2-approval-plan.md"
    approval.parent.mkdir(parents=True)
    approval.write_bytes(b"attempt-two\n")
    audio = approval.parent / "narration.wav"
    audio.write_bytes(b"locked-audio")
    monkeypatch.setattr(module, "ROOT", repo)
    module._configure_approved_project(
        project_root=project,
        approval_plan=approval,
        approved_plan_sha256=ATTEMPT_TWO_PLAN_SHA256,
        work_id=WORK_ID,
        attempt=2,
    )

    module._write_preparation_failure("provider-auth")
    receipt = json.loads(module.PUBLIC_RESULT.read_text(encoding="utf-8"))

    assert receipt["status"] == "failed"
    assert receipt["attempt"] == 2
    assert receipt["stage"] == "configured-group-refresh-or-selection"
    assert receipt["failure_category"] == "provider-auth"
    assert receipt["uploads"] == 0
    assert receipt["submit_attempts"] == 0
    assert receipt["master_created"] is False
    assert "work_id" not in receipt


def test_refreshed_look_selection_accepts_cli_v070_data_array(tmp_path: Path) -> None:
    module = _load_script("heygen_direct_cli_v070_list_shape")
    config = load_default_avatar_config(_write_config(tmp_path / "default-avatar.json"))
    payload = {
        "data": [
            {
                "id": PREFERRED_ID,
                "name": "Preferred",
                "avatar_type": "photo_avatar",
                "group_id": GROUP_ID,
                "image_width": 1080,
                "image_height": 1920,
                "preferred_orientation": "portrait",
                "preview_image_url": "https://example.invalid/preferred.png",
                "preview_video_url": None,
                "status": "completed",
                "supported_api_engines": ["avatar_iii"],
                "tags": [],
                "default_voice_id": None,
                "gender": None,
                "error": None,
            }
        ],
        "has_more": False,
        "next_token": None,
    }

    selection, look = module._select_refreshed_look(
        config,
        payload,
        engine="avatar_iii",
    )

    assert selection.reason == "preferred"
    assert selection.candidate_count == 1
    assert look["id"] == PREFERRED_ID


def test_recovery_selection_excludes_failed_preferred_look(tmp_path: Path) -> None:
    module = _load_script("heygen_direct_recovery_selection")
    config = load_default_avatar_config(_write_config(tmp_path / "default-avatar.json"))
    payload = {
        "data": [
            {
                "id": PREFERRED_ID,
                "name": "Failed preferred",
                "avatar_type": "digital_twin",
                "group_id": GROUP_ID,
                "image_width": 1080,
                "image_height": 1920,
                "preferred_orientation": "portrait",
                "preview_image_url": "https://example.invalid/preferred.png",
                "preview_video_url": None,
                "status": "completed",
                "supported_api_engines": ["avatar_iii"],
                "tags": [],
                "default_voice_id": None,
                "gender": None,
                "error": None,
            },
            {
                "id": FALLBACK_ID,
                "name": "Same-group fallback",
                "avatar_type": "digital_twin",
                "group_id": GROUP_ID,
                "image_width": 1080,
                "image_height": 1920,
                "preferred_orientation": "portrait",
                "preview_image_url": "https://example.invalid/fallback.png",
                "preview_video_url": None,
                "status": "completed",
                "supported_api_engines": ["avatar_iii"],
                "tags": [],
                "default_voice_id": None,
                "gender": None,
                "error": None,
            },
        ],
        "has_more": False,
        "next_token": None,
    }

    selection, look = module._select_refreshed_look(
        config,
        payload,
        engine="avatar_iii",
        excluded_look_ids=frozenset({PREFERRED_ID}),
    )

    assert selection.reason == "same-group-random"
    assert selection.candidate_count == 1
    assert selection.look_id == FALLBACK_ID
    assert look["id"] == FALLBACK_ID


def test_recovery_exclusion_requires_prior_terminal_generation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("heygen_direct_recovery_evidence")
    repo = tmp_path / "BoomEarth"
    project = (
        repo
        / "01-内容生产"
        / "视频工作台"
        / "制作中"
        / "2026-08-21-claude-academy-ai-learning-standard"
    )
    approval = project / "工程" / "media" / "heygen-attempt-4-approval-plan.md"
    approval.parent.mkdir(parents=True)
    approval.write_bytes(b"attempt-four\n")
    monkeypatch.setattr(module, "ROOT", repo)
    module._configure_approved_project(
        project_root=project,
        approval_plan=approval,
        approved_plan_sha256=ATTEMPT_FOUR_PLAN_SHA256,
        work_id=WORK_ID,
        attempt=4,
    )
    prior_runtime = module.PRIVATE_ROOT / WORK_ID / "attempt-3"
    prior_runtime.mkdir(parents=True)
    failed_look = {
        "schema_version": 2,
        "operation": "heygen.avatar.looks.get.v3",
        "look_id": PREFERRED_ID,
        "group_id": GROUP_ID,
        "avatar_type": "digital_twin",
        "orientation": "portrait",
        "preview_available": True,
        "engine": "avatar_iii",
        "captured_at": "2026-08-22T00:00:00Z",
        "payload_sha256": FAILED_LOOK_PAYLOAD_SHA256,
    }
    (prior_runtime / "selected-look.json").write_text(
        json.dumps(failed_look),
        encoding="utf-8",
    )
    prior_result = module.MEDIA / "heygen-direct-video-v3-attempt-3-result.json"
    prior_result.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "failed",
                "attempt": 3,
                "stage": "generation-or-download",
                "upload_completed": True,
                "submit_attempts": 1,
                "retry_attempts": 0,
                "master_created": False,
                "failure_category": "provider-generation-failed",
            }
        ),
        encoding="utf-8",
    )

    look_id, receipt_sha256 = module._load_recovery_exclusion(3)

    assert look_id == PREFERRED_ID
    assert receipt_sha256 == module._sha256(prior_runtime / "selected-look.json")

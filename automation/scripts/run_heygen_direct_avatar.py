"""Create one audio-driven HeyGen master with redacted receipts only.

Private HeyGen identifiers and provider URLs remain under the ignored runtime
directory. Project-visible receipts retain only hashes and failure categories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.video.avatar_defaults import (  # noqa: E402
    DEFAULT_COMPOSITE_PROFILE,
    AvatarDefaultError,
    AvatarLook,
    AvatarSelection,
    DefaultAvatarConfig,
    config_payload_sha256,
    load_default_avatar_config,
    select_default_look,
)


PROJECT = ROOT / "01-内容生产" / "视频工作台" / "制作中" / "2026-08-16-workbuddy-remote-assistant-v2"
MEDIA = PROJECT / "工程" / "media"
AUDIO = MEDIA / "narration.wav"
PRIVATE_ROOT = ROOT / "01-内容生产" / "视频工作台" / ".internal" / "heygen"
RUNTIME = PRIVATE_ROOT / "workbuddy-v2-direct-video-v3"
DEFAULT_CONFIG = PRIVATE_ROOT / "default-avatar.json"
PUBLIC_SELECTION = MEDIA / "heygen-user-avatar-v3-selection.json"
PUBLIC_PLAN = MEDIA / "heygen-direct-video-v3-plan.json"
PUBLIC_APPROVAL = MEDIA / "heygen-direct-video-v3-approval.json"
PUBLIC_RESULT = MEDIA / "heygen-direct-video-v3-result.json"
MASTER = MEDIA / "heygen-user-avatar-master-v3.mp4"
APPROVAL_PLAN: Path | None = None
APPROVED_PLAN_SHA256 = ""
WORK_ID = ""
ATTEMPT = 1
VIDEO_TITLE = "BoomEarth WorkBuddy V2 circular avatar master"


class HeyGenDirectError(RuntimeError):
    """Stable, redacted error suitable for public receipts."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attributes & 0x400)


def _configure_approved_project(
    *,
    project_root: Path,
    approval_plan: Path,
    approved_plan_sha256: str,
    work_id: str,
    attempt: int = 1,
) -> None:
    """Bind one approved run to an exact active project and immutable plan."""
    global PROJECT, MEDIA, AUDIO, PRIVATE_ROOT, RUNTIME, DEFAULT_CONFIG
    global PUBLIC_SELECTION, PUBLIC_PLAN, PUBLIC_APPROVAL, PUBLIC_RESULT, MASTER
    global APPROVAL_PLAN, APPROVED_PLAN_SHA256, WORK_ID, ATTEMPT, VIDEO_TITLE

    expected_active_root = (
        ROOT / "01-内容生产" / "视频工作台" / "制作中"
    ).absolute()
    project = Path(project_root).absolute()
    approval = Path(approval_plan).absolute()
    expected_approval_name = (
        "heygen-approval-plan.md"
        if attempt == 1
        else f"heygen-attempt-{attempt}-approval-plan.md"
    )
    if (
        not isinstance(attempt, int)
        or attempt < 1
        or project.parent != expected_active_root
        or not project.is_dir()
        or _is_reparse_point(project)
        or approval != project / "工程" / "media" / expected_approval_name
        or not approval.is_file()
        or _is_reparse_point(approval)
        or len(approved_plan_sha256) != 64
        or any(character not in "0123456789abcdef" for character in approved_plan_sha256)
        or _sha256(approval) != approved_plan_sha256
    ):
        raise HeyGenDirectError("approved-project-invalid")
    try:
        parsed_work_id = uuid.UUID(work_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise HeyGenDirectError("work-id-invalid") from exc
    if str(parsed_work_id) != work_id:
        raise HeyGenDirectError("work-id-invalid")

    PROJECT = project
    MEDIA = PROJECT / "工程" / "media"
    AUDIO = MEDIA / "narration.wav"
    PRIVATE_ROOT = ROOT / "01-内容生产" / "视频工作台" / ".internal" / "heygen"
    RUNTIME = PRIVATE_ROOT / work_id if attempt == 1 else PRIVATE_ROOT / work_id / f"attempt-{attempt}"
    DEFAULT_CONFIG = PRIVATE_ROOT / "default-avatar.json"
    suffix = "" if attempt == 1 else f"-attempt-{attempt}"
    PUBLIC_SELECTION = MEDIA / f"heygen-user-avatar-v3{suffix}-selection.json"
    PUBLIC_PLAN = MEDIA / f"heygen-direct-video-v3{suffix}-plan.json"
    PUBLIC_APPROVAL = MEDIA / f"heygen-direct-video-v3{suffix}-approval.json"
    PUBLIC_RESULT = MEDIA / f"heygen-direct-video-v3{suffix}-result.json"
    MASTER = MEDIA / "heygen-user-avatar-master-v3.mp4"
    APPROVAL_PLAN = approval
    APPROVED_PLAN_SHA256 = approved_plan_sha256
    WORK_ID = work_id
    ATTEMPT = attempt
    VIDEO_TITLE = f"BoomEarth {PROJECT.name} circular avatar master"


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_new(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise HeyGenDirectError("output-exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value) + b"\n")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise HeyGenDirectError("artifact-invalid") from exc
    if not isinstance(value, dict):
        raise HeyGenDirectError("artifact-invalid")
    return value


def _provider_error_category(stderr: bytes) -> str:
    """Classify CLI stderr without persisting or echoing it."""
    text = stderr.decode("utf-8", errors="replace").casefold()
    if any(marker in text for marker in ("unauthorized", "authentication", "invalid api key", "401")):
        return "provider-auth"
    if any(marker in text for marker in ("forbidden", "permission", "403", "entitlement")):
        return "provider-entitlement"
    if any(marker in text for marker in ("quota", "credit", "billing", "payment", "402", "insufficient")):
        return "provider-quota"
    if any(marker in text for marker in ("validation", "bad request", "invalid", "unsupported", "400", "422")):
        return "provider-request-invalid"
    return "provider-failed"


def _run(argv: list[str], *, timeout: int = 60) -> dict[str, Any]:
    try:
        result = subprocess.run(argv, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise HeyGenDirectError("provider-timeout") from exc
    except OSError as exc:
        raise HeyGenDirectError("cli-failed") from exc
    if result.returncode != 0:
        raise HeyGenDirectError(_provider_error_category(result.stderr))
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HeyGenDirectError("provider-response-invalid") from exc
    if not isinstance(payload, dict):
        raise HeyGenDirectError("provider-response-invalid")
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _look_data(look_id: str) -> dict[str, Any]:
    if len(look_id) != 32 or any(character not in "0123456789abcdef" for character in look_id):
        raise HeyGenDirectError("look-invalid")
    payload = _run(["heygen", "avatar", "looks", "get", look_id])
    data = payload.get("data")
    if not isinstance(data, dict) or str(data.get("id", "")) != look_id:
        raise HeyGenDirectError("look-invalid")
    return data


def _orientation(look: dict[str, Any]) -> str:
    try:
        width = int(look["image_width"])
        height = int(look["image_height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HeyGenDirectError("look-invalid") from exc
    if width <= 0 or height <= 0:
        raise HeyGenDirectError("look-invalid")
    return "portrait" if height > width else "landscape" if width > height else "square"


def _ready(look: dict[str, Any]) -> bool:
    return bool(look.get("preview_image_url")) and str(look.get("status", "")).casefold() in {
        "ready",
        "completed",
        "active",
    }


def _supports_engine(look: dict[str, Any], engine: str) -> bool:
    values = look.get("supported_api_engines", look.get("supported_engines", []))
    return isinstance(values, list) and engine in values


def _select_refreshed_look(
    config: DefaultAvatarConfig,
    payload: dict[str, Any],
    *,
    engine: str,
    excluded_look_ids: frozenset[str] = frozenset(),
) -> tuple[AvatarSelection, dict[str, Any]]:
    """Select only a ready, engine-capable look from the configured group."""
    data = payload.get("data")
    items = data if isinstance(data, list) else data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise HeyGenDirectError("provider-response-invalid")
    looks: list[AvatarLook] = []
    raw_by_id: dict[str, dict[str, Any]] = {}
    try:
        for item in items:
            if not isinstance(item, dict):
                raise HeyGenDirectError("provider-response-invalid")
            if str(item.get("id", "")) in excluded_look_ids:
                continue
            if not _supports_engine(item, engine):
                continue
            preferred_orientation = str(item.get("preferred_orientation", "")).casefold()
            orientation = (
                preferred_orientation
                if preferred_orientation in {"portrait", "landscape", "square"}
                else _orientation(item)
            )
            look = AvatarLook(
                look_id=str(item.get("id", "")),
                group_id=str(item.get("group_id", "")),
                status="completed" if _ready(item) else str(item.get("status", "")).casefold(),
                orientation=orientation,
                preview_available=bool(item.get("preview_image_url")),
            )
            looks.append(look)
            raw_by_id[look.look_id] = item
        selection = select_default_look(config, looks)
    except AvatarDefaultError as exc:
        raise HeyGenDirectError(str(exc)) from exc
    selected = raw_by_id.get(selection.look_id)
    if selected is None:
        raise HeyGenDirectError("avatar-look-unavailable")
    return selection, selected


def _load_recovery_exclusion(prior_attempt: int) -> tuple[str, str]:
    """Bind fallback selection to a prior terminally failed generated look."""
    if not isinstance(prior_attempt, int) or prior_attempt < 2 or prior_attempt >= ATTEMPT:
        raise HeyGenDirectError("recovery-evidence-invalid")
    prior_runtime = PRIVATE_ROOT / WORK_ID / f"attempt-{prior_attempt}"
    look_path = prior_runtime / "selected-look.json"
    result_path = MEDIA / f"heygen-direct-video-v3-attempt-{prior_attempt}-result.json"
    look = _load(look_path)
    result = _load(result_path)
    payload = {key: value for key, value in look.items() if key != "payload_sha256"}
    look_id = str(look.get("look_id", ""))
    if (
        look.get("payload_sha256") != hashlib.sha256(_canonical(payload)).hexdigest()
        or len(look_id) != 32
        or any(character not in "0123456789abcdef" for character in look_id)
        or result.get("status") != "failed"
        or result.get("attempt") != prior_attempt
        or result.get("stage") != "generation-or-download"
        or result.get("upload_completed") is not True
        or result.get("submit_attempts") != 1
        or result.get("retry_attempts") != 0
        or result.get("master_created") is not False
        or result.get("failure_category") != "provider-generation-failed"
    ):
        raise HeyGenDirectError("recovery-evidence-invalid")
    return look_id, _sha256(look_path)


def _probe_audio(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise HeyGenDirectError("audio-invalid")
    payload = _run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,sample_rate,channels",
            "-of", "json", str(path),
        ]
    )
    streams = payload.get("streams")
    if not isinstance(streams, list) or not isinstance(payload.get("format"), dict):
        raise HeyGenDirectError("audio-invalid")
    audio = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
    if len(audio) != 1:
        raise HeyGenDirectError("audio-invalid")
    try:
        duration_ms = round(float(payload["format"]["duration"]) * 1000)
        size = int(payload["format"]["size"])
        sample_rate = int(audio[0]["sample_rate"])
        channels = int(audio[0]["channels"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HeyGenDirectError("audio-invalid") from exc
    if duration_ms <= 0 or size <= 0 or size > 32 * 1024 * 1024:
        raise HeyGenDirectError("audio-invalid")
    return {
        "duration_ms": duration_ms, "size_bytes": size,
        "codec": str(audio[0].get("codec_name", "")),
        "sample_rate": sample_rate, "channels": channels,
    }


def set_default(look_id: str, engine: str) -> None:
    """Replace only the ignored default selector after live readiness verification."""
    if DEFAULT_CONFIG.exists() and (DEFAULT_CONFIG.is_symlink() or not stat.S_ISREG(DEFAULT_CONFIG.stat().st_mode)):
        raise HeyGenDirectError("avatar-config-invalid")
    look = _look_data(look_id)
    if not _ready(look):
        raise HeyGenDirectError("selected-look-not-ready")
    group_id = str(look.get("group_id", ""))
    if len(group_id) != 32:
        raise HeyGenDirectError("look-invalid")
    document: dict[str, Any] = {
        "schema_version": 2, "owner": "user", "display_name": "本人长期默认圆形头像",
        "group_id": group_id, "preferred_look_id": look_id,
        "preferred_engine": engine,
        "composite_profile": DEFAULT_COMPOSITE_PROFILE,
    }
    document["payload_sha256"] = config_payload_sha256(document)
    DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    staged = DEFAULT_CONFIG.with_name("default-avatar.next.json")
    if staged.exists():
        raise HeyGenDirectError("avatar-config-staged")
    try:
        staged.write_bytes(_canonical(document) + b"\n")
        os.replace(staged, DEFAULT_CONFIG)
    except OSError as exc:
        staged.unlink(missing_ok=True)
        raise HeyGenDirectError("avatar-config-write-failed") from exc


def prepare(look_id: str, engine: str) -> None:
    for path in (PUBLIC_SELECTION, PUBLIC_PLAN, PUBLIC_APPROVAL, PUBLIC_RESULT, MASTER):
        if path.exists():
            raise HeyGenDirectError("output-exists")
    config = load_default_avatar_config(DEFAULT_CONFIG)
    if config.preferred_look_id != look_id or config.preferred_engine != engine:
        raise HeyGenDirectError("avatar-config-mismatch")
    look = _look_data(look_id)
    if not _ready(look) or not _supports_engine(look, engine):
        raise HeyGenDirectError("selected-look-not-ready")
    group_id = str(look.get("group_id", ""))
    if len(group_id) != 32:
        raise HeyGenDirectError("look-invalid")
    private_look: dict[str, Any] = {
        "schema_version": 2, "operation": "heygen.avatar.looks.get.v3", "look_id": look_id,
        "group_id": group_id, "avatar_type": str(look.get("avatar_type", "")),
        "orientation": _orientation(look), "preview_available": True, "engine": engine,
        "captured_at": _utc_now(),
    }
    private_look["payload_sha256"] = hashlib.sha256(_canonical(private_look)).hexdigest()
    look_path = RUNTIME / "selected-look.json"
    _write_new(look_path, private_look)
    audio = _probe_audio(AUDIO)
    audio_hash = _sha256(AUDIO)
    private_audio: dict[str, Any] = {
        "schema_version": 2, "audio_path": "工程/media/narration.wav", "audio_sha256": audio_hash,
        **audio, "voice_id": "user-indextts2-black-gold-v3", "provider": "indextts2-local",
        "approved": True, "approved_at": _utc_now(),
    }
    private_audio["payload_sha256"] = hashlib.sha256(_canonical(private_audio)).hexdigest()
    audio_path = RUNTIME / "audio-approval.json"
    _write_new(audio_path, private_audio)
    _write_new(PUBLIC_SELECTION, {
        "schema_version": 2, "owner": "user", "rights": "confirmed", "selected_user_avatar": True,
        "selection_source": "explicit-user-specified", "ready": True,
        "selection_receipt_sha256": _sha256(look_path), "hand_sign_capability": "disabled",
    })
    plan = {
        "schema_version": 2, "action": "heygen-direct-avatar-audio-video",
        "provider_surface": "heygen-cli-v3-video-create", "avatar_selection_sha256": _sha256(look_path),
        "audio_approval_sha256": _sha256(audio_path), "audio_sha256": audio_hash,
        "audio_duration_ms": audio["duration_ms"], "voice_id": "user-indextts2-black-gold-v3",
        "used_heygen_tts": False, "script_absent": True, "voice_id_absent_from_request": True,
        "audio_asset_required": True, "request_type": "avatar", "engine": engine,
        "output_ratio": "9:16", "output_format": "mp4", "fit": "cover",
        "motion": "natural-subtle-no-sign-language", "upload_count": 1, "submit_count": 1,
        "retry_count": 0, "status_poll_limit": 45, "status_poll_interval_s": 60,
        "download_count": 1, "fee_possible": True, "no_provider_fallback": True,
        "no_stock_avatar": True, "composite_profile": DEFAULT_COMPOSITE_PROFILE,
    }
    _write_new(PUBLIC_PLAN, plan)
    _write_new(PUBLIC_APPROVAL, {
        "schema_version": 2, "approved": True, "plan_sha256": _sha256(PUBLIC_PLAN),
        "operation_scope": "one-audio-upload-one-direct-video-submit-bounded-status-polls-one-download",
        "failure_policy": "stop-no-retry-no-fallback-no-heygen-tts",
    })


def prepare_approved(engine: str, *, recovery_from_attempt: int | None = None) -> None:
    """Refresh the configured group and prepare the exact user-approved run."""
    if APPROVAL_PLAN is None or not APPROVED_PLAN_SHA256 or not WORK_ID:
        raise HeyGenDirectError("approval-binding-missing")
    for path in (PUBLIC_SELECTION, PUBLIC_PLAN, PUBLIC_APPROVAL, PUBLIC_RESULT, MASTER):
        if path.exists():
            raise HeyGenDirectError("output-exists")
    if _sha256(APPROVAL_PLAN) != APPROVED_PLAN_SHA256:
        raise HeyGenDirectError("approval-plan-mismatch")
    try:
        config = load_default_avatar_config(DEFAULT_CONFIG)
    except AvatarDefaultError as exc:
        raise HeyGenDirectError(str(exc)) from exc
    if config.preferred_engine != engine:
        raise HeyGenDirectError("avatar-config-mismatch")

    excluded_look_ids: frozenset[str] = frozenset()
    excluded_look_receipt_sha256: str | None = None
    if recovery_from_attempt is not None:
        excluded_look_id, excluded_look_receipt_sha256 = _load_recovery_exclusion(
            recovery_from_attempt
        )
        excluded_look_ids = frozenset({excluded_look_id})

    refreshed = _run(
        [
            "heygen", "avatar", "looks", "list",
            "--group-id", config.group_id, "--limit", "20",
        ],
        timeout=90,
    )
    selection, listed_look = _select_refreshed_look(
        config,
        refreshed,
        engine=engine,
        excluded_look_ids=excluded_look_ids,
    )
    if recovery_from_attempt is not None and selection.reason != "same-group-random":
        raise HeyGenDirectError("recovery-selection-invalid")
    look = _look_data(selection.look_id)
    if (
        str(look.get("group_id", "")) != config.group_id
        or not _ready(look)
        or not _supports_engine(look, engine)
        or str(look.get("id", "")) != str(listed_look.get("id", ""))
    ):
        raise HeyGenDirectError("selected-look-not-ready")

    private_refresh: dict[str, Any] = {
        "schema_version": 2,
        "operation": "heygen.avatar.looks.list.v3",
        "configured_group_id": config.group_id,
        "selected_look_id": selection.look_id,
        "selection_reason": selection.reason,
        "recovery_from_attempt": recovery_from_attempt,
        "excluded_look_receipt_sha256": excluded_look_receipt_sha256,
        "eligible_candidate_count": selection.candidate_count,
        "engine": engine,
        "captured_at": _utc_now(),
    }
    private_refresh["payload_sha256"] = hashlib.sha256(_canonical(private_refresh)).hexdigest()
    refresh_path = RUNTIME / "refreshed-look-selection.json"
    _write_new(refresh_path, private_refresh)

    private_look: dict[str, Any] = {
        "schema_version": 2,
        "operation": "heygen.avatar.looks.get.v3",
        "look_id": selection.look_id,
        "group_id": config.group_id,
        "avatar_type": str(look.get("avatar_type", "")),
        "orientation": _orientation(look),
        "preview_available": True,
        "engine": engine,
        "captured_at": _utc_now(),
    }
    private_look["payload_sha256"] = hashlib.sha256(_canonical(private_look)).hexdigest()
    look_path = RUNTIME / "selected-look.json"
    _write_new(look_path, private_look)

    audio = _probe_audio(AUDIO)
    audio_hash = _sha256(AUDIO)
    private_audio: dict[str, Any] = {
        "schema_version": 2,
        "audio_path": "工程/media/narration.wav",
        "audio_sha256": audio_hash,
        **audio,
        "voice_id": "user-indextts2-black-gold-v3",
        "provider": "indextts2-local",
        "approved": True,
        "approved_at": _utc_now(),
    }
    private_audio["payload_sha256"] = hashlib.sha256(_canonical(private_audio)).hexdigest()
    audio_path = RUNTIME / "audio-approval.json"
    _write_new(audio_path, private_audio)

    _write_new(PUBLIC_SELECTION, {
        "schema_version": 2,
        "owner": "user",
        "rights": "confirmed",
        "selected_user_avatar": True,
        "selection_source": "refreshed-configured-group",
        "selection_reason": selection.reason,
        "ready": True,
        "same_group": True,
        "engine_supported": True,
        "selection_receipt_sha256": _sha256(refresh_path),
        "look_receipt_sha256": _sha256(look_path),
        "hand_sign_capability": "disabled",
    })
    plan = {
        "schema_version": 2,
        "action": "heygen-direct-avatar-audio-video",
        "attempt": ATTEMPT,
        "provider_surface": "heygen-cli-v3-video-create",
        "approval_plan_sha256": APPROVED_PLAN_SHA256,
        "work_id": WORK_ID,
        "recovery_from_attempt": recovery_from_attempt,
        "excluded_look_receipt_sha256": excluded_look_receipt_sha256,
        "avatar_selection_sha256": _sha256(look_path),
        "audio_approval_sha256": _sha256(audio_path),
        "audio_sha256": audio_hash,
        "audio_duration_ms": audio["duration_ms"],
        "voice_id": "user-indextts2-black-gold-v3",
        "used_heygen_tts": False,
        "script_absent": True,
        "voice_id_absent_from_request": True,
        "audio_asset_required": True,
        "request_type": "avatar",
        "engine": engine,
        "output_ratio": "9:16",
        "output_resolution": "1080p",
        "output_format": "mp4",
        "fit": "cover",
        "motion": "natural-subtle-no-sign-language",
        "refresh_count": 1,
        "metadata_check_count": 1,
        "upload_count": 1,
        "submit_count": 1,
        "retry_count": 0,
        "status_poll_limit": 45,
        "status_poll_interval_s": 60,
        "download_count": 1,
        "fee_possible": True,
        "no_provider_fallback": True,
        "no_stock_avatar": True,
        "composite_profile": DEFAULT_COMPOSITE_PROFILE,
    }
    _write_new(PUBLIC_PLAN, plan)
    _write_new(PUBLIC_APPROVAL, {
        "schema_version": 2,
        "approved": True,
        "approval_plan_sha256": APPROVED_PLAN_SHA256,
        "plan_sha256": _sha256(PUBLIC_PLAN),
        "operation_scope": "one-refresh-one-metadata-check-one-audio-upload-one-direct-video-submit-bounded-status-polls-one-download",
        "failure_policy": "stop-no-retry-no-fallback-no-heygen-tts",
    })


def _verify_execute_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _load(PUBLIC_PLAN)
    approval = _load(PUBLIC_APPROVAL)
    approved_binding_valid = True
    if APPROVED_PLAN_SHA256:
        approved_binding_valid = bool(
            APPROVAL_PLAN is not None
            and _sha256(APPROVAL_PLAN) == APPROVED_PLAN_SHA256
            and plan.get("approval_plan_sha256") == APPROVED_PLAN_SHA256
            and approval.get("approval_plan_sha256") == APPROVED_PLAN_SHA256
            and plan.get("work_id") == WORK_ID
            and plan.get("attempt") == ATTEMPT
        )
    if (
        approval.get("approved") is not True or approval.get("plan_sha256") != _sha256(PUBLIC_PLAN)
        or plan.get("used_heygen_tts") is not False or plan.get("script_absent") is not True
        or plan.get("voice_id_absent_from_request") is not True or plan.get("upload_count") != 1
        or plan.get("submit_count") != 1 or plan.get("retry_count") != 0 or plan.get("download_count") != 1
        or plan.get("audio_sha256") != _sha256(AUDIO) or MASTER.exists() or PUBLIC_RESULT.exists()
        or not approved_binding_valid
    ):
        raise HeyGenDirectError("contract-invalid")
    look = _load(RUNTIME / "selected-look.json")
    audio = _load(RUNTIME / "audio-approval.json")
    if (
        look.get("payload_sha256") != hashlib.sha256(_canonical({key: value for key, value in look.items() if key != "payload_sha256"})).hexdigest()
        or look.get("engine") != plan.get("engine") or audio.get("audio_sha256") != _sha256(AUDIO)
        or audio.get("voice_id") != "user-indextts2-black-gold-v3"
    ):
        raise HeyGenDirectError("contract-invalid")
    return plan, look


def _asset_id(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HeyGenDirectError("provider-response-invalid")
    for key in ("asset_id", "id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    raise HeyGenDirectError("provider-response-invalid")


def _video_id(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("video_id"), str):
        raise HeyGenDirectError("provider-response-invalid")
    return data["video_id"]


def _status(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("status"), str):
        raise HeyGenDirectError("provider-response-invalid")
    return data["status"].casefold()


def _write_failure(stage: str, category: str, *, upload_completed: bool, submitted: bool, polls: int) -> None:
    if not PUBLIC_RESULT.exists():
        _write_new(PUBLIC_RESULT, {
            "schema_version": 2, "status": "failed", "attempt": ATTEMPT, "stage": stage,
            "plan_sha256": _sha256(PUBLIC_PLAN), "upload_completed": upload_completed,
            "submit_attempts": int(submitted), "retry_attempts": 0, "status_polls": polls,
            "downloads": 0, "master_created": False, "used_heygen_tts": False,
            "failure_category": category,
        })


def _write_preparation_failure(category: str) -> None:
    """Persist a redacted receipt when preparation fails before a run plan exists."""
    if PUBLIC_RESULT.exists():
        return
    audio_hash = _sha256(AUDIO) if AUDIO.is_file() and not AUDIO.is_symlink() else None
    _write_new(PUBLIC_RESULT, {
        "schema_version": 2,
        "status": "failed",
        "attempt": ATTEMPT,
        "stage": "configured-group-refresh-or-selection",
        "approval_plan_sha256": APPROVED_PLAN_SHA256,
        "audio_sha256": audio_hash,
        "failure_category": category,
        "refresh_command_attempted": True,
        "uploads": 0,
        "submit_attempts": 0,
        "retry_attempts": 0,
        "downloads": 0,
        "master_created": False,
        "used_heygen_tts": False,
        "provider_fallback_used": False,
        "provider_response_persisted": False,
    })


def execute() -> None:
    plan, look = _verify_execute_inputs()
    if not shutil.which("heygen"):
        raise HeyGenDirectError("cli-unavailable")
    uploaded = False
    submitted = False
    polls = 0
    try:
        asset_id = _asset_id(_run(["heygen", "asset", "create", "--file", str(AUDIO)], timeout=300))
        uploaded = True
        request: dict[str, Any] = {
            "type": "avatar", "avatar_id": look["look_id"], "audio_asset_id": asset_id,
            "aspect_ratio": "9:16", "resolution": "1080p", "fit": "cover",
            "engine": {"type": plan["engine"]},
            "output_format": "mp4", "title": VIDEO_TITLE,
        }
        if plan["engine"] == "avatar_v":
            request["motion_prompt"] = (
                "Natural, calm presenter delivery with subtle head movement and restrained hand gestures. "
                "No sign language or deliberate signing gestures."
            )
        request_path = RUNTIME / "direct-video-request.json"
        _write_new(request_path, request)
        video_id = _video_id(_run(["heygen", "video", "create", "--data", str(request_path)], timeout=300))
        submitted = True
        print("status=submitted", flush=True)
        final_status = ""
        for _ in range(int(plan["status_poll_limit"])):
            time.sleep(int(plan["status_poll_interval_s"]))
            polls += 1
            final_status = _status(_run(["heygen", "video", "get", video_id], timeout=90))
            if final_status in {"completed", "success", "done"}:
                break
            if final_status in {"failed", "error", "cancelled"}:
                raise HeyGenDirectError("provider-generation-failed")
        if final_status not in {"completed", "success", "done"}:
            raise HeyGenDirectError("provider-timeout")
        _run(["heygen", "video", "download", video_id, "--output-path", str(MASTER)], timeout=900)
        if not MASTER.is_file() or MASTER.stat().st_size <= 0:
            raise HeyGenDirectError("download-invalid")
        _write_new(PUBLIC_RESULT, {
            "schema_version": 2, "status": "completed-and-downloaded", "plan_sha256": _sha256(PUBLIC_PLAN),
            "master_path": "工程/media/heygen-user-avatar-master-v3.mp4", "master_sha256": _sha256(MASTER),
            "master_size_bytes": MASTER.stat().st_size,
            "video_id_sha256": hashlib.sha256(video_id.encode("utf-8")).hexdigest(),
            "audio_asset_id_sha256": hashlib.sha256(asset_id.encode("utf-8")).hexdigest(),
            "status_checks": polls, "upload_count": 1, "submit_count": 1, "retry_count": 0,
            "download_count": 1, "used_heygen_tts": False, "provider_response_persisted": False,
        })
    except HeyGenDirectError as exc:
        _write_failure(
            "audio-upload" if not uploaded else "direct-video-submit" if not submitted else "generation-or-download",
            str(exc), upload_completed=uploaded, submitted=submitted, polls=polls,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("set-default", "prepare", "execute", "run-approved"))
    parser.add_argument("--avatar-id")
    parser.add_argument("--engine", choices=("avatar_iii", "avatar_iv", "avatar_v"), default="avatar_iii")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--approval-plan", type=Path)
    parser.add_argument("--approved-plan-sha256")
    parser.add_argument("--work-id")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--recovery-from-attempt", type=int)
    arguments = parser.parse_args()
    binding_configured = False
    try:
        if arguments.action in {"set-default", "prepare"} and not arguments.avatar_id:
            raise HeyGenDirectError("look-invalid")
        if arguments.action == "run-approved":
            if not all(
                (
                    arguments.project_root,
                    arguments.approval_plan,
                    arguments.approved_plan_sha256,
                    arguments.work_id,
                )
            ):
                raise HeyGenDirectError("approval-binding-missing")
            _configure_approved_project(
                project_root=arguments.project_root,
                approval_plan=arguments.approval_plan,
                approved_plan_sha256=arguments.approved_plan_sha256,
                work_id=arguments.work_id,
                attempt=arguments.attempt,
            )
            binding_configured = True
        if arguments.action == "set-default":
            set_default(arguments.avatar_id, arguments.engine)
            print("status=default-configured")
        elif arguments.action == "prepare":
            prepare(arguments.avatar_id, arguments.engine)
            print("status=prepared")
        elif arguments.action == "run-approved":
            prepare_approved(
                arguments.engine,
                recovery_from_attempt=arguments.recovery_from_attempt,
            )
            print("status=prepared", flush=True)
            execute()
            print("status=completed")
        else:
            execute()
            print("status=completed")
    except (AvatarDefaultError, HeyGenDirectError) as exc:
        if arguments.action == "run-approved" and binding_configured and not PUBLIC_RESULT.exists():
            try:
                _write_preparation_failure(str(exc))
            except HeyGenDirectError:
                pass
        print("status=failed")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

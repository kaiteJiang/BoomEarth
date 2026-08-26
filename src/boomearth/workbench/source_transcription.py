"""Approved one-call Paraformer transcription of canonical private source audio."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from boomearth.config import Settings
from boomearth.providers.paraformer import (
    ParaformerClient,
    SourceTranscript,
)
from boomearth.workbench.source_artifacts import (
    ActionPlan,
    ArtifactRecord,
    SourceContractError,
    load_action_plan,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_approval,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import (
    SourceLedgerError,
    StageEvent,
    WashEventLedger,
    event_sha256,
)


_AUDIO_MANIFEST_KEYS = frozenset(
    {"artifact", "format", "schema_version", "upstream_sha256", "work_id"}
)
_TRANSCRIPT_KEYS = frozenset({"duration", "language", "sentences", "source_id"})
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class SourceTranscriptionError(RuntimeError):
    """A fixed-message source transcription failure."""

    def __repr__(self) -> str:
        return "SourceTranscriptionError(<redacted>)"


def _digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _plan_value(plan: ActionPlan) -> dict[str, object]:
    return {
        "action": plan.action,
        "fee_possible": plan.fee_possible,
        "input_sha256": plan.input_sha256,
        "network_required": plan.network_required,
        "no_fallback": plan.no_fallback,
        "no_retry": plan.no_retry,
        "provider": plan.provider,
        "request_count": plan.request_count,
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
    }


def _canonical_audio(
    root: Path, work_id: str
) -> tuple[ArtifactRecord, str, Path]:
    try:
        order = load_work_order(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if current.stage != "audio_ready":
            raise ValueError
        manifest_path = verify_private_relative(
            order.private_root, "source-audio-manifest.json"
        )
        manifest_sha256 = sha256_file(manifest_path)
        if manifest_sha256 != current.artifact_sha256:
            raise ValueError
        value = load_exact_json(manifest_path, _AUDIO_MANIFEST_KEYS)
        artifact = value["artifact"]
        audio_format = value["format"]
        if not (
            value["schema_version"] == 1
            and value["work_id"] == work_id
            and _digest(value["upstream_sha256"])
            and isinstance(artifact, dict)
            and set(artifact) == {"relative_path", "sha256", "size_bytes"}
            and artifact["relative_path"] == "source-audio-16k-mono.wav"
            and _digest(artifact["sha256"])
            and type(artifact["size_bytes"]) is int
            and artifact["size_bytes"] > 0
            and isinstance(audio_format, dict)
            and set(audio_format)
            == {"bits_per_sample", "channels", "duration_s", "sample_rate"}
            and audio_format["bits_per_sample"] == 16
            and audio_format["channels"] == 1
            and audio_format["sample_rate"] == 16_000
            and type(audio_format["duration_s"]) in {int, float}
            and math.isfinite(float(audio_format["duration_s"]))
            and float(audio_format["duration_s"]) > 0
        ):
            raise ValueError
        audio = verify_private_relative(order.private_root, artifact["relative_path"])
        if (
            sha256_file(audio) != artifact["sha256"]
            or audio.stat().st_size != artifact["size_bytes"]
        ):
            raise ValueError
        return (
            ArtifactRecord(
                relative_path=artifact["relative_path"],
                sha256=artifact["sha256"],
                size_bytes=artifact["size_bytes"],
                path=audio,
            ),
            manifest_sha256,
            order.private_root,
        )
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        raise SourceTranscriptionError("source-transcription-input-invalid") from None


def plan_source_transcription(root: Path, work_id: str) -> ActionPlan:
    """Publish an immutable one-request Paraformer action plan."""

    audio, _, private_root = _canonical_audio(Path(root), work_id)
    plan = ActionPlan(
        schema_version=1,
        work_id=work_id,
        provider="paraformer",
        action="source-transcription",
        input_sha256=audio.sha256,
        request_count=1,
        network_required=True,
        fee_possible=True,
        no_retry=True,
        no_fallback=True,
    )
    try:
        plan_path = verify_private_relative(private_root, "source-transcription-plan.json")
        publish_json_exclusive(private_root, plan_path, _plan_value(plan))
        return plan
    except SourceContractError:
        raise SourceTranscriptionError("source-transcription-plan-unavailable") from None


def _approved_plan(private_root: Path, work_id: str, approval: Path) -> ActionPlan:
    try:
        plan_path = verify_private_relative(private_root, "source-transcription-plan.json")
        plan = load_action_plan(plan_path)
        if not (
            plan.work_id == work_id
            and plan.provider == "paraformer"
            and plan.action == "source-transcription"
            and plan.request_count == 1
            and plan.network_required is True
            and plan.fee_possible is True
            and plan.no_retry is True
            and plan.no_fallback is True
        ):
            raise SourceContractError("approval-scope-mismatch")
        verify_approval(plan, approval)
        return plan
    except SourceContractError:
        raise SourceTranscriptionError("source-transcription-not-approved") from None


def _validate_transcript(path: Path) -> tuple[str, int, float, str, int]:
    try:
        value = load_exact_json(path, _TRANSCRIPT_KEYS)
        if not (
            isinstance(value["source_id"], str)
            and bool(value["source_id"])
            and type(value["duration"]) in {int, float}
            and math.isfinite(float(value["duration"]))
            and float(value["duration"]) >= 0
            and isinstance(value["language"], str)
            and bool(value["language"])
            and isinstance(value["sentences"], list)
            and all(isinstance(item, dict) for item in value["sentences"])
        ):
            raise ValueError
        return (
            sha256_file(path),
            path.stat().st_size,
            float(value["duration"]),
            value["language"],
            len(value["sentences"]),
        )
    except (SourceContractError, OSError, TypeError, ValueError):
        raise SourceTranscriptionError("source-transcription-artifact-invalid") from None


def _safe_request_id(value: object) -> str:
    if isinstance(value, str) and _REQUEST_ID_RE.fullmatch(value) is not None:
        return value
    return "UNAVAILABLE"


def _client_request_id(client: object) -> str:
    try:
        return _safe_request_id(getattr(client, "last_request_id", None))
    except Exception:
        return "UNAVAILABLE"


def run_source_transcription(
    root: Path,
    work_id: str,
    approval: Path,
    *,
    settings: Settings | None = None,
    client_factory: Callable[..., ParaformerClient] = ParaformerClient,
) -> SourceTranscript:
    """Revalidate the approved WAV, call Paraformer once, and publish its chain."""

    try:
        order = load_work_order(root, work_id)
        private_root = order.private_root
    except SourceContractError:
        raise SourceTranscriptionError("source-transcription-input-invalid") from None
    plan = _approved_plan(private_root, work_id, Path(approval))
    try:
        audio, upstream_sha256, verified_root = _canonical_audio(Path(root), work_id)
    except SourceTranscriptionError:
        raise SourceTranscriptionError("source-transcription-input-changed") from None
    if verified_root != private_root or audio.sha256 != plan.input_sha256:
        raise SourceTranscriptionError("source-transcription-input-changed")
    transcript_path = verify_private_relative(private_root, "transcript.json")
    manifest_path = verify_private_relative(private_root, "transcript-manifest.json")
    if (
        transcript_path.exists()
        or transcript_path.is_symlink()
        or manifest_path.exists()
        or manifest_path.is_symlink()
    ):
        raise SourceTranscriptionError("source-transcription-exists")

    try:
        active_settings = settings if settings is not None else Settings.load(Path(root))
        client = client_factory(settings=active_settings, root=Path(root))
        result = client.transcribe_file(
            audio.path,
            artifact_relative=f"{work_id}/transcript.json",
        )
    except SourceTranscriptionError:
        raise
    except Exception:
        raise SourceTranscriptionError("source-transcription-failed") from None
    manifest_published = False
    try:
        current_audio, current_upstream, _ = _canonical_audio(Path(root), work_id)
        if current_audio.sha256 != plan.input_sha256 or current_upstream != upstream_sha256:
            raise SourceTranscriptionError("source-transcription-input-changed")
        if not (
            isinstance(result, SourceTranscript)
            and result.artifact_id == f"{work_id}/transcript.json"
            and type(result.segment_count) is int
            and result.segment_count >= 0
            and type(result.duration) in {int, float}
            and math.isfinite(float(result.duration))
            and result.duration >= 0
            and isinstance(result.language, str)
            and bool(result.language)
        ):
            raise SourceTranscriptionError("source-transcription-artifact-invalid")
        (
            transcript_sha256,
            transcript_size,
            transcript_duration,
            transcript_language,
            transcript_segments,
        ) = _validate_transcript(transcript_path)
        if (
            transcript_duration != float(result.duration)
            or transcript_language != result.language
            or transcript_segments != result.segment_count
        ):
            raise SourceTranscriptionError("source-transcription-artifact-invalid")
        manifest = {
            "artifact": {
                "relative_path": "transcript.json",
                "sha256": transcript_sha256,
                "size_bytes": transcript_size,
            },
            "request_id": _client_request_id(client),
            "schema_version": 1,
            "summary": {
                "duration": result.duration,
                "language": result.language,
                "segment_count": result.segment_count,
            },
            "upstream_sha256": upstream_sha256,
            "work_id": work_id,
        }
        manifest_sha256 = publish_json_exclusive(
            private_root, manifest_path, manifest
        )
        manifest_published = True
        if (
            sha256_file(transcript_path) != transcript_sha256
            or sha256_file(manifest_path) != manifest_sha256
        ):
            raise SourceTranscriptionError("source-transcription-artifact-invalid")
        ledger = WashEventLedger(root)
        previous = ledger.current(work_id)
        ledger.append(
            StageEvent(
                schema_version=2,
                work_id=work_id,
                source_id=previous.source_id,
                source_kind=previous.source_kind,
                stage="transcript_ready",
                result="ok",
                artifact_label="transcript-manifest",
                artifact_sha256=manifest_sha256,
                timestamp=datetime_now_utc(),
                previous_event_sha256=event_sha256(previous),
            )
        )
        return result
    except SourceTranscriptionError:
        if manifest_published:
            try:
                manifest_path.unlink()
            except OSError:
                pass
        raise
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        if manifest_published:
            try:
                manifest_path.unlink()
            except OSError:
                pass
        raise SourceTranscriptionError("source-transcription-artifact-invalid") from None


def datetime_now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


__all__ = [
    "SourceTranscriptionError",
    "plan_source_transcription",
    "run_source_transcription",
]

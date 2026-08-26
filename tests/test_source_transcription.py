from __future__ import annotations

import json
import importlib.util
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from boomearth.providers.paraformer import SourceTranscript
from boomearth.workbench.source_artifacts import (
    canonical_json_bytes,
    publish_json_exclusive,
    sha256_file,
)
from boomearth.workbench.source_intake import create_local_intake
from boomearth.workbench.source_ledger import WashEventLedger
from boomearth.media.source_snapshot import snapshot_local_source
from boomearth.media.source_audio import normalize_source_audio
from boomearth.workbench.source_transcription import (
    SourceTranscriptionError,
    plan_source_transcription,
    run_source_transcription,
)


WORK_ID = "00000000-0000-4000-8000-000000000005"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 13, 7, 8, 9, tzinfo=timezone.utc)


def _work_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def _audio_work(root: Path) -> Path:
    source = root / "authorized" / "private-source.wav"
    source.parent.mkdir(parents=True)
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\0\0" * 16_000)
    create_local_intake(
        root,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    snapshot_local_source(root, WORK_ID)
    return normalize_source_audio(root, WORK_ID).artifact.path


def _approval(root: Path) -> Path:
    plan_path = _work_root(root) / "source-transcription-plan.json"
    plan = json.loads(plan_path.read_text("utf-8"))
    receipt = {
        "action": plan["action"],
        "approved": True,
        "input_sha256": plan["input_sha256"],
        "no_fallback": True,
        "no_retry": True,
        "plan_sha256": sha256_file(plan_path),
        "provider": "paraformer",
        "request_count": 1,
        "work_id": WORK_ID,
    }
    path = _work_root(root) / "source-transcription-approval.json"
    publish_json_exclusive(_work_root(root), path, receipt)
    return path


def test_transcription_plan_binds_exact_canonical_audio(tmp_path: Path) -> None:
    audio = _audio_work(tmp_path)

    plan = plan_source_transcription(tmp_path, WORK_ID)

    assert plan.provider == "paraformer"
    assert plan.action == "source-transcription"
    assert plan.input_sha256 == sha256_file(audio)
    assert plan.request_count == 1
    assert plan.network_required is True and plan.fee_possible is True
    assert plan.no_retry is True and plan.no_fallback is True


def test_transcription_rechecks_digest_then_calls_once_and_publishes_private_manifest(
    tmp_path: Path,
) -> None:
    audio = _audio_work(tmp_path)
    plan_source_transcription(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    calls: list[tuple[Path, str | None]] = []

    class RecordingClient:
        last_request_id = "synthetic-request-id"

        def __init__(self, **kwargs) -> None:
            assert kwargs["settings"] is configured
            assert kwargs["root"] == tmp_path

        def transcribe_file(
            self, path: Path, *, artifact_relative: str | None = None
        ) -> SourceTranscript:
            calls.append((path, artifact_relative))
            assert artifact_relative == f"{WORK_ID}/transcript.json"
            transcript = _work_root(tmp_path) / "transcript.json"
            publish_json_exclusive(
                _work_root(tmp_path),
                transcript,
                {
                    "duration": 1.0,
                    "language": "zh-CN",
                    "sentences": [{"text": "private sentence"}],
                    "source_id": "safe-source-id",
                },
            )
            return SourceTranscript(1.0, 1, "zh-CN", artifact_relative)

    configured = SimpleNamespace(dashscope_api_key="synthetic-credential")
    result = run_source_transcription(
        tmp_path,
        WORK_ID,
        approval,
        settings=configured,
        client_factory=RecordingClient,
    )

    assert calls == [(audio, f"{WORK_ID}/transcript.json")]
    assert result.segment_count == 1
    manifest_path = _work_root(tmp_path) / "transcript-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert manifest["artifact"]["sha256"] == sha256_file(
        _work_root(tmp_path) / "transcript.json"
    )
    assert manifest["upstream_sha256"] == sha256_file(
        _work_root(tmp_path) / "source-audio-manifest.json"
    )
    assert manifest["request_id"] == "synthetic-request-id"
    assert "private sentence" not in manifest_path.read_text("utf-8")
    assert "private sentence" not in repr(result)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "transcript_ready"


def test_transcription_rejects_bad_approval_before_client(
    tmp_path: Path,
) -> None:
    _audio_work(tmp_path)
    plan_source_transcription(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    value = json.loads(approval.read_text("utf-8"))
    value["plan_sha256"] = "0" * 64
    approval.write_bytes(canonical_json_bytes(value))
    calls = 0

    class ForbiddenClient:
        def __init__(self, **_kwargs) -> None:
            nonlocal calls
            calls += 1

    with pytest.raises(SourceTranscriptionError, match="^source-transcription-not-approved$"):
        run_source_transcription(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(),
            client_factory=ForbiddenClient,
        )
    assert calls == 0


def test_transcription_rejects_audio_mutation_after_plan_before_client(
    tmp_path: Path,
) -> None:
    audio = _audio_work(tmp_path)
    plan_source_transcription(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    audio.write_bytes(b"changed")
    calls = 0

    class ForbiddenClient:
        def __init__(self, **_kwargs) -> None:
            nonlocal calls
            calls += 1

    with pytest.raises(SourceTranscriptionError, match="^source-transcription-input-changed$"):
        run_source_transcription(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(),
            client_factory=ForbiddenClient,
        )
    assert calls == 0


def test_transcription_client_failure_is_redacted_and_not_retried(
    tmp_path: Path,
) -> None:
    _audio_work(tmp_path)
    plan_source_transcription(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    calls = 0

    class FailingClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def transcribe_file(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("private transcript and path")

    with pytest.raises(SourceTranscriptionError) as raised:
        run_source_transcription(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(),
            client_factory=FailingClient,
        )
    assert str(raised.value) == "source-transcription-failed"
    assert "private transcript" not in repr(raised.value)
    assert calls == 1
    assert WashEventLedger(tmp_path).status(WORK_ID) == "audio_ready"


def test_transcription_existing_artifact_blocks_client_before_request(
    tmp_path: Path,
) -> None:
    _audio_work(tmp_path)
    plan_source_transcription(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    (_work_root(tmp_path) / "transcript.json").write_bytes(b"foreign")
    calls = 0

    class ForbiddenClient:
        def __init__(self, **_kwargs) -> None:
            nonlocal calls
            calls += 1

    with pytest.raises(SourceTranscriptionError, match="^source-transcription-exists$"):
        run_source_transcription(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(),
            client_factory=ForbiddenClient,
        )
    assert calls == 0


def test_transcription_rejects_summary_that_disagrees_with_private_artifact(
    tmp_path: Path,
) -> None:
    _audio_work(tmp_path)
    plan_source_transcription(tmp_path, WORK_ID)
    approval = _approval(tmp_path)

    class MismatchedClient:
        last_request_id = "safe-id"

        def __init__(self, **_kwargs) -> None:
            pass

        def transcribe_file(self, _path: Path, *, artifact_relative: str | None = None):
            publish_json_exclusive(
                _work_root(tmp_path),
                _work_root(tmp_path) / "transcript.json",
                {
                    "duration": 1.0,
                    "language": "zh-CN",
                    "sentences": [{"text": "private"}],
                    "source_id": "safe-id",
                },
            )
            return SourceTranscript(1.0, 2, "zh-CN", artifact_relative or "")

    with pytest.raises(
        SourceTranscriptionError, match="^source-transcription-artifact-invalid$"
    ):
        run_source_transcription(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(),
            client_factory=MismatchedClient,
        )


def test_transcription_raising_request_id_property_is_redacted(
    tmp_path: Path,
) -> None:
    _audio_work(tmp_path)
    plan_source_transcription(tmp_path, WORK_ID)
    approval = _approval(tmp_path)

    class RaisingRequestIdClient:
        def __init__(self, **_kwargs) -> None:
            pass

        @property
        def last_request_id(self):
            raise RuntimeError("private transcript")

        def transcribe_file(self, _path: Path, *, artifact_relative: str | None = None):
            publish_json_exclusive(
                _work_root(tmp_path),
                _work_root(tmp_path) / "transcript.json",
                {
                    "duration": 1.0,
                    "language": "zh-CN",
                    "sentences": [{"text": "private"}],
                    "source_id": "safe-id",
                },
            )
            return SourceTranscript(1.0, 1, "zh-CN", artifact_relative or "")

    run_source_transcription(
        tmp_path,
        WORK_ID,
        approval,
        settings=SimpleNamespace(),
        client_factory=RaisingRequestIdClient,
    )
    manifest = json.loads(
        (_work_root(tmp_path) / "transcript-manifest.json").read_text("utf-8")
    )
    assert manifest["request_id"] == "UNAVAILABLE"


def test_transcribe_source_cli_plan_is_offline_and_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _audio_work(tmp_path)
    script_path = (
        Path(__file__).parents[1] / "automation" / "scripts" / "transcribe_source.py"
    )
    spec = importlib.util.spec_from_file_location("transcribe_source_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["plan", WORK_ID], root=tmp_path) == 0

    captured = capsys.readouterr()
    assert "action=source-transcription status=planned" in captured.out
    assert "private-source" not in captured.out
    assert captured.err == ""

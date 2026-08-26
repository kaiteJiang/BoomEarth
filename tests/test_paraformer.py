"""Behavioral tests for the private Paraformer source transcript boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import fields
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import httpx
import pytest

import boomearth.providers.paraformer as paraformer
from boomearth.providers.paraformer import (
    ParaformerClient,
    ParaformerCredentialError,
    ParaformerInputError,
    ParaformerMetadataError,
    ParaformerRecognitionError,
    SourceTranscript,
)


TOKEN = "synthetic-dashscope-token-never-real"
PRIVATE_TEXT = "synthetic private transcript must stay private"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _load_smoke_module() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    path = root / "automation" / "scripts" / "api_smoke.py"
    spec = importlib.util.spec_from_file_location("boomearth_api_smoke_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_transcribe_file_uses_realtime_sdk_and_writes_private_timestamped_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    response_data = _fixture("paraformer_ok.json")
    sentences = response_data["output"]["sentence"]  # type: ignore[index]
    response = SimpleNamespace(**response_data)
    calls: list[dict[str, object]] = []

    class FakeRecognition:
        def __init__(
            self,
            model: str,
            callback: object,
            format: str,
            sample_rate: int,
            **kwargs: object,
        ) -> None:
            calls.append(
                {
                    "model": model,
                    "callback": callback,
                    "format": format,
                    "sample_rate": sample_rate,
                    "kwargs": kwargs,
                }
            )

        def call(self, file: str) -> object:
            calls[-1]["file"] = file
            return response

    monkeypatch.setattr(paraformer, "Recognition", FakeRecognition)
    monkeypatch.setattr(
        paraformer,
        "_probe_audio",
        lambda path: SimpleNamespace(
            format="wav",
            sample_rate=16000,
            duration=2.5,
        ),
    )

    client = ParaformerClient(
        SimpleNamespace(dashscope_api_key=TOKEN),
        root=tmp_path,
    )
    result = client.transcribe_file(audio)

    assert calls == [
        {
            "model": "paraformer-realtime-v2",
            "callback": None,
            "format": "wav",
            "sample_rate": 16000,
            "kwargs": {"api_key": TOKEN},
            "file": str(audio),
        }
    ]
    assert tuple(field.name for field in fields(SourceTranscript)) == (
        "duration",
        "segment_count",
        "language",
        "artifact_id",
    )
    assert result.duration == 2.5
    assert result.segment_count == 1
    assert result.language == "zh-CN"

    source_id = hashlib.sha256(audio.read_bytes()).hexdigest()[:12]
    expected_relative_artifact = (
        f"{date.today().isoformat()}-{source_id}/transcript.json"
    )
    assert result.artifact_id == expected_relative_artifact

    artifact_path = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / expected_relative_artifact.replace("/", "\\")
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["sentences"] == sentences
    assert artifact["sentences"][0]["begin_time"] == 0
    assert artifact["sentences"][0]["end_time"] == 1250
    artifact_text = artifact_path.read_text(encoding="utf-8")
    assert audio.name not in artifact_text
    assert str(audio) not in artifact_text
    rendered = repr(result) + repr(client)
    assert PRIVATE_TEXT not in rendered
    assert TOKEN not in rendered
    assert str(audio) not in rendered


def test_missing_dashscope_credential_is_rejected_without_sdk_access(
    tmp_path: Path,
) -> None:
    with pytest.raises(ParaformerCredentialError) as raised:
        ParaformerClient(SimpleNamespace(dashscope_api_key="   "), root=tmp_path)

    rendered = str(raised.value) + repr(raised.value)
    assert TOKEN not in rendered
    assert PRIVATE_TEXT not in rendered


def test_unsupported_extension_is_rejected_before_ffprobe_or_sdk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "source.txt"
    audio.write_bytes(b"synthetic-audio-bytes")
    probe_calls: list[Path] = []
    sdk_calls: list[object] = []

    monkeypatch.setattr(
        paraformer,
        "_probe_audio",
        lambda path: probe_calls.append(path),
    )
    monkeypatch.setattr(
        paraformer,
        "Recognition",
        lambda *args, **kwargs: sdk_calls.append((args, kwargs)),
    )

    client = ParaformerClient(
        SimpleNamespace(dashscope_api_key=TOKEN),
        root=tmp_path,
    )
    with pytest.raises(ParaformerInputError):
        client.transcribe_file(audio)

    assert probe_calls == []
    assert sdk_calls == []


def test_unsupported_sample_rate_is_rejected_before_sdk_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    sdk_calls: list[object] = []

    monkeypatch.setattr(
        paraformer,
        "_probe_audio",
        lambda path: SimpleNamespace(
            format="wav",
            sample_rate=44100,
            duration=2.5,
        ),
    )
    monkeypatch.setattr(
        paraformer,
        "Recognition",
        lambda *args, **kwargs: sdk_calls.append((args, kwargs)),
    )

    client = ParaformerClient(
        SimpleNamespace(dashscope_api_key=TOKEN),
        root=tmp_path,
    )
    with pytest.raises(ParaformerMetadataError):
        client.transcribe_file(audio)

    assert sdk_calls == []


def test_ffprobe_metadata_command_is_used_without_echoing_probe_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "source.mp3"
    audio.write_bytes(b"synthetic-audio-bytes")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> object:
        calls.append(command)
        assert kwargs == {
            "capture_output": True,
            "check": False,
            "text": True,
        }
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [{"sample_rate": "8000"}],
                    "format": {"format_name": "mp3", "duration": "3.75"},
                }
            ),
            stderr=f"probe detail {PRIVATE_TEXT}",
        )

    monkeypatch.setattr(paraformer.subprocess, "run", fake_run)

    metadata = paraformer._probe_audio(audio)

    assert metadata.format == "mp3"
    assert metadata.sample_rate == 8000
    assert metadata.duration == 3.75
    assert calls == [
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=format_name,duration:stream=sample_rate",
            "-of",
            "json",
            str(audio),
        ]
    ]


def test_ffprobe_failure_is_typed_without_echoing_path_or_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "private-source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")

    def fake_run(command: list[str], **kwargs: object) -> object:
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=f"raw probe failure {PRIVATE_TEXT} {audio}",
        )

    monkeypatch.setattr(paraformer.subprocess, "run", fake_run)

    with pytest.raises(ParaformerMetadataError) as raised:
        paraformer._probe_audio(audio)

    rendered = str(raised.value) + repr(raised.value)
    assert PRIVATE_TEXT not in rendered
    assert str(audio) not in rendered


def test_response_failure_is_typed_without_raw_sdk_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "private-source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")

    class FakeRecognition:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def call(self, file: str) -> object:
            return SimpleNamespace(
                status_code=400,
                code="InvalidParameter",
                message=f"raw response {PRIVATE_TEXT} {TOKEN} {file}",
                request_id="synthetic-request-id-response-error",
            )

    monkeypatch.setattr(paraformer, "Recognition", FakeRecognition)
    monkeypatch.setattr(
        paraformer,
        "_probe_audio",
        lambda path: SimpleNamespace(
            format="wav",
            sample_rate=16000,
            duration=2.5,
        ),
    )

    client = ParaformerClient(
        SimpleNamespace(dashscope_api_key=TOKEN),
        root=tmp_path,
    )
    with pytest.raises(ParaformerRecognitionError) as raised:
        client.transcribe_file(audio)

    assert str(raised.value) == (
        "Paraformer recognition failed; "
        "request_id=synthetic-request-id-response-error"
    )
    rendered = str(raised.value) + repr(raised.value)
    assert PRIVATE_TEXT not in rendered
    assert TOKEN not in rendered
    assert str(audio) not in rendered
    assert client.last_request_id == "synthetic-request-id-response-error"


def test_sdk_failure_is_typed_and_redacted_but_keeps_safe_request_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "private-source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")

    class FakeRecognition:
        last_request_id = "synthetic-request-id-error"

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def call(self, file: str) -> object:
            raise RuntimeError(
                f"raw sdk message {PRIVATE_TEXT} {TOKEN} {file}"
            )

    monkeypatch.setattr(paraformer, "Recognition", FakeRecognition)
    monkeypatch.setattr(
        paraformer,
        "_probe_audio",
        lambda path: SimpleNamespace(
            format="wav",
            sample_rate=16000,
            duration=2.5,
        ),
    )

    client = ParaformerClient(
        SimpleNamespace(dashscope_api_key=TOKEN),
        root=tmp_path,
    )
    with pytest.raises(ParaformerRecognitionError) as raised:
        client.transcribe_file(audio)

    rendered = str(raised.value) + repr(raised.value) + repr(client)
    assert "synthetic-request-id-error" in rendered
    assert PRIVATE_TEXT not in rendered
    assert TOKEN not in rendered
    assert str(audio) not in rendered
    assert client.last_request_id == "synthetic-request-id-error"


def test_paraformer_smoke_prints_only_safe_summary_for_explicit_audio(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load_smoke_module()
    monkeypatch.setattr(module, "validate_local_audio", lambda path: object())
    audio = tmp_path / "private-source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    calls: list[tuple[object, Path, Path]] = []

    class FakeClient:
        last_request_id = "synthetic-request-id-smoke"

        def __init__(self, settings: object, root: Path) -> None:
            calls.append((settings, root, audio))

        def transcribe_file(self, path: Path) -> SourceTranscript:
            assert path == audio
            return SourceTranscript(
                duration=2.5,
                segment_count=1,
                language="zh-CN",
                artifact_id="private-only/transcript.json",
            )

    monkeypatch.setattr(module.Settings, "load", lambda root: object())

    exit_code = module.main(
        ["paraformer", "--audio", str(audio), "--authorized"],
        root=tmp_path,
        paraformer_client_factory=FakeClient,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert len(calls) == 1
    assert output.out.splitlines() == [
        "status=OK",
        "duration=2.5",
        "segments=1",
        "request_id=synthetic-request-id-smoke",
    ]
    assert output.err == ""
    assert PRIVATE_TEXT not in output.out + output.err
    assert TOKEN not in output.out + output.err
    assert str(audio) not in output.out + output.err


def test_paraformer_smoke_redacts_errors_and_keeps_request_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load_smoke_module()
    monkeypatch.setattr(module, "validate_local_audio", lambda path: object())
    audio = tmp_path / "private-source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")

    class FakeClient:
        last_request_id = "synthetic-request-id-cli-error"

        def __init__(self, settings: object, root: Path) -> None:
            pass

        def transcribe_file(self, path: Path) -> SourceTranscript:
            raise RuntimeError(f"raw sdk message {PRIVATE_TEXT} {TOKEN} {path}")

    monkeypatch.setattr(module.Settings, "load", lambda root: object())

    exit_code = module.main(
        ["paraformer", "--audio", str(audio), "--authorized"],
        root=tmp_path,
        paraformer_client_factory=FakeClient,
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert output.out.splitlines() == [
        "status=INTERNAL_ERROR",
        "duration=UNAVAILABLE",
        "segments=UNAVAILABLE",
        "request_id=synthetic-request-id-cli-error",
    ]
    assert output.err == ""
    assert PRIVATE_TEXT not in output.out + output.err
    assert TOKEN not in output.out + output.err
    assert str(audio) not in output.out + output.err


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ParaformerInputError("PRIVATE"), "INPUT_ERROR"),
        (ParaformerRecognitionError("PRIVATE"), "SERVICE_ERROR"),
        (
            httpx.ConnectError(
                "PRIVATE",
                request=httpx.Request("POST", "https://example.invalid"),
            ),
            "NETWORK_ERROR",
        ),
        (
            httpx.TimeoutException(
                "PRIVATE",
                request=httpx.Request("POST", "https://example.invalid"),
            ),
            "TIMEOUT",
        ),
        (RuntimeError("PRIVATE"), "INTERNAL_ERROR"),
    ],
)
def test_paraformer_smoke_classifies_failures_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    error: BaseException,
    expected: str,
) -> None:
    """Would fail if typed failures leaked detail or used an unstable generic status."""

    module = _load_smoke_module()
    monkeypatch.setattr(module, "validate_local_audio", lambda path: object())
    audio = tmp_path / "private-source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")

    class FakeClient:
        last_request_id = "synthetic-classification-id"

        def __init__(self, settings: object, root: Path) -> None:
            pass

        def transcribe_file(self, path: Path) -> SourceTranscript:
            raise error

    monkeypatch.setattr(module.Settings, "load", lambda root: object())

    exit_code = module.main(
        ["paraformer", "--audio", str(audio), "--authorized"],
        root=tmp_path,
        paraformer_client_factory=FakeClient,
    )
    output = capsys.readouterr()

    assert exit_code == (2 if expected == "INPUT_ERROR" else 1)
    assert output.out.splitlines() == [
        f"status={expected}",
        "duration=UNAVAILABLE",
        "segments=UNAVAILABLE",
        "request_id=synthetic-classification-id",
    ]
    assert "PRIVATE" not in output.out + output.err


def test_paraformer_smoke_blocks_missing_audio_before_settings_or_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Would fail if missing local input constructed a client or loaded configuration."""

    module = _load_smoke_module()
    missing_audio = tmp_path / "private-missing.wav"
    settings_calls: list[Path] = []
    client_calls: list[object] = []

    def forbidden_settings(root: Path) -> object:
        settings_calls.append(root)
        raise AssertionError("missing input must block settings loading")

    class ForbiddenClient:
        def __init__(self, settings: object, root: Path) -> None:
            client_calls.append(settings)
            raise AssertionError("missing input must block client construction")

    monkeypatch.setattr(module.Settings, "load", forbidden_settings)

    exit_code = module.main(
        ["paraformer", "--audio", str(missing_audio), "--authorized"],
        root=tmp_path,
        paraformer_client_factory=ForbiddenClient,
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert settings_calls == []
    assert client_calls == []
    assert output.out.splitlines() == [
        "status=INPUT_ERROR",
        "duration=UNAVAILABLE",
        "segments=UNAVAILABLE",
        "request_id=UNAVAILABLE",
    ]
    assert str(missing_audio) not in output.out + output.err


def test_paraformer_smoke_requires_authorization_before_settings_or_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load_smoke_module()
    audio = tmp_path / "private-source-name.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    settings_calls: list[Path] = []
    client_calls: list[object] = []

    def forbidden_settings(root: Path) -> object:
        settings_calls.append(root)
        raise AssertionError("Settings.load must not run without authorization")

    class ForbiddenClient:
        def __init__(self, settings: object, root: Path) -> None:
            client_calls.append((settings, root))
            raise AssertionError("client construction must not run without authorization")

    monkeypatch.setattr(module.Settings, "load", forbidden_settings)

    exit_code = module.main(
        ["paraformer", "--audio", str(audio)],
        root=tmp_path,
        paraformer_client_factory=ForbiddenClient,
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert settings_calls == []
    assert client_calls == []
    assert output.out.splitlines() == [
        "status=AUTHORIZATION_REQUIRED",
        "duration=UNAVAILABLE",
        "segments=UNAVAILABLE",
        "request_id=UNAVAILABLE",
    ]
    assert output.err == ""


def test_paraformer_help_discloses_dashscope_upload_credentials_and_cost(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_smoke_module()

    with pytest.raises(SystemExit) as raised:
        module.main(["paraformer", "--help"])

    output = capsys.readouterr()
    help_text = (output.out + output.err).lower()
    assert raised.value.code == 0
    assert "upload" in help_text
    assert "transcrib" in help_text
    assert "dashscope" in help_text
    assert "dashscope_api_key" in help_text
    assert "quota" in help_text
    assert "charg" in help_text


def test_audio_source_id_hashes_large_input_in_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    chunk_size = 1024 * 1024
    data = (b"0123456789abcdef" * ((chunk_size + 17) // 16))[: chunk_size + 17]
    audio = tmp_path / "large-source.wav"
    audio.write_bytes(data)
    read_sizes: list[int] = []
    original_open = Path.open

    class TrackingReader:
        def __init__(self, wrapped: object) -> None:
            self._wrapped = wrapped

        def __enter__(self) -> "TrackingReader":
            self._wrapped.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self._wrapped.__exit__(*args)  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self._wrapped.read(size)  # type: ignore[attr-defined]

    def tracking_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        wrapped = original_open(path, *args, **kwargs)
        if path == audio and args and args[0] == "rb":
            return TrackingReader(wrapped)
        return wrapped

    monkeypatch.setattr(Path, "open", tracking_open)

    source_id = paraformer._audio_source_id(audio)

    assert source_id == hashlib.sha256(data).hexdigest()[:12]
    assert len(read_sizes) >= 2
    assert max(read_sizes) <= chunk_size
    assert set(read_sizes) == {chunk_size}


def test_transcribe_file_accepts_explicit_private_artifact_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    monkeypatch.setattr(
        paraformer,
        "_probe_audio",
        lambda path: SimpleNamespace(format="wav", sample_rate=16000, duration=1.0),
    )

    class FakeRecognition:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def call(self, _file: str) -> object:
            return SimpleNamespace(
                status_code=200,
                output={"sentence": [{"text": "private"}], "language": "zh-CN"},
                request_id="safe-request-id",
            )

    monkeypatch.setattr(paraformer, "Recognition", FakeRecognition)
    client = ParaformerClient(SimpleNamespace(dashscope_api_key=TOKEN), root=tmp_path)

    result = client.transcribe_file(
        audio, artifact_relative="00000000-0000-4000-8000-000000000005/transcript.json"
    )

    assert result.artifact_id.endswith("/transcript.json")
    assert (tmp_path / "01-内容生产" / "视频工作台" / ".internal" / "洗稿" / result.artifact_id).is_file()


@pytest.mark.parametrize(
    ("extension", "format_name", "sample_rate", "expected_format"),
    [
        (".wav", "wav", "16000", "wav"),
        (".wav", "wave", "16000", "wav"),
        (".mp3", "mp3", "8000", "mp3"),
    ],
)
def test_ffprobe_accepts_supported_detected_wav_and_mp3_containers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    extension: str,
    format_name: str,
    sample_rate: str,
    expected_format: str,
) -> None:
    audio = tmp_path / f"source{extension}"
    audio.write_bytes(b"synthetic-audio-bytes")

    monkeypatch.setattr(
        paraformer.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [{"sample_rate": sample_rate}],
                    "format": {"format_name": format_name, "duration": "3.75"},
                }
            ),
            stderr="",
        ),
    )

    metadata = paraformer._probe_audio(audio)

    assert metadata.format == expected_format
    assert metadata.sample_rate == int(sample_rate)
    assert metadata.duration == 3.75


def test_renamed_container_is_rejected_before_recognition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "actually-mp3-renamed.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    recognition_calls: list[object] = []

    monkeypatch.setattr(
        paraformer.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [{"sample_rate": "16000"}],
                    "format": {"format_name": "mp3", "duration": "3.75"},
                }
            ),
            stderr="",
        ),
    )

    class ForbiddenRecognition:
        def __init__(self, *args: object, **kwargs: object) -> None:
            recognition_calls.append((args, kwargs))
            raise AssertionError("Recognition must not run for a renamed container")

    monkeypatch.setattr(paraformer, "Recognition", ForbiddenRecognition)
    client = ParaformerClient(
        SimpleNamespace(dashscope_api_key=TOKEN),
        root=tmp_path,
    )

    with pytest.raises(ParaformerMetadataError):
        client.transcribe_file(audio)

    assert recognition_calls == []


def test_paraformer_smoke_blocks_corrupt_existing_audio_before_settings_or_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Would fail if an existing but corrupt WAV reached settings or the client factory."""

    module = _load_smoke_module()
    audio = tmp_path / "private-corrupt.wav"
    audio.write_bytes(b"not a WAV")
    settings_calls: list[Path] = []
    client_calls: list[object] = []

    def forbidden_settings(root: Path) -> object:
        settings_calls.append(root)
        raise AssertionError("corrupt local audio must block settings loading")

    class ForbiddenClient:
        def __init__(self, settings: object, root: Path) -> None:
            client_calls.append(settings)

    monkeypatch.setattr(module.Settings, "load", forbidden_settings)
    exit_code = module.main(
        ["paraformer", "--audio", str(audio), "--authorized"],
        root=tmp_path,
        paraformer_client_factory=ForbiddenClient,
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert settings_calls == []
    assert client_calls == []
    assert output.out.splitlines()[0] == "status=INPUT_ERROR"
    assert "PRIVATE" not in output.out + output.err
    assert str(audio) not in output.out + output.err


def test_paraformer_provider_preserves_timeout_transport_and_service_classes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if SDK failures lost timeout, transport, or service distinctions."""

    audio = tmp_path / "source.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    monkeypatch.setattr(
        paraformer,
        "_probe_audio",
        lambda path: SimpleNamespace(format="wav", sample_rate=16000, duration=1.0),
    )

    cases = (
        (
            httpx.TimeoutException(
                "PRIVATE", request=httpx.Request("POST", "https://example.invalid")
            ),
            "ParaformerTimeoutError",
        ),
        (
            httpx.ConnectError(
                "PRIVATE", request=httpx.Request("POST", "https://example.invalid")
            ),
            "ParaformerTransportError",
        ),
        (RuntimeError("PRIVATE"), "ParaformerServiceError"),
    )

    for error, expected_name in cases:
        class FakeRecognition:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def call(self, file: str) -> object:
                raise error

        monkeypatch.setattr(paraformer, "Recognition", FakeRecognition)
        client = ParaformerClient(SimpleNamespace(dashscope_api_key=TOKEN), root=tmp_path)
        with pytest.raises(paraformer.ParaformerError) as raised:
            client.transcribe_file(audio)
        assert type(raised.value).__name__ == expected_name


def test_paraformer_smoke_unknown_settings_error_is_internal_without_factory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Would fail if unexpected settings errors escaped after local preflight."""

    module = _load_smoke_module()
    audio = tmp_path / "safe.wav"
    audio.write_bytes(b"synthetic-audio-bytes")
    calls: list[object] = []
    monkeypatch.setattr(module, "validate_local_audio", lambda path: object(), raising=False)
    monkeypatch.setattr(
        module.Settings,
        "load",
        lambda root: (_ for _ in ()).throw(RuntimeError("PRIVATE")),
    )

    class ForbiddenClient:
        def __init__(self, settings: object, root: Path) -> None:
            calls.append(settings)

    exit_code = module.main(
        ["paraformer", "--audio", str(audio), "--authorized"],
        root=tmp_path,
        paraformer_client_factory=ForbiddenClient,
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert calls == []
    assert output.out.splitlines()[0] == "status=INTERNAL_ERROR"
    assert "PRIVATE" not in output.out + output.err


@pytest.mark.parametrize(
    "value",
    ["line\nbreak", "x" * 129, True, object()],
)
def test_smoke_request_id_rejects_unsafe_values(
    value: object,
) -> None:
    """Would fail if unsafe request IDs could enter public smoke output."""

    module = _load_smoke_module()
    assert module._safe_smoke_request_id(SimpleNamespace(last_request_id=value)) == "UNAVAILABLE"


def test_smoke_request_id_rejects_a_raising_getter() -> None:
    """Would fail if a request-id getter exception escaped the redacted boundary."""

    module = _load_smoke_module()

    class RaisingClient:
        @property
        def last_request_id(self) -> object:
            raise RuntimeError("PRIVATE")

    assert module._safe_smoke_request_id(RaisingClient()) == "UNAVAILABLE"

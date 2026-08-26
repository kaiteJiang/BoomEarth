from __future__ import annotations

import json
import importlib.util
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.media import source_audio
from boomearth.media.source_audio import SourceAudioError, normalize_source_audio
from boomearth.media.source_snapshot import snapshot_local_source
from boomearth.workbench.source_artifacts import sha256_file
from boomearth.workbench.source_intake import create_local_intake
from boomearth.workbench.source_ledger import WashEventLedger


WORK_ID = "00000000-0000-4000-8000-000000000002"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 13, 5, 6, 7, tzinfo=timezone.utc)


def _work_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def _write_wav(
    path: Path,
    *,
    duration_s: float = 1.25,
    rate: int = 44_100,
    channels: int = 2,
    width: int = 2,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = round(rate * duration_s)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b"\0" * frames * channels * width)


def _prepared_media(root: Path) -> Path:
    source = root / "authorized" / "private-source.wav"
    _write_wav(source)
    create_local_intake(
        root,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    snapshot_local_source(root, WORK_ID)
    return source


def test_normalizer_creates_exact_pcm_contract(tmp_path: Path) -> None:
    _prepared_media(tmp_path)

    result = normalize_source_audio(tmp_path, WORK_ID)

    output = _work_root(tmp_path) / "source-audio-16k-mono.wav"
    manifest_path = _work_root(tmp_path) / "source-audio-manifest.json"
    media_manifest = _work_root(tmp_path) / "source-media-manifest.json"
    with wave.open(str(output), "rb") as stream:
        assert stream.getframerate() == 16_000
        assert stream.getnchannels() == 1
        assert stream.getsampwidth() == 2
        assert stream.getnframes() > 0
    assert (result.sample_rate, result.channels, result.bits_per_sample) == (
        16_000,
        1,
        16,
    )
    assert result.duration_s == pytest.approx(1.25, abs=0.03)
    assert result.artifact.path == output
    assert result.artifact.sha256 == sha256_file(output)
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert manifest["upstream_sha256"] == sha256_file(media_manifest)
    assert manifest["artifact"]["relative_path"] == "source-audio-16k-mono.wav"
    assert manifest["artifact"]["sha256"] == result.artifact.sha256
    assert manifest["format"] == {
        "bits_per_sample": 16,
        "channels": 1,
        "duration_s": pytest.approx(1.25, abs=0.03),
        "sample_rate": 16_000,
    }
    assert WashEventLedger(tmp_path).status(WORK_ID) == "audio_ready"
    assert "private-source" not in repr(result)


def test_normalizer_rejects_source_manifest_drift_before_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_media(tmp_path)
    manifest = _work_root(tmp_path) / "source-media-manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("ffmpeg must not run")

    monkeypatch.setattr(source_audio.subprocess, "run", fail_if_called)

    with pytest.raises(SourceAudioError, match="^source-audio-input-invalid$"):
        normalize_source_audio(tmp_path, WORK_ID)

    assert called is False
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_normalizer_suppresses_ffmpeg_failure_and_cleans_temporary_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_media(tmp_path)

    monkeypatch.setattr(
        source_audio.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"private source title"
        ),
    )

    with pytest.raises(SourceAudioError) as raised:
        normalize_source_audio(tmp_path, WORK_ID)

    assert str(raised.value) == "source-audio-conversion-failed"
    assert "private source title" not in repr(raised.value)
    assert not list(_work_root(tmp_path).glob(".source-audio.*.wav"))
    assert not (_work_root(tmp_path) / "source-audio-16k-mono.wav").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_normalizer_rejects_wrong_ffmpeg_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_media(tmp_path)

    def write_wrong_output(argv, **_kwargs):
        _write_wav(Path(argv[-1]), duration_s=1.25, rate=8_000, channels=2)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(source_audio.subprocess, "run", write_wrong_output)

    with pytest.raises(SourceAudioError, match="^source-audio-invalid$"):
        normalize_source_audio(tmp_path, WORK_ID)

    assert not list(_work_root(tmp_path).glob(".source-audio.*.wav"))
    assert not (_work_root(tmp_path) / "source-audio-manifest.json").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_normalizer_rechecks_upstream_after_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_media(tmp_path)
    run = source_audio.subprocess.run

    def run_then_mutate(argv, **kwargs):
        result = run(argv, **kwargs)
        if Path(argv[0]).name.casefold() in {"ffmpeg", "ffmpeg.exe"}:
            (_work_root(tmp_path) / "source-media" / "original.wav").write_bytes(
                b"changed-during-conversion"
            )
        return result

    monkeypatch.setattr(source_audio.subprocess, "run", run_then_mutate)

    with pytest.raises(SourceAudioError, match="^source-audio-input-invalid$"):
        normalize_source_audio(tmp_path, WORK_ID)

    assert not (_work_root(tmp_path) / "source-audio-16k-mono.wav").exists()
    assert not (_work_root(tmp_path) / "source-audio-manifest.json").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_normalizer_requires_ffprobe_pcm_s16le_codec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_media(tmp_path)
    monkeypatch.setattr(source_audio, "_probe_audio_codec", lambda *_args, **_kwargs: "pcm_f32le")

    with pytest.raises(SourceAudioError, match="^source-audio-invalid$"):
        normalize_source_audio(tmp_path, WORK_ID)

    assert not (_work_root(tmp_path) / "source-audio-16k-mono.wav").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_normalizer_rechecks_upstream_immediately_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepared_media(tmp_path)
    probe = source_audio._probe_pcm_wav

    def probe_then_mutate(path: Path, *, ffprobe: str):
        result = probe(path, ffprobe=ffprobe)
        (_work_root(tmp_path) / "source-media" / "original.wav").write_bytes(
            b"changed-during-verification"
        )
        return result

    monkeypatch.setattr(source_audio, "_probe_pcm_wav", probe_then_mutate)

    with pytest.raises(SourceAudioError, match="^source-audio-input-invalid$"):
        normalize_source_audio(tmp_path, WORK_ID)

    assert not (_work_root(tmp_path) / "source-audio-16k-mono.wav").exists()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_normalizer_never_overwrites_existing_output(tmp_path: Path) -> None:
    _prepared_media(tmp_path)
    output = _work_root(tmp_path) / "source-audio-16k-mono.wav"
    output.write_bytes(b"foreign")

    with pytest.raises(SourceAudioError, match="^source-audio-exists$"):
        normalize_source_audio(tmp_path, WORK_ID)

    assert output.read_bytes() == b"foreign"
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_normalize_source_audio_cli_reports_only_safe_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepared_media(tmp_path)
    script_path = (
        Path(__file__).parents[1]
        / "automation"
        / "scripts"
        / "normalize_source_audio.py"
    )
    spec = importlib.util.spec_from_file_location("normalize_source_audio_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main([WORK_ID], root=tmp_path) == 0

    captured = capsys.readouterr()
    assert "stage=audio_ready status=created" in captured.out
    assert "private-source" not in captured.out
    assert captured.err == ""

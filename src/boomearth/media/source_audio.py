"""Canonical private audio derived from one verified source-media snapshot."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import wave
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from boomearth.media.source_snapshot import SourceSnapshotError, probe_source_media
from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import (
    SourceLedgerError,
    StageEvent,
    WashEventLedger,
    event_sha256,
)


_MEDIA_MANIFEST_KEYS = frozenset(
    {"artifact", "metadata", "schema_version", "source_input_sha256", "work_id"}
)
_DIGEST_LENGTH = 64
_DURATION_TOLERANCE_S = 0.05


class SourceAudioError(RuntimeError):
    """A fixed-message canonical-audio failure."""

    def __repr__(self) -> str:
        return "SourceAudioError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class CanonicalAudio:
    sample_rate: int
    channels: int
    bits_per_sample: int
    duration_s: float
    artifact: ArtifactRecord = field(repr=False)

    def __repr__(self) -> str:
        return "CanonicalAudio(<redacted>)"


@dataclass(frozen=True, slots=True)
class _MediaInput:
    path: Path
    manifest_sha256: str
    duration_s: float


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _positive_number(value: object) -> bool:
    return (
        type(value) in {int, float}
        and math.isfinite(float(value))
        and float(value) > 0
    )


def _verified_media_input(root: Path, work_id: str) -> _MediaInput:
    try:
        order = load_work_order(root, work_id)
        ledger = WashEventLedger(root)
        current = ledger.current(work_id)
        if current.stage != "media_ready":
            raise ValueError
        manifest_path = verify_private_relative(
            order.private_root, "source-media-manifest.json"
        )
        manifest_sha256 = sha256_file(manifest_path)
        if manifest_sha256 != current.artifact_sha256:
            raise ValueError
        value = load_exact_json(manifest_path, _MEDIA_MANIFEST_KEYS)
        artifact = value["artifact"]
        metadata = value["metadata"]
        if not (
            value["schema_version"] == 1
            and value["work_id"] == work_id
            and value["source_input_sha256"] == order.source_input_sha256
            and isinstance(artifact, dict)
            and set(artifact) == {"relative_path", "sha256", "size_bytes"}
            and isinstance(artifact["relative_path"], str)
            and artifact["relative_path"].startswith("source-media/original.")
            and _digest(artifact["sha256"])
            and type(artifact["size_bytes"]) is int
            and artifact["size_bytes"] > 0
            and isinstance(metadata, dict)
            and set(metadata)
            == {"audio_streams", "container", "duration_s", "video_streams"}
            and type(metadata["audio_streams"]) is int
            and metadata["audio_streams"] >= 1
            and isinstance(metadata["container"], str)
            and _positive_number(metadata["duration_s"])
            and type(metadata["video_streams"]) is int
            and metadata["video_streams"] >= 0
        ):
            raise ValueError
        media = verify_private_relative(order.private_root, artifact["relative_path"])
        if (
            sha256_file(media) != artifact["sha256"]
            or media.stat().st_size != artifact["size_bytes"]
        ):
            raise ValueError
        return _MediaInput(media, manifest_sha256, float(metadata["duration_s"]))
    except (SourceContractError, SourceLedgerError, OSError, TypeError, ValueError):
        raise SourceAudioError("source-audio-input-invalid") from None


def _probe_audio_codec(path: Path, *, ffprobe: str) -> str:
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        value = json.loads(result.stdout)
        streams = value["streams"]
        if (
            result.returncode != 0
            or not isinstance(streams, list)
            or len(streams) != 1
            or not isinstance(streams[0], dict)
            or not isinstance(streams[0].get("codec_name"), str)
        ):
            raise ValueError
        return streams[0]["codec_name"]
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise SourceAudioError("source-audio-invalid") from None


def _probe_pcm_wav(path: Path, *, ffprobe: str) -> tuple[int, int, int, float]:
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            sample_width = stream.getsampwidth()
            sample_rate = stream.getframerate()
            frame_count = stream.getnframes()
            compression = stream.getcomptype()
        if (
            channels != 1
            or sample_width != 2
            or sample_rate != 16_000
            or frame_count <= 0
            or compression != "NONE"
            or _probe_audio_codec(path, ffprobe=ffprobe) != "pcm_s16le"
        ):
            raise ValueError
        metadata = probe_source_media(path, ffprobe=ffprobe)
        duration_s = frame_count / sample_rate
        if (
            metadata.container != "wav"
            or metadata.audio_streams != 1
            or metadata.video_streams != 0
            or abs(metadata.duration_s - duration_s) > _DURATION_TOLERANCE_S
        ):
            raise ValueError
        return sample_rate, channels, sample_width * 8, duration_s
    except (OSError, EOFError, ValueError, wave.Error, SourceSnapshotError):
        raise SourceAudioError("source-audio-invalid") from None


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_source_audio(
    root: Path,
    work_id: str,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> CanonicalAudio:
    """Create and verify one immutable 16 kHz mono PCM WAV."""

    source = _verified_media_input(Path(root), work_id)
    try:
        order = load_work_order(root, work_id)
        output = verify_private_relative(
            order.private_root, "source-audio-16k-mono.wav"
        )
        manifest_path = verify_private_relative(
            order.private_root, "source-audio-manifest.json"
        )
    except SourceContractError:
        raise SourceAudioError("source-audio-input-invalid") from None
    if (
        output.exists()
        or output.is_symlink()
        or manifest_path.exists()
        or manifest_path.is_symlink()
    ):
        raise SourceAudioError("source-audio-exists")

    temporary: Path | None = None
    output_published = False
    manifest_published = False
    stage_published = False
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".source-audio.", suffix=".wav", dir=order.private_root
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source.path),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(temporary),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise SourceAudioError("source-audio-conversion-failed")
        if _verified_media_input(Path(root), work_id) != source:
            raise SourceAudioError("source-audio-input-invalid")
        sample_rate, channels, bits_per_sample, duration_s = _probe_pcm_wav(
            temporary, ffprobe=ffprobe
        )
        if abs(duration_s - source.duration_s) > _DURATION_TOLERANCE_S:
            raise SourceAudioError("source-audio-invalid")
        if _verified_media_input(Path(root), work_id) != source:
            raise SourceAudioError("source-audio-input-invalid")
        digest = sha256_file(temporary)
        size_bytes = temporary.stat().st_size
        with temporary.open("r+b") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError:
            raise SourceAudioError("source-audio-exists") from None
        temporary.unlink()
        temporary = None
        output_published = True
        record = ArtifactRecord(
            relative_path="source-audio-16k-mono.wav",
            sha256=digest,
            size_bytes=size_bytes,
            path=output,
        )
        manifest = {
            "artifact": {
                "relative_path": record.relative_path,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            },
            "format": {
                "bits_per_sample": bits_per_sample,
                "channels": channels,
                "duration_s": duration_s,
                "sample_rate": sample_rate,
            },
            "schema_version": 1,
            "upstream_sha256": source.manifest_sha256,
            "work_id": work_id,
        }
        manifest_sha256 = publish_json_exclusive(
            order.private_root, manifest_path, manifest
        )
        manifest_published = True
        if (
            output.stat().st_size != record.size_bytes
            or sha256_file(output) != record.sha256
            or sha256_file(manifest_path) != manifest_sha256
        ):
            raise SourceAudioError("source-audio-invalid")
        ledger = WashEventLedger(root)
        previous = ledger.current(work_id)
        ledger.append(
            StageEvent(
                schema_version=2,
                work_id=work_id,
                source_id=previous.source_id,
                source_kind=previous.source_kind,
                stage="audio_ready",
                result="ok",
                artifact_label="source-audio-manifest",
                artifact_sha256=manifest_sha256,
                timestamp=_timestamp(),
                previous_event_sha256=event_sha256(previous),
            )
        )
        stage_published = True
        return CanonicalAudio(
            sample_rate, channels, bits_per_sample, duration_s, record
        )
    except SourceAudioError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise SourceAudioError("source-audio-conversion-failed") from None
    except (SourceContractError, SourceLedgerError):
        raise SourceAudioError("source-audio-invalid") from None
    finally:
        if not stage_published:
            if manifest_published:
                try:
                    manifest_path.unlink()
                except OSError:
                    pass
            if output_published:
                try:
                    output.unlink()
                except OSError:
                    pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = ["CanonicalAudio", "SourceAudioError", "normalize_source_audio"]

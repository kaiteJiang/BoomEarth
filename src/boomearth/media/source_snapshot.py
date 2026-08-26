"""Immutable, hash-bound snapshots of authorized local source media."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    SourceWorkOrder,
    publish_json_exclusive,
    sha256_file,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_source_locator, load_work_order
from boomearth.workbench.source_ledger import (
    SourceLedgerError,
    StageEvent,
    WashEventLedger,
    event_sha256,
)


_HASH_CHUNK_SIZE = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_CONTAINER_ALIASES = {
    "wav": "wav",
    "wave": "wav",
    "mp3": "mp3",
    "mov": "mp4",
    "mp4": "mp4",
    "m4a": "mp4",
    "3gp": "mp4",
    "3g2": "mp4",
    "mj2": "mp4",
    "matroska": "webm",
    "webm": "webm",
    "avi": "avi",
    "ogg": "ogg",
    "flac": "flac",
}
_EXTENSION_CONTAINERS = {
    ".wav": frozenset({"wav"}),
    ".mp3": frozenset({"mp3"}),
    ".mp4": frozenset({"mp4"}),
    ".m4a": frozenset({"mp4"}),
    ".mov": frozenset({"mp4"}),
    ".mkv": frozenset({"webm"}),
    ".webm": frozenset({"webm"}),
    ".avi": frozenset({"avi"}),
    ".ogg": frozenset({"ogg"}),
    ".flac": frozenset({"flac"}),
}


class SourceSnapshotError(RuntimeError):
    """A fixed-message local snapshot failure."""

    def __repr__(self) -> str:
        return "SourceSnapshotError(<redacted>)"


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    container: str
    duration_s: float
    audio_streams: int
    video_streams: int


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int


def _is_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        int(getattr(value, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _identity(value: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=int(value.st_dev),
        inode=int(value.st_ino),
        size=int(value.st_size),
        modified_ns=int(value.st_mtime_ns),
    )


def _source_identity_after_copy(handle) -> _FileIdentity:
    return _identity(os.fstat(handle.fileno()))


def _ordinary_source(path: Path) -> tuple[Path, os.stat_result]:
    try:
        source = Path(os.path.abspath(os.fspath(path)))
        value = source.lstat()
        if not stat.S_ISREG(value.st_mode) or _is_reparse(value) or value.st_size <= 0:
            raise SourceSnapshotError("source-unavailable")
        return source, value
    except SourceSnapshotError:
        raise
    except (OSError, TypeError, ValueError):
        raise SourceSnapshotError("source-unavailable") from None


def _normalized_container(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    for name in value.split(","):
        normalized = _CONTAINER_ALIASES.get(name.strip().lower())
        if normalized is not None:
            return normalized
    return None


def probe_source_media(path: Path, *, ffprobe: str = "ffprobe") -> MediaMetadata:
    """Read only accepted container, duration, and stream-count metadata."""

    source, _ = _ordinary_source(path)
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type",
                "-of",
                "json",
                str(source),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise SourceSnapshotError("source-media-invalid") from None
    if result.returncode != 0:
        raise SourceSnapshotError("source-media-invalid")
    try:
        value = json.loads(result.stdout)
        format_value = value["format"]
        streams = value["streams"]
        container = _normalized_container(format_value["format_name"])
        duration = float(format_value["duration"])
        if not isinstance(streams, list):
            raise ValueError
        audio_streams = sum(
            1 for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        )
        video_streams = sum(
            1 for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SourceSnapshotError("source-media-invalid") from None
    if (
        container is None
        or not math.isfinite(duration)
        or duration <= 0
        or audio_streams < 1
    ):
        raise SourceSnapshotError("source-media-invalid")
    return MediaMetadata(container, duration, audio_streams, video_streams)


def _container_matches_extension(path: Path, metadata: MediaMetadata) -> bool:
    accepted = _EXTENSION_CONTAINERS.get(path.suffix.lower())
    return accepted is not None and metadata.container in accepted


def _utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def snapshot_local_source(
    root: Path,
    work_id: str,
    *,
    copy_chunk_size: int = _HASH_CHUNK_SIZE,
) -> ArtifactRecord:
    """Copy one authorized local input into its immutable private work root."""

    if type(copy_chunk_size) is not int or copy_chunk_size <= 0:
        raise SourceSnapshotError("source-media-invalid")
    try:
        order = load_work_order(root, work_id)
    except SourceContractError:
        raise SourceSnapshotError("source-unavailable") from None
    if order.source_kind != "local":
        raise SourceSnapshotError("source-kind-invalid")
    try:
        source = load_source_locator(root, work_id)
    except SourceContractError:
        raise SourceSnapshotError("source-unavailable") from None
    source, path_stat = _ordinary_source(source)
    if source.suffix.lower() not in _EXTENSION_CONTAINERS:
        raise SourceSnapshotError("source-media-invalid")
    return _publish_media_snapshot(
        Path(root),
        order,
        source,
        path_stat,
        expected_media_sha256=order.source_input_sha256,
        copy_chunk_size=copy_chunk_size,
    )


def snapshot_acquired_source(
    root: Path,
    work_id: str,
    acquired: Path,
    *,
    copy_chunk_size: int = _HASH_CHUNK_SIZE,
) -> ArtifactRecord:
    """Publish one privately acquired URL result through the common media contract."""

    if type(copy_chunk_size) is not int or copy_chunk_size <= 0:
        raise SourceSnapshotError("source-media-invalid")
    try:
        order = load_work_order(root, work_id)
        if order.source_kind != "url":
            raise SourceSnapshotError("source-kind-invalid")
        acquisition_dir = verify_private_relative(order.private_root, "source-acquisition")
        source, path_stat = _ordinary_source(acquired)
        if source.parent != acquisition_dir:
            raise SourceSnapshotError("source-media-invalid")
    except SourceSnapshotError:
        raise
    except SourceContractError:
        raise SourceSnapshotError("source-media-invalid") from None
    if source.suffix.lower() not in _EXTENSION_CONTAINERS:
        raise SourceSnapshotError("source-media-invalid")
    return _publish_media_snapshot(
        Path(root),
        order,
        source,
        path_stat,
        expected_media_sha256=None,
        copy_chunk_size=copy_chunk_size,
    )


def _publish_media_snapshot(
    root: Path,
    order: SourceWorkOrder,
    source: Path,
    path_stat: os.stat_result,
    *,
    expected_media_sha256: str | None,
    copy_chunk_size: int,
) -> ArtifactRecord:

    try:
        media_dir = verify_private_relative(order.private_root, "source-media")
        manifest_path = verify_private_relative(
            order.private_root, "source-media-manifest.json"
        )
    except SourceContractError:
        raise SourceSnapshotError("source-media-invalid") from None
    formal = media_dir / f"original{source.suffix.lower()}"
    if formal.exists() or manifest_path.exists():
        raise SourceSnapshotError("source-media-exists")

    temporary: Path | None = None
    formal_published = False
    manifest_published = False
    stage_published = False
    try:
        media_dir.mkdir(parents=False, exist_ok=True)
        verify_private_relative(order.private_root, "source-media")
        if formal.exists() or formal.is_symlink():
            raise SourceSnapshotError("source-media-exists")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".source-copy.", suffix=source.suffix.lower(), dir=media_dir
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output:
            opened = _identity(os.fstat(input_stream.fileno()))
            if opened != _identity(path_stat):
                raise SourceSnapshotError("source-changed")
            for block in iter(lambda: input_stream.read(copy_chunk_size), b""):
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
            after = _source_identity_after_copy(input_stream)
        try:
            current = _identity(source.lstat())
        except OSError:
            raise SourceSnapshotError("source-changed") from None
        if after != opened or current != opened:
            raise SourceSnapshotError("source-changed")
        copied_sha256 = digest.hexdigest()
        if (
            expected_media_sha256 is not None
            and copied_sha256 != expected_media_sha256
        ):
            raise SourceSnapshotError("source-changed")
        metadata = probe_source_media(temporary)
        if not _container_matches_extension(source, metadata):
            raise SourceSnapshotError("source-media-invalid")
        try:
            os.link(temporary, formal)
        except FileExistsError:
            raise SourceSnapshotError("source-media-exists") from None
        temporary.unlink()
        temporary = None
        formal_published = True
        size_bytes = formal.stat().st_size
        record = ArtifactRecord(
            relative_path=f"source-media/{formal.name}",
            sha256=copied_sha256,
            size_bytes=size_bytes,
            path=formal,
        )
        manifest = {
            "artifact": {
                "relative_path": record.relative_path,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            },
            "metadata": {
                "audio_streams": metadata.audio_streams,
                "container": metadata.container,
                "duration_s": metadata.duration_s,
                "video_streams": metadata.video_streams,
            },
            "schema_version": 1,
            "source_input_sha256": order.source_input_sha256,
            "work_id": order.work_id,
        }
        manifest_sha256 = publish_json_exclusive(
            order.private_root, manifest_path, manifest
        )
        manifest_published = True
        if (
            formal.stat().st_size != record.size_bytes
            or sha256_file(formal) != record.sha256
            or sha256_file(manifest_path) != manifest_sha256
        ):
            raise SourceSnapshotError("source-media-invalid")
        ledger = WashEventLedger(root)
        previous = ledger.current(order.work_id)
        ledger.append(
            StageEvent(
                schema_version=2,
                work_id=order.work_id,
                source_id=order.source_input_sha256,
                source_kind=order.source_kind,
                stage="media_ready",
                result="ok",
                artifact_label="source-media-manifest",
                artifact_sha256=manifest_sha256,
                timestamp=_utc_timestamp(),
                previous_event_sha256=event_sha256(previous),
            )
        )
        stage_published = True
        return record
    except SourceSnapshotError:
        if not stage_published:
            if manifest_published:
                try:
                    manifest_path.unlink()
                except OSError:
                    pass
            if formal_published:
                try:
                    formal.unlink()
                except OSError:
                    pass
        raise
    except (OSError, SourceContractError, SourceLedgerError):
        if not stage_published:
            if manifest_published:
                try:
                    manifest_path.unlink()
                except OSError:
                    pass
            if formal_published:
                try:
                    formal.unlink()
                except OSError:
                    pass
        raise SourceSnapshotError("source-media-invalid") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


__all__ = [
    "MediaMetadata",
    "SourceSnapshotError",
    "probe_source_media",
    "snapshot_acquired_source",
    "snapshot_local_source",
]

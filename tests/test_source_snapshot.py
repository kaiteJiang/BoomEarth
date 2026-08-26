from __future__ import annotations

import json
import os
import subprocess
import wave
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.media import source_snapshot
from boomearth.media.source_snapshot import (
    SourceSnapshotError,
    probe_source_media,
    snapshot_local_source,
)
from boomearth.workbench.source_artifacts import sha256_file
from boomearth.workbench.source_intake import create_local_intake
from boomearth.workbench.source_ledger import WashEventLedger


WORK_ID = "00000000-0000-4000-8000-000000000001"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 13, 4, 5, 6, tzinfo=timezone.utc)


def _write_wav(path: Path, *, duration_s: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * round(16_000 * duration_s))


def _prepare(tmp_path: Path, *, filename: str = "private-title.wav") -> Path:
    source = tmp_path / "authorized" / filename
    _write_wav(source)
    create_local_intake(
        tmp_path,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    return source


def _work_root(tmp_path: Path) -> Path:
    return (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def test_probe_source_media_reads_real_audio_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_wav(source, duration_s=1.25)

    metadata = probe_source_media(source)

    assert metadata.container == "wav"
    assert metadata.duration_s == pytest.approx(1.25, abs=0.01)
    assert metadata.audio_streams == 1
    assert metadata.video_streams == 0


def test_snapshot_copies_exact_bytes_and_publishes_hash_bound_manifest(
    tmp_path: Path,
) -> None:
    source = _prepare(tmp_path)

    result = snapshot_local_source(tmp_path, WORK_ID)

    formal = _work_root(tmp_path) / "source-media" / "original.wav"
    manifest_path = _work_root(tmp_path) / "source-media-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert formal.read_bytes() == source.read_bytes()
    assert result.path == formal
    assert result.relative_path == "source-media/original.wav"
    assert result.sha256 == sha256_file(source) == sha256_file(formal)
    assert result.size_bytes == source.stat().st_size
    assert manifest == {
        "artifact": {
            "relative_path": "source-media/original.wav",
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
        },
        "metadata": {
            "audio_streams": 1,
            "container": "wav",
            "duration_s": 1.0,
            "video_streams": 0,
        },
        "schema_version": 1,
        "source_input_sha256": sha256_file(source),
        "work_id": WORK_ID,
    }
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"
    assert "private-title" not in repr(result)
    assert "private-title" not in manifest_path.read_text("utf-8")


def test_snapshot_rejects_source_changed_during_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(tmp_path)
    original = source_snapshot._source_identity_after_copy

    def changed(handle):
        identity = original(handle)
        return type(identity)(
            device=identity.device,
            inode=identity.inode,
            size=identity.size + 1,
            modified_ns=identity.modified_ns,
        )

    monkeypatch.setattr(source_snapshot, "_source_identity_after_copy", changed)

    with pytest.raises(SourceSnapshotError, match="^source-changed$"):
        snapshot_local_source(tmp_path, WORK_ID)

    assert not (_work_root(tmp_path) / "source-media-manifest.json").exists()
    assert not list((_work_root(tmp_path) / "source-media").glob("original.*"))
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"


def test_snapshot_rejects_extension_container_mismatch(tmp_path: Path) -> None:
    _prepare(tmp_path, filename="disguised.mp3")

    with pytest.raises(SourceSnapshotError, match="^source-media-invalid$"):
        snapshot_local_source(tmp_path, WORK_ID)

    assert not (_work_root(tmp_path) / "source-media-manifest.json").exists()


def test_probe_rejects_media_without_audio_and_discards_hostile_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "video.mp4"
    source.write_bytes(b"synthetic")
    private_error = "private-source-title"

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"format_name": "mov,mp4,m4a", "duration": "1.0"},
                    "streams": [{"codec_type": "video"}],
                }
            ),
            stderr=private_error,
        )

    monkeypatch.setattr(source_snapshot.subprocess, "run", fake_run)

    with pytest.raises(SourceSnapshotError) as raised:
        probe_source_media(source)

    assert str(raised.value) == "source-media-invalid"
    assert private_error not in repr(raised.value)


def test_probe_maps_ffprobe_failure_to_fixed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"synthetic")

    monkeypatch.setattr(
        source_snapshot.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="private transcript"
        ),
    )

    with pytest.raises(SourceSnapshotError) as raised:
        probe_source_media(source)
    assert str(raised.value) == "source-media-invalid"
    assert "private transcript" not in repr(raised.value)


def test_snapshot_never_overwrites_existing_media(tmp_path: Path) -> None:
    _prepare(tmp_path)
    media_dir = _work_root(tmp_path) / "source-media"
    media_dir.mkdir()
    existing = media_dir / "original.wav"
    existing.write_bytes(b"foreign")

    with pytest.raises(SourceSnapshotError, match="^source-media-exists$"):
        snapshot_local_source(tmp_path, WORK_ID)

    assert existing.read_bytes() == b"foreign"
    assert not (_work_root(tmp_path) / "source-media-manifest.json").exists()


def test_snapshot_revalidates_published_media_before_advancing_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prepare(tmp_path)
    publish = source_snapshot.publish_json_exclusive

    def publish_then_mutate(root: Path, target: Path, value: object) -> str:
        digest = publish(root, target, value)
        (_work_root(tmp_path) / "source-media" / "original.wav").write_bytes(
            b"changed-after-publication"
        )
        return digest

    monkeypatch.setattr(
        source_snapshot, "publish_json_exclusive", publish_then_mutate
    )

    with pytest.raises(SourceSnapshotError, match="^source-media-invalid$"):
        snapshot_local_source(tmp_path, WORK_ID)

    assert not (_work_root(tmp_path) / "source-media-manifest.json").exists()
    assert not list((_work_root(tmp_path) / "source-media").glob("original.*"))
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"


def test_snapshot_rejects_missing_original_after_intake(tmp_path: Path) -> None:
    source = _prepare(tmp_path)
    source.unlink()

    with pytest.raises(SourceSnapshotError, match="^source-unavailable$"):
        snapshot_local_source(tmp_path, WORK_ID)


def test_snapshot_rejects_url_work_order(tmp_path: Path) -> None:
    from boomearth.workbench.source_intake import create_url_intake

    url_file = tmp_path / "url.txt"
    url_file.write_text("https://example.invalid/item\n", encoding="utf-8")
    create_url_intake(
        tmp_path,
        url_file,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )

    with pytest.raises(SourceSnapshotError, match="^source-kind-invalid$"):
        snapshot_local_source(tmp_path, WORK_ID)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_snapshot_rejects_source_media_junction(tmp_path: Path) -> None:
    _prepare(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    media_dir = _work_root(tmp_path) / "source-media"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(media_dir), str(outside)],
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(SourceSnapshotError, match="^source-media-invalid$"):
            snapshot_local_source(tmp_path, WORK_ID)
        assert not list(outside.iterdir())
    finally:
        if media_dir.exists():
            os.rmdir(media_dir)

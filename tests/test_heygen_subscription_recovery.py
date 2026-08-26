from __future__ import annotations

from io import StringIO
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import boomearth.video.heygen_recovery as recovery
from boomearth.video.heygen_recovery import (
    HeyGenRecoveryError,
    HeyGenRecoveryState,
    PrivateVideoDownloadError,
    download_private_video,
    next_heygen_action,
)


MASTER_SHA256 = hashlib.sha256(b"verified-master").hexdigest()
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation" / "scripts" / "download_private_video.py"


def _load_download_cli():
    spec = importlib.util.spec_from_file_location(
        "download_private_video_cli_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_remote_job_requires_retrieval_approval_before_download(
    tmp_path: Path,
) -> None:
    state = HeyGenRecoveryState(
        generation_approved=True,
        job_created=True,
        job_completed=True,
        retrieval_approved=False,
    )

    assert next_heygen_action(state, tmp_path / "master.mp4") == (
        "request-retrieval-approval"
    )
    assert next_heygen_action(
        HeyGenRecoveryState(
            generation_approved=True,
            job_created=True,
            job_completed=True,
            retrieval_approved=True,
        ),
        tmp_path / "master.mp4",
    ) == "download-completed-job"


def test_incomplete_job_requires_separate_status_read_approval(
    tmp_path: Path,
) -> None:
    state = HeyGenRecoveryState(
        generation_approved=True,
        job_created=True,
        job_completed=False,
        retrieval_approved=False,
        status_read_approved=False,
    )

    assert next_heygen_action(state, tmp_path / "master.mp4") == (
        "request-status-read-approval"
    )
    assert next_heygen_action(
        HeyGenRecoveryState(
            generation_approved=True,
            job_created=True,
            job_completed=False,
            retrieval_approved=False,
            status_read_approved=True,
        ),
        tmp_path / "master.mp4",
    ) == "read-status"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            HeyGenRecoveryState(False, False, False, False),
            "request-generation-approval",
        ),
        (HeyGenRecoveryState(True, False, False, False), "generate-once"),
        (
            HeyGenRecoveryState(True, True, False, False),
            "request-status-read-approval",
        ),
    ],
)
def test_recovery_actions_follow_the_remote_job_evidence(
    tmp_path: Path,
    state: HeyGenRecoveryState,
    expected: str,
) -> None:
    assert next_heygen_action(state, tmp_path / "master.mp4") == expected


def test_verified_local_master_composes_without_provider_work(tmp_path: Path) -> None:
    master = tmp_path / "master.mp4"
    master.write_bytes(b"verified-master")
    state = HeyGenRecoveryState(
        generation_approved=False,
        job_created=False,
        job_completed=False,
        retrieval_approved=False,
        expected_master_sha256=MASTER_SHA256,
    )

    assert next_heygen_action(state, master) == "compose-locally"


@pytest.mark.parametrize(
    "state",
    [
        HeyGenRecoveryState(False, True, False, False),
        HeyGenRecoveryState(True, False, True, False),
        HeyGenRecoveryState(True, True, False, True),
    ],
)
def test_contradictory_recovery_evidence_is_redacted(
    tmp_path: Path,
    state: HeyGenRecoveryState,
) -> None:
    with pytest.raises(HeyGenRecoveryError, match="^recovery-evidence-invalid$"):
        next_heygen_action(state, tmp_path / "master.mp4")


def test_matching_master_does_not_hide_contradictory_job_evidence(
    tmp_path: Path,
) -> None:
    master = tmp_path / "master.mp4"
    master.write_bytes(b"verified-master")
    state = HeyGenRecoveryState(
        generation_approved=False,
        job_created=True,
        job_completed=False,
        retrieval_approved=False,
        expected_master_sha256=MASTER_SHA256,
    )

    with pytest.raises(HeyGenRecoveryError, match="^recovery-evidence-invalid$"):
        next_heygen_action(state, master)


def test_download_streams_once_and_writes_redacted_receipt(tmp_path: Path) -> None:
    body = b"synthetic-private-video"
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "master.receipt.json"
    opened: list[str] = []

    def opener(url: str) -> StringIO:
        opened.append(url)
        return StringIO(body.decode("ascii"))

    def probe(path: Path) -> dict[str, object]:
        assert path.read_bytes() == body
        return {
            "container": "mp4",
            "duration_seconds": 1.25,
            "codec_type": "video",
            "codec": "h264",
            "width": 1920,
            "height": 1080,
        }

    result = download_private_video(
        StringIO("https://provider.example.invalid/video?signature=private\n"),
        output,
        receipt,
        opener=opener,
        probe=probe,
    )

    assert result == output
    assert output.read_bytes() == body
    assert opened == ["https://provider.example.invalid/video?signature=private"]
    document = json.loads(receipt.read_text(encoding="utf-8"))
    assert document == {
        "status": "downloaded",
        "download_count": 1,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "media": {
            "container": "mp4",
            "duration_seconds": 1.25,
            "codec_type": "video",
            "codec": "h264",
            "width": 1920,
            "height": 1080,
        },
    }
    assert "provider.example.invalid" not in receipt.read_text(encoding="utf-8")
    assert "signature" not in receipt.read_text(encoding="utf-8")
    if os.name == "nt":
        assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "url_text",
    [
        "http://provider.example.invalid/video?signature=private\n",
        "https://provider.example.invalid/one?signature=private\nhttps://provider.example.invalid/two?signature=private\n",
    ],
)
def test_download_rejects_nonexclusive_or_non_https_url_without_opening(
    tmp_path: Path,
    url_text: str,
) -> None:
    calls = 0

    def opener(_url: str) -> StringIO:
        nonlocal calls
        calls += 1
        return StringIO("unexpected")

    with pytest.raises(PrivateVideoDownloadError, match="^download-url-rejected$"):
        download_private_video(
            StringIO(url_text),
            tmp_path / "master.mp4",
            tmp_path / "receipt.json",
            opener=opener,
            probe=lambda _path: {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
        )

    assert calls == 0


def test_download_failure_removes_partial_output_without_retry(tmp_path: Path) -> None:
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    calls = 0

    class FailingStream:
        def read(self, _size: int) -> bytes:
            raise OSError("private detail")

        def close(self) -> None:
            pass

    def opener(_url: str) -> FailingStream:
        nonlocal calls
        calls += 1
        return FailingStream()

    with pytest.raises(PrivateVideoDownloadError, match="^download-stream-failed$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=opener,
            probe=lambda _path: {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
        )

    assert calls == 1
    assert not output.exists()
    assert not receipt.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_download_refuses_existing_artifacts_before_opening(tmp_path: Path) -> None:
    output = tmp_path / "master.mp4"
    output.write_bytes(b"keep")
    calls = 0

    def opener(_url: str) -> StringIO:
        nonlocal calls
        calls += 1
        return StringIO("unexpected")

    with pytest.raises(PrivateVideoDownloadError, match="^download-target-exists$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            tmp_path / "receipt.json",
            opener=opener,
            probe=lambda _path: {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
        )

    assert output.read_bytes() == b"keep"
    assert calls == 0


def test_download_preserves_verified_master_when_receipt_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    original_link = recovery.os.link
    link_calls = 0

    def fail_receipt_publish(source: Path, destination: Path) -> None:
        nonlocal link_calls
        link_calls += 1
        if link_calls == 2:
            raise OSError("private filesystem detail")
        original_link(source, destination)

    monkeypatch.setattr(recovery.os, "link", fail_receipt_publish)

    with pytest.raises(PrivateVideoDownloadError, match="^download-publish-failed$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO("video"),
            probe=lambda _path: {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
        )

    assert output.read_bytes() == b"video"
    assert not receipt.exists()


def test_download_rejects_canonical_aliases_for_output_and_receipt(
    tmp_path: Path,
) -> None:
    output = tmp_path / "subdirectory" / ".." / "master.mp4"
    receipt = tmp_path / "master.mp4"

    with pytest.raises(PrivateVideoDownloadError, match="^download-target-rejected$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO("unexpected"),
            probe=lambda _path: {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
        )

    assert not receipt.exists()


def test_download_rejects_pure_audio_mp4_without_publication(tmp_path: Path) -> None:
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"

    with pytest.raises(PrivateVideoDownloadError, match="^download-probe-failed$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO("audio-only-mp4"),
            probe=lambda _path: {
                "container": "mp4",
                "codec_type": "audio",
                "codec": "aac",
                "width": 0,
                "height": 0,
            },
        )

    assert not output.exists()
    assert not receipt.exists()


def test_download_rejects_stream_over_deterministic_byte_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    probed = False

    def probe(_path: Path) -> dict[str, object]:
        nonlocal probed
        probed = True
        return {"codec_type": "video", "width": 1, "height": 1}

    monkeypatch.setattr(recovery, "MAX_PRIVATE_VIDEO_BYTES", 4, raising=False)
    with pytest.raises(PrivateVideoDownloadError, match="^download-size-exceeded$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO("12345"),
            probe=probe,
        )

    assert probed is False
    assert not output.exists()
    assert not receipt.exists()


def test_output_publish_is_atomic_no_replace_against_a_contender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    original_link = recovery.os.link

    def contender_wins(source: Path, destination: Path) -> None:
        if Path(destination) == output:
            output.write_bytes(b"contender")
        original_link(source, destination)

    monkeypatch.setattr(recovery.os, "link", contender_wins)
    with pytest.raises(PrivateVideoDownloadError, match="^download-target-exists$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO("owned-video"),
            probe=lambda _path: {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
        )

    assert output.read_bytes() == b"contender"
    assert not receipt.exists()


def test_failed_receipt_publish_never_unlinks_a_replaced_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    original_link = recovery.os.link
    link_calls = 0

    def replace_before_receipt_failure(source: Path, destination: Path) -> None:
        nonlocal link_calls
        link_calls += 1
        if link_calls == 2:
            output.unlink()
            output.write_bytes(b"contender")
            raise OSError("private filesystem detail")
        original_link(source, destination)

    monkeypatch.setattr(recovery.os, "link", replace_before_receipt_failure)
    with pytest.raises(PrivateVideoDownloadError, match="^download-publish-failed$"):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO("owned-video"),
            probe=lambda _path: {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
            },
        )

    assert output.read_bytes() == b"contender"
    assert not receipt.exists()


def test_source_replacement_at_link_entry_cannot_publish_mismatched_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"verified-original-video"
    replacement = b"attacker-replacement-video"
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    original_link = recovery.os.link
    replacement_succeeded = False
    replacement_blocked = False

    def replace_source_at_link_entry(source: Path, destination: Path) -> None:
        nonlocal replacement_succeeded, replacement_blocked
        if Path(destination) == output:
            try:
                Path(source).unlink()
                Path(source).write_bytes(replacement)
                replacement_succeeded = True
            except PermissionError:
                replacement_blocked = True
        original_link(source, destination)

    monkeypatch.setattr(recovery.os, "link", replace_source_at_link_entry)
    arguments = (
        StringIO("https://provider.example.invalid/video?signature=private\n"),
        output,
        receipt,
    )
    keywords = {
        "opener": lambda _url: StringIO(original.decode("ascii")),
        "probe": lambda _path: {
            "codec_type": "video",
            "codec": "h264",
            "width": 1920,
            "height": 1080,
        },
    }

    if os.name == "nt":
        assert download_private_video(*arguments, **keywords) == output
        assert replacement_blocked is True
        assert replacement_succeeded is False
        assert output.read_bytes() == original
        document = json.loads(receipt.read_text("utf-8"))
        assert document["sha256"] == hashlib.sha256(original).hexdigest()
        assert document["size_bytes"] == len(original)
    else:
        with pytest.raises(
            PrivateVideoDownloadError, match="^download-publish-failed$"
        ):
            download_private_video(*arguments, **keywords)
        assert replacement_succeeded is True
        assert output.read_bytes() == replacement
        assert not receipt.exists()


def test_receipt_source_replacement_at_link_entry_cannot_claim_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"verified-original-video"
    replacement = b"attacker-receipt"
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    original_link = recovery.os.link
    replaced_source: Path | None = None
    replacement_succeeded = False
    replacement_blocked = False

    def replace_receipt_source_at_link_entry(
        source: Path, destination: Path
    ) -> None:
        nonlocal replaced_source, replacement_succeeded, replacement_blocked
        if Path(destination) == receipt:
            replaced_source = Path(source)
            try:
                replaced_source.unlink()
                replaced_source.write_bytes(replacement)
                replacement_succeeded = True
            except PermissionError:
                replacement_blocked = True
        original_link(source, destination)

    monkeypatch.setattr(recovery.os, "link", replace_receipt_source_at_link_entry)
    arguments = (
        StringIO("https://provider.example.invalid/video?signature=private\n"),
        output,
        receipt,
    )
    keywords = {
        "opener": lambda _url: StringIO(original.decode("ascii")),
        "probe": lambda _path: {
            "codec_type": "video",
            "codec": "h264",
            "width": 1920,
            "height": 1080,
        },
    }

    if os.name == "nt":
        assert download_private_video(*arguments, **keywords) == output
        assert replacement_blocked is True
        assert replacement_succeeded is False
        document = json.loads(receipt.read_text("utf-8"))
        assert document == {
            "status": "downloaded",
            "download_count": 1,
            "sha256": hashlib.sha256(original).hexdigest(),
            "size_bytes": len(original),
            "media": {
                "codec_type": "video",
                "codec": "h264",
                "width": 1920,
                "height": 1080,
            },
        }
    else:
        with pytest.raises(
            PrivateVideoDownloadError, match="^download-publish-failed$"
        ):
            download_private_video(*arguments, **keywords)
        assert replacement_succeeded is True
        assert replaced_source is not None
        assert replaced_source.read_bytes() == replacement
        assert receipt.read_bytes() == replacement


@pytest.mark.skipif(os.name != "nt", reason="Windows handle cleanup contract")
def test_cleanup_never_path_unlinks_replacement_after_owned_handle_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"verified-original-video"
    replacement = b"unowned-cleanup-replacement"
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    original_lock_snapshot = recovery._lock_verified_snapshot
    original_close_lock = recovery._close_source_lock
    original_file_identity = recovery._file_identity
    source_path: Path | None = None
    source_handle: int | None = None
    source_identity: tuple[int, int] | None = None
    replacement_written = False
    stale_identity_returned = False

    def capture_source_snapshot(
        path: Path,
        identity: tuple[int, int],
        sha256: str,
        *,
        delete_access: bool = False,
    ) -> object:
        nonlocal source_path, source_handle, source_identity
        snapshot = original_lock_snapshot(
            path, identity, sha256, delete_access=delete_access
        )
        if source_path is None:
            source_path = snapshot.source
        elif snapshot.source == source_path:
            source_handle = snapshot.handle
            source_identity = snapshot.identity
        return snapshot

    def replace_after_source_lock_closes(handle: int | None) -> bool:
        nonlocal replacement_written
        closed = original_close_lock(handle)
        if handle == source_handle:
            assert source_path is not None
            if source_path.exists():
                source_path.unlink()
            source_path.write_bytes(replacement)
            replacement_written = True
        return closed

    def stale_identity_at_old_cleanup_boundary(
        path: Path,
    ) -> tuple[int, int] | None:
        nonlocal stale_identity_returned
        if (
            replacement_written
            and path == source_path
            and not stale_identity_returned
        ):
            stale_identity_returned = True
            return source_identity
        return original_file_identity(path)

    monkeypatch.setattr(recovery, "_lock_verified_snapshot", capture_source_snapshot)
    monkeypatch.setattr(recovery, "_close_source_lock", replace_after_source_lock_closes)
    monkeypatch.setattr(recovery, "_file_identity", stale_identity_at_old_cleanup_boundary)

    assert (
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO(original.decode("ascii")),
            probe=lambda _path: {
                "codec_type": "video",
                "codec": "h264",
                "width": 1920,
                "height": 1080,
            },
        )
        == output
    )

    assert replacement_written is True
    assert source_path is not None
    assert source_path.read_bytes() == replacement
    assert output.read_bytes() == original
    document = json.loads(receipt.read_text("utf-8"))
    assert document["sha256"] == hashlib.sha256(original).hexdigest()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle cleanup contract")
def test_cleanup_disposition_failure_preserves_temps_and_fails_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = b"verified-original-video"
    output = tmp_path / "master.mp4"
    receipt = tmp_path / "receipt.json"
    original_close_lock = recovery._close_source_lock
    retained_sources: list[Path] = []

    def fail_handle_disposition(
        locked_path: recovery._LockedPath | recovery._LockedSnapshot,
    ) -> bool:
        retained_sources.append(locked_path.source)
        assert original_close_lock(locked_path.handle) is True
        return False

    monkeypatch.setattr(recovery, "_dispose_locked_path", fail_handle_disposition)

    with pytest.raises(
        PrivateVideoDownloadError, match="^download-publish-failed$"
    ):
        download_private_video(
            StringIO("https://provider.example.invalid/video?signature=private\n"),
            output,
            receipt,
            opener=lambda _url: StringIO(original.decode("ascii")),
            probe=lambda _path: {
                "codec_type": "video",
                "codec": "h264",
                "width": 1920,
                "height": 1080,
            },
        )

    assert len(retained_sources) == 2
    assert all(path.exists() for path in retained_sources)
    assert original in {path.read_bytes() for path in retained_sources}
    assert output.read_bytes() == original
    document = json.loads(receipt.read_text("utf-8"))
    assert document["sha256"] == hashlib.sha256(original).hexdigest()


def test_cli_probe_requires_a_positive_dimension_video_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _load_download_cli()
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
                    "streams": [
                        {
                            "codec_type": "audio",
                            "codec_name": "aac",
                            "width": 0,
                            "height": 0,
                        }
                    ],
                }
            ),
        ),
    )

    with pytest.raises(PrivateVideoDownloadError, match="^download-probe-failed$"):
        cli._probe(Path("audio-only.mp4"))


def test_cli_argument_failure_is_a_fixed_redacted_category() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--unexpected", "private-input"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == "status=failed category=arguments-invalid\n"
    assert result.stderr == ""

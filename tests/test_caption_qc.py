"""Offline final-audio caption alignment and QC contract tests."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from boomearth.captions.align import Caption
import pytest

import boomearth.captions.qc as caption_qc
from boomearth.captions.qc import (
    CaptionArtifactError,
    CaptionQCError,
    evaluate_caption_qc,
    generate_captions_from_locked_final_audio,
    write_caption_artifacts,
)
from boomearth.providers.volcengine_asr import ASRTranscription, ASRWord, extract_word_timestamps


FIXTURES = Path(__file__).resolve().parent / "fixtures"
_ARTIFACT_FILENAMES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)


def _transcription(name: str) -> ASRTranscription:
    raw_response = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return ASRTranscription(
        raw_response=raw_response,
        words=extract_word_timestamps(raw_response),
        resource_id="volc.seedasr.auc",
        request_id="synthetic-request-id-caption-fixture",
    )


def _make_directory_reparse_link(link: Path, target: Path) -> None:
    """Create a Windows reparse directory; prefer a symlink and fall back to a junction."""

    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError as error:
        if getattr(error, "winerror", None) != 1314:
            raise

    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        pytest.skip("Windows symlink creation was denied and junctions are unavailable")


def _write_valid_original_artifacts(media_dir: Path) -> dict[str, bytes]:
    original_bytes = {
        "asr-result.json": b'{"old":"asr"}\n',
        "captions_words.json": b'[{"old":"words"}]\n',
        "captions.json": b'[{"old":"captions"}]\n',
        "captions.srt": b"old srt\n",
        "captions.vtt": b"WEBVTT\n\nold vtt\n",
        "caption-qc.json": (
            b'{"status":"pass","timing_source":"volcengine-word-timestamps"}\n'
        ),
    }
    for name, contents in original_bytes.items():
        (media_dir / name).write_bytes(contents)
    return original_bytes


def _assert_complete_original_delivery_set(media_dir: Path, original_bytes: dict[str, bytes]) -> None:
    assert {
        name: (media_dir / name).read_bytes() for name in _ARTIFACT_FILENAMES
    } == original_bytes
    qc_marker = json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))
    assert qc_marker == {
        "status": "pass",
        "timing_source": "volcengine-word-timestamps",
    }


def test_word_timestamp_fixture_writes_six_caption_artifacts_with_display_script(
    tmp_path: Path,
) -> None:
    """Would fail if captions used interpolated characters or rewrote display wording."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")

    artifacts = write_caption_artifacts(
        media_dir=media_dir,
        source_media=final_wav,
        display_script="这是 Claude Max 的测试，但是要保留英文空格。",
        transcription=_transcription("volcengine_words_ok.json"),
    )

    assert {path.name for path in artifacts.paths} == {
        "asr-result.json",
        "captions_words.json",
        "captions.json",
        "captions.srt",
        "captions.vtt",
        "caption-qc.json",
    }
    assert {path.parent for path in artifacts.paths} == {media_dir}

    word_units = json.loads((media_dir / "captions_words.json").read_text(encoding="utf-8"))
    assert word_units == [
        {"text": "这是", "start": 0.0, "end": 0.6, "isGap": False},
        {"text": "Claude Max", "start": 0.65, "end": 1.4, "isGap": False},
        {"text": "的测试", "start": 1.45, "end": 2.2, "isGap": False},
        {"text": "但是", "start": 2.25, "end": 2.9, "isGap": False},
        {"text": "要保留英文空格", "start": 2.95, "end": 4.5, "isGap": False},
    ]

    captions = json.loads((media_dir / "captions.json").read_text(encoding="utf-8"))
    assert captions == [
        {
            "start": 0.0,
            "end": 2.2,
            "text": "这是 Claude Max 的测试",
            "source": "volcengine-word-timestamps",
        },
        {
            "start": 2.25,
            "end": 4.5,
            "text": "但是要保留英文空格",
            "source": "volcengine-word-timestamps",
        },
    ]
    qc = json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))
    assert qc["status"] == "pass"
    assert qc["timing_source"] == "volcengine-word-timestamps"
    assert qc["alignment_coverage"] == 1.0
    assert qc["word_units"] == 5
    assert qc["source_media"] == "final.wav"
    assert "00:00:00,000 --> 00:00:02,200" in (media_dir / "captions.srt").read_text(
        encoding="utf-8"
    )
    assert "WEBVTT\n\n00:00:00.000 --> 00:00:02.200" in (media_dir / "captions.vtt").read_text(
        encoding="utf-8"
    )


def test_caption_transaction_binds_qc_to_exact_final_wav_bytes(tmp_path: Path) -> None:
    """Would fail if QC were written without the source-media hash or bound to stale bytes."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"first-final-wav")

    write_caption_artifacts(
        media_dir=media_dir,
        source_media=final_wav,
        display_script="这是 Claude Max 的测试，但是要保留英文空格。",
        transcription=_transcription("volcengine_words_ok.json"),
    )
    first_qc = json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))

    final_wav.write_bytes(b"second-final-wav")
    write_caption_artifacts(
        media_dir=media_dir,
        source_media=final_wav,
        display_script="这是 Claude Max 的测试，但是要保留英文空格。",
        transcription=_transcription("volcengine_words_ok.json"),
    )
    second_qc = json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))

    assert first_qc["narration_sha256"] == hashlib.sha256(b"first-final-wav").hexdigest()
    assert second_qc["narration_sha256"] == hashlib.sha256(b"second-final-wav").hexdigest()
    assert first_qc["narration_sha256"] != second_qc["narration_sha256"]


@pytest.mark.parametrize("replacement", [False, True])
def test_caption_transaction_rejects_source_change_before_qc_marker_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bool
) -> None:
    """Would fail if a changed final WAV could publish a QC marker for stale captured bytes."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"locked-final-wav")
    originals = _write_valid_original_artifacts(media_dir)
    original_publish = caption_qc._publish_staged_artifact
    changed = False

    def mutate_before_qc_marker(staged: Path, target: Path) -> None:
        nonlocal changed
        original_publish(staged, target)
        if target.name != "captions.vtt" or changed:
            return
        changed = True
        if replacement:
            replacement_path = media_dir / "replacement.wav"
            replacement_path.write_bytes(b"replacement-final-wav")
            final_wav.unlink()
            replacement_path.replace(final_wav)
        else:
            final_wav.write_bytes(b"changed-final-wav")

    monkeypatch.setattr(caption_qc, "_publish_staged_artifact", mutate_before_qc_marker)

    with pytest.raises(CaptionArtifactError):
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    assert changed is True
    _assert_complete_original_delivery_set(media_dir, originals)


def test_caption_qc_fails_coverage_overlap_short_fragments_connectors_and_speed() -> None:
    """Would fail if a delivery-blocking caption defect were reduced to a warning."""

    qc = evaluate_caption_qc(
        captions=(
            Caption(start=0.0, end=0.4, text="短"),
            Caption(start=0.3, end=1.3, text="但是"),
            Caption(start=1.3, end=2.3, text="甲乙丙丁戊己庚辛壬癸子丑寅"),
        ),
        alignment_coverage=0.89,
        script_characters=20,
        matched_characters=17,
        asr_characters=20,
        word_units=3,
        source_media="final.wav",
        resource_id="volc.seedasr.auc",
    )

    assert qc["status"] == "fail"
    assert qc["overlap_count"] == 1
    assert qc["short_fragments"] == [{"caption": 1, "text": "短", "duration": 0.4}]
    assert qc["split_connectors"] == [{"caption": 2, "phrase": "但是"}]
    assert qc["max_reading_units_per_second"] == 13.0
    assert any("coverage" in error for error in qc["errors"])
    assert any("shorter than 0.5s" in error for error in qc["errors"])
    assert any("overlaps" in error for error in qc["errors"])
    assert any("isolated" in error for error in qc["errors"])
    assert any("connectors" in error for error in qc["errors"])
    assert any("exceeds 12.0" in error for error in qc["errors"])


def test_caption_qc_warns_but_passes_above_preferred_reading_speed() -> None:
    """Would fail if the 9 units/s review threshold blocked delivery like 12 units/s."""

    qc = evaluate_caption_qc(
        captions=(Caption(start=0.0, end=1.0, text="甲乙丙丁戊己庚辛壬癸"),),
        alignment_coverage=1.0,
        script_characters=10,
        matched_characters=10,
        asr_characters=10,
        word_units=1,
        source_media="final.wav",
        resource_id="volc.seedasr.auc",
    )

    assert qc["status"] == "pass"
    assert qc["max_reading_units_per_second"] == 10.0
    assert qc["warnings"] == [
        "maximum reading speed 10.00 units/s exceeds preferred 9.0"
    ]
    assert qc["errors"] == []


def test_low_alignment_coverage_writes_failed_qc_then_blocks_delivery(
    tmp_path: Path,
) -> None:
    """Would fail if a display script with no ASR match were delivered with invented timing."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")

    with pytest.raises(CaptionQCError) as raised:
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="锘钅锗锛锝",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    assert str(raised.value) == "caption QC failed"
    assert json.loads((media_dir / "captions.json").read_text(encoding="utf-8")) == []
    qc = json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))
    assert qc["status"] == "fail"
    assert qc["alignment_coverage"] == 0.0
    assert any("coverage" in error for error in qc["errors"])


def test_partial_substantive_alignment_never_emits_trailing_unmatched_text(
    tmp_path: Path,
) -> None:
    """Would fail if a 90%-plus match placed an unmatched display character on a neighbour token."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")

    with pytest.raises(CaptionQCError):
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Max 的测试，但是要保留英文空格X。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    captions = json.loads((media_dir / "captions.json").read_text(encoding="utf-8"))
    qc = json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))
    assert captions == []
    assert 0.90 <= qc["alignment_coverage"] < 1.0
    assert any("unmatched substantive" in error for error in qc["errors"])


def test_unmatched_english_product_token_blocks_caption_delivery(tmp_path: Path) -> None:
    """Would fail if a product-token mismatch inherited the timing of an adjacent ASR token."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")

    with pytest.raises(CaptionQCError):
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Pro 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    captions = json.loads((media_dir / "captions.json").read_text(encoding="utf-8"))
    qc = json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))
    assert captions == []
    assert any("unmatched substantive" in error for error in qc["errors"])


def test_reparse_media_directory_and_source_are_rejected_before_artifact_writes(
    tmp_path: Path,
) -> None:
    """Would fail if a junction or symlink redirected the active media or final source."""

    actual_media = tmp_path / "actual-media"
    actual_media.mkdir()
    (actual_media / "final.wav").write_bytes(b"synthetic-locked-final-wav")
    reparse_media = tmp_path / "reparse-media"
    _make_directory_reparse_link(reparse_media, actual_media)

    with pytest.raises(CaptionArtifactError) as media_error:
        write_caption_artifacts(
            media_dir=reparse_media,
            source_media=reparse_media / "final.wav",
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )
    assert str(reparse_media) not in str(media_error.value)
    assert not (actual_media / "captions.json").exists()

    media_dir = tmp_path / "normal-media"
    media_dir.mkdir()
    external_media = tmp_path / "external-media"
    external_media.mkdir()
    (external_media / "final.wav").write_bytes(b"synthetic-locked-final-wav")
    reparse_source_dir = media_dir / "redirected-source"
    _make_directory_reparse_link(reparse_source_dir, external_media)

    with pytest.raises(CaptionArtifactError) as source_error:
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=reparse_source_dir / "final.wav",
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )
    assert str(reparse_source_dir) not in str(source_error.value)
    assert not (media_dir / "captions.json").exists()


def test_reparse_existing_artifact_target_is_rejected_before_any_write(tmp_path: Path) -> None:
    """Would fail if an artifact name could redirect a JSON write outside the active media directory."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected_target = media_dir / "captions.json"
    _make_directory_reparse_link(redirected_target, outside)

    with pytest.raises(CaptionArtifactError) as raised:
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    assert str(redirected_target) not in str(raised.value)
    assert list(outside.iterdir()) == []


def test_mid_publication_failure_restores_original_artifacts_only_after_qc_marker_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a consumer could see a QC commit marker during a mixed publication."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    original_bytes = {
        name: f"old artifact {name}\n".encode("utf-8") for name in _ARTIFACT_FILENAMES
    }
    for name, contents in original_bytes.items():
        (media_dir / name).write_bytes(contents)

    original_publish = caption_qc._publish_staged_artifact
    def fail_after_qc_marker_is_removed(staged: Path, target: Path) -> None:
        if target.name != "caption-qc.json":
            assert not (media_dir / "caption-qc.json").exists()
        if staged.name == "captions.json" and target.name == "captions.json":
            raise OSError("injected publication failure")
        original_publish(staged, target)

    monkeypatch.setattr(caption_qc, "_publish_staged_artifact", fail_after_qc_marker_is_removed)

    with pytest.raises(CaptionArtifactError):
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    assert {
        name: (media_dir / name).read_bytes() for name in _ARTIFACT_FILENAMES
    } == original_bytes
    assert not [
        path for path in media_dir.iterdir() if path.name.startswith(".caption-")
    ]


def test_restore_failure_keeps_recovery_workspace_and_removes_qc_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a failed rollback removed recovery data while a consumer could accept captions."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    for name in _ARTIFACT_FILENAMES:
        (media_dir / name).write_bytes(f"old artifact {name}\n".encode("utf-8"))

    real_replace = caption_qc.os.replace

    def fail_publish_then_restore(source: object, target: object) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if (
            source_path.parent.name.startswith(".caption-")
            and source_path.name == "captions.json"
            and target_path.name == "captions.json"
        ):
            raise OSError("injected-publication-secret")
        if source_path.name.startswith("backup-") and target_path.name == "asr-result.json":
            raise OSError("injected-restore-secret")
        real_replace(source, target)

    monkeypatch.setattr(caption_qc.os, "replace", fail_publish_then_restore)

    with pytest.raises(CaptionArtifactError) as raised:
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    rendered = str(raised.value)
    assert "manual recovery required" in rendered
    assert "injected-publication-secret" not in rendered
    assert "injected-restore-secret" not in rendered
    assert str(final_wav) not in rendered
    assert not (media_dir / "caption-qc.json").exists()
    workspaces = [path for path in media_dir.iterdir() if path.name.startswith(".caption-")]
    assert len(workspaces) == 1
    workspace = workspaces[0]
    assert any(path.name.startswith("backup-") for path in workspace.iterdir())
    assert (workspace / "captions.json").is_file()


def test_source_media_race_exception_is_redacted_and_has_no_os_cause(tmp_path: Path) -> None:
    """Would fail if source-media filesystem races chained a path-bearing OSError publicly."""

    media_dir = tmp_path / "private-sentinel" / "media"
    media_dir.mkdir(parents=True)
    missing = media_dir / "missing.wav"

    with pytest.raises(CaptionArtifactError) as error:
        caption_qc._capture_source_media_snapshot(missing)

    rendered = repr(error.value)
    assert error.value.__cause__ is None
    assert str(media_dir) not in rendered
    assert "missing.wav" not in rendered


def test_caption_filesystem_race_has_no_path_bearing_chained_cause(tmp_path: Path) -> None:
    """Would fail if another public caption filesystem gate retained an OSError cause."""

    missing = tmp_path / "private-sentinel" / "missing.json"

    with pytest.raises(CaptionArtifactError) as error:
        caption_qc._require_normal_file(missing, unavailable_message="caption input is unavailable")

    assert error.value.__cause__ is None
    assert str(missing) not in repr(error.value)


def test_non_qc_backup_move_failure_before_replace_preserves_complete_original_delivery_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a pre-move error marked a still-live original artifact as absent."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    original_bytes = _write_valid_original_artifacts(media_dir)
    real_replace = caption_qc.os.replace

    def fail_before_words_backup_move(source: object, target: object) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if (
            source_path.name == "captions_words.json"
            and target_path.name.startswith("backup-")
        ):
            raise OSError("injected-before-move-secret")
        real_replace(source, target)

    monkeypatch.setattr(caption_qc.os, "replace", fail_before_words_backup_move)

    with pytest.raises(CaptionArtifactError) as raised:
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    assert "injected-before-move-secret" not in str(raised.value)
    _assert_complete_original_delivery_set(media_dir, original_bytes)
    assert not [path for path in media_dir.iterdir() if path.name.startswith(".caption-")]


def test_ambiguous_non_qc_backup_move_uses_actual_backup_identity_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a move that completed then raised was recovered from optimistic flags instead of files."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    original_bytes = _write_valid_original_artifacts(media_dir)
    real_replace = caption_qc.os.replace

    def move_words_then_fail(source: object, target: object) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if (
            source_path.name == "captions_words.json"
            and target_path.name.startswith("backup-")
        ):
            real_replace(source, target)
            raise OSError("injected-after-move-secret")
        real_replace(source, target)

    monkeypatch.setattr(caption_qc.os, "replace", move_words_then_fail)

    with pytest.raises(CaptionArtifactError) as raised:
        write_caption_artifacts(
            media_dir=media_dir,
            source_media=final_wav,
            display_script="这是 Claude Max 的测试，但是要保留英文空格。",
            transcription=_transcription("volcengine_words_ok.json"),
        )

    assert "injected-after-move-secret" not in str(raised.value)
    _assert_complete_original_delivery_set(media_dir, original_bytes)
    assert not [path for path in media_dir.iterdir() if path.name.startswith(".caption-")]


def test_successful_publish_keeps_final_artifacts_when_stage_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if non-committed housekeeping could turn a completed QC commit into an error."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    original_bytes = {
        name: f"old artifact {name}\n".encode("utf-8") for name in _ARTIFACT_FILENAMES
    }
    for name, contents in original_bytes.items():
        (media_dir / name).write_bytes(contents)

    def fail_stage_cleanup(*, active_media_dir: Path, workspace: Path) -> None:
        raise CaptionArtifactError("injected cleanup failure")

    monkeypatch.setattr(caption_qc, "_cleanup_stage_workspace", fail_stage_cleanup)

    artifacts = write_caption_artifacts(
        media_dir=media_dir,
        source_media=final_wav,
        display_script="这是 Claude Max 的测试，但是要保留英文空格。",
        transcription=_transcription("volcengine_words_ok.json"),
    )

    assert artifacts.paths == tuple(media_dir / name for name in _ARTIFACT_FILENAMES)
    assert all(path.is_file() for path in artifacts.paths)
    assert {
        name: (media_dir / name).read_bytes() for name in _ARTIFACT_FILENAMES
    } != original_bytes
    assert json.loads((media_dir / "caption-qc.json").read_text(encoding="utf-8"))["status"] == "pass"


def test_direct_transcription_artifact_recursively_redacts_credentials(
    tmp_path: Path,
) -> None:
    """Would fail if callers constructing ASRTranscription directly could persist credentials."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    transcription = ASRTranscription(
        raw_response={
            "nested": {
                "X_Api_Key": "api-key-value",
                "Authorization": "authorization-value",
                "safe_field": "safe-value",
            },
            "result": {
                "utterances": [
                    {
                        "words": [
                            {
                                "text": "Claude Max",
                                "start_time": 0,
                                "end_time": 1500,
                            }
                        ]
                    }
                ]
            },
        },
        words=(ASRWord(text="Claude Max", start=0.0, end=1.5),),
        resource_id="volc.seedasr.auc",
        request_id="synthetic-request-id",
    )

    write_caption_artifacts(
        media_dir=media_dir,
        source_media=final_wav,
        display_script="Claude Max",
        transcription=transcription,
    )

    raw_artifact = json.loads((media_dir / "asr-result.json").read_text(encoding="utf-8"))
    rendered = json.dumps(raw_artifact, ensure_ascii=False)
    assert raw_artifact["nested"] == {
        "X_Api_Key": "<REDACTED>",
        "Authorization": "<REDACTED>",
        "safe_field": "safe-value",
    }
    assert raw_artifact["result"]["utterances"][0]["words"] == [
        {"text": "Claude Max", "start_time": 0, "end_time": 1500}
    ]
    for secret in ("api-key-value", "authorization-value"):
        assert secret not in rendered


def test_locked_final_audio_entrypoint_sends_only_committed_wav_to_asr(
    tmp_path: Path,
) -> None:
    """Would fail if a caption run could transcribe audio other than Task 6's locked WAV."""

    media_dir = tmp_path / "active-project" / "media"
    media_dir.mkdir(parents=True)
    final_wav = media_dir / "final.wav"
    final_wav.write_bytes(b"synthetic-locked-final-wav")
    manifest = media_dir / "voice_manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    committed_sha256 = "a" * 64
    calls: list[tuple[object, ...]] = []

    class FakeNarrator:
        def probe_committed_wav(self, wav_path: Path, manifest_path: Path) -> object:
            calls.append(("probe", wav_path, manifest_path))
            return SimpleNamespace(sha256=committed_sha256)

    class FakeASRClient:
        def transcribe_file(
            self,
            wav_path: Path,
            *,
            expected_sha256: str,
        ) -> ASRTranscription:
            calls.append(("transcribe", wav_path, expected_sha256))
            return _transcription("volcengine_words_ok.json")

    artifacts = generate_captions_from_locked_final_audio(
        narrator=FakeNarrator(),
        asr_client=FakeASRClient(),
        final_wav=final_wav,
        voice_manifest=manifest,
        display_script="这是 Claude Max 的测试，但是要保留英文空格。",
        media_dir=media_dir,
    )

    assert calls == [
        ("probe", final_wav, manifest),
        ("transcribe", final_wav, committed_sha256),
    ]
    assert (media_dir / "caption-qc.json") in artifacts.paths

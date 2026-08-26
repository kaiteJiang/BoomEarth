from __future__ import annotations

from array import array
import json
import math
from pathlib import Path
import sys
import wave

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "automation" / "scripts"))

import select_voice_reference as selector

from select_voice_reference import (
    WindowScore,
    analyze_pcm_frames,
    build_receipt,
    main,
    rank_windows,
)


SAMPLE_RATE = 24_000


def test_selector_contract_is_black_gold_v3() -> None:
    assert selector.VOICE_ID == "user-indextts2-black-gold-v3"
    assert selector.RECEIPT_SCHEMA == "boomearth.voice-reference-qc/v3"
    assert (
        selector.CANONICAL_REFERENCE_OUTPUT.name
        == "user-indextts2-black-gold-v3.wav"
    )
    assert (
        selector.CANONICAL_REFERENCE_RECEIPT.name
        == "user-indextts2-black-gold-v3.qc.json"
    )


def test_selector_resolves_linked_worktree_to_main_workspace(tmp_path: Path) -> None:
    main = tmp_path / "main"
    worktree = main / ".worktrees" / "voice-migration"
    git_dir = main / ".git" / "worktrees" / "voice-migration"
    (main / "automation" / "config").mkdir(parents=True)
    (main / "automation" / "config" / "tts-routing.json").write_text(
        "{}\n", encoding="utf-8"
    )
    worktree.mkdir(parents=True)
    git_dir.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")

    assert selector._canonical_workspace_root(worktree) == main


def _make_directory_reparse_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as error:
        if getattr(error, "winerror", None) != 1314:
            raise
    result = selector.subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        pytest.skip("reparse point creation is unavailable")


def metrics_for_three_known_regions():
    """Twenty seconds each of silence, clean speech, and clipped audio."""
    samples = array("h")
    samples.extend([0] * (20 * SAMPLE_RATE))
    samples.extend(
        int(8_000 * math.sin(2 * math.pi * 220 * index / SAMPLE_RATE))
        for index in range(20 * SAMPLE_RATE)
    )
    samples.extend([32_700] * (20 * SAMPLE_RATE))
    return analyze_pcm_frames(samples, SAMPLE_RATE)


def test_selector_prefers_continuous_clean_speech_over_silence_and_clipping():
    scores = rank_windows(metrics_for_three_known_regions(), SAMPLE_RATE, 18.0)

    assert scores[0].start_seconds == pytest.approx(20.0, abs=0.5)


def test_selector_excludes_windows_touching_either_recording_edge():
    """Would fail if edge windows merely lost points but remained eligible."""
    samples = array(
        "h",
        (
            int(8_000 * math.sin(2 * math.pi * 220 * index / SAMPLE_RATE))
            for index in range(40 * SAMPLE_RATE)
        ),
    )
    scores = rank_windows(analyze_pcm_frames(samples, SAMPLE_RATE), SAMPLE_RATE, 18.0)

    assert scores
    assert all(score.start_seconds > 0.0 for score in scores)
    assert all(score.start_seconds + score.duration_seconds < 40.0 for score in scores)


def test_cli_refuses_to_overwrite_private_reference(tmp_path):
    output = tmp_path / "user-indextts2-black-gold-v3.wav"
    receipt = tmp_path / "user-indextts2-black-gold-v3.qc.json"
    output.write_bytes(b"preserve")

    assert main(["--source", str(tmp_path / "source.wav"), "--output", str(output), "--receipt", str(receipt)]) == 2
    assert output.read_bytes() == b"preserve"


@pytest.mark.parametrize("seconds", [14.9, 20.1, float("nan")])
def test_window_length_outside_contract_fails(tmp_path, seconds):
    assert main(
        [
            "--source",
            str(tmp_path / "source.wav"),
            "--output",
            str(tmp_path / "user-indextts2-black-gold-v3.wav"),
            "--receipt",
            str(tmp_path / "user-indextts2-black-gold-v3.qc.json"),
            "--window-seconds",
            str(seconds),
        ]
    ) == 2


def test_receipt_has_only_fixed_metadata_schema():
    receipt = build_receipt(
        source_metadata={"name": "source.m4a", "size_bytes": 123},
        selected=WindowScore(
            start_seconds=20.0,
            duration_seconds=18.0,
            score=0.9,
            active_ratio=1.0,
            silence_ratio=0.0,
            clipping_ratio=0.0,
            rms_stability=0.0,
            edge_penalty=0.0,
        ),
        conversion={"codec": "pcm_s16le", "sample_rate": SAMPLE_RATE, "channels": 1},
        output_sha256="a" * 64,
    )

    assert set(receipt) == {
        "schema",
        "status",
        "source_metadata",
        "selected_interval",
        "metrics",
        "conversion",
        "output_sha256",
    }
    assert "transcript" not in repr(receipt)
    assert "source_bytes" not in repr(receipt)


def _write_clean_decoded_wav(path):
    samples = array("h", [8_000]) * (20 * SAMPLE_RATE)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(samples.tobytes())


def _main_paths(tmp_path, monkeypatch):
    source = tmp_path / "authorized" / "source.m4a"
    output = tmp_path / "private" / "user-indextts2-black-gold-v3.wav"
    receipt = tmp_path / "private" / "user-indextts2-black-gold-v3.qc.json"
    source.parent.mkdir()
    output.parent.mkdir()
    source.write_bytes(b"offline fixture")
    monkeypatch.setenv("BOOMEARTH_AUTHORIZED_VOICE_SOURCE", str(source))
    monkeypatch.setattr(selector, "WORKSPACE", tmp_path)
    monkeypatch.setattr(selector, "PRIVATE_VOICE_DIRECTORY", output.parent)
    monkeypatch.setattr(selector, "CANONICAL_REFERENCE_OUTPUT", output)
    monkeypatch.setattr(selector, "CANONICAL_REFERENCE_RECEIPT", receipt)
    monkeypatch.setattr(selector, "_path_is_git_ignored", lambda *_: True)
    monkeypatch.setattr(selector, "_path_is_tracked", lambda *_: False)
    return source, output, receipt


def _configure_successful_local_tools(monkeypatch):
    monkeypatch.setattr(selector, "_resolve_tool", lambda *_: Path("offline-tool"))
    monkeypatch.setattr(selector, "_verify_output", lambda *_: True)

    def decode(_ffmpeg, _source, decoded):
        _write_clean_decoded_wav(decoded)
        return True

    monkeypatch.setattr(selector, "_decode_to_pcm", decode)


def _main_args(source, output, receipt):
    return ["--source", str(source), "--output", str(output), "--receipt", str(receipt)]


def test_main_writes_private_wav_and_receipt_with_offline_tools(tmp_path, monkeypatch, capsys):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)

    assert selector.main(_main_args(source, output, receipt)) == 0
    assert output.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))["status"] == "PASS"
    assert capsys.readouterr().out.splitlines() == [
        "status=PASS",
        "voice_id=user-indextts2-black-gold-v3",
        "selected_duration_seconds=18",
    ]


def test_main_rolls_back_wav_when_post_output_validation_fails(tmp_path, monkeypatch):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)
    monkeypatch.setattr(selector, "_verify_output", lambda *_: False)

    assert selector.main(_main_args(source, output, receipt)) == 3
    assert not output.exists()
    assert not receipt.exists()


def test_main_rolls_back_only_its_wav_when_receipt_collides(tmp_path, monkeypatch):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)
    write_output = selector._write_pcm16_wav_exclusive

    def write_output_then_create_competing_receipt(*args, **kwargs):
        write_output(*args, **kwargs)
        receipt.write_bytes(b"preserve competing receipt")

    monkeypatch.setattr(selector, "_write_pcm16_wav_exclusive", write_output_then_create_competing_receipt)

    assert selector.main(_main_args(source, output, receipt)) == 2
    assert not output.exists()
    assert receipt.read_bytes() == b"preserve competing receipt"


@pytest.mark.parametrize("failure", [OSError("disk failure"), RuntimeError("internal failure")])
def test_main_classifies_output_write_failures_as_internal_or_filesystem(tmp_path, monkeypatch, failure):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)

    def fail_output_write(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(selector, "_write_pcm16_wav_exclusive", fail_output_write)

    assert selector.main(_main_args(source, output, receipt)) == 3
    assert not output.exists()
    assert not receipt.exists()


def test_main_rolls_back_created_artifacts_when_receipt_write_fails(tmp_path, monkeypatch):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)

    def fail_receipt_write(*_args, **_kwargs):
        raise OSError("disk failure")

    monkeypatch.setattr(selector.json, "dump", fail_receipt_write)

    assert selector.main(_main_args(source, output, receipt)) == 3
    assert not output.exists()
    assert not receipt.exists()


def test_main_classifies_malformed_ffmpeg_output_as_tool_failure(tmp_path, monkeypatch):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(selector, "_resolve_tool", lambda *_: Path("offline-tool"))

    def decode(_ffmpeg, _source, decoded):
        decoded.write_bytes(b"not a WAV")
        return True

    monkeypatch.setattr(selector, "_decode_to_pcm", decode)

    assert selector.main(_main_args(source, output, receipt)) == 3
    assert not output.exists()
    assert not receipt.exists()


def test_main_classifies_decode_rejection_as_input_failure(tmp_path, monkeypatch):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)
    monkeypatch.setattr(selector, "_decode_to_pcm", lambda *_: False)

    assert selector.main(_main_args(source, output, receipt)) == 2
    assert not output.exists()
    assert not receipt.exists()


def test_main_classifies_missing_output_parent_as_input_failure(tmp_path, monkeypatch):
    source, _, receipt = _main_paths(tmp_path, monkeypatch)
    output = tmp_path / "missing-output-parent" / "user-indextts2-black-gold-v3.wav"

    assert selector.main(_main_args(source, output, receipt)) == 2
    assert not output.exists()
    assert not receipt.exists()


def test_main_classifies_missing_receipt_parent_as_input_failure(tmp_path, monkeypatch):
    source, output, _ = _main_paths(tmp_path, monkeypatch)
    receipt = tmp_path / "missing-receipt-parent" / "user-indextts2-black-gold-v3.qc.json"

    assert selector.main(_main_args(source, output, receipt)) == 2
    assert not output.exists()
    assert not receipt.exists()


def test_main_removes_wav_when_wav_registration_fails(tmp_path, monkeypatch):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)

    def fail_registration(_descriptor):
        raise OSError("fstat failure")

    monkeypatch.setattr(selector.os, "fstat", fail_registration)

    assert selector.main(_main_args(source, output, receipt)) == 3
    assert not output.exists()
    assert not receipt.exists()


def test_main_removes_receipt_when_receipt_registration_fails(tmp_path, monkeypatch):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    _configure_successful_local_tools(monkeypatch)
    original_fstat = selector.os.fstat
    calls = 0

    def fail_second_registration(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("fstat failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(selector.os, "fstat", fail_second_registration)

    assert selector.main(_main_args(source, output, receipt)) == 3
    assert not output.exists()
    assert not receipt.exists()


def test_main_rejects_source_that_does_not_match_authorized_environment(
    tmp_path, monkeypatch, capsys
):
    source, output, receipt = _main_paths(tmp_path, monkeypatch)
    other_source = tmp_path / "authorized" / "other.m4a"
    other_source.write_bytes(b"offline fixture")

    assert selector.main(_main_args(other_source, output, receipt)) == 2
    assert capsys.readouterr().out == "status=FAIL\n"
    assert not output.exists()
    assert not receipt.exists()


def test_main_rejects_output_outside_canonical_private_directory(
    tmp_path, monkeypatch, capsys
):
    source, _output, receipt = _main_paths(tmp_path, monkeypatch)
    outside_output = tmp_path / "outside.wav"

    assert selector.main(_main_args(source, outside_output, receipt)) == 2
    assert capsys.readouterr().out == "status=FAIL\n"
    assert not outside_output.exists()


def test_main_rejects_authorized_reparse_source_before_tools(tmp_path, monkeypatch, capsys):
    """Would fail if a junction could redirect an otherwise-authorized source."""
    _source, output, receipt = _main_paths(tmp_path, monkeypatch)
    external = tmp_path / "external"
    external.mkdir()
    (external / "source.m4a").write_bytes(b"offline fixture")
    redirected = tmp_path / "redirected"
    _make_directory_reparse_link(redirected, external)
    source = redirected / "source.m4a"
    monkeypatch.setenv("BOOMEARTH_AUTHORIZED_VOICE_SOURCE", str(source))
    monkeypatch.setattr(
        selector,
        "_resolve_tool",
        lambda *_: pytest.fail("tools must not run for an unsafe source"),
    )

    assert selector.main(_main_args(source, output, receipt)) == 2
    assert capsys.readouterr().out == "status=FAIL\n"
    assert not output.exists()
    assert not receipt.exists()

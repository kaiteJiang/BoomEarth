from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "automation"
    / "scripts"
    / "activate_voice_reference.py"
)
SPEC = importlib.util.spec_from_file_location("voice_activation_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    voice = tmp_path / ".internal" / "voice"
    voice.mkdir(parents=True)
    reference = voice / "user-indextts2-black-gold-v3.wav"
    reference.write_bytes(b"RIFF" + b"black-gold-v3" * 32)
    reference_hash = _sha256(reference)
    receipt = reference.with_suffix(".qc.json")
    receipt.write_text(
        json.dumps(
            {
                "schema": "boomearth.voice-reference-qc/v3",
                "status": "PASS",
                "source_metadata": {"name": "private.m4a", "size_bytes": 1234},
                "selected_interval": {
                    "start_seconds": 1.5,
                    "duration_seconds": 18.0,
                },
                "metrics": {
                    "score": 0.9,
                    "active_ratio": 0.95,
                    "silence_ratio": 0.01,
                    "clipping_ratio": 0.0,
                    "rms_stability": 1.0,
                    "edge_penalty": 0.0,
                },
                "conversion": {
                    "codec": "pcm_s16le",
                    "sample_rate": 24000,
                    "channels": 1,
                    "attenuation_db": 0.0,
                },
                "output_sha256": reference_hash,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ledger = voice / "indextts2-provenance-ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "issued_output_sha256": ["a" * 64],
                "canonical_reference_provenance": [
                    {
                        "voice_id": "user-indextts2-calm-v1",
                        "reference_audio_path": str(voice / "user-indextts2-calm-v1.wav"),
                        "reference_audio_sha256": "1" * 64,
                    },
                    {
                        "voice_id": "user-indextts2-calm-v2",
                        "reference_audio_path": str(voice / "user-indextts2-calm-v2.wav"),
                        "reference_audio_sha256": "2" * 64,
                    },
                ],
                "issued_manifest_sha256_by_output_sha256": {
                    "a" * 64: "b" * 64
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    route = tmp_path / "tts-routing.json"
    route.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "indextts2-local",
                "model": "IndexTTS2",
                "voice_id": "user-indextts2-calm-v2",
                "reference_audio_path": str(voice / "user-indextts2-calm-v2.wav"),
                "reference_audio_sha256": "2" * 64,
                "provenance_ledger_path": str(ledger),
                "interpreter_path": "D:/index/.venv/Scripts/python.exe",
                "cli_script_path": "D:/index/indextts/cli_v2.py",
                "model_dir": "D:/index/checkpoints",
                "playback_speed": 1.12,
                "fp16": True,
                "deepspeed": False,
                "cuda_kernel": False,
                "accel": False,
                "torch_compile": False,
                "used_fallback": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return route, receipt, ledger, reference


def _build(workspace: tuple[Path, Path, Path, Path]):
    route, receipt, ledger, _reference = workspace
    return activation.build_activation_documents(
        route_path=route,
        receipt_path=receipt,
        ledger_path=ledger,
    )


def test_builder_binds_exact_v3_receipt_hash_without_mutating_files(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    route, _receipt, ledger, reference = workspace
    before_route = route.read_bytes()
    before_ledger = ledger.read_bytes()

    documents = _build(workspace)

    assert documents.route["voice_id"] == "user-indextts2-black-gold-v3"
    assert documents.route["reference_audio_sha256"] == _sha256(reference)
    assert documents.route["reference_audio_path"] == str(reference.resolve())
    assert documents.reference_sha256 == _sha256(reference)
    assert route.read_bytes() == before_route
    assert ledger.read_bytes() == before_ledger


def test_builder_preserves_runtime_flags_and_all_historical_provenance(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    documents = _build(workspace)

    assert documents.route["playback_speed"] == 1.12
    assert documents.route["fp16"] is True
    assert documents.route["used_fallback"] is False
    entries = documents.ledger["canonical_reference_provenance"]
    assert [entry["voice_id"] for entry in entries] == [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
        "user-indextts2-black-gold-v3",
    ]
    assert "retired" not in documents.ledger


def test_ledger_validator_accepts_the_exact_retirement_marker(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    _route, _receipt, ledger, _reference = workspace
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    payload["retired_voice_ids"] = [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
    ]

    entries = activation._validate_ledger(payload)

    assert [entry["voice_id"] for entry in entries] == [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
    ]


def test_builder_rejects_mismatched_receipt_hash(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    _route, receipt, _ledger, _reference = workspace
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["output_sha256"] = "0" * 64
    receipt.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(activation.VoiceActivationError, match="voice-activation-invalid"):
        _build(workspace)


@pytest.mark.parametrize("document_index", [0, 1, 2])
def test_builder_rejects_duplicate_json_keys(
    workspace: tuple[Path, Path, Path, Path], document_index: int
) -> None:
    route, receipt, ledger, _reference = workspace
    path = (route, receipt, ledger)[document_index]
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(activation.VoiceActivationError, match="voice-activation-invalid"):
        _build(workspace)


def test_builder_rejects_wrong_audio_contract(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    _route, receipt, _ledger, _reference = workspace
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["conversion"]["sample_rate"] = 16000
    receipt.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(activation.VoiceActivationError, match="voice-activation-invalid"):
        _build(workspace)


def test_builder_only_switches_the_current_v2_route(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    route, _receipt, _ledger, _reference = workspace
    value = json.loads(route.read_text(encoding="utf-8"))
    value["voice_id"] = "user-indextts2-calm-v1"
    route.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(activation.VoiceActivationError, match="voice-activation-invalid"):
        _build(workspace)


def test_publication_changes_both_documents_together(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    route, _receipt, ledger, _reference = workspace
    result = activation.publish_activation(_build(workspace))

    assert result.voice_id == "user-indextts2-black-gold-v3"
    assert json.loads(route.read_text(encoding="utf-8"))["voice_id"] == result.voice_id
    entries = json.loads(ledger.read_text(encoding="utf-8"))[
        "canonical_reference_provenance"
    ]
    assert entries[-1]["voice_id"] == result.voice_id


def test_publication_rolls_back_both_documents_on_second_replace_failure(
    workspace: tuple[Path, Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route, _receipt, ledger, _reference = workspace
    before_route = route.read_bytes()
    before_ledger = ledger.read_bytes()
    native_replace = activation.os.replace
    calls = 0

    def fail_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second replace failure")
        native_replace(source, destination)

    monkeypatch.setattr(activation.os, "replace", fail_second)
    with pytest.raises(
        activation.VoiceActivationError,
        match="voice-activation-publish-failed",
    ):
        activation.publish_activation(_build(workspace))

    assert route.read_bytes() == before_route
    assert ledger.read_bytes() == before_ledger


def test_publication_rejects_changed_input_before_first_replace(
    workspace: tuple[Path, Path, Path, Path],
) -> None:
    route, _receipt, ledger, _reference = workspace
    documents = _build(workspace)
    original_ledger = ledger.read_bytes()
    route.write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        activation.VoiceActivationError,
        match="voice-activation-input-changed",
    ):
        activation.publish_activation(documents)

    assert ledger.read_bytes() == original_ledger


def test_cli_prints_only_redacted_activation_status(
    workspace: tuple[Path, Path, Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    route, receipt, ledger, reference = workspace

    assert activation.main(
        [
            "--route",
            str(route),
            "--receipt",
            str(receipt),
            "--ledger",
            str(ledger),
        ]
    ) == 0

    output = capsys.readouterr().out.splitlines()
    assert output == [
        "status=activated",
        "voice_id=user-indextts2-black-gold-v3",
    ]
    assert str(reference) not in "\n".join(output)

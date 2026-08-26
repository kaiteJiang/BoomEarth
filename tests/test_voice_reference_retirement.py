from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from boomearth.audio.indextts2 import TTSRouting


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation" / "scripts" / "retire_voice_references.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("voice_retirement_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _current_route() -> TTSRouting:
    return TTSRouting.load(ROOT / "automation" / "config" / "tts-routing.json")


def _ledger(path: Path) -> bytes:
    payload = {
        "schema_version": 1,
        "issued_output_sha256": [],
        "canonical_reference_provenance": [
            {
                "voice_id": "user-indextts2-calm-v1",
                "reference_audio_path": "private-v1.wav",
                "reference_audio_sha256": "1" * 64,
            },
            {
                "voice_id": "user-indextts2-calm-v2",
                "reference_audio_path": "private-v2.wav",
                "reference_audio_sha256": "2" * 64,
            },
            {
                "voice_id": "user-indextts2-black-gold-v3",
                "reference_audio_path": "private-v3.wav",
                "reference_audio_sha256": "3" * 64,
            },
        ],
    }
    rendered = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(rendered)
    return rendered


def test_retirement_allowlist_contains_only_old_reference_and_receipt_names() -> None:
    retirement = _load_module()

    assert retirement.retirement_candidates() == frozenset(
        {
            "user-indextts2-calm-v1.wav",
            "user-indextts2-calm-v1.qc.json",
            "user-indextts2-calm-v2.wav",
            "user-indextts2-calm-v2.qc.json",
        }
    )


def test_retirement_refuses_until_active_route_is_v3(tmp_path: Path) -> None:
    retirement = _load_module()
    v2_route = replace(_current_route(), voice_id="user-indextts2-calm-v2")

    with pytest.raises(retirement.VoiceRetirementError, match="voice-retirement-blocked"):
        retirement.retire_voice_references(
            voice_dir=tmp_path,
            active_route=v2_route,
            acceptance_manifest=tmp_path / "voice_manifest.json",
            ledger_path=tmp_path / "ledger.json",
            recycle=lambda _path: None,
        )


def test_dry_run_never_mutates_ledger_or_calls_recycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retirement = _load_module()
    monkeypatch.setattr(retirement, "LOCKED_PRIVATE_VOICE_DIRECTORY", tmp_path)
    monkeypatch.setattr(retirement, "_validate_retirement_inputs", lambda **_kwargs: None)
    ledger_path = tmp_path / "ledger.json"
    before = _ledger(ledger_path)
    (tmp_path / "user-indextts2-calm-v2.wav").write_bytes(b"old")
    calls: list[Path] = []

    result = retirement.retire_voice_references(
        voice_dir=tmp_path,
        active_route=_current_route(),
        acceptance_manifest=tmp_path / "voice_manifest.json",
        ledger_path=ledger_path,
        recycle=calls.append,
        apply=False,
    )

    assert result.candidate_count == 1
    assert result.recycled_count == 0
    assert ledger_path.read_bytes() == before
    assert calls == []


def test_retired_status_is_durable_before_the_first_recycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retirement = _load_module()
    monkeypatch.setattr(retirement, "LOCKED_PRIVATE_VOICE_DIRECTORY", tmp_path)
    monkeypatch.setattr(retirement, "_validate_retirement_inputs", lambda **_kwargs: None)
    ledger_path = tmp_path / "ledger.json"
    _ledger(ledger_path)
    candidate = tmp_path / "user-indextts2-calm-v2.wav"
    candidate.write_bytes(b"old")
    observed: list[tuple[Path, list[str]]] = []

    def recycle(path: Path) -> None:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        observed.append((path, payload["retired_voice_ids"]))

    result = retirement.retire_voice_references(
        voice_dir=tmp_path,
        active_route=_current_route(),
        acceptance_manifest=tmp_path / "voice_manifest.json",
        ledger_path=ledger_path,
        recycle=recycle,
    )

    assert result.recycled_count == 1
    assert observed == [
        (
            candidate,
            ["user-indextts2-calm-v1", "user-indextts2-calm-v2"],
        )
    ]


def test_recycle_failure_stops_without_trying_later_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retirement = _load_module()
    monkeypatch.setattr(retirement, "LOCKED_PRIVATE_VOICE_DIRECTORY", tmp_path)
    monkeypatch.setattr(retirement, "_validate_retirement_inputs", lambda **_kwargs: None)
    ledger_path = tmp_path / "ledger.json"
    _ledger(ledger_path)
    for name in sorted(retirement.retirement_candidates()):
        (tmp_path / name).write_bytes(b"old")
    calls: list[Path] = []

    def fail(path: Path) -> None:
        calls.append(path)
        raise OSError("recycle unavailable")

    with pytest.raises(retirement.VoiceRetirementError, match="voice-retirement-failed"):
        retirement.retire_voice_references(
            voice_dir=tmp_path,
            active_route=_current_route(),
            acceptance_manifest=tmp_path / "voice_manifest.json",
            ledger_path=ledger_path,
            recycle=fail,
        )

    assert len(calls) == 1


def test_candidate_is_revalidated_immediately_before_recycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retirement = _load_module()
    monkeypatch.setattr(retirement, "LOCKED_PRIVATE_VOICE_DIRECTORY", tmp_path)
    monkeypatch.setattr(retirement, "_validate_retirement_inputs", lambda **_kwargs: None)
    ledger_path = tmp_path / "ledger.json"
    _ledger(ledger_path)
    candidate = tmp_path / "user-indextts2-calm-v2.wav"
    candidate.write_bytes(b"old")
    calls: list[Path] = []

    def replace_after_collection(_ledger_path: Path) -> None:
        candidate.unlink()
        candidate.mkdir()

    monkeypatch.setattr(retirement, "_publish_retired_status", replace_after_collection)

    with pytest.raises(retirement.VoiceRetirementError, match="voice-retirement-blocked"):
        retirement.retire_voice_references(
            voice_dir=tmp_path,
            active_route=_current_route(),
            acceptance_manifest=tmp_path / "voice_manifest.json",
            ledger_path=ledger_path,
            recycle=calls.append,
        )

    assert calls == []


def test_retirement_rejects_an_unknown_ledger_field_before_recycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retirement = _load_module()
    monkeypatch.setattr(retirement, "LOCKED_PRIVATE_VOICE_DIRECTORY", tmp_path)
    monkeypatch.setattr(retirement, "_validate_retirement_inputs", lambda **_kwargs: None)
    ledger_path = tmp_path / "ledger.json"
    _ledger(ledger_path)
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")
    calls: list[Path] = []

    with pytest.raises(retirement.VoiceRetirementError, match="voice-retirement-blocked"):
        retirement.retire_voice_references(
            voice_dir=tmp_path,
            active_route=_current_route(),
            acceptance_manifest=tmp_path / "voice_manifest.json",
            ledger_path=ledger_path,
            recycle=calls.append,
        )

    assert calls == []

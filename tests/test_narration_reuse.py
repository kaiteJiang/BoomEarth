from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from boomearth.audio import indextts2


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation" / "scripts" / "register_narration_reuse.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("narration_reuse_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_project(project: Path, narration: bytes, segments: bytes) -> Path:
    media = project / "工程" / "media"
    media.mkdir(parents=True)
    narration_path = media / "narration.wav"
    segment_path = project / "工程" / "tts-segments.jsonl"
    narration_path.write_bytes(narration)
    segment_path.write_bytes(segments)
    manifest = {
        "provider": "indextts2-local",
        "voice_id": indextts2.CURRENT_VOICE_ID,
        "model": "IndexTTS2",
        "reference_audio_path": "private-reference.wav",
        "reference_audio_sha256": "d" * 64,
        "output_path": str(narration_path.absolute()),
        "output_sha256": _sha256(narration_path),
        "segment_contract_path": str(segment_path.absolute()),
        "segment_contract_sha256": _sha256(segment_path),
        "segment_count": 1,
        "playback_speed": 1.0,
        "pronunciation_contract_path": "private-pronunciation.json",
        "pronunciation_contract_sha256": "e" * 64,
        "used_fallback": False,
    }
    manifest_path = media / "voice_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def _write_ledger(path: Path, output_hash: str, manifest_hash: str) -> None:
    indextts2._persist_provenance_ledger(
        path,
        [output_hash],
        issued_manifest_sha256_by_output_sha256={output_hash: manifest_hash},
    )


def test_register_narration_reuse_binds_only_identical_audio_package(
    tmp_path: Path,
) -> None:
    module = _load_module()
    source = tmp_path / "source-project"
    target = tmp_path / "target-project"
    source_manifest = _write_project(source, b"same-narration", b"same-segments")
    target_manifest = _write_project(target, b"same-narration", b"same-segments")
    output_hash = hashlib.sha256(b"same-narration").hexdigest()
    ledger = tmp_path / "ledger.json"
    _write_ledger(ledger, output_hash, _sha256(source_manifest))

    registered = module.register_narration_reuse(
        source_project=source,
        target_project=target,
        ledger_path=ledger,
    )

    assert registered == (_sha256(source_manifest), _sha256(target_manifest))
    _issued, _references, bindings = indextts2._load_provenance_ledger_document(ledger)
    assert bindings == {output_hash: registered}


def test_register_narration_reuse_rejects_tampered_target_without_mutating_ledger(
    tmp_path: Path,
) -> None:
    module = _load_module()
    source = tmp_path / "source-project"
    target = tmp_path / "target-project"
    source_manifest = _write_project(source, b"same-narration", b"same-segments")
    _write_project(target, b"same-narration", b"same-segments")
    (target / "工程" / "media" / "narration.wav").write_bytes(b"tampered")
    output_hash = hashlib.sha256(b"same-narration").hexdigest()
    ledger = tmp_path / "ledger.json"
    _write_ledger(ledger, output_hash, _sha256(source_manifest))
    before = ledger.read_bytes()

    with pytest.raises(module.NarrationReuseError, match="narration-reuse-bytes"):
        module.register_narration_reuse(
            source_project=source,
            target_project=target,
            ledger_path=ledger,
        )

    assert ledger.read_bytes() == before

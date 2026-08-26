#!/usr/bin/env python3
"""Authorize one exact local narration package for a second production project."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.audio import indextts2


_PATH_FIELDS = {"output_path", "segment_contract_path"}
_EXPECTED_FIELDS = {
    "provider",
    "voice_id",
    "model",
    "reference_audio_path",
    "reference_audio_sha256",
    "output_path",
    "output_sha256",
    "segment_contract_path",
    "segment_contract_sha256",
    "segment_count",
    "playback_speed",
    "pronunciation_contract_path",
    "pronunciation_contract_sha256",
    "used_fallback",
}


class NarrationReuseError(RuntimeError):
    """A fixed local narration-reuse contract failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(project: Path) -> tuple[dict[str, object], Path, str]:
    path = project / "工程" / "media" / "voice_manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise NarrationReuseError("narration-reuse-manifest") from None
    if not isinstance(value, dict) or set(value) != _EXPECTED_FIELDS:
        raise NarrationReuseError("narration-reuse-manifest")
    return value, path, _sha256(path)


def _exact_project_file(project: Path, value: object, relative: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise NarrationReuseError("narration-reuse-path")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = project / candidate
    expected = project / relative
    if candidate.absolute() != expected.absolute() or not expected.is_file():
        raise NarrationReuseError("narration-reuse-path")
    return expected


def register_narration_reuse(
    *,
    source_project: Path,
    target_project: Path,
    ledger_path: Path = indextts2.LOCKED_PROVENANCE_LEDGER_PATH,
) -> tuple[str, ...]:
    source = Path(source_project).absolute()
    target = Path(target_project).absolute()
    if (
        source == target
        or not source.is_dir()
        or not target.is_dir()
        or source.name == target.name
    ):
        raise NarrationReuseError("narration-reuse-project")
    source_manifest, _source_path, source_manifest_hash = _load_manifest(source)
    target_manifest, _target_path, target_manifest_hash = _load_manifest(target)
    if any(
        source_manifest[field] != target_manifest[field]
        for field in _EXPECTED_FIELDS - _PATH_FIELDS
    ):
        raise NarrationReuseError("narration-reuse-manifest")
    target_narration = _exact_project_file(
        target,
        target_manifest["output_path"],
        Path("工程/media/narration.wav"),
    )
    target_segments = _exact_project_file(
        target,
        target_manifest["segment_contract_path"],
        Path("工程/tts-segments.jsonl"),
    )
    source_narration = source / "工程" / "media" / "narration.wav"
    source_segments = source / "工程" / "tts-segments.jsonl"
    if not source_narration.is_file() or not source_segments.is_file():
        raise NarrationReuseError("narration-reuse-source")
    output_hash = target_manifest["output_sha256"]
    segment_hash = target_manifest["segment_contract_sha256"]
    if (
        not isinstance(output_hash, str)
        or not isinstance(segment_hash, str)
        or _sha256(source_narration) != output_hash
        or _sha256(target_narration) != output_hash
        or _sha256(source_segments) != segment_hash
        or _sha256(target_segments) != segment_hash
    ):
        raise NarrationReuseError("narration-reuse-bytes")
    try:
        issued, _references, bindings = indextts2._load_provenance_ledger_document(
            ledger_path
        )
    except indextts2.IndexTTS2ValidationError:
        raise NarrationReuseError("narration-reuse-ledger") from None
    current = bindings.get(output_hash) if bindings is not None else None
    accepted = (current,) if isinstance(current, str) else current or ()
    if output_hash not in issued or source_manifest_hash not in accepted:
        raise NarrationReuseError("narration-reuse-ledger")
    try:
        registered = indextts2.authorize_provenance_manifest_reuse(
            ledger_path,
            output_sha256=output_hash,
            source_manifest_sha256=source_manifest_hash,
            reuse_manifest_sha256=target_manifest_hash,
        )
    except indextts2.IndexTTS2ValidationError:
        raise NarrationReuseError("narration-reuse-ledger") from None
    if target_manifest_hash not in registered:
        raise NarrationReuseError("narration-reuse-ledger")
    return registered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", type=Path, required=True)
    parser.add_argument("--target-project", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        registered = register_narration_reuse(
            source_project=args.source_project,
            target_project=args.target_project,
        )
    except NarrationReuseError as error:
        print(f"NARRATION_REUSE=FAIL reason={error}", file=sys.stderr)
        return 2
    print(f"NARRATION_REUSE=PASS bindings={len(registered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

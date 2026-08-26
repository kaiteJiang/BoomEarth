from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.audio.indextts2 import (  # noqa: E402
    CURRENT_VOICE_ID,
    LOCKED_PRIVATE_VOICE_DIRECTORY,
    IndexTTS2Narrator,
    IndexTTS2ValidationError,
    TTSRouting,
)


HISTORICAL_VOICE_IDS = (
    "user-indextts2-calm-v1",
    "user-indextts2-calm-v2",
)
RETIREMENT_ORDER = (
    "user-indextts2-calm-v1.wav",
    "user-indextts2-calm-v1.qc.json",
    "user-indextts2-calm-v2.wav",
    "user-indextts2-calm-v2.qc.json",
)
_LEDGER_FIELDS = {
    "schema_version",
    "issued_output_sha256",
    "canonical_reference_provenance",
    "issued_manifest_sha256_by_output_sha256",
    "retired_voice_ids",
}
_REFERENCE_FIELDS = {
    "voice_id",
    "reference_audio_path",
    "reference_audio_sha256",
}
_MANIFEST_FIELDS = {
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


class VoiceRetirementError(RuntimeError):
    """A redacted retirement failure safe for CLI output."""


@dataclass(frozen=True, slots=True)
class RetirementResult:
    candidate_count: int
    recycled_count: int


def retirement_candidates() -> frozenset[str]:
    return frozenset(RETIREMENT_ORDER)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attributes & 0x400)


def _has_reparse_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    for candidate in (absolute, *absolute.parents):
        if candidate.exists() or candidate.is_symlink():
            if _is_reparse_point(candidate):
                return True
    return False


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(str(left.resolve(strict=True))) == os.path.normcase(
            str(right.resolve(strict=True))
        )
    except (OSError, RuntimeError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    document: dict[object, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _load_json(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        payload = path.read_bytes()
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise VoiceRetirementError("voice-retirement-blocked") from None
    if not isinstance(document, dict):
        raise VoiceRetirementError("voice-retirement-blocked")
    return document, payload


def _validate_ledger(document: dict[str, object]) -> None:
    issued = document.get("issued_output_sha256")
    references = document.get("canonical_reference_provenance")
    bindings = document.get("issued_manifest_sha256_by_output_sha256")
    retired = document.get("retired_voice_ids")
    if (
        not {"schema_version", "issued_output_sha256", "canonical_reference_provenance"}.issubset(
            document
        )
        or not set(document).issubset(_LEDGER_FIELDS)
        or document.get("schema_version") != 1
        or not isinstance(issued, list)
        or not all(_is_sha256(value) for value in issued)
        or len(set(issued)) != len(issued)
        or not isinstance(references, list)
        or not all(
            isinstance(entry, dict)
            and set(entry) == _REFERENCE_FIELDS
            and entry.get("voice_id")
            in {*HISTORICAL_VOICE_IDS, CURRENT_VOICE_ID}
            and isinstance(entry.get("reference_audio_path"), str)
            and bool(entry.get("reference_audio_path"))
            and _is_sha256(entry.get("reference_audio_sha256"))
            for entry in references
        )
        or {entry["voice_id"] for entry in references}
        != {*HISTORICAL_VOICE_IDS, CURRENT_VOICE_ID}
        or (
            retired is not None
            and retired != list(HISTORICAL_VOICE_IDS)
        )
    ):
        raise VoiceRetirementError("voice-retirement-blocked")
    if bindings is not None and (
        not isinstance(bindings, dict)
        or set(bindings) != set(issued)
        or not all(
            _is_sha256(key)
            and (
                _is_sha256(value)
                or (
                    isinstance(value, list)
                    and bool(value)
                    and all(_is_sha256(item) for item in value)
                    and len(set(value)) == len(value)
                )
            )
            for key, value in bindings.items()
        )
    ):
        raise VoiceRetirementError("voice-retirement-blocked")


def _validate_retirement_inputs(
    *,
    voice_dir: Path,
    active_route: TTSRouting,
    acceptance_manifest: Path,
    ledger_path: Path,
) -> None:
    if (
        active_route.voice_id != CURRENT_VOICE_ID
        or active_route.provider != "indextts2-local"
        or active_route.model != "IndexTTS2"
        or active_route.playback_speed != 1.12
        or active_route.used_fallback is not False
        or not _same_path(voice_dir, LOCKED_PRIVATE_VOICE_DIRECTORY)
        or _has_reparse_component(voice_dir)
        or not _same_path(ledger_path, active_route.provenance_ledger_path)
        or ledger_path.parent.resolve(strict=True) != voice_dir.resolve(strict=True)
        or not _same_path(
            active_route.reference_audio_path,
            voice_dir / f"{CURRENT_VOICE_ID}.wav",
        )
        or _has_reparse_component(active_route.reference_audio_path)
        or _sha256(active_route.reference_audio_path)
        != active_route.reference_audio_sha256
    ):
        raise VoiceRetirementError("voice-retirement-blocked")

    ledger, _ = _load_json(ledger_path)
    _validate_ledger(ledger)
    current = next(
        entry
        for entry in ledger["canonical_reference_provenance"]
        if entry["voice_id"] == CURRENT_VOICE_ID
    )
    if (
        not _same_path(Path(current["reference_audio_path"]), active_route.reference_audio_path)
        or current["reference_audio_sha256"] != active_route.reference_audio_sha256
    ):
        raise VoiceRetirementError("voice-retirement-blocked")

    expected_manifest = (
        voice_dir / "acceptance" / "black-gold-v3" / "voice_manifest.json"
    )
    if (
        not _same_path(acceptance_manifest, expected_manifest)
        or _has_reparse_component(acceptance_manifest)
    ):
        raise VoiceRetirementError("voice-retirement-blocked")
    manifest, _ = _load_json(acceptance_manifest)
    output_path = Path(str(manifest.get("output_path", "")))
    raw_path = output_path.with_name("narration.raw.wav")
    if (
        set(manifest) != _MANIFEST_FIELDS
        or manifest.get("provider") != "indextts2-local"
        or manifest.get("model") != "IndexTTS2"
        or manifest.get("voice_id") != CURRENT_VOICE_ID
        or manifest.get("reference_audio_sha256")
        != active_route.reference_audio_sha256
        or manifest.get("playback_speed") != 1.12
        or manifest.get("used_fallback") is not False
        or output_path.name != "narration.wav"
        or output_path.parent != acceptance_manifest.parent
        or not raw_path.is_file()
        or _has_reparse_component(output_path)
        or _has_reparse_component(raw_path)
    ):
        raise VoiceRetirementError("voice-retirement-blocked")
    try:
        narrator = IndexTTS2Narrator(
            active_route,
            pronunciation_lexicon_path=Path(
                str(manifest["pronunciation_contract_path"])
            ),
        )
        narrator.probe_committed_wav(output_path, acceptance_manifest)
    except (OSError, RuntimeError, ValueError, IndexTTS2ValidationError):
        raise VoiceRetirementError("voice-retirement-blocked") from None


def _validate_candidate(candidate: Path, resolved_voice_dir: Path) -> None:
    try:
        candidate_stat = candidate.lstat()
        if (
            candidate.parent.resolve(strict=True) != resolved_voice_dir
            or _is_reparse_point(candidate)
            or not stat.S_ISREG(candidate_stat.st_mode)
        ):
            raise VoiceRetirementError("voice-retirement-blocked")
    except (OSError, RuntimeError):
        raise VoiceRetirementError("voice-retirement-blocked") from None


def _collect_candidates(voice_dir: Path) -> tuple[Path, ...]:
    try:
        resolved_voice_dir = voice_dir.resolve(strict=True)
    except (OSError, RuntimeError):
        raise VoiceRetirementError("voice-retirement-blocked") from None
    candidates: list[Path] = []
    for name in RETIREMENT_ORDER:
        candidate = voice_dir / name
        if not candidate.exists():
            continue
        _validate_candidate(candidate, resolved_voice_dir)
        candidates.append(candidate)
    return tuple(candidates)


def _canonical_json(document: dict[str, object]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _write_stage(destination: Path, payload: bytes) -> Path:
    stage = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with stage.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise VoiceRetirementError("voice-retirement-blocked") from None
    return stage


def _publish_retired_status(ledger_path: Path) -> None:
    document, original = _load_json(ledger_path)
    _validate_ledger(document)
    candidate = dict(document)
    candidate["retired_voice_ids"] = list(HISTORICAL_VOICE_IDS)
    rendered = _canonical_json(candidate)
    stage = _write_stage(ledger_path, rendered)
    try:
        if ledger_path.read_bytes() != original:
            raise OSError("ledger changed")
        os.replace(stage, ledger_path)
        if ledger_path.read_bytes() != rendered:
            raise OSError("ledger readback failed")
    except OSError:
        try:
            restore = _write_stage(ledger_path, original)
            os.replace(restore, ledger_path)
        except (OSError, VoiceRetirementError):
            pass
        raise VoiceRetirementError("voice-retirement-blocked") from None


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def recycle_to_windows_bin(path: Path) -> None:
    if os.name != "nt":
        raise OSError("Windows recycle is unavailable")
    operation = _SHFILEOPSTRUCTW()
    operation.wFunc = 3
    operation.pFrom = f"{path}\0\0"
    operation.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError("Windows recycle failed")


def retire_voice_references(
    *,
    voice_dir: Path,
    active_route: TTSRouting,
    acceptance_manifest: Path,
    ledger_path: Path,
    recycle: Callable[[Path], None],
    apply: bool = True,
) -> RetirementResult:
    voice_dir = Path(voice_dir).absolute()
    acceptance_manifest = Path(acceptance_manifest).absolute()
    ledger_path = Path(ledger_path).absolute()
    if active_route.voice_id != CURRENT_VOICE_ID:
        raise VoiceRetirementError("voice-retirement-blocked")
    _validate_retirement_inputs(
        voice_dir=voice_dir,
        active_route=active_route,
        acceptance_manifest=acceptance_manifest,
        ledger_path=ledger_path,
    )
    candidates = _collect_candidates(voice_dir)
    if not apply:
        return RetirementResult(candidate_count=len(candidates), recycled_count=0)

    _publish_retired_status(ledger_path)
    recycled_count = 0
    resolved_voice_dir = voice_dir.resolve(strict=True)
    for candidate in candidates:
        _validate_candidate(candidate, resolved_voice_dir)
        try:
            recycle(candidate)
        except OSError:
            raise VoiceRetirementError("voice-retirement-failed") from None
        recycled_count += 1
    return RetirementResult(
        candidate_count=len(candidates), recycled_count=recycled_count
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--voice-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        voice_dir = arguments.voice_dir.absolute()
        route = TTSRouting.load(ROOT / "automation" / "config" / "tts-routing.json")
        result = retire_voice_references(
            voice_dir=voice_dir,
            active_route=route,
            acceptance_manifest=(
                voice_dir / "acceptance" / "black-gold-v3" / "voice_manifest.json"
            ),
            ledger_path=voice_dir / "indextts2-provenance-ledger.json",
            recycle=recycle_to_windows_bin,
            apply=arguments.apply,
        )
    except (OSError, ValueError, VoiceRetirementError, SystemExit):
        print("status=failed")
        return 2
    print("status=recycled" if arguments.apply else "status=ready")
    if arguments.apply:
        print(f"recycled={result.recycled_count}")
    else:
        print(f"candidates={result.candidate_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

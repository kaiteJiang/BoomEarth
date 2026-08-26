from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid


TARGET_VOICE_ID = "user-indextts2-black-gold-v3"
TARGET_RECEIPT_SCHEMA = "boomearth.voice-reference-qc/v3"
SOURCE_VOICE_ID = "user-indextts2-calm-v2"
HISTORICAL_VOICE_IDS = frozenset(
    {"user-indextts2-calm-v1", "user-indextts2-calm-v2"}
)
_ROUTE_FIELDS = {
    "schema_version",
    "provider",
    "model",
    "voice_id",
    "reference_audio_path",
    "reference_audio_sha256",
    "provenance_ledger_path",
    "interpreter_path",
    "cli_script_path",
    "model_dir",
    "playback_speed",
    "fp16",
    "deepspeed",
    "cuda_kernel",
    "accel",
    "torch_compile",
    "used_fallback",
}
_RECEIPT_FIELDS = {
    "schema",
    "status",
    "source_metadata",
    "selected_interval",
    "metrics",
    "conversion",
    "output_sha256",
}
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


class VoiceActivationError(RuntimeError):
    """A fixed, redacted voice activation failure."""


@dataclass(frozen=True, slots=True)
class ActivationDocuments:
    route_path: Path
    ledger_path: Path
    route: dict[str, object]
    ledger: dict[str, object]
    route_payload: bytes
    ledger_payload: bytes
    original_route_payload: bytes
    original_ledger_payload: bytes
    reference_sha256: str


@dataclass(frozen=True, slots=True)
class ActivationResult:
    voice_id: str


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("non-finite JSON value")
            ),
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise VoiceActivationError("voice-activation-invalid") from None
    if not isinstance(value, dict):
        raise VoiceActivationError("voice-activation-invalid")
    return value, payload


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError:
        raise VoiceActivationError("voice-activation-invalid") from None
    return digest.hexdigest()


def _canonical_json(value: dict[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _validate_receipt(receipt: dict[str, object]) -> None:
    interval = receipt.get("selected_interval")
    conversion = receipt.get("conversion")
    if (
        set(receipt) != _RECEIPT_FIELDS
        or receipt.get("schema") != TARGET_RECEIPT_SCHEMA
        or receipt.get("status") != "PASS"
        or not isinstance(receipt.get("source_metadata"), dict)
        or not isinstance(receipt.get("metrics"), dict)
        or not isinstance(interval, dict)
        or set(interval) != {"start_seconds", "duration_seconds"}
        or not isinstance(interval.get("start_seconds"), (int, float))
        or isinstance(interval.get("start_seconds"), bool)
        or not math.isfinite(float(interval["start_seconds"]))
        or float(interval["start_seconds"]) < 0
        or interval.get("duration_seconds") != 18.0
        or not isinstance(conversion, dict)
        or set(conversion)
        != {"codec", "sample_rate", "channels", "attenuation_db"}
        or conversion.get("codec") != "pcm_s16le"
        or conversion.get("sample_rate") != 24_000
        or conversion.get("channels") != 1
        or isinstance(conversion.get("sample_rate"), bool)
        or isinstance(conversion.get("channels"), bool)
        or not isinstance(conversion.get("attenuation_db"), (int, float))
        or isinstance(conversion.get("attenuation_db"), bool)
        or not math.isfinite(float(conversion["attenuation_db"]))
        or not _is_sha256(receipt.get("output_sha256"))
    ):
        raise VoiceActivationError("voice-activation-invalid")


def _validate_route(route: dict[str, object], ledger_path: Path) -> None:
    booleans = (
        "fp16",
        "deepspeed",
        "cuda_kernel",
        "accel",
        "torch_compile",
        "used_fallback",
    )
    try:
        configured_ledger = Path(str(route["provenance_ledger_path"])).resolve(
            strict=True
        )
        expected_ledger = ledger_path.resolve(strict=True)
    except (KeyError, OSError, RuntimeError):
        raise VoiceActivationError("voice-activation-invalid") from None
    if (
        set(route) != _ROUTE_FIELDS
        or route.get("schema_version") != 1
        or route.get("provider") != "indextts2-local"
        or route.get("model") != "IndexTTS2"
        or route.get("voice_id") != SOURCE_VOICE_ID
        or not _is_sha256(route.get("reference_audio_sha256"))
        or route.get("playback_speed") != 1.12
        or route.get("fp16") is not True
        or any(not isinstance(route.get(field), bool) for field in booleans)
        or any(
            not isinstance(route.get(field), str) or not route.get(field)
            for field in (
                "reference_audio_path",
                "provenance_ledger_path",
                "interpreter_path",
                "cli_script_path",
                "model_dir",
            )
        )
        or route.get("deepspeed") is not False
        or route.get("cuda_kernel") is not False
        or route.get("accel") is not False
        or route.get("torch_compile") is not False
        or route.get("used_fallback") is not False
        or configured_ledger != expected_ledger
    ):
        raise VoiceActivationError("voice-activation-invalid")


def _validate_ledger(ledger: dict[str, object]) -> list[dict[str, str]]:
    issued = ledger.get("issued_output_sha256")
    references = ledger.get("canonical_reference_provenance", [])
    bindings = ledger.get("issued_manifest_sha256_by_output_sha256")
    retired_voice_ids = ledger.get("retired_voice_ids")
    if (
        not set(ledger).issubset(_LEDGER_FIELDS)
        or not {"schema_version", "issued_output_sha256"}.issubset(ledger)
        or ledger.get("schema_version") != 1
        or not isinstance(issued, list)
        or not all(_is_sha256(value) for value in issued)
        or len(set(issued)) != len(issued)
        or not isinstance(references, list)
        or not all(
            isinstance(entry, dict)
            and set(entry) == _REFERENCE_FIELDS
            and entry.get("voice_id") in HISTORICAL_VOICE_IDS | {TARGET_VOICE_ID}
            and isinstance(entry.get("reference_audio_path"), str)
            and bool(entry.get("reference_audio_path"))
            and _is_sha256(entry.get("reference_audio_sha256"))
            for entry in references
        )
        or len({entry["voice_id"] for entry in references}) != len(references)
        or (
            retired_voice_ids is not None
            and retired_voice_ids
            != ["user-indextts2-calm-v1", "user-indextts2-calm-v2"]
        )
    ):
        raise VoiceActivationError("voice-activation-invalid")
    if bindings is not None:
        if (
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
            raise VoiceActivationError("voice-activation-invalid")
    return [dict(entry) for entry in references]


def build_activation_documents(
    *, route_path: Path, receipt_path: Path, ledger_path: Path
) -> ActivationDocuments:
    route_path = Path(route_path).absolute()
    receipt_path = Path(receipt_path).absolute()
    ledger_path = Path(ledger_path).absolute()
    route, original_route = _load_json(route_path)
    receipt, _receipt_payload = _load_json(receipt_path)
    ledger, original_ledger = _load_json(ledger_path)
    _validate_receipt(receipt)
    _validate_route(route, ledger_path)
    references = _validate_ledger(ledger)

    if receipt_path.name != f"{TARGET_VOICE_ID}.qc.json":
        raise VoiceActivationError("voice-activation-invalid")
    reference_path = receipt_path.with_name(f"{TARGET_VOICE_ID}.wav")
    try:
        resolved_reference = reference_path.resolve(strict=True)
        if not resolved_reference.is_file() or resolved_reference.parent != receipt_path.parent.resolve(
            strict=True
        ):
            raise VoiceActivationError("voice-activation-invalid")
    except (OSError, RuntimeError):
        raise VoiceActivationError("voice-activation-invalid") from None
    reference_hash = _hash_file(resolved_reference)
    if reference_hash != receipt["output_sha256"]:
        raise VoiceActivationError("voice-activation-invalid")

    current_target = next(
        (entry for entry in references if entry["voice_id"] == TARGET_VOICE_ID),
        None,
    )
    target_entry = {
        "voice_id": TARGET_VOICE_ID,
        "reference_audio_path": str(resolved_reference),
        "reference_audio_sha256": reference_hash,
    }
    if current_target is not None and current_target != target_entry:
        raise VoiceActivationError("voice-activation-invalid")
    if current_target is None:
        references.append(target_entry)

    route_candidate = dict(route)
    route_candidate.update(
        {
            "voice_id": TARGET_VOICE_ID,
            "reference_audio_path": str(resolved_reference),
            "reference_audio_sha256": reference_hash,
        }
    )
    if (
        set(route_candidate) != _ROUTE_FIELDS
        or route_candidate["voice_id"] != TARGET_VOICE_ID
        or route_candidate["playback_speed"] != 1.12
        or route_candidate["used_fallback"] is not False
    ):
        raise VoiceActivationError("voice-activation-invalid")
    ledger_candidate = dict(ledger)
    ledger_candidate["canonical_reference_provenance"] = references
    return ActivationDocuments(
        route_path=route_path,
        ledger_path=ledger_path,
        route=route_candidate,
        ledger=ledger_candidate,
        route_payload=_canonical_json(route_candidate),
        ledger_payload=_canonical_json(ledger_candidate),
        original_route_payload=original_route,
        original_ledger_payload=original_ledger,
        reference_sha256=reference_hash,
    )


def _stage_payload(destination: Path, payload: bytes) -> Path:
    staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with staged.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        try:
            staged.unlink()
        except OSError:
            pass
        raise VoiceActivationError("voice-activation-publish-failed") from None
    return staged


def _restore_payload(destination: Path, payload: bytes) -> None:
    staged = _stage_payload(destination, payload)
    try:
        os.replace(staged, destination)
    finally:
        try:
            staged.unlink()
        except OSError:
            pass


def publish_activation(documents: ActivationDocuments) -> ActivationResult:
    try:
        if (
            documents.route_path.read_bytes() != documents.original_route_payload
            or documents.ledger_path.read_bytes() != documents.original_ledger_payload
        ):
            raise VoiceActivationError("voice-activation-input-changed")
    except OSError:
        raise VoiceActivationError("voice-activation-input-changed") from None

    route_stage = _stage_payload(documents.route_path, documents.route_payload)
    ledger_stage = _stage_payload(documents.ledger_path, documents.ledger_payload)
    replaced = False
    try:
        os.replace(route_stage, documents.route_path)
        replaced = True
        os.replace(ledger_stage, documents.ledger_path)
        if (
            documents.route_path.read_bytes() != documents.route_payload
            or documents.ledger_path.read_bytes() != documents.ledger_payload
        ):
            raise OSError("activation readback mismatch")
    except (OSError, VoiceActivationError):
        try:
            if replaced:
                _restore_payload(
                    documents.route_path, documents.original_route_payload
                )
            if documents.ledger_path.read_bytes() != documents.original_ledger_payload:
                _restore_payload(
                    documents.ledger_path, documents.original_ledger_payload
                )
        except (OSError, VoiceActivationError):
            pass
        raise VoiceActivationError("voice-activation-publish-failed") from None
    finally:
        for staged in (route_stage, ledger_stage):
            try:
                staged.unlink()
            except OSError:
                pass
    return ActivationResult(voice_id=TARGET_VOICE_ID)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--route", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        result = publish_activation(
            build_activation_documents(
                route_path=arguments.route,
                receipt_path=arguments.receipt,
                ledger_path=arguments.ledger,
            )
        )
    except (VoiceActivationError, SystemExit):
        print("status=failed")
        return 2
    print("status=activated")
    print(f"voice_id={result.voice_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

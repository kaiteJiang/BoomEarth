"""Locked local IndexTTS2 routing contract."""

from __future__ import annotations

import json
import hashlib
import math
import os
import shutil
import stat
import subprocess
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


def _canonical_workspace_root(source_root: Path) -> Path:
    """Return the main checkout root when this module runs from a linked worktree."""

    marker = source_root / ".git"
    if not marker.is_file():
        return source_root
    try:
        prefix, separator, raw_git_dir = marker.read_text(
            encoding="utf-8"
        ).strip().partition(":")
        if prefix.casefold() != "gitdir" or separator != ":":
            return source_root
        git_dir = Path(raw_git_dir.strip())
        if not git_dir.is_absolute():
            git_dir = marker.parent / git_dir
        common_value = (git_dir / "commondir").read_text(encoding="utf-8").strip()
        common_dir = Path(common_value)
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir
        candidate = common_dir.resolve(strict=True).parent
        if (candidate / "automation" / "config" / "tts-routing.json").is_file():
            return candidate
    except (OSError, RuntimeError, UnicodeDecodeError):
        pass
    return source_root


WORKSPACE_ROOT = _canonical_workspace_root(Path(__file__).resolve().parents[3])
LOCKED_PRIVATE_VOICE_DIRECTORY = (
    WORKSPACE_ROOT / "01-内容生产" / "视频工作台" / ".internal" / "voice"
)

def _local_path_setting(name: str, fallback: Path) -> Path:
    value = os.environ.get(name)
    if not isinstance(value, str) or not value.strip():
        local = dotenv_values(WORKSPACE_ROOT / ".env").get(name)
        value = local if isinstance(local, str) else None
    return Path(value.strip()) if isinstance(value, str) and value.strip() else fallback


LOCKED_INDEXTTS2_ROOT = _local_path_setting(
    "INDEXTTS2_ROOT", WORKSPACE_ROOT / ".local" / "IndexTTS2"
)
LOCKED_INTERPRETER_PATH = _local_path_setting(
    "INDEXTTS2_PYTHON", LOCKED_INDEXTTS2_ROOT / ".venv" / "Scripts" / "python.exe"
)
LOCKED_CLI_SCRIPT_PATH = LOCKED_INDEXTTS2_ROOT / "indextts" / "cli_v2.py"
LOCKED_MODEL_DIR = LOCKED_INDEXTTS2_ROOT / "checkpoints"
LOCKED_PROVENANCE_LEDGER_PATH = LOCKED_PRIVATE_VOICE_DIRECTORY / "indextts2-provenance-ledger.json"
CURRENT_VOICE_ID = "user-indextts2-black-gold-v3"
SUPPORTED_VOICE_IDS = frozenset(
    {"user-indextts2-calm-v1", "user-indextts2-calm-v2", CURRENT_VOICE_ID}
)


@dataclass(frozen=True)
class TTSRouting:
    provider: str
    model: str
    voice_id: str
    reference_audio_path: Path
    reference_audio_sha256: str
    interpreter_path: Path
    cli_script_path: Path
    model_dir: Path
    playback_speed: float
    fp16: bool
    deepspeed: bool
    cuda_kernel: bool
    accel: bool
    torch_compile: bool
    used_fallback: bool
    provenance_ledger_path: Path

    @classmethod
    def load(cls, path: Path) -> "TTSRouting":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("unable to load TTS routing") from exc

        if not isinstance(payload, dict):
            raise ValueError("TTS routing must be a JSON object")

        required = {
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
        missing = sorted(required - payload.keys())
        if missing:
            raise ValueError(f"TTS routing is missing fields: {', '.join(missing)}")

        locked_values = {
            "schema_version": 1,
            "provider": "indextts2-local",
            "model": "IndexTTS2",
            "voice_id": CURRENT_VOICE_ID,
            "playback_speed": 1.12,
            "fp16": True,
            "deepspeed": False,
            "cuda_kernel": False,
            "accel": False,
            "torch_compile": False,
            "used_fallback": False,
        }
        if any(payload[name] != expected for name, expected in locked_values.items()):
            raise ValueError("TTS routing violates the locked local contract")
        if any(
            not isinstance(payload[name], bool)
            for name in ("fp16", "deepspeed", "cuda_kernel", "accel", "torch_compile", "used_fallback")
        ):
            raise ValueError("TTS routing boolean values must be explicit")

        reference_hash = payload["reference_audio_sha256"]
        if (
            not isinstance(reference_hash, str)
            or len(reference_hash) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in reference_hash)
        ):
            raise ValueError("TTS routing reference hash is invalid")
        if not _same_resolved_path(
            Path(str(payload["interpreter_path"])),
            LOCKED_INTERPRETER_PATH,
        ):
            raise ValueError("TTS routing interpreter path is not the locked production binary")
        if not _same_resolved_path(
            Path(str(payload["cli_script_path"])),
            LOCKED_CLI_SCRIPT_PATH,
        ):
            raise ValueError("TTS routing CLI script path is not the locked production script")
        if not _same_resolved_path(
            Path(str(payload["provenance_ledger_path"])),
            LOCKED_PROVENANCE_LEDGER_PATH,
        ):
            raise ValueError("TTS routing provenance ledger path is not the locked private ledger")
        if not _same_resolved_path(Path(str(payload["model_dir"])), LOCKED_MODEL_DIR):
            raise ValueError("TTS routing model directory is not the locked production directory")

        return cls(
            provider=str(payload["provider"]),
            model=str(payload["model"]),
            voice_id=str(payload["voice_id"]),
            reference_audio_path=Path(str(payload["reference_audio_path"])),
            reference_audio_sha256=str(payload["reference_audio_sha256"]),
            interpreter_path=Path(str(payload["interpreter_path"])),
            cli_script_path=Path(str(payload["cli_script_path"])),
            model_dir=Path(str(payload["model_dir"])),
            playback_speed=float(payload["playback_speed"]),
            fp16=bool(payload["fp16"]),
            deepspeed=bool(payload["deepspeed"]),
            cuda_kernel=bool(payload["cuda_kernel"]),
            accel=bool(payload["accel"]),
            torch_compile=bool(payload["torch_compile"]),
            used_fallback=bool(payload["used_fallback"]),
            provenance_ledger_path=Path(str(payload["provenance_ledger_path"])),
        )


class IndexTTS2ValidationError(ValueError):
    """Raised when local narration inputs are not safe to use."""


def validate_historical_voice_id(voice_id: object) -> str:
    """Validate a voice identity only for explicitly historical artifacts."""
    if not isinstance(voice_id, str) or voice_id not in SUPPORTED_VOICE_IDS:
        raise IndexTTS2ValidationError("historical voice ID is unsupported")
    return voice_id


def validate_new_production_voice_id(voice_id: object) -> str:
    """Require the current identity for every newly produced artifact."""
    if voice_id != CURRENT_VOICE_ID:
        raise IndexTTS2ValidationError("current voice ID is required")
    return CURRENT_VOICE_ID


@dataclass(frozen=True)
class AudioProbe:
    container: str
    codec: str
    sample_rate: int
    channels: int
    duration_seconds: float
    sha256: str


@dataclass(frozen=True)
class EnvironmentValidation:
    reference: AudioProbe


@dataclass(frozen=True)
class DryRunValidation:
    reference: AudioProbe
    segment_count: int
    segment_contract_sha256: str


MAX_SILENCE_AFTER_MS = 60_000


@dataclass(frozen=True)
class NarrationSegment:
    text: str
    tts_text: str
    emotion_vector: tuple[float, ...] | None
    emotion_weight: float | None
    silence_after_ms: int


@dataclass(frozen=True)
class _NarrationSnapshot:
    batch_path: Path
    batch_bytes: bytes
    batch_sha256: str
    lexicon_path: Path
    lexicon_bytes: bytes
    lexicon_sha256: str
    segments: tuple[NarrationSegment, ...]


@dataclass(frozen=True)
class VoiceManifest:
    provider: str
    voice_id: str
    model: str
    reference_audio_path: str
    reference_audio_sha256: str
    output_path: str
    output_sha256: str
    segment_contract_path: str
    segment_contract_sha256: str
    segment_count: int
    playback_speed: float
    pronunciation_contract_path: str
    pronunciation_contract_sha256: str
    used_fallback: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "voice_id": self.voice_id,
            "model": self.model,
            "reference_audio_path": self.reference_audio_path,
            "reference_audio_sha256": self.reference_audio_sha256,
            "output_path": self.output_path,
            "output_sha256": self.output_sha256,
            "segment_contract_path": self.segment_contract_path,
            "segment_contract_sha256": self.segment_contract_sha256,
            "segment_count": self.segment_count,
            "playback_speed": self.playback_speed,
            "pronunciation_contract_path": self.pronunciation_contract_path,
            "pronunciation_contract_sha256": self.pronunciation_contract_sha256,
            "used_fallback": self.used_fallback,
        }


@dataclass(frozen=True)
class _PriorManifestEvidence:
    output_path: Path
    output_sha256: str


@dataclass(frozen=True)
class _LedgerLock:
    path: Path
    marker: bytes


@dataclass(frozen=True)
class _PublishedArtifact:
    path: Path
    device: int
    inode: int
    sha256: str
    snapshot: bytes | None = None


@dataclass(frozen=True)
class _NarrationBoundary:
    workspace_root: Path
    private_voice_directory: Path
    model_dir: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError as exc:
        raise IndexTTS2ValidationError("audio file is unavailable") from exc
    return digest.hexdigest()


def _write_durable_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    indent: int | None = None,
) -> None:
    """Create a staged JSON file only after its data has reached the OS."""
    encoded = (json.dumps(payload, ensure_ascii=False, indent=indent) + "\n").encode("utf-8")
    with path.open("xb") as destination:
        destination.write(encoded)
        destination.flush()
        os.fsync(destination.fileno())


def _run_available(command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IndexTTS2ValidationError("required local command is unavailable") from exc
    if result.returncode != 0:
        raise IndexTTS2ValidationError("required local command did not validate")


def _probe_pcm_wav(path: Path, *, ffprobe: str) -> AudioProbe:
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "format=format_name,duration:stream=codec_name,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise IndexTTS2ValidationError("ffprobe is unavailable") from exc
    if result.returncode != 0:
        raise IndexTTS2ValidationError("audio metadata is unavailable")

    try:
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        container = str(payload["format"]["format_name"]).lower()
        codec = str(stream["codec_name"]).lower()
        sample_rate = int(stream["sample_rate"])
        channels = int(stream["channels"])
        duration_seconds = float(payload["format"]["duration"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IndexTTS2ValidationError("audio metadata is invalid") from exc

    if (
        "wav" not in {part.strip() for part in container.split(",")}
        or not codec.startswith("pcm_")
        or sample_rate <= 0
        or channels <= 0
        or not math.isfinite(duration_seconds)
        or duration_seconds <= 0
    ):
        raise IndexTTS2ValidationError("audio must be a non-empty PCM WAV")

    return AudioProbe(
        container="wav",
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        duration_seconds=duration_seconds,
        sha256=_sha256(path),
    )


def _contains_generated_directory(path: Path) -> bool:
    resolved = path.resolve(strict=True)
    return any(
        component.casefold()
        in {"generated", "output", "outputs", "render", "renders"}
        for component in resolved.parts
    )


def _same_resolved_path(left: Path, right: Path) -> bool:
    return str(left.resolve(strict=False)).casefold() == str(
        right.resolve(strict=False)
    ).casefold()


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


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def _is_ordinary_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _is_ordinary_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _path_is_git_ignored(path: Path, workspace_root: Path) -> bool:
    try:
        relative = path.resolve(strict=False).relative_to(workspace_root.resolve(strict=True))
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(relative)],
            cwd=workspace_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, ValueError):
        return False
    return result.returncode == 0


def _path_is_tracked(path: Path, workspace_root: Path) -> bool:
    try:
        relative = path.resolve(strict=False).relative_to(workspace_root.resolve(strict=True))
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(relative)],
            cwd=workspace_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, ValueError):
        return True
    return result.returncode == 0


def _reject_unsafe_path(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts or _has_reparse_component(path):
        raise IndexTTS2ValidationError("private narration boundary is invalid")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def _ledger_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


def _acquire_provenance_ledger_lock(path: Path) -> _LedgerLock:
    lock_path = _ledger_lock_path(path)
    marker = uuid.uuid4().hex.encode("ascii")
    try:
        with lock_path.open("xb") as lock_file:
            lock_file.write(marker)
            lock_file.flush()
            os.fsync(lock_file.fileno())
    except FileExistsError as exc:
        raise IndexTTS2ValidationError("provenance ledger is unavailable or invalid") from exc
    except OSError as exc:
        raise IndexTTS2ValidationError("provenance ledger could not be registered") from exc
    return _LedgerLock(path=lock_path, marker=marker)


def _release_provenance_ledger_lock(lock: _LedgerLock) -> None:
    try:
        if lock.path.read_bytes() == lock.marker:
            lock.path.unlink()
    except OSError:
        pass


def _load_provenance_ledger(
    path: Path,
    *,
    allow_owned_lock: bool = False,
) -> tuple[str, ...]:
    return _load_provenance_ledger_document(
        path,
        allow_owned_lock=allow_owned_lock,
    )[0]


def _load_provenance_ledger_document(
    path: Path,
    *,
    allow_owned_lock: bool = False,
    include_retired_voice_ids: bool = False,
) -> tuple[
    tuple[str, ...],
    tuple[dict[str, str], ...],
    dict[str, str | tuple[str, ...]] | None,
] | tuple[
    tuple[str, ...],
    tuple[dict[str, str], ...],
    dict[str, str | tuple[str, ...]] | None,
    frozenset[str],
]:
    if not allow_owned_lock and _ledger_lock_path(path).exists():
        raise IndexTTS2ValidationError("provenance ledger is unavailable or invalid")

    def reject_duplicate_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
        document: dict[object, object] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("duplicate JSON key")
            document[key] = value
        return document

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
        hashes = payload["issued_output_sha256"]
    except (OSError, UnicodeDecodeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IndexTTS2ValidationError("provenance ledger is unavailable or invalid") from exc
    allowed_fields = {
        "schema_version",
        "issued_output_sha256",
        "canonical_reference_provenance",
        "issued_manifest_sha256_by_output_sha256",
        "retired_voice_ids",
    }
    retired_voice_ids = payload.get("retired_voice_ids")
    if (
        not isinstance(payload, dict)
        or not {"schema_version", "issued_output_sha256"}.issubset(payload)
        or not set(payload).issubset(allowed_fields)
        or payload.get("schema_version") != 1
        or not isinstance(hashes, list)
        or not all(_is_sha256(item) for item in hashes)
        or len({item.casefold() for item in hashes}) != len(hashes)
        or (
            retired_voice_ids is not None
            and retired_voice_ids
            != ["user-indextts2-calm-v1", "user-indextts2-calm-v2"]
        )
    ):
        raise IndexTTS2ValidationError("provenance ledger is unavailable or invalid")

    canonical_reference_provenance = payload.get("canonical_reference_provenance", [])
    if (
        not isinstance(canonical_reference_provenance, list)
        or not all(
            isinstance(entry, dict)
            and set(entry)
            == {"voice_id", "reference_audio_path", "reference_audio_sha256"}
            and isinstance(entry["reference_audio_path"], str)
            and entry["reference_audio_path"]
            and isinstance(entry["voice_id"], str)
            and _is_sha256(entry["reference_audio_sha256"])
            for entry in canonical_reference_provenance
        )
        or len({entry["voice_id"] for entry in canonical_reference_provenance})
        != len(canonical_reference_provenance)
    ):
        raise IndexTTS2ValidationError("provenance ledger is unavailable or invalid")
    try:
        for entry in canonical_reference_provenance:
            validate_historical_voice_id(entry["voice_id"])
    except IndexTTS2ValidationError as exc:
        raise IndexTTS2ValidationError("provenance ledger is unavailable or invalid") from exc

    manifest_bindings: dict[str, str | tuple[str, ...]] | None = None
    if "issued_manifest_sha256_by_output_sha256" in payload:
        raw_bindings = payload["issued_manifest_sha256_by_output_sha256"]
        normalized_bindings: dict[str, str | tuple[str, ...]] = {}
        if isinstance(raw_bindings, dict):
            for output_hash, raw_manifest_hashes in raw_bindings.items():
                if isinstance(raw_manifest_hashes, str):
                    manifest_hashes = (raw_manifest_hashes,)
                    normalized: str | tuple[str, ...] = raw_manifest_hashes
                elif isinstance(raw_manifest_hashes, list):
                    manifest_hashes = tuple(raw_manifest_hashes)
                    normalized = manifest_hashes
                else:
                    manifest_hashes = ()
                    normalized = ()
                if (
                    not isinstance(output_hash, str)
                    or not _is_sha256(output_hash)
                    or output_hash != output_hash.casefold()
                    or not manifest_hashes
                    or not all(
                        isinstance(manifest_hash, str)
                        and _is_sha256(manifest_hash)
                        and manifest_hash == manifest_hash.casefold()
                        for manifest_hash in manifest_hashes
                    )
                    or len(set(manifest_hashes)) != len(manifest_hashes)
                ):
                    raise IndexTTS2ValidationError(
                        "provenance ledger is unavailable or invalid"
                    )
                normalized_bindings[output_hash] = normalized
        if (
            not isinstance(raw_bindings, dict)
            or set(raw_bindings) != {item.casefold() for item in hashes}
        ):
            raise IndexTTS2ValidationError("provenance ledger is unavailable or invalid")
        manifest_bindings = normalized_bindings
    result = (
        tuple(item.casefold() for item in hashes),
        tuple(
            {
                "voice_id": entry["voice_id"],
                "reference_audio_path": entry["reference_audio_path"],
                "reference_audio_sha256": entry["reference_audio_sha256"].casefold(),
            }
            for entry in canonical_reference_provenance
        ),
        manifest_bindings,
    )
    if include_retired_voice_ids:
        return (*result, frozenset(retired_voice_ids or ()))
    return result


def _validate_canonical_reference_provenance(
    route: TTSRouting,
    canonical_reference_provenance: tuple[dict[str, str], ...],
    *,
    ffprobe: str,
    retired_voice_ids: frozenset[str] = frozenset(),
) -> AudioProbe:
    current_entry = next(
        (
            entry
            for entry in canonical_reference_provenance
            if entry["voice_id"] == CURRENT_VOICE_ID
        ),
        None,
    )
    if (
        current_entry is None
        or not _same_resolved_path(
            Path(current_entry["reference_audio_path"]),
            route.reference_audio_path,
        )
        or current_entry["reference_audio_sha256"].casefold()
        != route.reference_audio_sha256.casefold()
    ):
        raise IndexTTS2ValidationError("canonical provenance does not match the current route")

    current_probe: AudioProbe | None = None
    for entry in canonical_reference_provenance:
        reference_path = Path(entry["reference_audio_path"])
        if not reference_path.is_file():
            unsafe_component = any(
                component.casefold()
                in {"generated", "output", "outputs", "render", "renders"}
                for component in reference_path.parts
            )
            if (
                entry["voice_id"] in retired_voice_ids
                and not reference_path.exists()
                and not unsafe_component
            ):
                continue
            raise IndexTTS2ValidationError("canonical provenance reference is unavailable or unsafe")
        if _contains_generated_directory(reference_path):
            raise IndexTTS2ValidationError("canonical provenance reference is unavailable or unsafe")
        reference = _probe_pcm_wav(reference_path, ffprobe=ffprobe)
        if reference.sha256.casefold() != entry["reference_audio_sha256"].casefold():
            raise IndexTTS2ValidationError("canonical provenance reference hash is invalid")
        if entry["voice_id"] == CURRENT_VOICE_ID:
            current_probe = reference

    if current_probe is None:
        raise IndexTTS2ValidationError("canonical provenance does not contain the current voice")
    return current_probe


def _persist_provenance_ledger(
    path: Path,
    hashes: list[str],
    canonical_reference_provenance: tuple[dict[str, str], ...] = (),
    issued_manifest_sha256_by_output_sha256: Mapping[
        str, str | tuple[str, ...]
    ]
    | None = None,
) -> None:
    """Atomically replace the ledger after its private staging file is flushed."""
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_durable_json(
            temporary_path,
            {
                "schema_version": 1,
                "issued_output_sha256": hashes,
                **(
                    {"canonical_reference_provenance": list(canonical_reference_provenance)}
                    if canonical_reference_provenance
                    else {}
                ),
                **(
                    {
                        "issued_manifest_sha256_by_output_sha256": dict(
                            issued_manifest_sha256_by_output_sha256
                        )
                    }
                    if issued_manifest_sha256_by_output_sha256 is not None
                    else {}
                ),
            },
        )
        os.replace(temporary_path, path)
        with path.open("r+b") as persisted:
            os.fsync(persisted.fileno())
    except OSError as exc:
        raise IndexTTS2ValidationError("provenance ledger could not be registered") from exc
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _register_provenance_manifest_while_locked(
    path: Path,
    output_sha256: str,
    manifest_sha256: str,
) -> tuple[str, ...]:
    """Atomically register an issued output and its exact manifest while locked."""
    loaded_hashes, canonical_reference_provenance, loaded_bindings = _load_provenance_ledger_document(
        path,
        allow_owned_lock=True,
    )
    hashes = list(loaded_hashes)
    issued_hash = output_sha256.casefold()
    issued_manifest_hash = manifest_sha256.casefold()
    if not _is_sha256(issued_hash) or not _is_sha256(issued_manifest_hash):
        raise IndexTTS2ValidationError("provenance ledger could not be registered")
    if loaded_bindings is None and hashes:
        raise IndexTTS2ValidationError("provenance ledger could not be registered")
    bindings = dict(loaded_bindings or {})
    if issued_hash in hashes:
        current = bindings.get(issued_hash)
        accepted = (current,) if isinstance(current, str) else current or ()
        if issued_manifest_hash not in accepted:
            raise IndexTTS2ValidationError("provenance ledger could not be registered")
    else:
        hashes.append(issued_hash)
        bindings[issued_hash] = issued_manifest_hash
    if set(bindings) != set(hashes):
        raise IndexTTS2ValidationError("provenance ledger could not be registered")
    _persist_provenance_ledger(
        path,
        hashes,
        canonical_reference_provenance,
        bindings,
    )
    return tuple(hashes)


def authorize_provenance_manifest_reuse(
    path: Path,
    *,
    output_sha256: str,
    source_manifest_sha256: str,
    reuse_manifest_sha256: str,
) -> tuple[str, ...]:
    """Append one explicit manifest binding for unchanged approved narration bytes."""

    issued_hash = output_sha256.casefold()
    source_hash = source_manifest_sha256.casefold()
    reuse_hash = reuse_manifest_sha256.casefold()
    if not all(_is_sha256(value) for value in (issued_hash, source_hash, reuse_hash)):
        raise IndexTTS2ValidationError("provenance ledger could not be registered")
    lock = _acquire_provenance_ledger_lock(path)
    try:
        hashes, canonical_reference_provenance, loaded_bindings = (
            _load_provenance_ledger_document(path, allow_owned_lock=True)
        )
        if issued_hash not in hashes or loaded_bindings is None:
            raise IndexTTS2ValidationError("provenance ledger could not be registered")
        current = loaded_bindings.get(issued_hash)
        accepted = (current,) if isinstance(current, str) else current or ()
        if source_hash not in accepted:
            raise IndexTTS2ValidationError("provenance ledger could not be registered")
        if reuse_hash not in accepted:
            accepted = (*accepted, reuse_hash)
        bindings = dict(loaded_bindings)
        bindings[issued_hash] = accepted
        _persist_provenance_ledger(
            path,
            list(hashes),
            canonical_reference_provenance,
            bindings,
        )
        return accepted
    finally:
        _release_provenance_ledger_lock(lock)


def _artifact_token(
    path: Path,
    *,
    capture_snapshot: bool = False,
) -> _PublishedArtifact:
    digest = hashlib.sha256()
    chunks: list[bytes] | None = [] if capture_snapshot else None
    try:
        with path.open("rb") as source:
            details = os.fstat(source.fileno())
            while block := source.read(1024 * 1024):
                digest.update(block)
                if chunks is not None:
                    chunks.append(block)
    except OSError as exc:
        raise IndexTTS2ValidationError("final publication could not be verified") from exc
    return _PublishedArtifact(
        path=path,
        device=details.st_dev,
        inode=details.st_ino,
        sha256=digest.hexdigest(),
        snapshot=b"".join(chunks) if chunks is not None else None,
    )


def _publish_no_clobber(
    staged_file: Path,
    target_path: Path,
    *,
    capture_snapshot: bool = False,
) -> _PublishedArtifact:
    staged_token = _artifact_token(staged_file)
    try:
        os.link(staged_file, target_path)
    except FileExistsError as exc:
        raise IndexTTS2ValidationError("final publish target already exists") from exc
    except OSError as exc:
        raise IndexTTS2ValidationError("final no-clobber publication failed") from exc
    try:
        target_token = _artifact_token(
            target_path,
            capture_snapshot=capture_snapshot,
        )
    except IndexTTS2ValidationError as exc:
        raise IndexTTS2ValidationError("final publication ownership verification failed") from exc
    if not (
        target_token.device == staged_token.device
        and target_token.inode == staged_token.inode
        and target_token.sha256.casefold() == staged_token.sha256.casefold()
    ):
        raise IndexTTS2ValidationError("final publication ownership verification failed")
    return target_token


def _prior_manifest_evidence(manifest_path: Path) -> _PriorManifestEvidence | None:
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        output_path = payload["output_path"]
        output_sha256 = payload["output_sha256"]
    except (OSError, UnicodeDecodeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IndexTTS2ValidationError("existing voice manifest is invalid") from exc
    if not isinstance(output_path, str) or not output_path or not _is_sha256(output_sha256):
        raise IndexTTS2ValidationError("existing voice manifest is invalid")
    return _PriorManifestEvidence(
        output_path=Path(output_path),
        output_sha256=output_sha256.casefold(),
    )


def _reject_duplicate_json_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    document: dict[object, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _read_manifest_snapshot(manifest_path: Path) -> tuple[dict[str, Any], str]:
    try:
        with manifest_path.open("rb") as source:
            snapshot = source.read()
        payload = json.loads(
            snapshot.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise IndexTTS2ValidationError("output is uncommitted without a valid manifest") from exc
    if not isinstance(payload, dict):
        raise IndexTTS2ValidationError("output is uncommitted without a valid manifest")
    return payload, hashlib.sha256(snapshot).hexdigest()


def _assert_committed_manifest(
    output_wav: Path,
    payload: Mapping[str, Any],
    *,
    output_sha256: str,
) -> None:
    if (
        set(payload) != set(VoiceManifest.__dataclass_fields__)
        or not isinstance(payload.get("output_path"), str)
        or not _is_sha256(payload.get("output_sha256"))
        or not _same_resolved_path(Path(payload["output_path"]), output_wav)
        or payload["output_sha256"].casefold() != output_sha256.casefold()
    ):
        raise IndexTTS2ValidationError("output is uncommitted without a valid manifest")


def _assert_published_output_hash(output_wav: Path, expected_sha256: str) -> None:
    if not output_wav.is_file() or _sha256(output_wav).casefold() != expected_sha256.casefold():
        raise IndexTTS2ValidationError("published output changed during render")


def _assert_published_manifest_binding(
    output_wav: Path,
    manifest: VoiceManifest,
    manifest_publication: _PublishedArtifact,
) -> None:
    """Validate the manifest from the one identity-bound bytes snapshot at publish."""
    try:
        if manifest_publication.snapshot is None:
            raise ValueError("manifest snapshot is unavailable")
        payload = json.loads(manifest_publication.snapshot)
    except (TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise IndexTTS2ValidationError("published manifest changed during render") from exc
    if payload != manifest.to_dict():
        raise IndexTTS2ValidationError("published manifest changed during render")
    _assert_published_output_hash(output_wav, manifest.output_sha256)


def _assert_locked_route(route: TTSRouting) -> None:
    if (
        route.provider != "indextts2-local"
        or route.model != "IndexTTS2"
        or route.voice_id != CURRENT_VOICE_ID
        or route.playback_speed != 1.12
        or route.fp16 is not True
        or route.deepspeed is not False
        or route.cuda_kernel is not False
        or route.accel is not False
        or route.torch_compile is not False
        or route.used_fallback is not False
    ):
        raise IndexTTS2ValidationError("TTS route violates the locked local contract")
    if not _same_resolved_path(route.interpreter_path, LOCKED_INTERPRETER_PATH):
        raise IndexTTS2ValidationError("TTS route interpreter is not the locked production binary")
    if not _same_resolved_path(route.cli_script_path, LOCKED_CLI_SCRIPT_PATH):
        raise IndexTTS2ValidationError("TTS route CLI script is not the locked production script")
    if not _same_resolved_path(route.model_dir, LOCKED_MODEL_DIR):
        raise IndexTTS2ValidationError("TTS route model directory is not the locked production directory")
    if not _same_resolved_path(
        route.provenance_ledger_path,
        LOCKED_PROVENANCE_LEDGER_PATH,
    ):
        raise IndexTTS2ValidationError("TTS route provenance ledger is not the locked private ledger")


def _build_batch_argv(
    route: TTSRouting,
    prepared_batch: Path,
    raw_dir: Path,
    reference_snapshot: Path | None = None,
) -> list[str]:
    """Return the complete direct official IndexTTS2 batch invocation."""
    return [
        str(route.interpreter_path),
        str(route.cli_script_path),
        "batch",
        "--batch-file",
        str(prepared_batch),
        "--voice",
        str(reference_snapshot or route.reference_audio_path),
        "--model-dir",
        str(route.model_dir),
        "--output-dir",
        str(raw_dir),
        "--output-prefix",
        "segment",
        "--fp16",
        "--no-deepspeed",
        "--no-cuda-kernel",
        "--no-accel",
        "--no-torch-compile",
    ]


def _load_lexicon(lexicon_bytes: bytes) -> dict[str, Mapping[str, Any]]:
    try:
        payload = json.loads(lexicon_bytes.decode("utf-8"))
        terms = payload["terms"]
    except (UnicodeDecodeError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IndexTTS2ValidationError("pronunciation contract is unavailable") from exc
    if not isinstance(terms, dict):
        raise IndexTTS2ValidationError("pronunciation contract is invalid")

    normalized: dict[str, Mapping[str, Any]] = {}
    for display, definition in terms.items():
        if (
            not isinstance(display, str)
            or not display
            or not isinstance(definition, Mapping)
            or not isinstance(definition.get("tts"), str)
            or not definition["tts"].strip()
            or not isinstance(definition.get("forbidden_tts"), list)
            or not all(isinstance(item, str) and item for item in definition["forbidden_tts"])
        ):
            raise IndexTTS2ValidationError("pronunciation contract is invalid")
        normalized[display] = definition
    return normalized


def _parse_segments(
    batch_bytes: bytes,
    lexicon_bytes: bytes,
) -> tuple[NarrationSegment, ...]:
    try:
        lines = batch_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise IndexTTS2ValidationError("narration batch is unavailable") from exc
    if not lines:
        raise IndexTTS2ValidationError("narration batch must not be empty")

    lexicon = _load_lexicon(lexicon_bytes)
    forbidden = tuple(
        item
        for definition in lexicon.values()
        for item in definition["forbidden_tts"]
    )
    segments: list[NarrationSegment] = []
    allowed_fields = {"text", "emotion_vector", "emotion_weight", "silence_after_ms"}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise IndexTTS2ValidationError("narration batch contains an empty line")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IndexTTS2ValidationError("narration batch contains malformed JSON") from exc
        if not isinstance(payload, dict):
            raise IndexTTS2ValidationError("narration batch lines must be JSON objects")
        if set(payload) - allowed_fields:
            raise IndexTTS2ValidationError("narration batch contains unsafe output routing")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise IndexTTS2ValidationError("narration batch text must not be empty")
        if any(token in text for token in forbidden):
            raise IndexTTS2ValidationError("narration batch contains a forbidden pronunciation")

        emotion_vector = payload.get("emotion_vector")
        if emotion_vector is not None:
            if (
                not isinstance(emotion_vector, list)
                or len(emotion_vector) != 8
                or any(isinstance(value, bool) for value in emotion_vector)
            ):
                raise IndexTTS2ValidationError("emotion_vector is unsupported")
            try:
                emotion_vector = tuple(float(value) for value in emotion_vector)
            except (TypeError, ValueError) as exc:
                raise IndexTTS2ValidationError("emotion_vector is unsupported") from exc
            if (
                any(not math.isfinite(value) or value < 0.0 or value > 1.0 for value in emotion_vector)
                or sum(emotion_vector) > 0.8
            ):
                raise IndexTTS2ValidationError("emotion_vector is unsupported")

        emotion_weight = payload.get("emotion_weight")
        if emotion_weight is not None:
            if isinstance(emotion_weight, bool):
                raise IndexTTS2ValidationError("emotion_weight is unsupported")
            try:
                emotion_weight = float(emotion_weight)
            except (TypeError, ValueError) as exc:
                raise IndexTTS2ValidationError("emotion_weight is unsupported") from exc
            if not math.isfinite(emotion_weight) or emotion_weight < 0.0:
                raise IndexTTS2ValidationError("emotion_weight is unsupported")

        silence_after_ms = payload.get("silence_after_ms", 0)
        if (
            isinstance(silence_after_ms, bool)
            or not isinstance(silence_after_ms, int)
            or not 0 <= silence_after_ms <= MAX_SILENCE_AFTER_MS
        ):
            raise IndexTTS2ValidationError("silence_after_ms is unsupported")

        tts_text = text
        for display, definition in lexicon.items():
            tts_text = tts_text.replace(display, str(definition["tts"]))
        segments.append(
            NarrationSegment(
                text=text,
                tts_text=tts_text,
                emotion_vector=emotion_vector,
                emotion_weight=emotion_weight,
                silence_after_ms=silence_after_ms,
            )
        )
    return tuple(segments)


def _read_snapshot_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise IndexTTS2ValidationError(f"{label} is unavailable") from exc


def _capture_narration_snapshot(
    batch_file: Path,
    lexicon_path: Path,
) -> _NarrationSnapshot:
    batch_bytes = _read_snapshot_bytes(batch_file, "narration batch")
    lexicon_bytes = _read_snapshot_bytes(lexicon_path, "pronunciation contract")
    return _NarrationSnapshot(
        batch_path=batch_file,
        batch_bytes=batch_bytes,
        batch_sha256=hashlib.sha256(batch_bytes).hexdigest(),
        lexicon_path=lexicon_path,
        lexicon_bytes=lexicon_bytes,
        lexicon_sha256=hashlib.sha256(lexicon_bytes).hexdigest(),
        segments=_parse_segments(batch_bytes, lexicon_bytes),
    )


def _assert_narration_snapshot_unchanged(snapshot: _NarrationSnapshot) -> None:
    try:
        if (
            snapshot.batch_path.read_bytes() != snapshot.batch_bytes
            or snapshot.lexicon_path.read_bytes() != snapshot.lexicon_bytes
        ):
            raise IndexTTS2ValidationError("narration input changed during render")
    except OSError as exc:
        raise IndexTTS2ValidationError("narration input changed during render") from exc


def _stage_reference_snapshot(
    source_path: Path,
    destination_path: Path,
    *,
    expected_sha256: str,
    ffprobe: str,
) -> None:
    try:
        shutil.copyfile(source_path, destination_path)
    except OSError as exc:
        raise IndexTTS2ValidationError("reference audio snapshot is unavailable") from exc
    snapshot = _probe_pcm_wav(destination_path, ffprobe=ffprobe)
    if snapshot.sha256.casefold() != expected_sha256.casefold():
        raise IndexTTS2ValidationError("reference audio changed during render")


def _write_silence_wav(path: Path, *, sample_rate: int, channels: int, duration_ms: int) -> None:
    import wave

    frame_count = sample_rate * duration_ms // 1000
    with wave.open(str(path), "wb") as silence:
        silence.setnchannels(channels)
        silence.setsampwidth(2)
        silence.setframerate(sample_rate)
        silence.writeframes(b"\0\0" * channels * frame_count)


class IndexTTS2Narrator:
    """Validate the locked local IndexTTS2 environment without model loading."""

    def __init__(self, route: TTSRouting, pronunciation_lexicon_path: Path | None = None) -> None:
        _assert_locked_route(route)
        self.route = route
        self._boundary = _NarrationBoundary(
            workspace_root=WORKSPACE_ROOT,
            private_voice_directory=LOCKED_PRIVATE_VOICE_DIRECTORY,
            model_dir=LOCKED_MODEL_DIR,
        )
        self.pronunciation_lexicon_path = pronunciation_lexicon_path or (
            Path(__file__).resolve().parents[3]
            / "automation"
            / "config"
            / "pronunciation-lexicon.json"
        )

    def load_segments(self, batch_file: Path) -> tuple[NarrationSegment, ...]:
        self._validate_input_boundary(batch_file)
        return _capture_narration_snapshot(
            batch_file,
            self.pronunciation_lexicon_path,
        ).segments

    def dry_run(self, batch_file: Path) -> DryRunValidation:
        self._validate_input_boundary(batch_file)
        snapshot = _capture_narration_snapshot(
            batch_file,
            self.pronunciation_lexicon_path,
        )
        environment = self.validate_environment()
        return DryRunValidation(
            reference=environment.reference,
            segment_count=len(snapshot.segments),
            segment_contract_sha256=snapshot.batch_sha256,
        )

    def probe_existing_wav(self, wav_path: Path) -> AudioProbe:
        if not wav_path.is_file():
            raise IndexTTS2ValidationError("existing WAV is unavailable")
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise IndexTTS2ValidationError("ffprobe is required")
        return _probe_pcm_wav(wav_path, ffprobe=ffprobe)

    def probe_committed_wav(self, wav_path: Path, manifest_path: Path) -> AudioProbe:
        """Probe only a WAV that has its matching manifest commit marker."""
        probe = self.probe_existing_wav(wav_path)
        manifest_payload, manifest_sha256 = _read_manifest_snapshot(manifest_path)
        _assert_committed_manifest(
            wav_path,
            manifest_payload,
            output_sha256=probe.sha256,
        )
        issued_hashes, _canonical_reference_provenance, manifest_bindings = (
            _load_provenance_ledger_document(self.route.provenance_ledger_path)
        )
        if (
            manifest_bindings is None
            or probe.sha256.casefold() not in issued_hashes
            or manifest_bindings.get(probe.sha256.casefold())
            != manifest_sha256.casefold()
        ):
            raise IndexTTS2ValidationError("output is uncommitted without a valid manifest")
        return probe

    def _run_index_tts_batch(
        self,
        prepared_batch: Path,
        raw_dir: Path,
        reference_snapshot: Path,
    ) -> None:
        command = _build_batch_argv(
            self.route,
            prepared_batch,
            raw_dir,
            reference_snapshot,
        )
        self._validate_execution_topology()
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise IndexTTS2ValidationError("IndexTTS2 batch process is unavailable") from exc
        if result.returncode != 0:
            raise IndexTTS2ValidationError("IndexTTS2 batch process failed")

    def _after_narration_snapshot(self) -> None:
        """Testing seam for deterministic source-mutation checks."""

    def _before_publish(self) -> None:
        """Testing seam for deterministic reference-mutation checks."""

    def _before_output_publish(self, staged_output: Path) -> None:
        """Testing seam for no-clobber final-output collisions."""

    def _before_manifest_publish(self, staged_manifest: Path) -> None:
        """Testing seam for committed-output replacement checks."""

    def _validate_execution_topology(self) -> None:
        if not _same_resolved_path(self.route.model_dir, self._boundary.model_dir):
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        for path in (self.route.interpreter_path, self.route.cli_script_path):
            _reject_unsafe_path(path)
        if any(
            not _is_ordinary_file(path)
            for path in (self.route.interpreter_path, self.route.cli_script_path)
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        if _has_reparse_component(self.route.model_dir) or not _is_ordinary_directory(
            self.route.model_dir
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")

    def _validate_environment_boundary(self) -> None:
        boundary = self._boundary
        workspace_root = boundary.workspace_root
        private_voice_directory = boundary.private_voice_directory
        for path in (
            self.route.reference_audio_path,
            self.route.provenance_ledger_path,
            self.pronunciation_lexicon_path,
        ):
            _reject_unsafe_path(path)
        self._validate_execution_topology()
        if any(
            not _is_within(path, workspace_root)
            for path in (
                self.route.reference_audio_path,
                self.route.provenance_ledger_path,
                self.pronunciation_lexicon_path,
            )
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        if any(
            not _is_within(path, private_voice_directory)
            for path in (self.route.reference_audio_path, self.route.provenance_ledger_path)
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        if any(
            not _is_ordinary_file(path)
            for path in (
                self.route.reference_audio_path,
                self.route.provenance_ledger_path,
                self.pronunciation_lexicon_path,
            )
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        if any(
            not _path_is_git_ignored(path, workspace_root)
            or _path_is_tracked(path, workspace_root)
            for path in (
                self.route.reference_audio_path,
                self.route.provenance_ledger_path,
            )
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")

    def _validate_input_boundary(self, batch_file: Path) -> None:
        self._validate_environment_boundary()
        boundary = self._boundary
        _reject_unsafe_path(batch_file)
        if (
            not _is_within(batch_file, boundary.workspace_root)
            or not _is_ordinary_file(batch_file)
            or not _path_is_git_ignored(batch_file, boundary.workspace_root)
            or _path_is_tracked(batch_file, boundary.workspace_root)
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")

    def _validate_render_boundary(
        self,
        batch_file: Path,
        output_wav: Path,
        manifest_path: Path,
        raw_audit_wav: Path,
    ) -> None:
        self._validate_input_boundary(batch_file)
        boundary = self._boundary
        reference_path = self.route.reference_audio_path.resolve(strict=True)
        if _same_resolved_path(output_wav, reference_path):
            raise IndexTTS2ValidationError("output cannot equal reference audio")
        if _same_resolved_path(manifest_path, reference_path):
            raise IndexTTS2ValidationError("manifest cannot equal reference audio")
        if _same_resolved_path(manifest_path, output_wav):
            raise IndexTTS2ValidationError("manifest cannot equal output WAV")
        for path in (output_wav, manifest_path, raw_audit_wav):
            _reject_unsafe_path(path)
        if len(
            {
                str(path.resolve(strict=False)).casefold()
                for path in (output_wav, manifest_path, raw_audit_wav)
            }
        ) != 3:
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        if any(not _is_within(path, boundary.workspace_root) for path in (output_wav, manifest_path, raw_audit_wav)):
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        if len(
            {
                str(path.parent.resolve(strict=False)).casefold()
                for path in (output_wav, manifest_path, raw_audit_wav)
            }
        ) != 1 or (output_wav.parent.exists() and not _is_ordinary_directory(output_wav.parent)):
            raise IndexTTS2ValidationError("private narration boundary is invalid")
        if any(
            not _path_is_git_ignored(path, boundary.workspace_root)
            or _path_is_tracked(path, boundary.workspace_root)
            for path in (output_wav, manifest_path, raw_audit_wav)
        ):
            raise IndexTTS2ValidationError("private narration boundary is invalid")

    def _assert_reference_unchanged(self, expected_sha256: str) -> AudioProbe:
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise IndexTTS2ValidationError("ffprobe is required")
        if not self.route.reference_audio_path.is_file() or _contains_generated_directory(
            self.route.reference_audio_path
        ):
            raise IndexTTS2ValidationError("reference audio changed during render")
        reference = _probe_pcm_wav(self.route.reference_audio_path, ffprobe=ffprobe)
        if reference.sha256.casefold() != expected_sha256.casefold():
            raise IndexTTS2ValidationError("reference audio changed during render")
        return reference

    def _concatenate_and_tempo(
        self,
        raw_wavs: tuple[Path, ...],
        segments: tuple[NarrationSegment, ...],
        output_path: Path,
        raw_audit_path: Path,
    ) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise IndexTTS2ValidationError("ffmpeg is required")

        inputs: list[Path] = []
        raw_probes = tuple(self.probe_existing_wav(raw_wav) for raw_wav in raw_wavs)
        if not raw_probes:
            raise IndexTTS2ValidationError("IndexTTS2 produced no raw WAV segments")
        target_probe = raw_probes[0]
        channel_layout = {1: "mono", 2: "stereo"}.get(target_probe.channels)
        if channel_layout is None:
            raise IndexTTS2ValidationError("raw WAV channel layout is unsupported")

        for index, ((raw_wav, segment), probe) in enumerate(
            zip(zip(raw_wavs, segments), raw_probes),
            start=1,
        ):
            inputs.append(raw_wav)
            if segment.silence_after_ms:
                silence_path = raw_wav.with_name(f"silence-{index:04d}.wav")
                _write_silence_wav(
                    silence_path,
                    sample_rate=probe.sample_rate,
                    channels=probe.channels,
                    duration_ms=segment.silence_after_ms,
                )
                inputs.append(silence_path)

        normalized_labels = []
        normalization_filters = []
        for index in range(len(inputs)):
            label = f"normalized{index}"
            normalized_labels.append(f"[{label}]")
            normalization_filters.append(
                f"[{index}:a]aresample={target_probe.sample_rate},"
                f"aformat=sample_fmts=s16:channel_layouts={channel_layout}[{label}]"
            )
        filter_graph = (
            f"{';'.join(normalization_filters)};"
            f"{''.join(normalized_labels)}concat=n={len(inputs)}:v=0:a=1[concatenated];"
            f"[concatenated]asplit=2[raw-audit][tempo-input];"
            f"[tempo-input]atempo={self.route.playback_speed}[narration]"
        )
        command = [
            ffmpeg,
            *[argument for input_path in inputs for argument in ("-i", str(input_path))],
            "-filter_complex",
            filter_graph,
            "-map",
            "[raw-audit]",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(raw_audit_path),
            "-map",
            "[narration]",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(output_path),
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise IndexTTS2ValidationError("ffmpeg concatenation is unavailable") from exc
        if result.returncode != 0 or not output_path.is_file() or not raw_audit_path.is_file():
            raise IndexTTS2ValidationError("ffmpeg concatenation failed")

    def render(self, batch_file: Path, output_wav: Path, manifest_path: Path) -> VoiceManifest:
        raw_audit_wav = output_wav.with_name(f"{output_wav.stem}.raw.wav")
        self._validate_render_boundary(batch_file, output_wav, manifest_path, raw_audit_wav)
        snapshot = _capture_narration_snapshot(
            batch_file,
            self.pronunciation_lexicon_path,
        )
        environment = self.validate_environment()
        provenance_hashes = _load_provenance_ledger(self.route.provenance_ledger_path)
        if environment.reference.sha256.casefold() in provenance_hashes:
            raise IndexTTS2ValidationError(
                "reference audio matches a provenance ledger output hash"
            )
        reference_path = self.route.reference_audio_path.resolve(strict=True)
        if _same_resolved_path(output_wav, reference_path):
            raise IndexTTS2ValidationError("output cannot equal reference audio")
        if _same_resolved_path(manifest_path, reference_path):
            raise IndexTTS2ValidationError("manifest cannot equal reference audio")
        if _same_resolved_path(manifest_path, output_wav):
            raise IndexTTS2ValidationError("manifest cannot equal output WAV")
        prior_manifest = _prior_manifest_evidence(manifest_path)
        if prior_manifest is not None and (
            _same_resolved_path(prior_manifest.output_path, reference_path)
            or prior_manifest.output_sha256 == environment.reference.sha256.casefold()
        ):
            raise IndexTTS2ValidationError(
                "reference audio matches a prior manifest output hash"
            )
        if prior_manifest is not None:
            raise IndexTTS2ValidationError("voice manifest already exists")
        if output_wav.exists():
            raise IndexTTS2ValidationError("output WAV already exists")
        if raw_audit_wav.exists():
            raise IndexTTS2ValidationError("raw audit WAV already exists")
        segments = snapshot.segments
        output_workspace = output_wav.parent / f".{output_wav.name}.indextts2-{uuid.uuid4().hex}"
        manifest_workspace = manifest_path.parent / f".{manifest_path.name}.indextts2-{uuid.uuid4().hex}"
        raw_dir = output_workspace / "raw"
        prepared_batch = output_workspace / "narration.jsonl"
        reference_snapshot = output_workspace / "reference.wav"
        temporary_output = output_workspace / "final.wav"
        temporary_raw_audit = output_workspace / "raw-audit.wav"
        staged_manifest = manifest_workspace / "voice_manifest.json"
        try:
            raw_dir.mkdir(parents=True)
            manifest_workspace.mkdir(parents=True)
            ffprobe = shutil.which("ffprobe")
            if ffprobe is None:
                raise IndexTTS2ValidationError("ffprobe is required")
            self._assert_reference_unchanged(environment.reference.sha256)
            _stage_reference_snapshot(
                self.route.reference_audio_path,
                reference_snapshot,
                expected_sha256=environment.reference.sha256,
                ffprobe=ffprobe,
            )
            prepared_batch.write_text(
                "".join(
                    json.dumps(
                        {
                            **{"text": segment.tts_text},
                            **(
                                {"emotion_vector": list(segment.emotion_vector)}
                                if segment.emotion_vector is not None
                                else {}
                            ),
                            **(
                                {"emotion_weight": segment.emotion_weight}
                                if segment.emotion_weight is not None
                                else {}
                            ),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                    for segment in segments
                ),
                encoding="utf-8",
            )
            self._after_narration_snapshot()
            _assert_narration_snapshot_unchanged(snapshot)
            self._assert_reference_unchanged(environment.reference.sha256)
            self._run_index_tts_batch(prepared_batch, raw_dir, reference_snapshot)
            raw_wavs = tuple(raw_dir / f"segment-{index:04d}.wav" for index in range(1, len(segments) + 1))
            self._concatenate_and_tempo(
                raw_wavs,
                segments,
                temporary_output,
                temporary_raw_audit,
            )
            manifest = VoiceManifest(
                provider=self.route.provider,
                voice_id=self.route.voice_id,
                model=self.route.model,
                reference_audio_path=str(self.route.reference_audio_path.resolve(strict=True)),
                reference_audio_sha256=environment.reference.sha256,
                output_path=str(output_wav.resolve(strict=False)),
                output_sha256=_sha256(temporary_output),
                segment_contract_path=str(snapshot.batch_path.resolve(strict=True)),
                segment_contract_sha256=snapshot.batch_sha256,
                segment_count=len(segments),
                playback_speed=self.route.playback_speed,
                pronunciation_contract_path=str(snapshot.lexicon_path.resolve(strict=True)),
                pronunciation_contract_sha256=snapshot.lexicon_sha256,
                used_fallback=self.route.used_fallback,
            )
            _write_durable_json(staged_manifest, manifest.to_dict(), indent=2)
            self._before_publish()
            _assert_narration_snapshot_unchanged(snapshot)
            self._assert_reference_unchanged(environment.reference.sha256)
            ledger_lock = _acquire_provenance_ledger_lock(self.route.provenance_ledger_path)
            try:
                current_hashes = _load_provenance_ledger(
                    self.route.provenance_ledger_path,
                    allow_owned_lock=True,
                )
                if environment.reference.sha256.casefold() in current_hashes:
                    raise IndexTTS2ValidationError(
                        "reference audio matches a provenance ledger output hash"
                    )
                _assert_narration_snapshot_unchanged(snapshot)
                self._assert_reference_unchanged(environment.reference.sha256)
                _register_provenance_manifest_while_locked(
                    self.route.provenance_ledger_path,
                    manifest.output_sha256,
                    _sha256(staged_manifest),
                )
                _publish_no_clobber(temporary_raw_audit, raw_audit_wav)
                self._before_output_publish(temporary_output)
                _publish_no_clobber(temporary_output, output_wav)
                _assert_published_output_hash(output_wav, manifest.output_sha256)
                self._before_manifest_publish(staged_manifest)
                _assert_published_output_hash(output_wav, manifest.output_sha256)
                manifest_publication = _publish_no_clobber(
                    staged_manifest,
                    manifest_path,
                    capture_snapshot=True,
                )
                _assert_published_manifest_binding(
                    output_wav,
                    manifest,
                    manifest_publication,
                )
            finally:
                _release_provenance_ledger_lock(ledger_lock)
            return manifest
        finally:
            shutil.rmtree(output_workspace, ignore_errors=True)
            shutil.rmtree(manifest_workspace, ignore_errors=True)

    def validate_environment(self) -> EnvironmentValidation:
        self._validate_environment_boundary()
        required_files = (
            self.route.interpreter_path,
            self.route.cli_script_path,
            self.route.reference_audio_path,
        )
        if any(not path.is_file() for path in required_files):
            raise IndexTTS2ValidationError("a required local file is unavailable")
        if not self.route.model_dir.is_dir():
            raise IndexTTS2ValidationError("IndexTTS2 model directory is unavailable")

        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            raise IndexTTS2ValidationError("ffmpeg and ffprobe are required")

        _run_available([str(self.route.interpreter_path), "--version"])
        _run_available([str(self.route.interpreter_path), str(self.route.cli_script_path), "batch", "--help"])
        _run_available([ffmpeg, "-version"])
        _run_available([ffprobe, "-version"])
        if _contains_generated_directory(self.route.reference_audio_path):
            raise IndexTTS2ValidationError(
                "reference audio cannot be inside an output or render directory"
            )
        (
            _,
            canonical_reference_provenance,
            _manifest_bindings,
            retired_voice_ids,
        ) = _load_provenance_ledger_document(
            self.route.provenance_ledger_path,
            include_retired_voice_ids=True,
        )
        reference = _validate_canonical_reference_provenance(
            self.route,
            canonical_reference_provenance,
            ffprobe=ffprobe,
            retired_voice_ids=retired_voice_ids,
        )
        return EnvironmentValidation(reference=reference)

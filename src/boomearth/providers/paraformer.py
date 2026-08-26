"""Private, redacted DashScope Paraformer source transcription boundary."""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
import subprocess
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import httpx

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from dashscope.audio.asr import Recognition

from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.source_artifacts import (
    SourceContractError,
    publish_json_exclusive,
    verify_private_relative,
)


PARAFORMER_MODEL = "paraformer-realtime-v2"
_SUPPORTED_EXTENSIONS = frozenset({".wav", ".mp3"})
_SUPPORTED_SAMPLE_RATES = frozenset({8000, 16000})
_HASH_CHUNK_SIZE = 1024 * 1024
_CONTAINER_ALIASES = {
    "wav": "wav",
    "wave": "wav",
    "mp3": "mp3",
}
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


class ParaformerError(RuntimeError):
    """Base class for safe Paraformer failures."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"


class ParaformerCredentialError(ParaformerError):
    """Raised when the DashScope credential is unavailable."""

    def __init__(self) -> None:
        super().__init__("DashScope credential is unavailable")


class ParaformerInputError(ParaformerError):
    """Raised when the local audio input is not accepted."""


class ParaformerMetadataError(ParaformerInputError):
    """Raised when ffprobe cannot provide accepted audio metadata."""


class ParaformerRecognitionError(ParaformerError):
    """Raised for redacted SDK, transport, and recognition failures."""

    def __init__(self, request_id: str | None) -> None:
        self.request_id = request_id or "UNAVAILABLE"
        super().__init__(
            f"Paraformer recognition failed; request_id={self.request_id}"
        )


class ParaformerServiceError(ParaformerRecognitionError):
    """Raised for redacted provider-side recognition rejections."""


class ParaformerTransportError(ParaformerRecognitionError):
    """Raised when the recognition SDK cannot complete transport."""


class ParaformerTimeoutError(ParaformerRecognitionError):
    """Raised when the recognition SDK exceeds its request timeout."""


class ParaformerArtifactError(ParaformerError):
    """Raised when the private transcript artifact cannot be written."""

    def __init__(self) -> None:
        super().__init__("Paraformer transcript artifact unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class SourceTranscript:
    """The only source-transcript data that crosses the public boundary."""

    duration: float
    segment_count: int
    language: str
    artifact_id: str

    def __repr__(self) -> str:
        return (
            "SourceTranscript("
            f"duration={self.duration!r}, "
            f"segment_count={self.segment_count!r}, "
            f"language={self.language!r}, "
            f"artifact_id={self.artifact_id!r})"
        )


@dataclass(frozen=True, slots=True)
class _AudioMetadata:
    format: str
    sample_rate: int
    duration: float


def _audio_source_id(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while True:
                block = source.read(_HASH_CHUNK_SIZE)
                if not block:
                    break
                digest.update(block)
    except OSError:
        raise ParaformerInputError("audio unavailable") from None
    return digest.hexdigest()[:12]


def _normalize_container(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    for name in value.split(","):
        normalized = _CONTAINER_ALIASES.get(name.strip().lower())
        if normalized is not None:
            return normalized
    return None


def _probe_audio(path: Path) -> _AudioMetadata:
    """Read only the audio metadata needed by the realtime SDK."""

    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "format=format_name,duration:stream=sample_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise ParaformerMetadataError("ffprobe unavailable") from None

    if result.returncode != 0:
        raise ParaformerMetadataError("audio metadata unavailable")

    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        format_data = payload["format"]
        audio_format = _normalize_container(format_data["format_name"])
        raw_sample_rate = streams[0]["sample_rate"]
        raw_duration = format_data["duration"]
        sample_rate = int(raw_sample_rate)
        duration = float(raw_duration)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        raise ParaformerMetadataError("audio metadata invalid") from None

    if (
        audio_format is None
        or sample_rate not in _SUPPORTED_SAMPLE_RATES
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise ParaformerMetadataError("audio metadata unsupported")

    return _AudioMetadata(
        format=audio_format,
        sample_rate=sample_rate,
        duration=duration,
    )


def _is_reparse_point(file_stat: object) -> bool:
    attributes = int(getattr(file_stat, "st_file_attributes", 0))
    return stat.S_ISLNK(getattr(file_stat, "st_mode", 0)) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def validate_local_audio(path: Path) -> tuple[Path, _AudioMetadata]:
    """Validate local Paraformer input without constructing a client or making a request."""

    try:
        audio_path = Path(path).absolute()
    except (TypeError, ValueError):
        raise ParaformerInputError("audio path invalid") from None

    current = audio_path
    while True:
        try:
            file_stat = current.lstat()
        except OSError:
            raise ParaformerInputError("audio file does not exist") from None
        if _is_reparse_point(file_stat):
            raise ParaformerInputError("audio input must not use a reparse point")
        if current == current.parent:
            break
        current = current.parent

    try:
        file_stat = audio_path.lstat()
    except OSError:
        raise ParaformerInputError("audio file does not exist") from None
    if not stat.S_ISREG(file_stat.st_mode):
        raise ParaformerInputError("audio input is not a file")
    if file_stat.st_size <= 0:
        raise ParaformerInputError("audio input is empty")

    extension = audio_path.suffix.lower()
    if extension not in _SUPPORTED_EXTENSIONS:
        raise ParaformerInputError("audio format unsupported")

    metadata = _probe_audio(audio_path)
    try:
        sample_rate = int(metadata.sample_rate)
        duration = float(metadata.duration)
        audio_format = str(metadata.format).lower().lstrip(".")
    except (AttributeError, TypeError, ValueError):
        raise ParaformerMetadataError("audio metadata invalid") from None

    if (
        audio_format != extension.lstrip(".")
        or sample_rate not in _SUPPORTED_SAMPLE_RATES
        or not math.isfinite(duration)
        or duration < 0
    ):
        raise ParaformerMetadataError("audio metadata unsupported")

    return audio_path, _AudioMetadata(audio_format, sample_rate, duration)


_validate_audio = validate_local_audio


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    request_id = str(value)
    if _REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        return None
    return request_id


def _response_value(response: object, name: str) -> object:
    if isinstance(response, Mapping):
        return response.get(name)
    return getattr(response, name, None)


def _response_request_id(response: object) -> str | None:
    request_id = _safe_request_id(_response_value(response, "request_id"))
    if request_id is not None:
        return request_id

    getter = getattr(response, "get_request_id", None)
    if callable(getter):
        try:
            return _safe_request_id(getter())
        except Exception:
            return None
    return None


def _recognition_request_id(recognition: object | None) -> str | None:
    if recognition is None:
        return None
    request_id = _safe_request_id(getattr(recognition, "last_request_id", None))
    if request_id is not None:
        return request_id
    getter = getattr(recognition, "get_last_request_id", None)
    if callable(getter):
        try:
            return _safe_request_id(getter())
        except Exception:
            return None
    return None


def _response_success(response: object) -> bool:
    status_code = _response_value(response, "status_code")
    if status_code is not None:
        try:
            if int(status_code) != 200:
                return False
        except (TypeError, ValueError):
            return False

    code = _response_value(response, "code")
    if code not in (None, "", 0, "0", 200, "200"):
        return False
    return True


def _response_output(response: object) -> Mapping[str, Any]:
    output = _response_value(response, "output")
    if isinstance(output, Mapping):
        return output
    return {}


def _response_sentences(response: object) -> list[dict[str, Any]]:
    getter = getattr(response, "get_sentence", None)
    sentence_value: object = None
    if callable(getter):
        try:
            sentence_value = getter()
        except Exception:
            sentence_value = None

    if sentence_value is None:
        output = _response_output(response)
        sentence_value = output.get("sentence", output.get("sentences"))

    if sentence_value is None:
        return []
    if isinstance(sentence_value, Mapping):
        sentence_value = [sentence_value]
    if not isinstance(sentence_value, (list, tuple)):
        raise ParaformerRecognitionError(None)

    sentences: list[dict[str, Any]] = []
    for sentence in sentence_value:
        if not isinstance(sentence, Mapping):
            raise ParaformerRecognitionError(None)
        try:
            copied = json.loads(json.dumps(sentence, ensure_ascii=False))
        except (TypeError, ValueError):
            raise ParaformerRecognitionError(None) from None
        if not isinstance(copied, dict):
            raise ParaformerRecognitionError(None)
        sentences.append(copied)
    return sentences


def _response_language(response: object) -> str:
    output = _response_output(response)
    candidates = (
        _response_value(response, "language"),
        output.get("language"),
        output.get("lang"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and _LANGUAGE_PATTERN.fullmatch(candidate):
            return candidate
    return "unknown"


class ParaformerClient:
    """Synchronous local-file Paraformer client with a private artifact sink."""

    def __init__(
        self,
        settings: object | None = None,
        root: Path | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        credential = api_key
        if credential is None:
            credential = getattr(settings, "dashscope_api_key", None)
        if not isinstance(credential, str) or not credential.strip():
            raise ParaformerCredentialError()

        self._api_key = credential.strip()
        self._paths = WorkbenchPaths(root or Path.cwd())
        self._last_request_id: str | None = None

    @property
    def last_request_id(self) -> str | None:
        """Return only the last safe request identifier for explicit smoke use."""

        return self._last_request_id

    def __repr__(self) -> str:
        return "ParaformerClient(<redacted>)"

    def transcribe_file(
        self,
        path: Path,
        *,
        artifact_relative: str | None = None,
    ) -> SourceTranscript:
        audio_path, metadata = validate_local_audio(path)
        source_id = _audio_source_id(audio_path)
        self._last_request_id = None

        recognition: object | None = None
        try:
            recognition = Recognition(
                PARAFORMER_MODEL,
                None,
                metadata.format,
                metadata.sample_rate,
                api_key=self._api_key,
            )
            response = recognition.call(str(audio_path))
        except Exception as error:
            request_id = _safe_request_id(getattr(error, "request_id", None))
            if request_id is None:
                request_id = _recognition_request_id(recognition)
            self._last_request_id = request_id
            if isinstance(error, httpx.TimeoutException):
                raise ParaformerTimeoutError(request_id) from None
            if isinstance(error, httpx.RequestError):
                raise ParaformerTransportError(request_id) from None
            raise ParaformerServiceError(request_id) from None

        request_id = _response_request_id(response)
        self._last_request_id = request_id
        if not _response_success(response):
            raise ParaformerRecognitionError(request_id)

        try:
            sentences = _response_sentences(response)
        except ParaformerRecognitionError as error:
            if error.request_id == "UNAVAILABLE" and request_id is not None:
                raise ParaformerRecognitionError(request_id) from None
            raise
        language = _response_language(response)
        if artifact_relative is None:
            directory_name = f"{date.today().isoformat()}-{source_id}"
            selected_artifact = f"{directory_name}/transcript.json"
        else:
            selected_artifact = artifact_relative
        artifact = {
            "source_id": source_id,
            "duration": metadata.duration,
            "language": language,
            "sentences": sentences,
        }

        try:
            artifact_path = verify_private_relative(
                self._paths.private_wash, selected_artifact
            )
            publish_json_exclusive(
                self._paths.private_wash,
                artifact_path,
                artifact,
            )
        except (SourceContractError, OSError, TypeError, ValueError):
            raise ParaformerArtifactError() from None

        return SourceTranscript(
            duration=metadata.duration,
            segment_count=len(sentences),
            language=language,
            artifact_id=selected_artifact,
        )

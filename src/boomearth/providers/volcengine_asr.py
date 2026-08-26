"""Volcengine/Doubao turbo ASR boundary for final-audio word timestamps."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import stat
import uuid
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


RECOGNIZE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
DEFAULT_RESOURCE_ID = "volc.bigasr.auc_turbo"
MAX_RECORDING_FILE_BYTES = 20 * 1024 * 1024
_SUCCESS_STATUS = "20000000"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_REDACTED = "<REDACTED>"


class VolcengineASRError(RuntimeError):
    """Base class for redacted Volcengine/Doubao ASR failures."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"


class VolcengineCredentialError(VolcengineASRError):
    """Raised when the Volcengine/Doubao credential is unavailable."""

    def __init__(self) -> None:
        super().__init__("Volcengine/Doubao ASR credential is unavailable")


class VolcengineServiceError(VolcengineASRError):
    """Raised when Volcengine/Doubao rejects or cannot process an ASR request."""


class VolcengineTransportError(VolcengineASRError):
    """Raised when the HTTP client cannot reach Volcengine/Doubao."""


class VolcengineTimeoutError(VolcengineTransportError):
    """Raised when a Volcengine/Doubao HTTP request times out."""


class TimingUnavailableError(VolcengineServiceError):
    """Raised when ASR does not supply usable word/token timestamps."""


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    attributes = int(getattr(stat_result, "st_file_attributes", 0))
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _expected_sha256(value: str | None) -> str:
    if value is None:
        raise VolcengineASRError("committed final-audio hash is required")
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        raise VolcengineASRError("committed final-audio hash is invalid")
    return value.casefold()


def _validated_resource_id(value: object) -> str:
    if not isinstance(value, str) or value.strip() != DEFAULT_RESOURCE_ID:
        raise VolcengineASRError("Volcengine/Doubao ASR turbo resource ID is invalid")
    return DEFAULT_RESOURCE_ID


def _absolute_non_reparse_path(audio_path: Path) -> Path:
    """Keep the lexical WAV path so a reparse-point parent cannot be followed."""

    absolute_path = Path(audio_path).absolute()
    current = absolute_path
    while True:
        try:
            current_stat = current.lstat()
        except OSError:
            raise VolcengineASRError("final audio is unavailable") from None
        if _is_reparse_point(current_stat):
            raise VolcengineASRError("final audio must be a regular WAV")
        if current == current.parent:
            return absolute_path
        current = current.parent


def _read_final_wav_bytes(audio_path: Path, expected_sha256: str | None) -> bytes:
    """Read one stable, committed regular final WAV without following reparse points."""

    expected = _expected_sha256(expected_sha256)
    if audio_path.suffix.casefold() != ".wav":
        raise VolcengineASRError("final audio must be a regular WAV")
    audio_path = _absolute_non_reparse_path(audio_path)
    try:
        before = audio_path.lstat()
    except OSError:
        raise VolcengineASRError("final audio is unavailable") from None
    if _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
        raise VolcengineASRError("final audio must be a regular WAV")
    if before.st_size <= 0:
        raise VolcengineASRError("final audio is empty")
    if before.st_size > MAX_RECORDING_FILE_BYTES:
        raise VolcengineASRError("final audio exceeds turbo direct-upload limit")

    try:
        with audio_path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or not _same_file_identity(before, opened):
                raise VolcengineASRError("final audio changed during read")
            audio_bytes = stream.read(MAX_RECORDING_FILE_BYTES + 1)
            closed = os.fstat(stream.fileno())
        after = audio_path.lstat()
    except VolcengineASRError:
        raise
    except OSError:
        raise VolcengineASRError("final audio is unavailable") from None
    if (
        _is_reparse_point(after)
        or not stat.S_ISREG(after.st_mode)
        or not _same_file_identity(before, after)
        or not _same_file_identity(opened, closed)
        or len(audio_bytes) != before.st_size
        or len(audio_bytes) > MAX_RECORDING_FILE_BYTES
    ):
        raise VolcengineASRError("final audio changed during read")
    if hashlib.sha256(audio_bytes).hexdigest() != expected:
        raise VolcengineASRError("final audio does not match the committed probe")
    return audio_bytes


def _riff_data_size(audio_bytes: bytes) -> int:
    """Return the complete declared data-chunk size from one bounded RIFF image."""

    if len(audio_bytes) < 12 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        raise VolcengineASRError("final audio must be a valid PCM WAV")
    riff_end = 8 + int.from_bytes(audio_bytes[4:8], "little")
    if riff_end != len(audio_bytes):
        raise VolcengineASRError("final audio must be a valid PCM WAV")

    offset = 12
    data_size: int | None = None
    while offset < riff_end:
        if offset + 8 > riff_end:
            raise VolcengineASRError("final audio must be a valid PCM WAV")
        chunk_id = audio_bytes[offset : offset + 4]
        chunk_size = int.from_bytes(audio_bytes[offset + 4 : offset + 8], "little")
        payload_end = offset + 8 + chunk_size
        padded_end = payload_end + (chunk_size % 2)
        if padded_end > riff_end:
            raise VolcengineASRError("final audio must be a valid PCM WAV")
        if chunk_id == b"data":
            if data_size is not None:
                raise VolcengineASRError("final audio must be a valid PCM WAV")
            data_size = chunk_size
        offset = padded_end

    if data_size is None:
        raise VolcengineASRError("final audio must be a valid PCM WAV")
    return data_size


def _wav_metadata(audio_bytes: bytes) -> tuple[int, int]:
    """Validate the required uncompressed 16-bit PCM WAV fields from locked bytes."""

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2:
                raise VolcengineASRError("final audio must be 16-bit PCM WAV")
            data_size = _riff_data_size(audio_bytes)
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            frames = wav.getnframes()
            block_align = channels * wav.getsampwidth()
            if (
                block_align <= 0
                or data_size % block_align
                or frames * block_align != data_size
                or len(wav.readframes(frames)) != data_size
            ):
                raise VolcengineASRError("final audio must be a valid PCM WAV")
    except VolcengineASRError:
        raise
    except (wave.Error, EOFError):
        raise VolcengineASRError("final audio must be a valid PCM WAV") from None
    if sample_rate <= 0 or channels <= 0:
        raise VolcengineASRError("final audio must be a valid PCM WAV")
    if frames <= 0:
        raise VolcengineASRError("final audio must contain PCM frames")
    return sample_rate, channels


def _safe_provider_status(headers: Mapping[str, str]) -> str:
    value = headers.get("x-api-status-code", "")
    return value if re.fullmatch(r"[0-9]{8}", value) else "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ASRWord:
    """One provider-issued token and its unmodified timing bounds."""

    text: str
    start: float
    end: float

    def to_dict(self) -> dict[str, object]:
        return {"text": self.text, "start": self.start, "end": self.end, "isGap": False}


@dataclass(frozen=True, slots=True, repr=False)
class ASRTranscription:
    """Raw response plus real word/token timestamps from the final audio."""

    raw_response: dict[str, Any]
    words: tuple[ASRWord, ...]
    resource_id: str
    request_id: str

    @property
    def timing_granularity(self) -> str:
        return "word"

    @property
    def duration(self) -> float:
        return max(word.end for word in self.words)

    @property
    def token_count(self) -> int:
        return len(self.words)

    def __repr__(self) -> str:
        return (
            "ASRTranscription("
            f"resource_id={self.resource_id!r}, "
            f"request_id={self.request_id!r}, "
            f"token_count={self.token_count!r})"
        )


def _normalized_credential_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).casefold())


def _is_sensitive_credential_key(value: object) -> bool:
    normalized = _normalized_credential_key(value)
    if not normalized:
        return False
    if any(
        marker in normalized
        for marker in (
            "apikey",
            "accesskey",
            "secret",
            "appkey",
            "password",
            "passphrase",
            "authorization",
            "auth",
            "bearer",
            "token",
            "credential",
            "cookie",
            "session",
        )
    ):
        return True
    return normalized.endswith("key") and any(
        marker in normalized for marker in ("api", "access", "secret", "app")
    )


def _configured_sensitive_substrings(
    configured_key: str | None,
    configured_audio_data: str | None,
) -> tuple[str, ...]:
    return tuple(
        value
        for value in (configured_key, configured_audio_data)
        if isinstance(value, str) and value
    )


def redact_sensitive_data(
    value: Any,
    *,
    configured_key: str | None = None,
    configured_audio_data: str | None = None,
) -> Any:
    """Recursively redact configured payload secrets without altering ASR word timings."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            if _is_sensitive_credential_key(key_text):
                redacted[key_text] = _REDACTED
            else:
                redacted[key_text] = redact_sensitive_data(
                    child,
                    configured_key=configured_key,
                    configured_audio_data=configured_audio_data,
                )
        return redacted
    if isinstance(value, list):
        return [
            redact_sensitive_data(
                item,
                configured_key=configured_key,
                configured_audio_data=configured_audio_data,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            redact_sensitive_data(
                item,
                configured_key=configured_key,
                configured_audio_data=configured_audio_data,
            )
            for item in value
        )
    if isinstance(value, str) and any(
        sensitive in value
        for sensitive in _configured_sensitive_substrings(
            configured_key, configured_audio_data
        )
    ):
        return _REDACTED
    return value


def extract_word_timestamps(response: Mapping[str, Any]) -> tuple[ASRWord, ...]:
    """Extract only real provider word/token timings from an ASR response."""

    result = response.get("result", response)
    utterances = result.get("utterances") if isinstance(result, Mapping) else None
    if not isinstance(utterances, list):
        raise TimingUnavailableError("Volcengine/Doubao ASR word timing is unavailable")

    words: list[ASRWord] = []
    for utterance in utterances:
        if not isinstance(utterance, Mapping):
            continue
        raw_words = utterance.get("words")
        if not isinstance(raw_words, list):
            continue
        for raw_word in raw_words:
            if not isinstance(raw_word, Mapping):
                continue
            text = raw_word.get("text")
            start_time = raw_word.get("start_time")
            end_time = raw_word.get("end_time")
            if not isinstance(text, str) or not text.strip():
                continue
            try:
                start = float(start_time) / 1000.0
                end = float(end_time) / 1000.0
            except (TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                continue
            words.append(ASRWord(text=text, start=start, end=end))

    if not words:
        raise TimingUnavailableError("Volcengine/Doubao ASR word timing is unavailable")
    return tuple(words)


class VolcengineASRClient:
    """Synchronous turbo ASR client with a single redacted HTTP boundary."""

    def __init__(
        self,
        settings: object | None = None,
        *,
        api_key: str | None = None,
        resource_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        credential = api_key if api_key is not None else getattr(settings, "volcengine_api_key", None)
        if not isinstance(credential, str) or not credential.strip():
            raise VolcengineCredentialError()
        configured_resource_id = (
            resource_id
            if resource_id is not None
            else getattr(settings, "volcengine_resource_id", DEFAULT_RESOURCE_ID)
        )
        self._api_key = credential.strip()
        self.resource_id = configured_resource_id
        self._http_client = http_client or httpx.Client(timeout=180.0)
        self._owns_http_client = http_client is None
        self._last_request_id: str | None = None

    @property
    def last_request_id(self) -> str | None:
        """Return only the generated safe request identifier for explicit smoke use."""

        return self._last_request_id

    def __repr__(self) -> str:
        return "VolcengineASRClient(<redacted>)"

    def close(self) -> None:
        if self._owns_http_client:
            self._http_client.close()

    def _post(
        self,
        url: str,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        *,
        request_id: str,
    ) -> tuple[dict[str, Any], Mapping[str, str]]:
        try:
            response = self._http_client.post(
                url,
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise VolcengineTimeoutError(
                f"Volcengine/Doubao ASR transport failed; request_id={request_id}"
            ) from None
        except httpx.RequestError:
            raise VolcengineTransportError(
                f"Volcengine/Doubao ASR transport failed; request_id={request_id}"
            ) from None
        except httpx.HTTPError:
            raise VolcengineServiceError(
                f"Volcengine/Doubao ASR transport failed; request_id={request_id}"
            ) from None
        if response.status_code < 200 or response.status_code >= 300:
            status = _safe_provider_status(response.headers)
            raise VolcengineServiceError(
                "Volcengine/Doubao ASR HTTP failure; "
                f"status_code={status}; request_id={request_id}"
            )
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError):
            raise VolcengineServiceError(
                f"Volcengine/Doubao ASR response was invalid; request_id={request_id}"
            ) from None
        if not isinstance(body, dict):
            raise VolcengineServiceError(
                f"Volcengine/Doubao ASR response was invalid; request_id={request_id}"
            )
        return body, response.headers

    def transcribe_file(
        self,
        audio: Path,
        *,
        expected_sha256: str | None = None,
    ) -> ASRTranscription:
        """Send the committed final WAV in one turbo Base64 request."""

        audio_bytes = _read_final_wav_bytes(Path(audio), expected_sha256)
        _wav_metadata(audio_bytes)
        resource_id = _validated_resource_id(self.resource_id)
        encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
        request_id = str(uuid.uuid4())
        self._last_request_id = request_id
        payload = {
            "user": {"uid": "boomearth"},
            "audio": {"data": encoded_audio},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": False,
                "enable_ddc": False,
                "enable_speaker_info": False,
                "enable_channel_split": False,
                "show_utterances": True,
                "vad_segment": False,
                "sensitive_words_filter": "",
            },
        }
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": resource_id,
            "X-Api-Request-Id": request_id,
            "X-Api-Sequence": "-1",
        }
        response_body, response_headers = self._post(
            RECOGNIZE_URL,
            payload,
            headers,
            request_id=request_id,
        )
        status = _safe_provider_status(response_headers)
        if status == _SUCCESS_STATUS:
            words = extract_word_timestamps(response_body)
            return ASRTranscription(
                raw_response=redact_sensitive_data(
                    response_body,
                    configured_key=self._api_key,
                    configured_audio_data=encoded_audio,
                ),
                words=words,
                resource_id=resource_id,
                request_id=request_id,
            )
        if status == "20000003":
            raise VolcengineServiceError(
                "Volcengine/Doubao ASR found no audible speech; "
                f"status_code={status}; request_id={request_id}"
            )
        raise VolcengineServiceError(
            "Volcengine/Doubao ASR recognize failed; "
            f"status_code={status}; request_id={request_id}"
        )

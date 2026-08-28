"""Offline contract tests for the Volcengine/Doubao turbo ASR boundary."""

from __future__ import annotations

import base64
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import struct
from types import ModuleType
import sys
import wave

import httpx
import pytest

from boomearth.providers import volcengine_asr
from boomearth.providers.volcengine_asr import (
    ASRTranscription,
    ASRWord,
    TimingUnavailableError,
    VolcengineASRClient,
    VolcengineASRError,
    VolcengineServiceError,
    VolcengineTimeoutError,
    VolcengineTransportError,
    extract_word_timestamps,
    redact_sensitive_data,
)


TOKEN = "synthetic-volcengine-secret-never-real"
PRIVATE_TEXT = "private recognized text must not escape diagnostics"


def _pcm_wav_bytes(
    frames: bytes = b"\x00\x00\x01\x00",
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return buffer.getvalue()


def _wav_bytes_with_format_tag(format_tag: int) -> bytes:
    """Build one small WAV whose non-PCM format tag wave rejects."""

    frames = b"\x00\x00"
    fmt_chunk = struct.pack("<HHIIHH", format_tag, 1, 16_000, 32_000, 2, 16)
    return (
        b"RIFF"
        + struct.pack("<I", 4 + (8 + len(fmt_chunk)) + (8 + len(frames)))
        + b"WAVEfmt "
        + struct.pack("<I", len(fmt_chunk))
        + fmt_chunk
        + b"data"
        + struct.pack("<I", len(frames))
        + frames
    )


def _pcm_wav_bytes_with_data_chunk(data: bytes) -> bytes:
    """Build exact 16-bit mono PCM bytes, including RIFF padding for odd data."""

    fmt_chunk = struct.pack("<HHIIHH", 1, 1, 16_000, 32_000, 2, 16)
    data_chunk = b"data" + struct.pack("<I", len(data)) + data
    if len(data) % 2:
        data_chunk += b"\x00"
    chunks = b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _riff_chunk(chunk_id: bytes, payload: bytes) -> bytes:
    chunk = chunk_id + struct.pack("<I", len(payload)) + payload
    return chunk + (b"\x00" if len(payload) % 2 else b"")


def _write_pcm_wav(path: Path) -> bytes:
    content = _pcm_wav_bytes()
    path.write_bytes(content)
    return content


def _offline_http_client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _success_response(
    *,
    words: list[dict[str, object]] | None = None,
) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"X-Api-Status-Code": "20000000"},
        json={
            "result": {
                "text": PRIVATE_TEXT,
                "utterances": [{"words": words or [{"text": "好", "start_time": 0, "end_time": 500}]}],
            }
        },
    )


def _client(handler: object) -> VolcengineASRClient:
    return VolcengineASRClient(api_key=TOKEN, http_client=_offline_http_client(handler))


def _load_smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "automation" / "scripts" / "api_smoke.py"
    spec = importlib.util.spec_from_file_location("boomearth_api_smoke_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_subtitle_module() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / ".agents"
        / "skills"
        / "ra-audio-to-subtitles"
        / "scripts"
        / "generate_subtitles.py"
    )
    spec = importlib.util.spec_from_file_location("generate_subtitles_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _subtitle_artifact_names() -> set[str]:
    return {
        "asr-result.json",
        "captions_words.json",
        "captions.json",
        "captions.srt",
        "captions.vtt",
        "caption-qc.json",
    }


def _write_script_contract(
    path: Path,
    texts: tuple[str, ...] = ("你好",),
) -> tuple[bytes, str]:
    contents = "".join(
        json.dumps({"text": text}, ensure_ascii=False) + "\n" for text in texts
    ).encode("utf-8")
    path.write_bytes(contents)
    return contents, hashlib.sha256(contents).hexdigest()


def test_caption_display_replaces_ascii_period_with_word_separator() -> None:
    """Would fail if a filename period leaked into strict punctuation-free captions."""

    module = _load_subtitle_module()

    assert module._display_caption_text("长期规则写进 AGENTS.md例如目录边界") == (
        "长期规则写进 AGENTS md例如目录边界"
    )


def test_caption_grouping_uses_oral_linebreak_soft_target() -> None:
    module = _load_subtitle_module()

    captions = module.split_caption_text(
        "最稳的顺序是先写清楚自己要完成什么任务再判断缺的是方法连接还是整套工作流",
        16,
    )

    assert captions
    assert all(module.reading_units(text) <= 16 for text in captions)
    assert all("\n" not in text for text in captions)
    assert all(not any(mark in text for mark in "，、；：。！？") for text in captions)


def test_caption_cli_defaults_to_short_subtitle_hard_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if production silently returned to the old 20-unit captions."""

    module = _load_subtitle_module()
    monkeypatch.setattr(sys, "argv", ["generate_subtitles.py"])

    assert module.parse_args().max_chars == 14


def test_caption_grouping_keeps_terms_connectors_and_negation_units_intact() -> None:
    module = _load_subtitle_module()

    captions = module.split_caption_text(
        "具体被弃用的是 Codex CLI 里的 codex mcp-server 命令，但是这项变更没有淘汰 MCP 整体。",
        16,
    )

    assert any("Codex CLI" in text for text in captions)
    assert any("codex mcp-server" in text for text in captions)
    assert not any(text.endswith(("但", "但是", "不", "没", "没有", "未")) for text in captions)


def test_subtitle_jsonl_script_preserves_order_and_ignores_metadata(
    tmp_path: Path,
) -> None:
    module = _load_subtitle_module()
    script = tmp_path / "segments.jsonl"
    script.write_text(
        "\n".join(
            (
                '{"text":"先把想法说清楚。","silence_after_ms":200}',
                "",
                '{"text":"画面、声音和节奏，再慢慢对齐。","extra":true}',
            )
        ),
        encoding="utf-8",
    )

    assert module.load_script(str(script), None) == (
        "先把想法说清楚。\n画面、声音和节奏，再慢慢对齐。"
    )


@pytest.mark.parametrize(
    "contents",
    (
        b"{not-json}\n",
        b"[]\n",
        b'{"silence_after_ms":200}\n',
        b'{"text":1}\n',
        b'{"text":"   "}\n',
        b"\xff\xfe\x00\x00",
        b"\n \r\n",
    ),
)
def test_subtitle_jsonl_script_rejects_invalid_rows(
    tmp_path: Path,
    contents: bytes,
) -> None:
    module = _load_subtitle_module()
    script = tmp_path / "segments.jsonl"
    script.write_bytes(contents)

    with pytest.raises((RuntimeError, UnicodeDecodeError)):
        module.load_script(str(script), None)


def test_live_locked_jsonl_requires_matching_upstream_hash(tmp_path: Path) -> None:
    module = _load_subtitle_module()
    script = tmp_path / "segments.jsonl"
    contents = b'{"text":"locked narration"}\n'
    script.write_bytes(contents)

    assert module.load_script(
        str(script),
        None,
        expected_sha256=hashlib.sha256(contents).hexdigest(),
        require_locked_file=True,
    ) == "locked narration"

    with pytest.raises(RuntimeError, match="script SHA-256"):
        module.load_script(
            str(script),
            None,
            expected_sha256="0" * 64,
            require_locked_file=True,
        )


@pytest.mark.parametrize("expected_sha256", (None, "not-a-sha"))
def test_locked_script_requires_a_valid_expected_hash(
    tmp_path: Path,
    expected_sha256: str | None,
) -> None:
    module = _load_subtitle_module()
    script = tmp_path / "segments.jsonl"
    _write_script_contract(script)

    with pytest.raises(RuntimeError, match="script SHA-256"):
        module.load_script(
            str(script),
            None,
            expected_sha256=expected_sha256,
            require_locked_file=True,
        )


def test_locked_script_rejects_directory_and_reparse_input(tmp_path: Path) -> None:
    module = _load_subtitle_module()
    with pytest.raises(RuntimeError, match="ordinary file"):
        module.load_script(
            str(tmp_path),
            None,
            expected_sha256="0" * 64,
            require_locked_file=True,
        )

    script = tmp_path / "segments.jsonl"
    contents, expected_sha256 = _write_script_contract(script)
    linked_script = tmp_path / "linked.jsonl"
    try:
        linked_script.symlink_to(script)
    except OSError:
        pytest.skip("symlink creation is unavailable for this Windows test account")
    assert hashlib.sha256(contents).hexdigest() == expected_sha256
    with pytest.raises(RuntimeError, match="ordinary file"):
        module.load_script(
            str(linked_script),
            None,
            expected_sha256=expected_sha256,
            require_locked_file=True,
        )


def test_locked_script_rejects_file_changed_during_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_subtitle_module()
    script = tmp_path / "segments.jsonl"
    _, expected_sha256 = _write_script_contract(script)
    monkeypatch.setattr(
        module,
        "_after_script_snapshot_read",
        lambda path: path.write_bytes(path.read_bytes() + b" "),
    )

    with pytest.raises(RuntimeError, match="changed during read"):
        module.load_script(
            str(script),
            None,
            expected_sha256=expected_sha256,
            require_locked_file=True,
        )


def test_turbo_contract_posts_locked_wav_base64_once_and_extracts_words(
    tmp_path: Path,
) -> None:
    """Would fail if turbo submitted a URL, polled, or omitted provider word timings."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    expected_sha256 = hashlib.sha256(audio_bytes).hexdigest()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"X-Api-Status-Code": "20000000"},
            json={
                "result": {
                    "text": PRIVATE_TEXT,
                    "utterances": [
                        {
                            "words": [
                                {"text": "Claude Max", "start_time": 0, "end_time": 1500},
                                {"text": "很好", "start_time": 1600, "end_time": 2400},
                            ]
                        }
                    ],
                }
            },
        )

    client = VolcengineASRClient(
        api_key=TOKEN,
        http_client=_offline_http_client(handler),
    )
    result = client.transcribe_file(audio, expected_sha256=expected_sha256)

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == (
        "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
    )
    assert request.headers["X-Api-Key"] == TOKEN
    assert request.headers["X-Api-Resource-Id"] == "volc.bigasr.auc_turbo"
    assert request.headers["X-Api-Sequence"] == "-1"
    assert request.headers["X-Api-Request-Id"] == result.request_id
    payload = json.loads(request.content)
    assert payload["audio"] == {
        "data": base64.b64encode(audio_bytes).decode("ascii")
    }
    assert "url" not in payload["audio"]
    assert payload["request"]["model_name"] == "bigmodel"
    assert payload["request"]["show_utterances"] is True
    assert result.resource_id == "volc.bigasr.auc_turbo"
    assert result.timing_granularity == "word"
    assert result.duration == 2.4
    assert result.token_count == 2


def test_turbo_rejects_wrong_resource_before_http(tmp_path: Path) -> None:
    """Would fail if a non-turbo resource could reach the ASR endpoint."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    calls: list[httpx.Request] = []
    client = VolcengineASRClient(
        api_key=TOKEN,
        resource_id="volc.bigasr.auc",
        http_client=_offline_http_client(
            lambda request: calls.append(request) or httpx.Response(500)
        ),
    )
    with pytest.raises(VolcengineASRError, match="resource ID is invalid"):
        client.transcribe_file(
            audio,
            expected_sha256=hashlib.sha256(audio_bytes).hexdigest(),
        )
    assert calls == []


def test_turbo_direct_upload_limit_fails_before_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if an oversized direct upload reached the ASR endpoint."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    calls: list[httpx.Request] = []
    monkeypatch.setattr(volcengine_asr, "MAX_RECORDING_FILE_BYTES", len(audio_bytes) - 1)
    client = _client(lambda request: calls.append(request) or httpx.Response(500))
    with pytest.raises(VolcengineASRError, match="direct-upload limit"):
        client.transcribe_file(
            audio,
            expected_sha256=hashlib.sha256(audio_bytes).hexdigest(),
        )
    assert calls == []


def test_turbo_requires_upstream_hash_before_http(tmp_path: Path) -> None:
    """Would fail if a live ASR request could bypass the committed-WAV hash gate."""

    audio = tmp_path / "final.wav"
    _write_pcm_wav(audio)
    calls: list[httpx.Request] = []
    client = _client(lambda request: calls.append(request) or httpx.Response(500))
    with pytest.raises(VolcengineASRError, match="committed final-audio hash is required"):
        client.transcribe_file(audio)
    assert calls == []


def test_turbo_base64_and_api_key_never_escape_response_or_diagnostics(
    tmp_path: Path,
) -> None:
    """Would fail if echoed upload bytes or credentials escaped the redacted result boundary."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    encoded = base64.b64encode(audio_bytes).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-Api-Status-Code": "20000000"},
            json={
                "api_key": TOKEN,
                "audio": {"data": encoded},
                "result": {
                    "text": PRIVATE_TEXT,
                    "utterances": [
                        {"words": [{"text": "好", "start_time": 0, "end_time": 500}]}
                    ],
                },
            },
        )

    client = _client(handler)
    result = client.transcribe_file(
        audio,
        expected_sha256=hashlib.sha256(audio_bytes).hexdigest(),
    )
    rendered = json.dumps(result.raw_response, ensure_ascii=False) + repr(client) + repr(result)
    assert TOKEN not in rendered
    assert encoded not in rendered
    assert result.raw_response["api_key"] == "<REDACTED>"
    assert result.raw_response["audio"]["data"] == "<REDACTED>"
    assert PRIVATE_TEXT in rendered


@pytest.mark.parametrize(
    ("exception", "error_type"),
    (
        (httpx.TimeoutException("synthetic timeout"), VolcengineTimeoutError),
        (httpx.ConnectError("synthetic transport"), VolcengineTransportError),
    ),
)
def test_turbo_classifies_request_errors_without_retry(
    tmp_path: Path,
    exception: httpx.RequestError,
    error_type: type[VolcengineASRError],
) -> None:
    """Would fail if a one-request transport failure retried or lost its typed error."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise exception

    with pytest.raises(error_type) as raised:
        _client(handler).transcribe_file(
            audio,
            expected_sha256=hashlib.sha256(audio_bytes).hexdigest(),
        )
    assert len(calls) == 1
    assert TOKEN not in str(raised.value)


def test_turbo_non_2xx_has_safe_status_and_request_id_once(tmp_path: Path) -> None:
    """Would fail if a failed HTTP response exposed body content or triggered another request."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            500,
            headers={"X-Api-Status-Code": "20001234"},
            content=TOKEN.encode("utf-8"),
        )

    client = _client(handler)
    with pytest.raises(VolcengineServiceError) as raised:
        client.transcribe_file(audio, expected_sha256=hashlib.sha256(audio_bytes).hexdigest())
    rendered = str(raised.value)
    assert len(calls) == 1
    assert "status_code=20001234" in rendered
    assert client.last_request_id is not None
    assert client.last_request_id in rendered
    assert TOKEN not in rendered


@pytest.mark.parametrize(
    ("status", "message"),
    (("20000003", "found no audible speech"), ("20009999", "recognize failed")),
)
def test_turbo_rejects_non_success_provider_status_once(
    tmp_path: Path,
    status: str,
    message: str,
) -> None:
    """Would fail if a non-success turbo provider status yielded timings or retried."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    calls: list[httpx.Request] = []
    client = _client(
        lambda request: calls.append(request)
        or httpx.Response(200, headers={"X-Api-Status-Code": status}, json={"result": {}})
    )
    with pytest.raises(VolcengineServiceError, match=message):
        client.transcribe_file(audio, expected_sha256=hashlib.sha256(audio_bytes).hexdigest())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    (
        httpx.Response(200, headers={"X-Api-Status-Code": "20000000"}, content=b"not json"),
        httpx.Response(200, headers={"X-Api-Status-Code": "20000000"}, json=[]),
    ),
)
def test_turbo_rejects_invalid_response_body_once(
    tmp_path: Path, response: httpx.Response
) -> None:
    """Would fail if malformed turbo response bodies became a successful transcription."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    calls: list[httpx.Request] = []
    client = _client(lambda request: calls.append(request) or response)
    with pytest.raises(VolcengineServiceError, match="response was invalid"):
        client.transcribe_file(audio, expected_sha256=hashlib.sha256(audio_bytes).hexdigest())
    assert len(calls) == 1


def test_turbo_rejects_success_without_usable_words_once(tmp_path: Path) -> None:
    """Would fail if sentence-only turbo data could impersonate word-level timing."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    calls: list[httpx.Request] = []
    client = _client(
        lambda request: calls.append(request)
        or _success_response(words=[{"text": "", "start_time": 0, "end_time": 500}])
    )
    with pytest.raises(TimingUnavailableError):
        client.transcribe_file(audio, expected_sha256=hashlib.sha256(audio_bytes).hexdigest())
    assert len(calls) == 1


@pytest.mark.parametrize("suffix", (".mp3", ".mp4"))
def test_locked_final_audio_rejects_non_wav_before_http(tmp_path: Path, suffix: str) -> None:
    """Would fail if an uncommitted non-WAV input reached the provider boundary."""

    audio = tmp_path / f"final{suffix}"
    audio.write_bytes(b"synthetic-media")
    calls: list[httpx.Request] = []
    with pytest.raises(VolcengineASRError, match="regular WAV"):
        _client(lambda request: calls.append(request) or _success_response()).transcribe_file(
            audio,
            expected_sha256="a" * 64,
        )
    assert calls == []


def test_locked_final_audio_requires_matching_committed_hash_before_http(
    tmp_path: Path,
) -> None:
    """Would fail if changed final-audio bytes were submitted after the manifest lock."""

    audio = tmp_path / "final.wav"
    _write_pcm_wav(audio)
    calls: list[httpx.Request] = []
    with pytest.raises(VolcengineASRError, match="does not match the committed probe"):
        _client(lambda request: calls.append(request) or _success_response()).transcribe_file(
            audio,
            expected_sha256="a" * 64,
        )
    assert calls == []


def test_locked_final_audio_rejects_reparse_file_before_http(tmp_path: Path) -> None:
    """Would fail if a symlinked WAV could bypass the locked-file boundary."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    linked_audio = tmp_path / "linked.wav"
    try:
        linked_audio.symlink_to(audio)
    except OSError:
        pytest.skip("symlink creation is unavailable for this Windows test account")
    calls: list[httpx.Request] = []
    with pytest.raises(VolcengineASRError, match="regular WAV"):
        _client(lambda request: calls.append(request) or _success_response()).transcribe_file(
            linked_audio,
            expected_sha256=hashlib.sha256(audio_bytes).hexdigest(),
        )
    assert calls == []


def test_locked_final_audio_rejects_declared_pcm_frames_without_payload_before_http(
    tmp_path: Path,
) -> None:
    """Would fail if WAV header frame counts could bypass local PCM-byte validation."""

    audio = tmp_path / "truncated.wav"
    bad_audio_bytes = _pcm_wav_bytes()[:-4]
    audio.write_bytes(bad_audio_bytes)
    calls: list[httpx.Request] = []

    with pytest.raises(VolcengineASRError, match="valid PCM WAV"):
        _client(lambda request: calls.append(request) or _success_response()).transcribe_file(
            audio,
            expected_sha256=hashlib.sha256(bad_audio_bytes).hexdigest(),
        )

    assert calls == []


def test_locked_final_audio_rejects_partial_trailing_pcm_frame_before_http(
    tmp_path: Path,
) -> None:
    """Would fail if wave's floored frame count hid a partial PCM frame byte."""

    audio = tmp_path / "partial-frame.wav"
    bad_audio_bytes = _pcm_wav_bytes_with_data_chunk(b"\x00\x00\x01")
    audio.write_bytes(bad_audio_bytes)
    calls: list[httpx.Request] = []

    with pytest.raises(VolcengineASRError, match="valid PCM WAV"):
        _client(lambda request: calls.append(request) or _success_response()).transcribe_file(
            audio,
            expected_sha256=hashlib.sha256(bad_audio_bytes).hexdigest(),
        )

    assert calls == []


def test_locked_final_audio_accepts_ancillary_chunks_with_riff_padding(
    tmp_path: Path,
) -> None:
    """Would fail if PCM validation rejected valid ancillary chunks or their RIFF padding."""

    base_audio = _pcm_wav_bytes_with_data_chunk(b"\x00\x00")
    chunks = (
        _riff_chunk(b"JUNK", b"abc")
        + base_audio[12:]
        + _riff_chunk(b"LIST", b"INFO")
    )
    audio_bytes = b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks
    audio = tmp_path / "ancillary.wav"
    audio.write_bytes(audio_bytes)
    calls: list[httpx.Request] = []

    result = _client(
        lambda request: calls.append(request) or _success_response()
    ).transcribe_file(audio, expected_sha256=hashlib.sha256(audio_bytes).hexdigest())

    assert result.token_count == 1
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("audio_bytes", "error"),
    (
        (_pcm_wav_bytes(frames=b"\x00", sample_width=1), "16-bit PCM WAV"),
        (_wav_bytes_with_format_tag(3), "valid PCM WAV"),
        (_pcm_wav_bytes(frames=b""), "contain PCM frames"),
        (b"not-a-riff-wav", "valid PCM WAV"),
        (b"", "final audio is empty"),
    ),
)
def test_locked_final_audio_rejects_invalid_pcm_variants_before_http(
    tmp_path: Path,
    audio_bytes: bytes,
    error: str,
) -> None:
    """Would fail if invalid local WAV variants could reach the ASR request boundary."""

    audio = tmp_path / "invalid.wav"
    audio.write_bytes(audio_bytes)
    calls: list[httpx.Request] = []

    with pytest.raises(VolcengineASRError, match=error):
        _client(lambda request: calls.append(request) or _success_response()).transcribe_file(
            audio,
            expected_sha256=hashlib.sha256(audio_bytes).hexdigest(),
        )

    assert calls == []


def test_turbo_request_disables_redirects_for_an_injected_redirecting_client(
    tmp_path: Path,
) -> None:
    """Would fail if injected-client defaults could follow a redirect after uploading audio."""

    audio = tmp_path / "final.wav"
    audio_bytes = _write_pcm_wav(audio)
    requests: list[httpx.Request] = []
    client = VolcengineASRClient(
        api_key=TOKEN,
        http_client=httpx.Client(
            follow_redirects=True,
            transport=httpx.MockTransport(
                lambda request: requests.append(request)
                or httpx.Response(302, headers={"Location": "https://example.invalid/redirect"})
            ),
        ),
    )

    with pytest.raises(VolcengineServiceError, match="HTTP failure"):
        client.transcribe_file(audio, expected_sha256=hashlib.sha256(audio_bytes).hexdigest())

    assert len(requests) == 1


def test_extract_word_timestamps_preserves_real_asr_token_bounds() -> None:
    """Would fail if provider milliseconds were rounded, inferred, or reordered."""

    words = extract_word_timestamps(
        {
            "result": {
                "utterances": [
                    {
                        "words": [
                            {"text": "Claude Max", "start_time": 0, "end_time": 1500},
                            {"text": "很好", "start_time": 1600, "end_time": 2400},
                        ]
                    }
                ]
            }
        }
    )
    assert words == (
        ASRWord(text="Claude Max", start=0.0, end=1.5),
        ASRWord(text="很好", start=1.6, end=2.4),
    )


@pytest.mark.parametrize(
    "response",
    (
        {"result": {"text": "sentence timing is not enough"}},
        {"result": {"utterances": [{"words": [{"text": "word", "start_time": "bad", "end_time": 500}]}]}},
    ),
)
def test_sentence_or_invalid_timestamp_data_cannot_pass_as_words(
    response: dict[str, object],
) -> None:
    """Would fail if non-word provider metadata entered caption timing artifacts."""

    with pytest.raises(TimingUnavailableError):
        extract_word_timestamps(response)


def test_redaction_preserves_words_and_removes_configured_upload_data() -> None:
    """Would fail if recursive response redaction damaged words or leaked configured secrets."""

    encoded = base64.b64encode(b"synthetic audio").decode("ascii")
    raw_response = {
        "authorization": "provider-secret",
        "result": {"utterances": [{"words": [{"text": "Claude Max", "start_time": 0, "end_time": 1500}]}]},
        "diagnostic": f"key={TOKEN}; audio={encoded}",
    }
    redacted = redact_sensitive_data(
        raw_response,
        configured_key=TOKEN,
        configured_audio_data=encoded,
    )
    assert redacted["authorization"] == "<REDACTED>"
    assert redacted["diagnostic"] == "<REDACTED>"
    assert redacted["result"]["utterances"][0]["words"] == [
        {"text": "Claude Max", "start_time": 0, "end_time": 1500}
    ]


def test_asr_transcription_representation_has_no_raw_response() -> None:
    """Would fail if a debug representation exposed raw provider response data."""

    transcription = ASRTranscription(
        raw_response={"api_key": TOKEN, "result": {"text": PRIVATE_TEXT}},
        words=(ASRWord(text="好", start=0.0, end=0.5),),
        resource_id="volc.bigasr.auc_turbo",
        request_id="synthetic-request-id",
    )
    rendered = repr(transcription)
    assert TOKEN not in rendered
    assert PRIVATE_TEXT not in rendered


def test_subtitle_doctor_requires_key_and_turbo_resource_but_not_audio_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Would fail if caption readiness retained the obsolete signed-URL requirement."""

    module = _load_subtitle_module()
    monkeypatch.setattr(
        module,
        "load_config",
        lambda explicit_env: (
            {
                "VOLCENGINE_API_KEY": TOKEN,
                "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
            },
            None,
        ),
    )
    monkeypatch.setattr(module.shutil, "which", lambda command: f"C:/tools/{command}.exe")

    assert module.doctor(module.argparse.Namespace(env_file=None)) == 0

    checks = json.loads(capsys.readouterr().out)
    assert checks["ready"] is True
    assert checks["resource_id"] == "volc.bigasr.auc_turbo"
    assert "volcengine_audio_url" not in checks


def test_subtitle_live_path_requires_explicit_original_script_before_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if live captions derived display text from raw ASR text or constructed a client first."""

    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    expected_sha256 = hashlib.sha256(_write_pcm_wav(final_wav)).hexdigest()
    client_calls: list[dict[str, str]] = []

    class FakeClient:
        def __init__(self, *, api_key: str, resource_id: str) -> None:
            client_calls.append({"api_key": api_key, "resource_id": resource_id})

    monkeypatch.setattr(volcengine_asr, "VolcengineASRClient", FakeClient)
    monkeypatch.setattr(
        module,
        "load_config",
        lambda explicit_env: (
            {
                "VOLCENGINE_API_KEY": TOKEN,
                "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
            },
            None,
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--expected-sha256",
            expected_sha256,
            "--out-dir",
            str(tmp_path / "captions"),
        ],
    )

    with pytest.raises(
        RuntimeError,
        match="--script is required for live ASR captions",
    ):
        module.main()

    assert client_calls == []
    assert not (tmp_path / "captions").exists()


def test_subtitle_live_path_uses_canonical_turbo_client_for_locked_wav(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if captions passed obsolete URL/polling inputs instead of the locked-WAV turbo contract."""

    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    expected_sha256 = hashlib.sha256(_write_pcm_wav(final_wav)).hexdigest()
    script_path = tmp_path / "segments.jsonl"
    _, script_sha256 = _write_script_contract(script_path)
    out_dir = tmp_path / "captions"
    constructor_calls: list[dict[str, str]] = []
    transcribe_calls: list[tuple[Path, str]] = []

    class FakeClient:
        def __init__(self, *, api_key: str, resource_id: str) -> None:
            constructor_calls.append({"api_key": api_key, "resource_id": resource_id})

        def transcribe_file(self, audio: Path, *, expected_sha256: str) -> ASRTranscription:
            transcribe_calls.append((audio, expected_sha256))
            return ASRTranscription(
                raw_response={
                    "result": {
                        "utterances": [
                            {
                                "words": [
                                    {"text": "你好", "start_time": 0, "end_time": 600},
                                ]
                            }
                        ]
                    }
                },
                words=(ASRWord(text="你好", start=0.0, end=0.6),),
                resource_id="volc.bigasr.auc_turbo",
                request_id="synthetic-request-id",
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(volcengine_asr, "VolcengineASRClient", FakeClient)
    monkeypatch.setattr(
        module,
        "load_config",
        lambda explicit_env: (
            {
                "VOLCENGINE_API_KEY": TOKEN,
                "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
            },
            None,
        ),
    )
    monkeypatch.setattr(module, "media_duration", lambda media: 1.0)
    observed_stages: list[Path] = []

    def inspect_stage(stage: Path, destination: Path) -> None:
        assert not destination.exists()
        assert {path.name for path in stage.iterdir()} == _subtitle_artifact_names()
        observed_stages.append(stage)

    monkeypatch.setattr(module, "_before_artifact_publish", inspect_stage)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--expected-sha256",
            expected_sha256,
            "--script",
            str(script_path),
            "--expected-script-sha256",
            script_sha256,
            "--out-dir",
            str(out_dir),
        ],
    )

    assert module.main() == 0
    assert constructor_calls == [
        {"api_key": TOKEN, "resource_id": "volc.bigasr.auc_turbo"},
    ]
    assert transcribe_calls == [(final_wav.resolve(), expected_sha256)]
    assert {path.name for path in out_dir.iterdir()} == _subtitle_artifact_names()
    assert len(observed_stages) == 1
    assert list(out_dir.parent.glob(f".{out_dir.name}.stage-*")) == []


@pytest.mark.parametrize(
    ("resource_argument", "configured_resource"),
    (
        ("volc.seedasr.auc", "volc.bigasr.auc_turbo"),
        ("", "volc.bigasr.auc_turbo"),
        (None, "volc.seedasr.auc"),
    ),
)
def test_subtitle_live_path_rejects_non_turbo_resource_before_output_or_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resource_argument: str | None,
    configured_resource: str,
) -> None:
    """Would fail if a non-turbo CLI or environment resource reached provider construction."""

    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    expected_sha256 = hashlib.sha256(_write_pcm_wav(final_wav)).hexdigest()
    script_path = tmp_path / "segments.jsonl"
    _, script_sha256 = _write_script_contract(script_path)
    out_dir = tmp_path / "captions"
    client_calls: list[dict[str, str]] = []
    network_calls: list[tuple[Path, str]] = []

    class FakeClient:
        def __init__(self, *, api_key: str, resource_id: str) -> None:
            client_calls.append({"api_key": api_key, "resource_id": resource_id})

        def transcribe_file(self, audio: Path, *, expected_sha256: str) -> ASRTranscription:
            network_calls.append((audio, expected_sha256))
            raise RuntimeError("network request reached")

        def close(self) -> None:
            pass

    monkeypatch.setattr(volcengine_asr, "VolcengineASRClient", FakeClient)
    monkeypatch.setattr(
        module,
        "load_config",
        lambda explicit_env: (
            {
                "VOLCENGINE_API_KEY": TOKEN,
                "VOLCENGINE_RESOURCE_ID": configured_resource,
            },
            None,
        ),
    )
    command = [
        "generate_subtitles.py",
        str(final_wav),
        "--expected-sha256",
        expected_sha256,
        "--script",
        str(script_path),
        "--expected-script-sha256",
        script_sha256,
        "--out-dir",
        str(out_dir),
    ]
    if resource_argument is not None:
        command.extend(("--resource-id", resource_argument))
    monkeypatch.setattr(sys, "argv", command)

    with pytest.raises(RuntimeError, match="live ASR requires resource ID volc.bigasr.auc_turbo"):
        module.main()

    assert not out_dir.exists()
    assert client_calls == []
    assert network_calls == []


@pytest.mark.parametrize(
    "case",
    (
        "missing_script",
        "script_text",
        "missing_script_hash",
        "malformed_script_hash",
        "mismatched_script_hash",
        "existing_output",
    ),
)
def test_subtitle_live_path_rejects_invalid_preflight_before_output_or_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
) -> None:
    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    expected_sha256 = hashlib.sha256(_write_pcm_wav(final_wav)).hexdigest()
    script_path = tmp_path / "segments.jsonl"
    _, script_sha256 = _write_script_contract(script_path)
    out_dir = tmp_path / "captions"
    constructor_calls: list[dict[str, str]] = []
    transcribe_calls: list[tuple[Path, str]] = []

    class FakeClient:
        def __init__(self, *, api_key: str, resource_id: str) -> None:
            constructor_calls.append({"api_key": api_key, "resource_id": resource_id})

        def transcribe_file(self, audio: Path, *, expected_sha256: str) -> ASRTranscription:
            transcribe_calls.append((audio, expected_sha256))
            raise RuntimeError("network request reached")

        def close(self) -> None:
            pass

    monkeypatch.setattr(volcengine_asr, "VolcengineASRClient", FakeClient)
    monkeypatch.setattr(
        module,
        "load_config",
        lambda explicit_env: (
            {
                "VOLCENGINE_API_KEY": TOKEN,
                "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
            },
            None,
        ),
    )
    command = [
        "generate_subtitles.py",
        str(final_wav),
        "--expected-sha256",
        expected_sha256,
        "--out-dir",
        str(out_dir),
    ]
    if case == "missing_script":
        command.extend(("--expected-script-sha256", script_sha256))
    elif case == "script_text":
        command.extend(
            ("--script-text", "你好", "--expected-script-sha256", script_sha256)
        )
    else:
        command.extend(("--script", str(script_path)))
        if case == "malformed_script_hash":
            command.extend(("--expected-script-sha256", "not-a-sha"))
        elif case == "mismatched_script_hash":
            command.extend(("--expected-script-sha256", "0" * 64))
        elif case != "missing_script_hash":
            command.extend(("--expected-script-sha256", script_sha256))
        if case == "existing_output":
            out_dir.mkdir()
    monkeypatch.setattr(sys, "argv", command)

    with pytest.raises(RuntimeError):
        module.main()

    assert constructor_calls == []
    assert transcribe_calls == []
    assert out_dir.exists() is (case == "existing_output")


def test_subtitle_artifact_write_failure_leaves_no_formal_or_stage_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    _write_pcm_wav(final_wav)
    result_path = tmp_path / "recorded-result.json"
    result_path.write_text(
        json.dumps(
            {
                "result": {
                    "text": "你好",
                    "utterances": [
                        {"words": [{"text": "你好", "start_time": 0, "end_time": 600}]}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "captions"
    sibling = tmp_path / "keep.txt"
    sibling.write_bytes(b"keep-unchanged")
    writes: list[Path] = []

    def fail_third_write(path: Path, contents: bytes) -> None:
        writes.append(path)
        if len(writes) == 3:
            raise OSError("synthetic write failure")
        path.write_bytes(contents)

    monkeypatch.setattr(module, "_write_stage_artifact", fail_third_write)
    monkeypatch.setattr(module, "media_duration", lambda media: 1.0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--asr-result",
            str(result_path),
            "--out-dir",
            str(out_dir),
        ],
    )

    with pytest.raises(OSError, match="synthetic write failure"):
        module.main()

    assert not out_dir.exists()
    assert list(out_dir.parent.glob(f".{out_dir.name}.stage-*")) == []
    assert sibling.read_bytes() == b"keep-unchanged"


def test_subtitle_destination_race_preserves_foreign_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    _write_pcm_wav(final_wav)
    result_path = tmp_path / "recorded-result.json"
    result_path.write_text(
        json.dumps(
            {
                "result": {
                    "text": "你好",
                    "utterances": [
                        {"words": [{"text": "你好", "start_time": 0, "end_time": 600}]}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "captions"
    sentinel = b"foreign-directory-must-survive"

    def create_foreign_destination(stage: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "sentinel.bin").write_bytes(sentinel)

    monkeypatch.setattr(module, "_before_artifact_publish", create_foreign_destination)
    monkeypatch.setattr(module, "media_duration", lambda media: 1.0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--asr-result",
            str(result_path),
            "--out-dir",
            str(out_dir),
        ],
    )

    with pytest.raises((FileExistsError, RuntimeError)):
        module.main()

    assert (out_dir / "sentinel.bin").read_bytes() == sentinel
    assert {path.name for path in out_dir.iterdir()} == {"sentinel.bin"}
    assert list(out_dir.parent.glob(f".{out_dir.name}.stage-*")) == []


def test_subtitle_artifact_parent_identity_change_prevents_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_subtitle_module()
    parent = tmp_path / "media"
    parent.mkdir()
    displaced_parent = tmp_path / "displaced-media"
    out_dir = parent / "captions"
    artifacts = {name: b"synthetic" for name in module.ARTIFACT_FILENAMES}

    def replace_checked_parent(checked_parent: Path) -> None:
        assert checked_parent == parent
        checked_parent.rename(displaced_parent)
        checked_parent.mkdir()

    monkeypatch.setattr(
        module,
        "_after_artifact_parent_check",
        replace_checked_parent,
    )

    with pytest.raises(RuntimeError, match="parent identity changed"):
        module.publish_artifact_directory(out_dir, artifacts)

    assert not out_dir.exists()
    assert list(parent.glob(".captions.stage-*")) == []
    assert list(displaced_parent.glob(".captions.stage-*")) == []


def test_subtitle_qc_fail_publishes_complete_diagnostic_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    expected_sha256 = hashlib.sha256(_write_pcm_wav(final_wav)).hexdigest()
    script_path = tmp_path / "segments.jsonl"
    _, script_sha256 = _write_script_contract(script_path)
    out_dir = tmp_path / "captions"

    class FakeClient:
        def __init__(self, *, api_key: str, resource_id: str) -> None:
            pass

        def transcribe_file(self, audio: Path, *, expected_sha256: str) -> ASRTranscription:
            return ASRTranscription(
                raw_response={"result": {"text": "你好"}},
                words=(ASRWord(text="你好", start=0.0, end=0.6),),
                resource_id="volc.bigasr.auc_turbo",
                request_id="synthetic-qc-fail-id",
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(volcengine_asr, "VolcengineASRClient", FakeClient)
    monkeypatch.setattr(
        module,
        "load_config",
        lambda explicit_env: (
            {
                "VOLCENGINE_API_KEY": TOKEN,
                "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
            },
            None,
        ),
    )
    monkeypatch.setattr(module, "media_duration", lambda media: 1.0)
    real_build_qc = module.build_qc

    def force_failed_qc(*args: object) -> dict[str, object]:
        report = real_build_qc(*args)
        report["status"] = "fail"
        report["errors"] = ["synthetic alignment failure"]
        return report

    monkeypatch.setattr(module, "build_qc", force_failed_qc)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--expected-sha256",
            expected_sha256,
            "--script",
            str(script_path),
            "--expected-script-sha256",
            script_sha256,
            "--out-dir",
            str(out_dir),
        ],
    )

    assert module.main() == 1
    assert {path.name for path in out_dir.iterdir()} == _subtitle_artifact_names()
    assert json.loads(
        (out_dir / "caption-qc.json").read_text(encoding="utf-8")
    )["status"] == "fail"


def test_subtitle_artifact_publish_contains_no_provider_secret_or_audio_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    wav_bytes = _write_pcm_wav(final_wav)
    expected_sha256 = hashlib.sha256(wav_bytes).hexdigest()
    encoded_audio = base64.b64encode(wav_bytes).decode("ascii")
    script_path = tmp_path / "segments.jsonl"
    _, script_sha256 = _write_script_contract(script_path)
    out_dir = tmp_path / "captions"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"X-Api-Status-Code": "20000000"},
            json={
                "credential_echo": TOKEN,
                "audio_echo": f"prefix-{encoded_audio}-suffix",
                "result": {
                    "text": "你好",
                    "utterances": [
                        {"words": [{"text": "你好", "start_time": 0, "end_time": 600}]}
                    ],
                },
            },
        )

    real_client = VolcengineASRClient

    def client_factory(*, api_key: str, resource_id: str) -> VolcengineASRClient:
        return real_client(
            api_key=api_key,
            resource_id=resource_id,
            http_client=_offline_http_client(handler),
        )

    monkeypatch.setattr(volcengine_asr, "VolcengineASRClient", client_factory)
    monkeypatch.setattr(
        module,
        "load_config",
        lambda explicit_env: (
            {
                "VOLCENGINE_API_KEY": TOKEN,
                "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
            },
            None,
        ),
    )
    monkeypatch.setattr(module, "media_duration", lambda media: 1.0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--expected-sha256",
            expected_sha256,
            "--script",
            str(script_path),
            "--expected-script-sha256",
            script_sha256,
            "--out-dir",
            str(out_dir),
        ],
    )

    assert module.main() == 0
    output = capsys.readouterr()
    artifact_bytes = b"".join(path.read_bytes() for path in out_dir.iterdir())
    rendered = artifact_bytes.decode("utf-8") + output.out + output.err
    assert len(requests) == 1
    assert TOKEN not in rendered
    assert encoded_audio not in rendered


def test_subtitle_skill_documents_locked_jsonl_and_atomic_publication() -> None:
    root = Path(__file__).resolve().parents[1]
    skill = (
        root / ".agents/skills/katerj-audio-subtitles/SKILL.md"
    ).read_text(encoding="utf-8")
    artifact_contract = (
        root
        / ".agents/skills/ra-audio-to-subtitles/references/artifact-contract.md"
    ).read_text(encoding="utf-8")

    assert "--expected-script-sha256" in skill
    assert "segments.jsonl" in skill
    assert "existing destination" in skill
    assert "complete diagnostic" in skill
    assert "six" in artifact_contract.lower()
    assert "QC" in artifact_contract
    assert "no-clobber" in artifact_contract


def test_subtitle_offline_result_can_supply_display_text_when_script_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if offline ASR-result reuse lost its documented display-text fallback."""

    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    _write_pcm_wav(final_wav)
    result_path = tmp_path / "recorded-result.json"
    result_path.write_text(
        json.dumps(
            {
                "result": {
                    "text": "你好",
                    "utterances": [
                        {"words": [{"text": "你好", "start_time": 0, "end_time": 600}]}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "captions"
    monkeypatch.setattr(module, "media_duration", lambda media: 1.0)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--asr-result",
            str(result_path),
            "--out-dir",
            str(out_dir),
        ],
    )

    assert module.main() == 0
    assert json.loads((out_dir / "captions.json").read_text(encoding="utf-8")) == [
        {
            "start": 0.0,
            "end": 0.72,
            "text": "你好",
            "source": "volcengine-word-timestamps",
        }
    ]
    qc = json.loads((out_dir / "caption-qc.json").read_text(encoding="utf-8"))
    assert qc["source_media"] == "final.wav"
    assert qc["narration_sha256"] == hashlib.sha256(final_wav.read_bytes()).hexdigest()
    assert set(qc) == {
        "status",
        "timing_source",
        "alignment_coverage",
        "minimum_coverage",
        "script_characters",
        "matched_characters",
        "asr_characters",
        "word_units",
        "caption_count",
        "overlap_count",
        "max_reading_units_per_second",
        "short_fragments",
        "split_connectors",
        "narration_sha256",
        "source_media",
        "asr_resource_id",
        "warnings",
        "errors",
    }


def test_subtitle_offline_reuse_rejects_media_changed_during_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if saved ASR timing could be rebound to replacement audio bytes."""

    module = _load_subtitle_module()
    final_wav = tmp_path / "final.wav"
    _write_pcm_wav(final_wav)
    result_path = tmp_path / "recorded-result.json"
    result_path.write_text(
        json.dumps(
            {
                "result": {
                    "text": "你好",
                    "utterances": [
                        {"words": [{"text": "你好", "start_time": 0, "end_time": 600}]}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "captions"
    monkeypatch.setattr(module, "media_duration", lambda media: 1.0)
    real_build_qc = module.build_qc

    def mutate_media(*args: object) -> dict[str, object]:
        report = real_build_qc(*args)
        final_wav.write_bytes(b"replacement-audio")
        return report

    monkeypatch.setattr(module, "build_qc", mutate_media)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_subtitles.py",
            str(final_wav),
            "--asr-result",
            str(result_path),
            "--out-dir",
            str(out_dir),
        ],
    )

    with pytest.raises(RuntimeError, match="Final media changed during subtitle generation"):
        module.main()

    assert not out_dir.exists()


def test_volcengine_smoke_redacts_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Would fail if the smoke command printed provider text, credentials, or WAV paths."""

    module = _load_smoke_module()
    audio = tmp_path / "private-final.wav"
    _write_pcm_wav(audio)

    class FakeNarrator:
        def probe_committed_wav(self, wav_path: Path, manifest_path: Path) -> object:
            return type("Probe", (), {"sha256": "c" * 64})()

    class FakeClient:
        last_request_id = "synthetic-volcengine-error-id"

        def __init__(self, settings: object) -> None:
            pass

        def transcribe_file(self, path: Path, *, expected_sha256: str) -> ASRTranscription:
            raise RuntimeError(f"{PRIVATE_TEXT} {TOKEN} {path}")

    monkeypatch.setattr(module.Settings, "load", lambda root: object())

    exit_code = module.main(
        ["volcengine", "--audio", str(audio)],
        root=tmp_path,
        volcengine_client_factory=FakeClient,
        volcengine_narrator_factory=lambda root: FakeNarrator(),
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert output.out.splitlines() == [
        "status=INTERNAL_ERROR",
        "request_id=synthetic-volcengine-error-id",
        "timing_granularity=UNAVAILABLE",
        "duration=UNAVAILABLE",
        "token_count=UNAVAILABLE",
    ]
    assert output.err == ""
    assert PRIVATE_TEXT not in output.out + output.err
    assert TOKEN not in output.out + output.err
    assert str(audio) not in output.out + output.err

import importlib
import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path
import sys
from unittest.mock import patch
import wave

import pytest


@pytest.fixture(autouse=True)
def _allow_ignored_private_test_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep boundary tests offline while production still invokes Git ignore checks."""
    module = importlib.import_module("boomearth.audio.indextts2")
    monkeypatch.setattr(module, "_path_is_git_ignored", lambda *_: True, raising=False)
    monkeypatch.setattr(module, "_path_is_tracked", lambda *_: False, raising=False)


def _write_pcm_wav(
    path: Path,
    *,
    duration_ms: int = 100,
    sample_rate: int = 8000,
    channels: int = 1,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * (sample_rate * channels * duration_ms // 1000))


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_run_available_ignores_non_system_encoded_tool_output() -> None:
    """Would fail if a local tool's undecodable stdout/stderr escaped its validation call."""
    module = importlib.import_module("boomearth.audio.indextts2")
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'\\xff'); sys.stderr.buffer.write(b'\\xff')",
    ]

    module._run_available(command)


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_run_available_uses_return_code_even_with_non_system_encoded_tool_output() -> None:
    """Would fail if decode errors hid a tool's nonzero validation status."""
    module = importlib.import_module("boomearth.audio.indextts2")
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'\\xff'); sys.stderr.buffer.write(b'\\xff'); sys.exit(7)",
    ]

    with pytest.raises(module.IndexTTS2ValidationError, match="did not validate"):
        module._run_available(command)


def _write_provenance_ledger(
    path: Path,
    hashes: tuple[str, ...] = (),
    canonical_reference_provenance: tuple[dict[str, str], ...] = (),
    issued_manifest_sha256_by_output_sha256: dict[str, str | list[str]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "schema_version": 1,
        "issued_output_sha256": list(hashes),
    }
    if canonical_reference_provenance:
        payload["canonical_reference_provenance"] = list(canonical_reference_provenance)
    if issued_manifest_sha256_by_output_sha256 is not None:
        payload["issued_manifest_sha256_by_output_sha256"] = (
            issued_manifest_sha256_by_output_sha256
        )
    path.write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


def _canonical_reference_entry(voice_id: str, reference: Path) -> dict[str, str]:
    return {
        "voice_id": voice_id,
        "reference_audio_path": str(reference.resolve()),
        "reference_audio_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
    }


def _local_route(
    module: object,
    root: Path,
    reference: Path,
    provenance_ledger_path: Path | None = None,
    voice_id: str | None = None,
    reference_audio_sha256: str | None = None,
) -> object:
    voice_id = voice_id or module.CURRENT_VOICE_ID
    cli_script = root / "cli.py"
    cli_script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    model_dir = root / "checkpoints"
    model_dir.mkdir(exist_ok=True)
    if provenance_ledger_path is None:
        provenance_ledger_path = root / "private" / "indextts2-provenance-ledger.json"
        _write_provenance_ledger(
            provenance_ledger_path,
            canonical_reference_provenance=(
                _canonical_reference_entry(voice_id, reference),
            ),
        )
    return module.TTSRouting(
        provider="indextts2-local",
        model="IndexTTS2",
        voice_id=voice_id,
        reference_audio_path=reference,
        reference_audio_sha256=reference_audio_sha256
        or hashlib.sha256(reference.read_bytes()).hexdigest(),
        interpreter_path=Path(sys.executable),
        cli_script_path=cli_script,
        model_dir=model_dir,
        playback_speed=1.12,
        fp16=True,
        deepspeed=False,
        cuda_kernel=False,
        accel=False,
        torch_compile=False,
        used_fallback=False,
        provenance_ledger_path=provenance_ledger_path,
    )


def _local_narrator(
    module: object,
    root: Path,
    reference: Path,
    pronunciation_lexicon_path: Path | None = None,
    provenance_ledger_path: Path | None = None,
    voice_id: str | None = None,
    reference_audio_sha256: str | None = None,
) -> object:
    if pronunciation_lexicon_path is None:
        pronunciation_lexicon_path = root / "pronunciation-lexicon.json"
        pronunciation_lexicon_path.write_bytes(
            (Path(__file__).resolve().parents[1] / "automation" / "config" / "pronunciation-lexicon.json").read_bytes()
        )
    route = _local_route(
        module,
        root,
        reference,
        provenance_ledger_path=provenance_ledger_path,
        voice_id=voice_id,
        reference_audio_sha256=reference_audio_sha256,
    )
    with (
        patch.object(module, "LOCKED_INTERPRETER_PATH", route.interpreter_path),
        patch.object(module, "LOCKED_CLI_SCRIPT_PATH", route.cli_script_path),
        patch.object(module, "LOCKED_PROVENANCE_LEDGER_PATH", route.provenance_ledger_path),
        patch.object(module, "LOCKED_MODEL_DIR", route.model_dir),
        patch.object(module, "WORKSPACE_ROOT", root),
        patch.object(module, "LOCKED_PRIVATE_VOICE_DIRECTORY", root),
    ):
        return module.IndexTTS2Narrator(
            route,
            pronunciation_lexicon_path=pronunciation_lexicon_path,
        )


def _narrator_ready_to_render(
    module: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, Path, Path, Path, Path]:
    """Create an offline render fixture whose local batch process is faked."""
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    return narrator, reference, batch_file, output_wav, manifest_path


def test_locked_route_loads_the_local_indextts2_contract() -> None:
    """Would fail if a cloud/fallback route or unsafe acceleration is selected."""
    try:
        module = importlib.import_module("boomearth.audio.indextts2")
    except ModuleNotFoundError:
        pytest.fail("the locked IndexTTS2 narration interface is not available")

    routing_path = Path(__file__).resolve().parents[1] / "automation" / "config" / "tts-routing.json"
    route = module.TTSRouting.load(routing_path)

    assert route.provider == "indextts2-local"
    assert route.model == "IndexTTS2"
    assert module.CURRENT_VOICE_ID == "user-indextts2-black-gold-v3"
    assert module.SUPPORTED_VOICE_IDS == frozenset(
        {
            "user-indextts2-calm-v1",
            "user-indextts2-calm-v2",
            "user-indextts2-black-gold-v3",
        }
    )
    assert route.voice_id == module.CURRENT_VOICE_ID
    assert route.interpreter_path == module.LOCKED_INTERPRETER_PATH
    assert route.cli_script_path == module.LOCKED_CLI_SCRIPT_PATH
    assert route.provenance_ledger_path == module.LOCKED_PROVENANCE_LEDGER_PATH
    assert route.playback_speed == 1.12
    assert route.fp16 is True
    assert route.deepspeed is False
    assert route.cuda_kernel is False
    assert route.accel is False
    assert route.torch_compile is False
    assert route.used_fallback is False


@pytest.mark.parametrize(
    "voice_id",
    [
        "pluvio-indextts2-calm-v1",
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
    ],
)
def test_current_route_rejects_legacy_voice_ids(
    tmp_path: Path,
    voice_id: str,
) -> None:
    """Would fail if a new route could silently use a legacy voice identity."""
    module = importlib.import_module("boomearth.audio.indextts2")
    source = Path(__file__).resolve().parents[1] / "automation" / "config" / "tts-routing.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["voice_id"] = voice_id
    unsafe_route = tmp_path / "tts-routing.json"
    unsafe_route.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="locked"):
        module.TTSRouting.load(unsafe_route)


@pytest.mark.parametrize(
    "voice_id", ["user-indextts2-calm-v1", "user-indextts2-calm-v2"]
)
def test_retired_voice_ids_are_historical_only(voice_id: str) -> None:
    module = importlib.import_module("boomearth.audio.indextts2")

    assert module.validate_historical_voice_id(voice_id) == voice_id
    with pytest.raises(module.IndexTTS2ValidationError, match="current voice"):
        module.validate_new_production_voice_id(voice_id)


def test_new_manifest_records_the_current_voice_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a new manifest could be emitted with a legacy voice ID."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )

    manifest = narrator.render(batch_file, output_wav, manifest_path)

    assert manifest.voice_id == module.CURRENT_VOICE_ID
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["voice_id"] == module.CURRENT_VOICE_ID


def test_locked_route_rejects_any_provider_other_than_local_indextts2(
    tmp_path: Path,
) -> None:
    """Would fail if configuration could silently introduce a cloud fallback."""
    module = importlib.import_module("boomearth.audio.indextts2")
    source = Path(__file__).resolve().parents[1] / "automation" / "config" / "tts-routing.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["provider"] = "cloud-tts"
    unsafe_route = tmp_path / "tts-routing.json"
    unsafe_route.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="locked"):
        module.TTSRouting.load(unsafe_route)


def test_locked_route_rejects_an_interpreter_other_than_the_production_binary(
    tmp_path: Path,
) -> None:
    """Would fail if a route could activate or invoke another Python runtime."""
    module = importlib.import_module("boomearth.audio.indextts2")
    source = Path(__file__).resolve().parents[1] / "automation" / "config" / "tts-routing.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["interpreter_path"] = str(tmp_path / "python.exe")
    unsafe_route = tmp_path / "tts-routing.json"
    unsafe_route.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="interpreter"):
        module.TTSRouting.load(unsafe_route)


def test_locked_route_rejects_a_cli_other_than_the_production_script(
    tmp_path: Path,
) -> None:
    """Would fail if JSON could redirect direct batch work to another CLI script."""
    module = importlib.import_module("boomearth.audio.indextts2")
    source = Path(__file__).resolve().parents[1] / "automation" / "config" / "tts-routing.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["cli_script_path"] = str(tmp_path / "cli.py")
    unsafe_route = tmp_path / "tts-routing.json"
    unsafe_route.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="CLI"):
        module.TTSRouting.load(unsafe_route)


def test_locked_route_rejects_a_ledger_other_than_the_private_provenance_path(
    tmp_path: Path,
) -> None:
    """Would fail if route configuration could bypass issued-output provenance."""
    module = importlib.import_module("boomearth.audio.indextts2")
    source = Path(__file__).resolve().parents[1] / "automation" / "config" / "tts-routing.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["provenance_ledger_path"] = str(tmp_path / "other-ledger.json")
    unsafe_route = tmp_path / "tts-routing.json"
    unsafe_route.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="ledger"):
        module.TTSRouting.load(unsafe_route)


def test_batch_argv_is_the_direct_locked_cli_contract(tmp_path: Path) -> None:
    """Would fail if synthesis used uv, activation, force, or unsafe acceleration."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    route = _local_route(module, tmp_path, reference)
    prepared_batch = tmp_path / "workspace" / "narration.jsonl"
    raw_dir = tmp_path / "workspace" / "raw"
    reference_snapshot = tmp_path / "workspace" / "reference.wav"
    _write_pcm_wav(reference_snapshot)

    assert hasattr(module, "_build_batch_argv")
    assert module._build_batch_argv(route, prepared_batch, raw_dir, reference_snapshot) == [
        str(route.interpreter_path),
        str(route.cli_script_path),
        "batch",
        "--batch-file",
        str(prepared_batch),
        "--voice",
        str(reference_snapshot),
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


def test_narrator_rejects_a_directly_constructed_fallback_route(
    tmp_path: Path,
) -> None:
    """Would fail if callers could bypass JSON locking and enable fallback."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    unsafe_route = replace(
        _local_route(module, tmp_path, reference),
        used_fallback=True,
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="locked"):
        module.IndexTTS2Narrator(unsafe_route)


def test_narrator_rejects_a_directly_constructed_other_interpreter(
    tmp_path: Path,
) -> None:
    """Would fail if callers could bypass the JSON route and activate another Python."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    unsafe_route = replace(
        _local_route(module, tmp_path, reference),
        interpreter_path=tmp_path / "another-python.exe",
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="interpreter"):
        module.IndexTTS2Narrator(unsafe_route)


def test_narrator_rejects_a_directly_constructed_other_provenance_ledger(
    tmp_path: Path,
) -> None:
    """Would fail if callers could replace the private issued-hash ledger."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    route = _local_route(module, tmp_path, reference)
    unsafe_route = replace(
        route,
        provenance_ledger_path=tmp_path / "other-private" / "ledger.json",
    )

    with (
        patch.object(module, "LOCKED_INTERPRETER_PATH", route.interpreter_path),
        patch.object(module, "LOCKED_CLI_SCRIPT_PATH", route.cli_script_path),
        patch.object(module, "LOCKED_PROVENANCE_LEDGER_PATH", route.provenance_ledger_path),
        patch.object(module, "LOCKED_MODEL_DIR", route.model_dir),
        pytest.raises(module.IndexTTS2ValidationError, match="ledger"),
    ):
        module.IndexTTS2Narrator(unsafe_route)


def test_narrator_rejects_a_directly_constructed_other_model_directory(
    tmp_path: Path,
) -> None:
    """Would fail if a direct caller could point model loading outside the lock."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    route = _local_route(module, tmp_path, reference)
    unsafe_route = replace(route, model_dir=tmp_path / "other-model")

    with (
        patch.object(module, "LOCKED_INTERPRETER_PATH", route.interpreter_path),
        patch.object(module, "LOCKED_CLI_SCRIPT_PATH", route.cli_script_path),
        patch.object(module, "LOCKED_PROVENANCE_LEDGER_PATH", route.provenance_ledger_path),
        patch.object(module, "LOCKED_MODEL_DIR", route.model_dir),
        pytest.raises(module.IndexTTS2ValidationError, match="model"),
    ):
        module.IndexTTS2Narrator(unsafe_route)


def test_narrator_constructor_has_no_runtime_lock_bypass() -> None:
    """Would fail if a production caller could inject different runtime lock paths."""
    module = importlib.import_module("boomearth.audio.indextts2")
    parameters = inspect.signature(module.IndexTTS2Narrator).parameters

    assert "_expected_interpreter_path" not in parameters
    assert "_expected_cli_script_path" not in parameters


def test_environment_validation_probes_a_pcm_reference_without_loading_model(
    tmp_path: Path,
) -> None:
    """Would fail if validation skips the actual reference codec or hash lock."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)

    assert hasattr(module, "IndexTTS2Narrator")
    result = _local_narrator(module, tmp_path, reference).validate_environment()

    assert result.reference.codec == "pcm_s16le"
    assert result.reference.container == "wav"
    assert result.reference.sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()


@pytest.mark.parametrize("directory", ["generated", "OutPuTs"])
def test_environment_validation_rejects_reference_inside_generated_or_output_tree(
    tmp_path: Path,
    directory: str,
) -> None:
    """Would fail if a generated output could become the cloned reference."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / directory / "reference.wav"
    _write_pcm_wav(reference)

    with pytest.raises(module.IndexTTS2ValidationError, match="output"):
        _local_narrator(module, tmp_path, reference).validate_environment()


def test_environment_validation_rejects_a_wrong_current_reference_hash(
    tmp_path: Path,
) -> None:
    """Would fail if v2 reference bytes could differ from the locked route hash."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)

    with pytest.raises(module.IndexTTS2ValidationError, match="canonical provenance"):
        _local_narrator(
            module,
            tmp_path,
            reference,
            reference_audio_sha256="0" * 64,
        ).validate_environment()


def test_environment_validation_accepts_retired_v1_v2_and_current_v3_provenance(
    tmp_path: Path,
) -> None:
    """Would fail if retired voice facts could not coexist with the current v3 reference."""
    module = importlib.import_module("boomearth.audio.indextts2")
    v1_reference = tmp_path / "references" / "v1.wav"
    v2_reference = tmp_path / "references" / "v2.wav"
    reference = tmp_path / "references" / "v3.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(v1_reference)
    _write_pcm_wav(v2_reference)
    _write_pcm_wav(reference)
    issued_hash = hashlib.sha256(b"historic-issued-output").hexdigest()
    _write_provenance_ledger(
        ledger_path,
        hashes=(issued_hash,),
        canonical_reference_provenance=(
            _canonical_reference_entry("user-indextts2-calm-v1", v1_reference),
            _canonical_reference_entry("user-indextts2-calm-v2", v2_reference),
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )

    result = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    ).validate_environment()

    assert result.reference.sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()


def test_environment_validation_accepts_missing_explicitly_retired_references(
    tmp_path: Path,
) -> None:
    """Retired v1/v2 files may be recycled while their audit facts remain."""
    module = importlib.import_module("boomearth.audio.indextts2")
    v1_reference = tmp_path / "references" / "v1.wav"
    v2_reference = tmp_path / "references" / "v2.wav"
    reference = tmp_path / "references" / "v3.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(v1_reference)
    _write_pcm_wav(v2_reference)
    _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry("user-indextts2-calm-v1", v1_reference),
            _canonical_reference_entry("user-indextts2-calm-v2", v2_reference),
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["retired_voice_ids"] = [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
    ]
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")
    v1_reference.unlink()
    v2_reference.unlink()

    result = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    ).validate_environment()

    assert result.reference.sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()


def test_environment_validation_accepts_current_v3_only_canonical_provenance(
    tmp_path: Path,
) -> None:
    """Would fail if the current v3-only provenance contract were rejected."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "references" / "v3.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )

    result = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    ).validate_environment()

    assert result.reference.sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()


def test_provenance_loader_accepts_exact_historical_retirement_marker(
    tmp_path: Path,
) -> None:
    """Would fail if recording retirement made the active v3 ledger unreadable."""
    module = importlib.import_module("boomearth.audio.indextts2")
    ledger_path = tmp_path / "indextts2-provenance-ledger.json"
    references = [tmp_path / name for name in ("v1.wav", "v2.wav", "v3.wav")]
    for reference in references:
        _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry("user-indextts2-calm-v1", references[0]),
            _canonical_reference_entry("user-indextts2-calm-v2", references[1]),
            _canonical_reference_entry(module.CURRENT_VOICE_ID, references[2]),
        ),
    )
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["retired_voice_ids"] = [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
    ]
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    _issued, entries, _bindings = module._load_provenance_ledger_document(ledger_path)

    assert [entry["voice_id"] for entry in entries] == [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
        module.CURRENT_VOICE_ID,
    ]


@pytest.mark.parametrize("mismatch", ["path", "hash"])
def test_environment_validation_rejects_current_v3_provenance_mismatch(
    tmp_path: Path,
    mismatch: str,
) -> None:
    """Would fail if a current route did not bind to its own canonical provenance entry."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "references" / "v3.wav"
    other_reference = tmp_path / "references" / "other.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(reference)
    _write_pcm_wav(other_reference)
    entry = _canonical_reference_entry(module.CURRENT_VOICE_ID, reference)
    if mismatch == "path":
        entry["reference_audio_path"] = str(other_reference.resolve())
    else:
        entry["reference_audio_sha256"] = "0" * 64
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(entry,),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="canonical provenance"):
        _local_narrator(
            module,
            tmp_path,
            reference,
            provenance_ledger_path=ledger_path,
        ).validate_environment()


def test_environment_validation_rejects_invalid_historical_v1_provenance(
    tmp_path: Path,
) -> None:
    """Would fail if a historical voice could claim bytes that do not match its own entry."""
    module = importlib.import_module("boomearth.audio.indextts2")
    historical_reference = tmp_path / "references" / "v1.wav"
    v2_reference = tmp_path / "references" / "v2.wav"
    reference = tmp_path / "references" / "v3.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(historical_reference)
    _write_pcm_wav(v2_reference)
    _write_pcm_wav(reference)
    historical_entry = _canonical_reference_entry("user-indextts2-calm-v1", historical_reference)
    historical_entry["reference_audio_sha256"] = "0" * 64
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            historical_entry,
            _canonical_reference_entry("user-indextts2-calm-v2", v2_reference),
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="canonical provenance"):
        _local_narrator(
            module,
            tmp_path,
            reference,
            provenance_ledger_path=ledger_path,
        ).validate_environment()


@pytest.mark.parametrize(
    "voice_ids",
    [
        ("user-indextts2-black-gold-v3", "user-indextts2-black-gold-v3"),
        ("user-indextts2-calm-v1", "unknown-voice"),
    ],
)
def test_canonical_provenance_rejects_duplicate_or_unknown_voice_ids(
    tmp_path: Path,
    voice_ids: tuple[str, str],
) -> None:
    """Would fail if provenance could carry duplicate or unrecognized voice identities."""
    module = importlib.import_module("boomearth.audio.indextts2")
    first_reference = tmp_path / "references" / "first.wav"
    second_reference = tmp_path / "references" / "second.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(first_reference)
    _write_pcm_wav(second_reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry(voice_ids[0], first_reference),
            _canonical_reference_entry(voice_ids[1], second_reference),
        ),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="provenance"):
        module._load_provenance_ledger_document(ledger_path)


def test_provenance_ledger_rejects_manifest_binding_keyset_not_equal_to_issued_outputs(
    tmp_path: Path,
) -> None:
    """A lower-case binding map must still cover precisely the issued output set."""
    module = importlib.import_module("boomearth.audio.indextts2")
    ledger_path = tmp_path / "ledger.json"
    _write_provenance_ledger(
        ledger_path,
        ("a" * 64,),
        issued_manifest_sha256_by_output_sha256={"b" * 64: "c" * 64},
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        module._load_provenance_ledger_document(ledger_path)


def test_provenance_ledger_rejects_duplicate_manifest_binding_output_key(
    tmp_path: Path,
) -> None:
    """Duplicate JSON object keys cannot silently forge the final manifest binding."""
    module = importlib.import_module("boomearth.audio.indextts2")
    ledger_path = tmp_path / "ledger.json"
    output_hash = "a" * 64
    first_manifest_hash = "b" * 64
    duplicate_manifest_hash = "c" * 64
    ledger_path.write_text(
        "{"
        '"schema_version":1,'
        f'"issued_output_sha256":["{output_hash}"],'
        '"issued_manifest_sha256_by_output_sha256":{'
        f'"{output_hash}":"{first_manifest_hash}",'
        f'"{output_hash}":"{duplicate_manifest_hash}"'
        "}}",
        encoding="utf-8",
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        module._load_provenance_ledger_document(ledger_path)


def test_provenance_ledger_accepts_explicit_multi_manifest_audio_reuse(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("boomearth.audio.indextts2")
    ledger_path = tmp_path / "ledger.json"
    output_hash = "a" * 64
    source_manifest_hash = "b" * 64
    reuse_manifest_hash = "c" * 64
    _write_provenance_ledger(
        ledger_path,
        (output_hash,),
        issued_manifest_sha256_by_output_sha256={
            output_hash: [source_manifest_hash, reuse_manifest_hash]
        },
    )

    issued, _references, bindings = module._load_provenance_ledger_document(
        ledger_path
    )

    assert issued == (output_hash,)
    assert bindings == {output_hash: (source_manifest_hash, reuse_manifest_hash)}


def test_explicit_audio_reuse_appends_without_weakening_render_rebind_guard(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("boomearth.audio.indextts2")
    ledger_path = tmp_path / "ledger.json"
    output_hash = "a" * 64
    source_manifest_hash = "b" * 64
    reuse_manifest_hash = "c" * 64
    unbound_manifest_hash = "d" * 64
    _write_provenance_ledger(
        ledger_path,
        (output_hash,),
        issued_manifest_sha256_by_output_sha256={
            output_hash: source_manifest_hash
        },
    )

    module.authorize_provenance_manifest_reuse(
        ledger_path,
        output_sha256=output_hash,
        source_manifest_sha256=source_manifest_hash,
        reuse_manifest_sha256=reuse_manifest_hash,
    )

    recorded = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert recorded["issued_manifest_sha256_by_output_sha256"][output_hash] == [
        source_manifest_hash,
        reuse_manifest_hash,
    ]
    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        module._register_provenance_manifest_while_locked(
            ledger_path,
            output_hash,
            unbound_manifest_hash,
        )
    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        module.authorize_provenance_manifest_reuse(
            ledger_path,
            output_sha256=output_hash,
            source_manifest_sha256=unbound_manifest_hash,
            reuse_manifest_sha256="e" * 64,
        )


def test_manifest_registration_preserves_retired_voice_ids(tmp_path: Path) -> None:
    """Publishing a new narration must not reactivate retired references."""
    module = importlib.import_module("boomearth.audio.indextts2")
    ledger_path = tmp_path / "ledger.json"
    output_hash = "a" * 64
    manifest_hash = "b" * 64
    retired_voice_ids = [
        "user-indextts2-calm-v1",
        "user-indextts2-calm-v2",
    ]
    _write_provenance_ledger(
        ledger_path,
        issued_manifest_sha256_by_output_sha256={},
    )
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["retired_voice_ids"] = retired_voice_ids
    ledger_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    module._register_provenance_manifest_while_locked(
        ledger_path,
        output_hash,
        manifest_hash,
    )

    recorded = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert recorded["retired_voice_ids"] == retired_voice_ids


def test_canonical_provenance_persistence_retains_v1_when_v2_is_appended_or_updated(
    tmp_path: Path,
) -> None:
    """Would fail if adding or updating v2 rewrote the v1 provenance record."""
    module = importlib.import_module("boomearth.audio.indextts2")
    historical_reference = tmp_path / "references" / "v1.wav"
    v2_reference = tmp_path / "references" / "v2.wav"
    updated_v2_reference = tmp_path / "references" / "v2-updated.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(historical_reference)
    _write_pcm_wav(v2_reference, duration_ms=120)
    _write_pcm_wav(updated_v2_reference, duration_ms=140)
    issued_hash = hashlib.sha256(b"historic-issued-output").hexdigest()
    v1_entry = _canonical_reference_entry("user-indextts2-calm-v1", historical_reference)
    v2_entry = _canonical_reference_entry("user-indextts2-calm-v2", v2_reference)
    updated_v2_entry = _canonical_reference_entry("user-indextts2-calm-v2", updated_v2_reference)
    ledger_path.parent.mkdir()

    module._persist_provenance_ledger(ledger_path, [issued_hash], (v1_entry,))
    module._persist_provenance_ledger(ledger_path, [issued_hash], (v1_entry, v2_entry))
    module._persist_provenance_ledger(
        ledger_path,
        [issued_hash],
        (v1_entry, updated_v2_entry),
    )
    issued_history, entries, manifest_bindings = module._load_provenance_ledger_document(
        ledger_path
    )

    assert issued_history == (issued_hash,)
    assert entries[0] == v1_entry
    assert entries[1] == updated_v2_entry
    assert manifest_bindings is None


def test_dry_run_validates_segments_without_creating_audio(
    tmp_path: Path,
) -> None:
    """Would fail if a dry run could not validate narration without synthesis."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n{"text":"Claude"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)

    assert hasattr(narrator, "dry_run")
    result = narrator.dry_run(batch_file)

    assert result.reference.codec == "pcm_s16le"
    assert result.segment_count == 2
    assert not list(tmp_path.rglob("*.wav"))[1:]


@pytest.mark.parametrize("silence_after_ms", [0, 60_000])
def test_segment_loading_accepts_the_inclusive_trailing_silence_bounds(
    tmp_path: Path,
    silence_after_ms: int,
) -> None:
    """Would fail if a valid boundary silence duration were rejected or rewritten."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text(
        json.dumps({"text": "Codex", "silence_after_ms": silence_after_ms}) + "\n",
        encoding="utf-8",
    )

    segments = _local_narrator(module, tmp_path, reference).load_segments(batch_file)

    assert segments[0].silence_after_ms == silence_after_ms


@pytest.mark.parametrize("silence_after_ms", [60_001, -1, True, 1.5, "1000"])
def test_segment_loading_rejects_an_unsupported_trailing_silence_value(
    tmp_path: Path,
    silence_after_ms: object,
) -> None:
    """Would fail if unsafe silence values could reach the local narration renderer."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text(
        json.dumps({"text": "Codex", "silence_after_ms": silence_after_ms}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="^silence_after_ms is unsupported$"):
        _local_narrator(module, tmp_path, reference).load_segments(batch_file)


def test_render_rejects_excessive_silence_before_model_work_or_output_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if an excessive silence value allocated output or invoked the model runner."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex","silence_after_ms":60001}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("model runner must not start for excessive silence"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="^silence_after_ms is unsupported$"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert not output_wav.exists()
    assert not manifest_path.exists()


def test_segment_loading_rejects_a_forbidden_pronunciation_form(
    tmp_path: Path,
) -> None:
    """Would fail if an explicitly rejected TTS spelling reaches IndexTTS2."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"扣代克斯"}\n', encoding="utf-8")

    narrator = _local_narrator(module, tmp_path, reference)
    assert hasattr(narrator, "load_segments")
    with pytest.raises(module.IndexTTS2ValidationError, match="forbidden pronunciation"):
        narrator.load_segments(batch_file)


def test_existing_wav_probe_returns_pcm_metadata_without_generation(
    tmp_path: Path,
) -> None:
    """Would fail if acceptance could not verify a prior generated WAV safely."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    existing_wav = tmp_path / "prior" / "narration.wav"
    _write_pcm_wav(reference)
    _write_pcm_wav(existing_wav, duration_ms=250)
    narrator = _local_narrator(module, tmp_path, reference)

    assert hasattr(narrator, "probe_existing_wav")
    result = narrator.probe_existing_wav(existing_wav)

    assert result.codec == "pcm_s16le"
    assert result.container == "wav"
    assert result.duration_seconds == pytest.approx(0.25, abs=0.01)
    assert result.sha256 == hashlib.sha256(existing_wav.read_bytes()).hexdigest()


def test_render_concatenates_raw_wavs_once_and_writes_a_text_free_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if raw speech bypassed FFmpeg speed control or leaked into metadata."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text(
        '{"text":"Matt Pocock","silence_after_ms":1000}\n{"text":"Codex"}\n',
        encoding="utf-8",
    )
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    narrator = _local_narrator(module, tmp_path, reference)

    def fake_index_tts_batch(
        prepared_batch: Path,
        raw_dir: Path,
        reference_snapshot: Path,
    ) -> None:
        assert "Matt Poe cock" in prepared_batch.read_text(encoding="utf-8")
        assert reference_snapshot.is_file()
        _write_pcm_wav(raw_dir / "segment-0001.wav", duration_ms=1000)
        _write_pcm_wav(raw_dir / "segment-0002.wav", duration_ms=1000)

    assert hasattr(narrator, "render")
    monkeypatch.setattr(narrator, "_run_index_tts_batch", fake_index_tts_batch)
    manifest = narrator.render(batch_file, output_wav, manifest_path)
    recorded = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_probe = narrator.probe_existing_wav(output_wav)

    assert output_probe.container == "wav"
    assert output_probe.codec == "pcm_s16le"
    # FFmpeg's atempo filter has a bounded startup/tail frame adjustment.
    assert output_probe.duration_seconds == pytest.approx(3.0 / 1.12, abs=0.05)
    expected_manifest = {
        "provider": "indextts2-local",
        "voice_id": module.CURRENT_VOICE_ID,
        "model": "IndexTTS2",
        "reference_audio_path": str(reference.resolve()),
        "reference_audio_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "output_path": str(output_wav.resolve()),
        "output_sha256": hashlib.sha256(output_wav.read_bytes()).hexdigest(),
        "segment_contract_path": str(batch_file.resolve()),
        "segment_contract_sha256": hashlib.sha256(batch_file.read_bytes()).hexdigest(),
        "segment_count": 2,
        "playback_speed": 1.12,
        "pronunciation_contract_path": str(narrator.pronunciation_lexicon_path.resolve()),
        "pronunciation_contract_sha256": hashlib.sha256(
            narrator.pronunciation_lexicon_path.read_bytes()
        ).hexdigest(),
        "used_fallback": False,
    }
    assert set(recorded) == set(expected_manifest)
    assert recorded == expected_manifest
    assert manifest.to_dict() == expected_manifest
    assert "Matt Pocock" not in manifest_path.read_text(encoding="utf-8")
    assert list(output_wav.parent.glob(".narration.indextts2-*")) == []


def test_render_publishes_pre_tempo_raw_audit_wav_without_expanding_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if audit output were missing, tempo-adjusted, or added to VoiceManifest."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text(
        '{"text":"first","silence_after_ms":1000}\n{"text":"second"}\n',
        encoding="utf-8",
    )
    narrator = _local_narrator(module, tmp_path, reference)

    def fake_index_tts_batch(
        _prepared_batch: Path,
        raw_dir: Path,
        _reference_snapshot: Path,
    ) -> None:
        _write_pcm_wav(raw_dir / "segment-0001.wav", duration_ms=1000)
        _write_pcm_wav(raw_dir / "segment-0002.wav", duration_ms=1000)

    monkeypatch.setattr(narrator, "_run_index_tts_batch", fake_index_tts_batch)
    narrator.render(batch_file, output_wav, manifest_path)

    raw_audit = output_wav.with_name("narration.raw.wav")
    raw_probe = narrator.probe_existing_wav(raw_audit)
    final_probe = narrator.probe_existing_wav(output_wav)
    assert raw_probe.codec == "pcm_s16le"
    assert raw_probe.duration_seconds == pytest.approx(3.0, abs=0.05)
    assert final_probe.duration_seconds == pytest.approx(3.0 / 1.12, abs=0.05)
    assert set(json.loads(manifest_path.read_text(encoding="utf-8"))) == set(
        module.VoiceManifest.__dataclass_fields__
    )


def test_render_rejects_existing_raw_audit_before_batch_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a raw audit could overwrite an existing user-owned WAV."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    output_wav = tmp_path / "production" / "narration.wav"
    batch_file = tmp_path / "narration.jsonl"
    _write_pcm_wav(reference)
    _write_pcm_wav(output_wav.with_name("narration.raw.wav"))
    batch_file.write_text('{"text":"safe"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="raw audit WAV already exists"):
        narrator.render(batch_file, output_wav, tmp_path / "production" / "voice_manifest.json")


def test_render_rejects_nonprivate_output_before_batch_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if direct narrator use bypassed the ignored-output boundary."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"safe"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(module, "_path_is_git_ignored", lambda *_: False)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="private narration boundary"):
        narrator.render(
            batch_file,
            tmp_path / "production" / "narration.wav",
            tmp_path / "production" / "voice_manifest.json",
        )


def test_concatenation_normalizes_mismatched_pcm_inputs_in_one_ffmpeg_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if mixed raw segment formats could bypass one normalized concat graph."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    first_raw = tmp_path / "raw" / "segment-0001.wav"
    second_raw = tmp_path / "raw" / "segment-0002.wav"
    output_wav = tmp_path / "final.wav"
    _write_pcm_wav(reference)
    _write_pcm_wav(first_raw, duration_ms=200, sample_rate=8000, channels=1)
    _write_pcm_wav(second_raw, duration_ms=200, sample_rate=16000, channels=2)
    narrator = _local_narrator(module, tmp_path, reference)
    real_run = module.subprocess.run
    ffmpeg_commands: list[list[str]] = []

    def recording_run(command: list[str], *args: object, **kwargs: object) -> object:
        if command[0] == module.shutil.which("ffmpeg"):
            ffmpeg_commands.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", recording_run)
    narrator._concatenate_and_tempo(
        (first_raw, second_raw),
        (
            module.NarrationSegment("first", "first", None, None, 0),
            module.NarrationSegment("second", "second", None, None, 0),
        ),
        output_wav,
        tmp_path / "final.raw.wav",
    )

    probe = narrator.probe_existing_wav(output_wav)
    assert probe.codec == "pcm_s16le"
    assert probe.sample_rate == 8000
    assert probe.channels == 1
    assert probe.duration_seconds == pytest.approx(0.4 / 1.12, abs=0.04)
    assert len(ffmpeg_commands) == 1
    graph = ffmpeg_commands[0][ffmpeg_commands[0].index("-filter_complex") + 1]
    assert graph.count("aresample=8000") == 2
    assert graph.count("aformat=sample_fmts=s16:channel_layouts=mono") == 2
    assert graph.count("atempo=1.12") == 1


def test_render_rejects_an_output_path_that_is_the_locked_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if rendering could overwrite the voice-cloning reference."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)

    def forbidden_batch_process(*args: object) -> None:
        raise module.IndexTTS2ValidationError("batch process must not start")

    monkeypatch.setattr(narrator, "_run_index_tts_batch", forbidden_batch_process)
    with pytest.raises(module.IndexTTS2ValidationError, match="output cannot equal reference"):
        narrator.render(batch_file, reference, tmp_path / "voice_manifest.json")


def test_render_rejects_an_existing_output_before_batch_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a fresh render could overwrite an existing narration WAV."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    output_wav = tmp_path / "production" / "narration.wav"
    _write_pcm_wav(reference)
    _write_pcm_wav(output_wav)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="output WAV already exists"):
        narrator.render(batch_file, output_wav, output_wav.with_name("voice_manifest.json"))


def test_render_rejects_manifest_path_equal_to_output_before_batch_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if manifest serialization could replace the final WAV."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="manifest cannot equal output"):
        narrator.render(batch_file, output_wav, output_wav)


def test_render_rejects_reference_recorded_as_a_prior_manifest_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a prior generated WAV could become the next reference audio."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "output_path": str(reference),
                "output_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="prior manifest output"):
        narrator.render(batch_file, output_wav, manifest_path)


def test_render_rejects_an_existing_manifest_before_batch_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a fresh render could overwrite prior manifest evidence."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "output_path": str(tmp_path / "prior" / "narration.wav"),
                "output_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="manifest already exists"):
        narrator.render(batch_file, output_wav, manifest_path)


def test_render_rejects_a_manifest_path_that_is_the_locked_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if manifest writing could replace the reference WAV."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="manifest cannot equal reference"):
        narrator.render(batch_file, output_wav, reference)


def test_render_rejects_a_non_utf8_existing_manifest_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if malformed prior evidence escaped the redacted validation boundary."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_bytes(b"\xff")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="existing voice manifest is invalid"):
        narrator.render(batch_file, output_wav, manifest_path)


def test_render_rejects_renamed_reference_matching_prior_manifest_output_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if copied generated bytes could evade path-only provenance checks."""
    module = importlib.import_module("boomearth.audio.indextts2")
    prior_output = tmp_path / "prior" / "original-output.wav"
    reference = tmp_path / "inputs" / "renamed-reference.wav"
    _write_pcm_wav(prior_output)
    reference.parent.mkdir()
    reference.write_bytes(prior_output.read_bytes())
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(
            {
                "output_path": str(prior_output),
                "output_sha256": hashlib.sha256(prior_output.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="prior manifest output hash"):
        narrator.render(batch_file, output_wav, manifest_path)


@pytest.mark.parametrize("source_name", ["batch", "lexicon"])
def test_render_rejects_batch_or_lexicon_mutation_before_model_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
) -> None:
    """Would fail if a parsed source could drift before IndexTTS2 starts."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    original_batch = b'{"text":"Matt Pocock"}\n'
    batch_file.write_bytes(original_batch)
    lexicon_path = tmp_path / "pronunciation-lexicon.json"
    original_lexicon = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "config"
        / "pronunciation-lexicon.json"
    ).read_bytes()
    lexicon_path.write_bytes(original_lexicon)
    narrator = _local_narrator(
        module,
        tmp_path,
        reference,
        pronunciation_lexicon_path=lexicon_path,
    )
    def mutate_source() -> None:
        if source_name == "batch":
            batch_file.write_text('{"text":"Claude"}\n', encoding="utf-8")
        else:
            lexicon_path.write_bytes(original_lexicon + b"\n")

    monkeypatch.setattr(narrator, "_after_narration_snapshot", mutate_source, raising=False)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("model work must not start after narration input drift"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="narration input changed"):
        narrator.render(
            batch_file,
            tmp_path / "production" / "narration.wav",
            tmp_path / "production" / "voice_manifest.json",
        )


@pytest.mark.parametrize("source_name", ["batch", "lexicon"])
def test_render_rejects_batch_or_lexicon_mutation_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_name: str,
) -> None:
    """Would fail if input drift during model work could still publish an artifact."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    lexicon_path = tmp_path / "pronunciation-lexicon.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Matt Pocock"}\n', encoding="utf-8")
    original_lexicon = (
        Path(__file__).resolve().parents[1]
        / "automation"
        / "config"
        / "pronunciation-lexicon.json"
    ).read_bytes()
    lexicon_path.write_bytes(original_lexicon)
    narrator = _local_narrator(
        module,
        tmp_path,
        reference,
        pronunciation_lexicon_path=lexicon_path,
    )
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"

    def mutate_source() -> None:
        if source_name == "batch":
            batch_file.write_text('{"text":"Claude"}\n', encoding="utf-8")
        else:
            lexicon_path.write_bytes(original_lexicon + b"\n")

    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    monkeypatch.setattr(narrator, "_before_publish", mutate_source, raising=False)

    with pytest.raises(module.IndexTTS2ValidationError, match="narration input changed"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert not output_wav.exists()
    assert not manifest_path.exists()


def test_render_passes_private_reference_snapshot_to_batch_and_rejects_live_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if the official CLI received mutable live reference audio."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert "reference_snapshot" in inspect.signature(
        module.IndexTTS2Narrator._run_index_tts_batch
    ).parameters
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    _write_pcm_wav(reference)
    original_reference = reference.read_bytes()
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    received_snapshot: Path | None = None

    def fake_index_tts_batch(
        prepared_batch: Path,
        raw_dir: Path,
        reference_snapshot: Path,
    ) -> None:
        nonlocal received_snapshot
        received_snapshot = reference_snapshot
        assert reference_snapshot.resolve() != reference.resolve()
        assert reference_snapshot.read_bytes() == original_reference
        _write_pcm_wav(raw_dir / "segment-0001.wav")
        _write_pcm_wav(reference, duration_ms=200)

    monkeypatch.setattr(narrator, "_run_index_tts_batch", fake_index_tts_batch)
    with pytest.raises(module.IndexTTS2ValidationError, match="reference audio changed"):
        narrator.render(
            batch_file,
            tmp_path / "production" / "narration.wav",
            tmp_path / "production" / "voice_manifest.json",
        )

    assert received_snapshot is not None
    assert not received_snapshot.exists()


def test_render_rejects_reference_change_immediately_before_model_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a changed cloning reference reached IndexTTS2."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)

    def mutate_reference() -> None:
        _write_pcm_wav(reference, duration_ms=200)

    monkeypatch.setattr(narrator, "_after_narration_snapshot", mutate_reference, raising=False)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("batch generation must not start"),
    )
    with pytest.raises(module.IndexTTS2ValidationError, match="reference audio changed"):
        narrator.render(
            batch_file,
            tmp_path / "production" / "narration.wav",
            tmp_path / "production" / "voice_manifest.json",
        )


def test_render_discards_temporary_audio_when_reference_changes_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a post-generation reference change could publish narration."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    narrator = _local_narrator(module, tmp_path, reference)

    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    monkeypatch.setattr(
        narrator,
        "_before_publish",
        lambda: _write_pcm_wav(reference, duration_ms=200),
        raising=False,
    )
    with pytest.raises(module.IndexTTS2ValidationError, match="reference audio changed"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert not output_wav.exists()
    assert not manifest_path.exists()


def test_render_preserves_user_output_created_at_final_publish_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if final no-clobber publication could overwrite user bytes."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    user_bytes = b"do-not-overwrite-user-file"

    def create_collision(staged_output: Path) -> None:
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        output_wav.write_bytes(user_bytes)

    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    monkeypatch.setattr(narrator, "_before_output_publish", create_collision, raising=False)
    with pytest.raises(module.IndexTTS2ValidationError, match="publish target"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert output_wav.read_bytes() == user_bytes
    assert not manifest_path.exists()


def test_render_retains_uncommitted_output_and_raw_audit_when_manifest_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsafe pathname cleanup must not delete this render's uncommitted evidence."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    assert hasattr(module, "_publish_no_clobber")
    real_publish = module._publish_no_clobber

    def fail_manifest_publish(
        staged_file: Path,
        target_path: Path,
        **kwargs: object,
    ) -> object:
        if target_path == manifest_path:
            raise module.IndexTTS2ValidationError("injected manifest publish failure")
        return real_publish(staged_file, target_path, **kwargs)

    monkeypatch.setattr(module, "_publish_no_clobber", fail_manifest_publish)
    with pytest.raises(module.IndexTTS2ValidationError, match="manifest publish failure"):
        narrator.render(batch_file, output_wav, manifest_path)

    raw_audit_wav = output_wav.with_name("narration.raw.wav")
    assert output_wav.exists()
    assert raw_audit_wav.exists()
    assert not manifest_path.exists()
    with pytest.raises(module.IndexTTS2ValidationError):
        narrator.probe_committed_wav(output_wav, manifest_path)
    recorded = json.loads(narrator.route.provenance_ledger_path.read_text(encoding="utf-8"))
    assert len(recorded["issued_output_sha256"]) == 1


def test_render_preserves_replaced_user_output_during_manifest_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a failed commit deleted a final path replaced after WAV publication."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    user_bytes = b"replaced-user-output-must-survive"

    def replace_published_output(staged_output: Path) -> None:
        output_wav.unlink()
        output_wav.write_bytes(user_bytes)

    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    monkeypatch.setattr(
        narrator,
        "_before_manifest_publish",
        replace_published_output,
        raising=False,
    )
    with pytest.raises(module.IndexTTS2ValidationError, match="published output changed"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert output_wav.read_bytes() == user_bytes
    assert not manifest_path.exists()


def test_render_preserves_in_place_mutated_output_during_manifest_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a failed commit deleted the same hard-linked file after user mutation."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)

    def mutate_published_output(staged_output: Path) -> None:
        with output_wav.open("r+b") as changed:
            changed.write(b"user-mutated-output")

    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    monkeypatch.setattr(
        narrator,
        "_before_manifest_publish",
        mutate_published_output,
        raising=False,
    )
    with pytest.raises(module.IndexTTS2ValidationError, match="published output changed"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert output_wav.read_bytes().startswith(b"user-mutated-output")
    assert not manifest_path.exists()


def test_probe_committed_wav_accepts_only_a_matching_manifest_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a WAV without its hash-valid manifest was treated as committed."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert hasattr(module.IndexTTS2Narrator, "probe_committed_wav")
    reference = tmp_path / "inputs" / "reference.wav"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(module, tmp_path, reference)
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    narrator.render(batch_file, output_wav, manifest_path)

    assert narrator.probe_committed_wav(output_wav, manifest_path).sha256 == hashlib.sha256(
        output_wav.read_bytes()
    ).hexdigest()
    recorded = json.loads(narrator.route.provenance_ledger_path.read_text(encoding="utf-8"))
    assert recorded["issued_manifest_sha256_by_output_sha256"] == {
        hashlib.sha256(output_wav.read_bytes()).hexdigest(): hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
    }
    manifest_path.unlink()
    with pytest.raises(module.IndexTTS2ValidationError, match="uncommitted"):
        narrator.probe_committed_wav(output_wav, manifest_path)


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    (
        ("voice_id", "tampered-voice"),
        ("reference_audio_path", "C:\\redacted\\replacement.wav"),
        ("segment_contract_sha256", "0" * 64),
        ("pronunciation_contract_sha256", "1" * 64),
    ),
)
def test_canonical_probe_rejects_full_field_manifest_provenance_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    tampered_value: str,
) -> None:
    """The issued output must bind the complete exact manifest, not only WAV metadata."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    narrator.render(batch_file, output_wav, manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload[field] = tampered_value
    manifest_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(module.IndexTTS2ValidationError, match="uncommitted"):
        narrator.probe_committed_wav(output_wav, manifest_path)


def test_canonical_probe_fails_closed_when_legacy_issued_output_has_no_manifest_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older issued hashes remain readable but cannot pass the canonical committed probe."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    manifest = narrator.render(batch_file, output_wav, manifest_path)
    _write_provenance_ledger(
        narrator.route.provenance_ledger_path,
        (manifest.output_sha256,),
        (_canonical_reference_entry(module.CURRENT_VOICE_ID, narrator.route.reference_audio_path),),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="uncommitted"):
        narrator.probe_committed_wav(output_wav, manifest_path)


def test_canonical_probe_rejects_wrong_manifest_binding_for_issued_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A syntactically valid but mismatched ledger binding cannot authorize a manifest."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    manifest = narrator.render(batch_file, output_wav, manifest_path)
    _write_provenance_ledger(
        narrator.route.provenance_ledger_path,
        (manifest.output_sha256,),
        (_canonical_reference_entry(module.CURRENT_VOICE_ID, narrator.route.reference_audio_path),),
        {manifest.output_sha256: "0" * 64},
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="uncommitted"):
        narrator.probe_committed_wav(output_wav, manifest_path)


def test_provenance_ledger_rejects_noncanonical_manifest_binding_keys_and_values(
    tmp_path: Path,
) -> None:
    """Binding keys and values must be lower-case SHA-256 and cover issued outputs exactly."""
    module = importlib.import_module("boomearth.audio.indextts2")
    ledger_path = tmp_path / "ledger.json"
    output_hash = "a" * 64
    _write_provenance_ledger(
        ledger_path,
        (output_hash,),
        issued_manifest_sha256_by_output_sha256={output_hash.upper(): "B" * 64},
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        module._load_provenance_ledger_document(ledger_path)


def test_render_does_not_publish_when_manifest_binding_registration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The output and manifest must remain absent when durable pair registration fails."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    raw_audit_wav = output_wav.with_name("narration.raw.wav")

    monkeypatch.setattr(
        module,
        "_register_provenance_manifest_while_locked",
        lambda *args: (_ for _ in ()).throw(
            module.IndexTTS2ValidationError("provenance ledger could not be registered")
        ),
        raising=False,
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert not raw_audit_wav.exists()
    assert not output_wav.exists()
    assert not manifest_path.exists()


def test_render_rejects_reference_hash_registered_by_another_fresh_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if cross-job copied output bytes bypassed known provenance evidence."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert "provenance_ledger_path" in module.TTSRouting.__dataclass_fields__
    job_a_output = tmp_path / "job-a" / "issued.wav"
    reference = tmp_path / "job-b-input" / "renamed-reference.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(job_a_output)
    reference.parent.mkdir()
    reference.write_bytes(job_a_output.read_bytes())
    _write_provenance_ledger(
        ledger_path,
        (hashlib.sha256(job_a_output.read_bytes()).hexdigest(),),
        (_canonical_reference_entry(module.CURRENT_VOICE_ID, reference),),
    )
    batch_file = tmp_path / "job-b-input" / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    output_wav = tmp_path / "job-b-output" / "narration.wav"
    manifest_path = tmp_path / "job-b-output" / "voice_manifest.json"
    narrator = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    )
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("model work must not start after ledger provenance match"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="reference audio matches a provenance"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert not output_wav.exists()
    assert not manifest_path.exists()


@pytest.mark.parametrize("ledger_bytes", [None, b"not-json"])
def test_render_fails_closed_for_unavailable_or_malformed_provenance_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ledger_bytes: bytes | None,
) -> None:
    """Would fail if broken provenance evidence permitted a render to start."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert "provenance_ledger_path" in module.TTSRouting.__dataclass_fields__
    reference = tmp_path / "inputs" / "reference.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(reference)
    if ledger_bytes is not None:
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_bytes(ledger_bytes)
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    )
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("model work must not start with invalid provenance ledger"),
    )

    expected_error = "private narration boundary" if ledger_bytes is None else "provenance ledger"
    with pytest.raises(module.IndexTTS2ValidationError, match=expected_error):
        narrator.render(
            batch_file,
            tmp_path / "production" / "narration.wav",
            tmp_path / "production" / "voice_manifest.json",
        )


def test_initialized_empty_provenance_ledger_passes_environment_validation(
    tmp_path: Path,
) -> None:
    """Would fail if the initialized no-history ledger was not accepted."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert "provenance_ledger_path" in module.TTSRouting.__dataclass_fields__
    reference = tmp_path / "inputs" / "reference.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )

    result = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    ).validate_environment()

    assert result.reference.sha256 == hashlib.sha256(reference.read_bytes()).hexdigest()


def test_render_fails_closed_when_provenance_ledger_is_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if concurrent ledger registration could race a render."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )
    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")
    lock_path.write_text("busy\n", encoding="utf-8")
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    )
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda *args: pytest.fail("model work must not start while ledger is busy"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        narrator.render(
            batch_file,
            tmp_path / "production" / "narration.wav",
            tmp_path / "production" / "voice_manifest.json",
        )


def test_render_registers_a_committed_output_hash_in_the_provenance_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if successful committed output was absent from durable provenance evidence."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert "provenance_ledger_path" in module.TTSRouting.__dataclass_fields__
    reference = tmp_path / "inputs" / "reference.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )
    batch_file = tmp_path / "narration.jsonl"
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    )
    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    manifest = narrator.render(
        batch_file,
        tmp_path / "production" / "narration.wav",
        tmp_path / "production" / "voice_manifest.json",
    )

    recorded = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert recorded["issued_output_sha256"] == [manifest.output_sha256]


def test_render_persists_ledger_before_no_clobber_output_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if a final WAV could appear before its issued hash is durable."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    batch_file = tmp_path / "narration.jsonl"
    output_wav = tmp_path / "production" / "narration.wav"
    manifest_path = tmp_path / "production" / "voice_manifest.json"
    _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")
    narrator = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    )
    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")

    monkeypatch.setattr(
        narrator,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )

    def assert_ledger_precedes_output(staged_output: Path) -> None:
        recorded = json.loads(ledger_path.read_text(encoding="utf-8"))
        assert hashlib.sha256(staged_output.read_bytes()).hexdigest() in recorded[
            "issued_output_sha256"
        ]
        assert lock_path.exists()
        assert not output_wav.exists()

    monkeypatch.setattr(
        narrator,
        "_before_output_publish",
        assert_ledger_precedes_output,
        raising=False,
    )
    narrator.render(batch_file, output_wav, manifest_path)


def test_same_hash_different_manifest_cannot_replace_successful_job_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One issued output hash cannot be rebound to a different exact manifest."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert not hasattr(module, "_unregister_provenance_hash")
    reference = tmp_path / "inputs" / "reference.wav"
    ledger_path = tmp_path / "private" / "indextts2-provenance-ledger.json"
    batch_file = tmp_path / "narration.jsonl"
    _write_pcm_wav(reference)
    _write_provenance_ledger(
        ledger_path,
        canonical_reference_provenance=(
            _canonical_reference_entry(module.CURRENT_VOICE_ID, reference),
        ),
    )
    batch_file.write_text('{"text":"Codex"}\n', encoding="utf-8")

    successful = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    )
    monkeypatch.setattr(
        successful,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )
    successful_output = tmp_path / "job-a" / "narration.wav"
    successful_manifest = tmp_path / "job-a" / "voice_manifest.json"
    issued = successful.render(batch_file, successful_output, successful_manifest)

    failed = _local_narrator(
        module,
        tmp_path,
        reference,
        provenance_ledger_path=ledger_path,
    )
    failed_output = tmp_path / "job-b" / "narration.wav"
    failed_manifest = tmp_path / "job-b" / "voice_manifest.json"
    monkeypatch.setattr(
        failed,
        "_run_index_tts_batch",
        lambda prepared_batch, raw_dir, reference_snapshot: _write_pcm_wav(
            raw_dir / "segment-0001.wav"
        ),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="provenance ledger"):
        failed.render(batch_file, failed_output, failed_manifest)

    assert not failed_output.exists()
    assert not failed_manifest.exists()
    recorded = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert recorded["issued_output_sha256"] == [issued.output_sha256]
    assert successful.probe_committed_wav(successful_output, successful_manifest).sha256 == issued.output_sha256


def test_write_durable_json_flushes_staged_manifest_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if staged manifest data were not flushed before hard-link publication."""
    module = importlib.import_module("boomearth.audio.indextts2")
    assert hasattr(module, "_write_durable_json")
    staged_manifest = tmp_path / "private" / "voice_manifest.json"
    staged_manifest.parent.mkdir()
    fsync_calls: list[int] = []
    real_fsync = module.os.fsync

    def record_fsync(file_descriptor: int) -> None:
        fsync_calls.append(file_descriptor)
        real_fsync(file_descriptor)

    monkeypatch.setattr(module.os, "fsync", record_fsync)
    module._write_durable_json(staged_manifest, {"commit": "marker"}, indent=2)

    assert fsync_calls
    assert json.loads(staged_manifest.read_text(encoding="utf-8")) == {"commit": "marker"}


def test_environment_boundary_rejects_interpreter_leaf_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locked interpreter leaf must not be a reparse target before tool use."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    narrator = _local_narrator(module, tmp_path, reference)
    real_has_reparse_component = module._has_reparse_component

    monkeypatch.setattr(
        module,
        "_has_reparse_component",
        lambda path: path == narrator.route.interpreter_path
        or real_has_reparse_component(path),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="private narration boundary"):
        narrator._validate_environment_boundary()


def test_environment_boundary_rejects_cli_leaf_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The locked IndexTTS CLI leaf must not be a reparse target before tool use."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    _write_pcm_wav(reference)
    narrator = _local_narrator(module, tmp_path, reference)
    real_has_reparse_component = module._has_reparse_component

    monkeypatch.setattr(
        module,
        "_has_reparse_component",
        lambda path: path == narrator.route.cli_script_path
        or real_has_reparse_component(path),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="private narration boundary"):
        narrator._validate_environment_boundary()


def test_batch_execution_rechecks_locked_executable_and_model_topology(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last model boundary check must run before subprocess execution."""
    module = importlib.import_module("boomearth.audio.indextts2")
    reference = tmp_path / "inputs" / "reference.wav"
    prepared_batch = tmp_path / "prepared.jsonl"
    raw_dir = tmp_path / "raw"
    reference_snapshot = tmp_path / "reference-snapshot.wav"
    _write_pcm_wav(reference)
    _write_pcm_wav(reference_snapshot)
    prepared_batch.write_text('{"text":"Codex"}\n', encoding="utf-8")
    raw_dir.mkdir()
    narrator = _local_narrator(module, tmp_path, reference)
    observed: list[str] = []

    def reject_topology() -> None:
        observed.append("topology")
        raise module.IndexTTS2ValidationError("private narration boundary is invalid")

    monkeypatch.setattr(narrator, "_validate_execution_topology", reject_topology, raising=False)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("batch subprocess must not run"),
    )

    with pytest.raises(module.IndexTTS2ValidationError, match="private narration boundary"):
        narrator._run_index_tts_batch(prepared_batch, raw_dir, reference_snapshot)

    assert observed == ["topology"]


@pytest.mark.parametrize("replacement", ["raw", "final", "manifest"])
def test_render_rejects_and_preserves_competitor_replacement_during_publish_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    """A target swapped after link is never accepted or owned by this render."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    raw_audit_wav = output_wav.with_name("narration.raw.wav")
    target_by_name = {
        "raw": raw_audit_wav,
        "final": output_wav,
        "manifest": manifest_path,
    }
    replacement_target = target_by_name[replacement]
    competitor_bytes = f"competitor-{replacement}".encode("ascii")
    real_link = module.os.link

    def link_then_replace(staged_file: Path, target_path: Path) -> None:
        real_link(staged_file, target_path)
        if target_path == replacement_target:
            target_path.unlink()
            target_path.write_bytes(competitor_bytes)

    monkeypatch.setattr(module.os, "link", link_then_replace)

    with pytest.raises(module.IndexTTS2ValidationError, match="publication"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert replacement_target.read_bytes() == competitor_bytes
    if replacement == "raw":
        assert not output_wav.exists()
        assert not manifest_path.exists()
    elif replacement == "final":
        assert raw_audit_wav.exists()
        assert not manifest_path.exists()
    else:
        assert raw_audit_wav.exists()
        assert output_wav.exists()
        with pytest.raises(module.IndexTTS2ValidationError):
            narrator.probe_committed_wav(output_wav, manifest_path)


def test_publish_token_record_failure_retains_uncommitted_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure to record a token must not risk pathname deletion of a replacement."""
    module = importlib.import_module("boomearth.audio.indextts2")
    staged_file = tmp_path / "staged.wav"
    target_path = tmp_path / "published.wav"
    staged_file.write_bytes(b"staged-audio")
    real_token = module._artifact_token
    token_calls = 0

    def fail_while_recording_target(path: Path, **kwargs: object) -> object:
        nonlocal token_calls
        token_calls += 1
        if token_calls == 2:
            raise module.IndexTTS2ValidationError("token recording failed")
        return real_token(path, **kwargs)

    monkeypatch.setattr(module, "_artifact_token", fail_while_recording_target, raising=False)

    with pytest.raises(module.IndexTTS2ValidationError, match="publication"):
        module._publish_no_clobber(staged_file, target_path)

    assert target_path.read_bytes() == b"staged-audio"
    assert staged_file.read_bytes() == b"staged-audio"


def test_render_never_deletes_manifest_replaced_after_observed_publication_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later non-cooperative replacement survives; consumers reject its invalid marker."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    raw_audit_wav = output_wav.with_name("narration.raw.wav")
    real_publish = module._publish_no_clobber
    competitor_payload = b"{}\n"

    def publish_then_replace_manifest(
        staged_file: Path,
        target_path: Path,
        **kwargs: object,
    ) -> object:
        token = real_publish(staged_file, target_path, **kwargs)
        if target_path == manifest_path:
            target_path.unlink()
            target_path.write_bytes(competitor_payload)
        return token

    monkeypatch.setattr(module, "_publish_no_clobber", publish_then_replace_manifest)

    narrator.render(batch_file, output_wav, manifest_path)

    assert manifest_path.read_bytes() == competitor_payload
    assert raw_audit_wav.exists()
    assert output_wav.exists()
    with pytest.raises(module.IndexTTS2ValidationError):
        narrator.probe_committed_wav(output_wav, manifest_path)


def test_render_keeps_original_manifest_publish_failure_when_cleanup_hash_would_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure handling must not replace the original error with an audit hash error."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    raw_audit_wav = output_wav.with_name("narration.raw.wav")
    real_publish = module._publish_no_clobber
    real_sha256 = module._sha256
    manifest_publish_failed = False

    def fail_manifest_publish(
        staged_file: Path,
        target_path: Path,
        **kwargs: object,
    ) -> object:
        nonlocal manifest_publish_failed
        if target_path == manifest_path:
            manifest_publish_failed = True
            raise module.IndexTTS2ValidationError("original manifest publish failure")
        return real_publish(staged_file, target_path, **kwargs)

    def fail_cleanup_hash(path: Path) -> str:
        if manifest_publish_failed and path == raw_audit_wav:
            raise module.IndexTTS2ValidationError("cleanup hash failure")
        return real_sha256(path)

    monkeypatch.setattr(module, "_publish_no_clobber", fail_manifest_publish)
    monkeypatch.setattr(module, "_sha256", fail_cleanup_hash)

    with pytest.raises(module.IndexTTS2ValidationError, match="original manifest publish failure"):
        narrator.render(batch_file, output_wav, manifest_path)

    assert raw_audit_wav.exists()
    assert output_wav.exists()
    assert not manifest_path.exists()


def test_render_reads_published_manifest_once_before_returning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement scheduled for a second manifest read must have no second read to race."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    real_open = Path.open
    manifest_reads = 0
    competitor_payload = b"{}\n"

    def replace_before_second_manifest_read(path: Path, *args: object, **kwargs: object) -> object:
        nonlocal manifest_reads
        if path == manifest_path:
            manifest_reads += 1
            if manifest_reads == 2:
                path.unlink()
                path.write_bytes(competitor_payload)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replace_before_second_manifest_read)

    manifest = narrator.render(batch_file, output_wav, manifest_path)

    assert manifest_reads == 1
    with real_open(manifest_path, "rb") as persisted:
        assert persisted.read() == json.dumps(
            manifest.to_dict(), ensure_ascii=False, indent=2
        ).encode("utf-8") + b"\n"


def test_canonical_probe_rejects_manifest_swapped_between_structure_and_ledger_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A probe must not validate one manifest object and hash another object."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    narrator.render(batch_file, output_wav, manifest_path)
    ledger_bound_bytes = manifest_path.read_bytes()
    manifest_payload = json.loads(ledger_bound_bytes)
    manifest_payload["pronunciation_contract_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    real_open = Path.open
    manifest_opens = 0

    def restore_ledger_bound_manifest_on_second_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal manifest_opens
        if path == manifest_path:
            manifest_opens += 1
            if manifest_opens == 2:
                manifest_path.write_bytes(ledger_bound_bytes)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", restore_ledger_bound_manifest_on_second_open)

    with pytest.raises(module.IndexTTS2ValidationError, match="uncommitted"):
        narrator.probe_committed_wav(output_wav, manifest_path)

    assert manifest_opens == 1


def test_canonical_probe_rejects_duplicate_manifest_keys_even_when_ledger_hash_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate JSON keys cannot be normalized into a ledger-authorized manifest."""
    module = importlib.import_module("boomearth.audio.indextts2")
    narrator, _reference, batch_file, output_wav, manifest_path = _narrator_ready_to_render(
        module,
        tmp_path,
        monkeypatch,
    )
    manifest = narrator.render(batch_file, output_wav, manifest_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    duplicate_bytes = (
        "{"
        + ",".join(
            [
                f'"voice_id":{json.dumps(payload["voice_id"])}',
                f'"voice_id":{json.dumps(payload["voice_id"])}',
                *[
                    f"{json.dumps(key)}:{json.dumps(value)}"
                    for key, value in payload.items()
                    if key != "voice_id"
                ],
            ]
        )
        + "}"
    ).encode("utf-8")
    manifest_path.write_bytes(duplicate_bytes)
    ledger = json.loads(narrator.route.provenance_ledger_path.read_text(encoding="utf-8"))
    ledger["issued_manifest_sha256_by_output_sha256"][manifest.output_sha256] = hashlib.sha256(
        duplicate_bytes
    ).hexdigest()
    narrator.route.provenance_ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    with pytest.raises(module.IndexTTS2ValidationError, match="uncommitted"):
        narrator.probe_committed_wav(output_wav, manifest_path)

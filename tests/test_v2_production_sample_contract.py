from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from boomearth.audio import indextts2
from boomearth.captions.align import reading_units


SCRIPT = Path(__file__).resolve().parents[1] / "automation" / "scripts" / "prepare_v2_production_sample.py"
CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)


def _load_preparer():
    spec = importlib.util.spec_from_file_location("v2_preparer_under_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("production preparer is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preparer = _load_preparer()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wav(path: Path, *, seconds: float) -> None:
    sample_rate = 8_000
    frame_count = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * frame_count)


def _format_timestamp(value: float, *, vtt: bool) -> str:
    milliseconds = int(round(value * 1_000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    separator = "." if vtt else ","
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def _write_caption_text_artifacts(media: Path, captions: list[dict[str, object]]) -> None:
    srt_blocks: list[str] = []
    vtt_blocks: list[str] = []
    for index, caption in enumerate(captions, 1):
        start = float(caption["start"])
        end = float(caption["end"])
        text = str(caption["text"])
        srt_blocks.append(
            f"{index}\n{_format_timestamp(start, vtt=False)} --> {_format_timestamp(end, vtt=False)}\n{text}"
        )
        vtt_blocks.append(
            f"{_format_timestamp(start, vtt=True)} --> {_format_timestamp(end, vtt=True)}\n{text}"
        )
    (media / "captions.srt").write_text("\n\n".join(srt_blocks) + "\n", encoding="utf-8")
    (media / "captions.vtt").write_text("WEBVTT\n\n" + "\n\n".join(vtt_blocks) + "\n", encoding="utf-8")


def _configure_locked_probe(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> Path:
    private = workspace / ".private"
    private.mkdir(parents=True)
    interpreter = private / "python.exe"
    cli_script = private / "cli_v2.py"
    model_dir = private / "models"
    reference = private / "reference.wav"
    ledger = private / "indextts2-provenance-ledger.json"
    interpreter.write_bytes(b"")
    cli_script.write_text("# test only\n", encoding="utf-8")
    model_dir.mkdir()
    _write_wav(reference, seconds=0.2)

    monkeypatch.setattr(indextts2, "WORKSPACE_ROOT", workspace)
    monkeypatch.setattr(indextts2, "LOCKED_PRIVATE_VOICE_DIRECTORY", private)
    monkeypatch.setattr(indextts2, "LOCKED_INTERPRETER_PATH", interpreter)
    monkeypatch.setattr(indextts2, "LOCKED_CLI_SCRIPT_PATH", cli_script)
    monkeypatch.setattr(indextts2, "LOCKED_MODEL_DIR", model_dir)
    monkeypatch.setattr(indextts2, "LOCKED_PROVENANCE_LEDGER_PATH", ledger)

    config_dir = workspace / "automation" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "tts-routing.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "indextts2-local",
                "model": "IndexTTS2",
                "voice_id": indextts2.CURRENT_VOICE_ID,
                "reference_audio_path": str(reference),
                "reference_audio_sha256": _sha256(reference),
                "provenance_ledger_path": str(ledger),
                "interpreter_path": str(interpreter),
                "cli_script_path": str(cli_script),
                "model_dir": str(model_dir),
                "playback_speed": 1.12,
                "fp16": True,
                "deepspeed": False,
                "cuda_kernel": False,
                "accel": False,
                "torch_compile": False,
                "used_fallback": False,
            }
        ),
        encoding="utf-8",
    )
    return ledger


def _make_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = _configure_locked_probe(monkeypatch, workspace)
    media = workspace / "active-project" / "media"
    media.mkdir(parents=True)
    captions_dir = media / "captions"
    captions_dir.mkdir()
    narration = media / "narration.wav"
    _write_wav(narration, seconds=2.4)
    narration_hash = _sha256(narration)
    manifest = media / "voice_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "provider": "indextts2-local",
                "voice_id": indextts2.CURRENT_VOICE_ID,
                "model": "IndexTTS2",
                "reference_audio_path": "private/reference.wav",
                "reference_audio_sha256": "a" * 64,
                "output_path": str(narration.resolve()),
                "output_sha256": narration_hash,
                "segment_contract_path": "private/segments.jsonl",
                "segment_contract_sha256": "b" * 64,
                "segment_count": 1,
                "playback_speed": 1.12,
                "pronunciation_contract_path": "private/pronunciation.json",
                "pronunciation_contract_sha256": "c" * 64,
                "used_fallback": False,
            }
        ),
        encoding="utf-8",
    )
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "issued_output_sha256": [narration_hash],
                "canonical_reference_provenance": [],
                "issued_manifest_sha256_by_output_sha256": {
                    narration_hash: _sha256(manifest)
                },
            }
        ),
        encoding="utf-8",
    )
    captions = [
        {"start": 0.2, "end": 1.1, "text": "第一句", "source": "volcengine-word-timestamps"},
        {"start": 1.3, "end": 2.2, "text": "第二句", "source": "volcengine-word-timestamps"},
    ]
    words = [
        {"start": 0.2, "end": 1.1, "text": "第一句", "isGap": False},
        {"start": 1.3, "end": 2.2, "text": "第二句", "isGap": False},
    ]
    (captions_dir / "asr-result.json").write_text(
        json.dumps(
            {
                "audio_info": {"duration": 2400},
                "result": {
                    "text": "第一句第二句",
                    "utterances": [
                        {
                            "words": [
                                {"text": "第一句", "start_time": 200, "end_time": 1100},
                                {"text": "第二句", "start_time": 1300, "end_time": 2200},
                            ]
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (captions_dir / "captions_words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    (captions_dir / "captions.json").write_text(json.dumps(captions, ensure_ascii=False), encoding="utf-8")
    _write_caption_text_artifacts(captions_dir, captions)
    (captions_dir / "caption-qc.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "timing_source": "volcengine-word-timestamps",
                "alignment_coverage": 1.0,
                "minimum_coverage": 0.90,
                "script_characters": 6,
                "matched_characters": 6,
                "asr_characters": 6,
                "word_units": 2,
                "caption_count": 2,
                "overlap_count": 0,
                "max_reading_units_per_second": 3.333,
                "short_fragments": [],
                "split_connectors": [],
                "narration_sha256": narration_hash,
                "source_media": narration.name,
                "asr_resource_id": "local-contract-fixture",
                "warnings": [],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    return workspace, narration, manifest, captions_dir, workspace / "renders" / "v2-project"


def _set_caption_speed_fixture(captions_dir: Path, text: str) -> tuple[float, float]:
    captions_path = captions_dir / "captions.json"
    captions = json.loads(captions_path.read_text(encoding="utf-8"))
    captions[0]["text"] = text
    captions_path.write_text(json.dumps(captions, ensure_ascii=False), encoding="utf-8")
    _write_caption_text_artifacts(captions_dir, captions)

    words_path = captions_dir / "captions_words.json"
    words = json.loads(words_path.read_text(encoding="utf-8"))
    words[0]["text"] = text
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    asr_path = captions_dir / "asr-result.json"
    asr = json.loads(asr_path.read_text(encoding="utf-8"))
    asr["result"]["utterances"][0]["words"][0]["text"] = text
    asr["result"]["text"] = text + words[1]["text"]
    asr_path.write_text(json.dumps(asr, ensure_ascii=False), encoding="utf-8")

    actual = max(reading_units(item["text"]) / (item["end"] - item["start"]) for item in captions)
    reported = round(actual, 3)
    qc_path = captions_dir / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    character_count = sum(character.isalnum() for character in asr["result"]["text"])
    qc.update(
        {
            "alignment_coverage": 1.0,
            "script_characters": character_count,
            "matched_characters": character_count,
            "asr_characters": character_count,
            "max_reading_units_per_second": reported,
            "warnings": (
                [f"maximum reading speed {actual:.2f} units/s exceeds preferred 9.0"]
                if 9.0 < actual <= 12.0
                else []
            ),
            "errors": [],
        }
    )
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    return actual, reported


def _prepare(
    workspace: Path, narration: Path, manifest: Path, captions: Path, output: Path
) -> object:
    return preparer.prepare_project(
        narration=narration,
        manifest=manifest,
        captions_dir=captions,
        output_dir=output,
        workspace_root=workspace,
    )


def test_preparer_snapshots_canonical_inputs_and_preserves_duration_and_cue_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if preparation changed active inputs or re-timed renderer assets."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    input_bytes = {path: path.read_bytes() for path in (narration, manifest, *(captions / name for name in CAPTION_FILES))}

    prepared = _prepare(workspace, narration, manifest, captions, output)

    assert output.is_dir()
    assert getattr(prepared, "output_dir") == output
    assert getattr(prepared, "duration_seconds") == pytest.approx(2.4)
    assert (output / "index.html").is_file()
    for source, expected in input_bytes.items():
        assert source.read_bytes() == expected
        assert (output / "media" / source.name).read_bytes() == expected
    rendered = (output / "index.html").read_text(encoding="utf-8")
    assert 'data-duration="2.400000"' in rendered
    assert 'data-caption-safe-zone="anchor-dark"' in rendered
    assert "__DURATION_SECONDS__" not in rendered
    assert "__SCENE_SPLIT_SECONDS__" not in rendered
    assert json.loads((output / "media" / "captions.json").read_text(encoding="utf-8")) == json.loads(
        (captions / "captions.json").read_text(encoding="utf-8")
    )
    scene_times = [float(value) for value in re.findall(r'data-scene-(?:one|two)-(?:start|end)="([^"]+)"', rendered)]
    assert len(scene_times) == 4
    assert all(math.isfinite(value) and 0.0 <= value <= 2.4 for value in scene_times)
    assert scene_times[0] == 0.0
    assert scene_times[-1] == pytest.approx(2.4)
    dependency = output / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    assert dependency.is_file()
    assert dependency.read_bytes() == (SCRIPT.parents[2] / "node_modules" / "gsap" / "dist" / "gsap.min.js").read_bytes()


def test_preparer_rejects_caption_artifacts_in_the_media_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a second production caption topology remained accepted."""

    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    media = narration.parent
    for name in CAPTION_FILES:
        (captions / name).replace(media / name)
    captions.rmdir()

    with pytest.raises(ValueError, match="production render inputs are invalid"):
        _prepare(workspace, narration, manifest, media, output)

    assert not output.exists()


@pytest.mark.parametrize(
    "change",
    [
        "v1-manifest",
        "nonlocal-provider",
        "fallback",
        "altered-wav",
        "altered-manifest-binding",
        "synthetic-manifest",
        "synthetic-qc",
        "failed-qc",
        "low-coverage-qc",
        "missing-caption-file",
        "overlapping-cues",
        "out-of-range-cues",
    ],
)
def test_preparer_rejects_noncanonical_production_inputs_without_publishing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Would fail if any non-production artifact reached a disposable renderer directory."""
    workspace, narration, manifest_path, captions_dir, output = _make_inputs(tmp_path, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if change == "v1-manifest":
        manifest["voice_id"] = "user-indextts2-calm-v1"
    elif change == "nonlocal-provider":
        manifest["provider"] = "other-provider"
    elif change == "fallback":
        manifest["used_fallback"] = True
    elif change == "altered-wav":
        _write_wav(narration, seconds=2.0)
    elif change == "altered-manifest-binding":
        manifest["reference_audio_sha256"] = "d" * 64
    elif change == "synthetic-manifest":
        manifest["sample_mode"] = True
    elif change in {"synthetic-qc", "failed-qc", "low-coverage-qc"}:
        qc_path = captions_dir / "caption-qc.json"
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        if change == "synthetic-qc":
            qc["timing_source"] = "synthetic-local-sample"
        elif change == "failed-qc":
            qc["status"] = "fail"
        else:
            qc["alignment_coverage"] = 0.89
        qc_path.write_text(json.dumps(qc), encoding="utf-8")
    elif change == "missing-caption-file":
        (captions_dir / "captions.vtt").unlink()
    else:
        captions_path = captions_dir / "captions.json"
        records = json.loads(captions_path.read_text(encoding="utf-8"))
        if change == "overlapping-cues":
            records[1]["start"] = 1.0
        else:
            records[1]["end"] = 2.5
        captions_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    if change in {"v1-manifest", "nonlocal-provider", "fallback", "altered-manifest-binding", "synthetic-manifest"}:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        _prepare(workspace, narration, manifest_path, captions_dir, output)

    assert not output.exists()
    rendered_error = str(raised.value)
    assert str(narration) not in rendered_error
    assert str(manifest_path) not in rendered_error


def test_preparer_rejects_output_collision_and_external_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if preparation could overwrite an existing or external directory."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    output.mkdir(parents=True)

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)
    assert output.is_dir()

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, tmp_path / "outside")
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("change", ["wrong-narration-name", "wrong-manifest-name", "non-sibling-manifest", "non-sibling-captions"])
def test_preparer_requires_canonical_input_names_and_sibling_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Would fail if an arbitrary file layout could impersonate the production render inputs."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    if change == "wrong-narration-name":
        renamed = narration.with_name("other.wav")
        narration.rename(renamed)
        narration = renamed
    elif change == "wrong-manifest-name":
        renamed = manifest.with_name("other.json")
        manifest.rename(renamed)
        manifest = renamed
    elif change == "non-sibling-manifest":
        moved = workspace / "other" / "voice_manifest.json"
        moved.parent.mkdir()
        manifest.replace(moved)
        manifest = moved
    else:
        moved = workspace / "other-captions"
        captions.rename(moved)
        captions = moved

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


@pytest.mark.parametrize(
    "change",
    [
        "empty-asr",
        "missing-resource-id",
        "wrong-minimum-coverage",
        "coverage-over-one",
        "caption-count",
        "word-units",
        "nonempty-errors",
    ],
)
def test_preparer_requires_canonical_volcengine_asr_and_qc_linkage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Would fail if a weakened ASR/QC record could replace canonical word-level provenance."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    if change == "empty-asr":
        (captions / "asr-result.json").write_text("{}", encoding="utf-8")
    else:
        qc_path = captions / "caption-qc.json"
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        if change == "missing-resource-id":
            qc["asr_resource_id"] = ""
        elif change == "wrong-minimum-coverage":
            qc["minimum_coverage"] = 0.89
        elif change == "coverage-over-one":
            qc["alignment_coverage"] = 1.01
        elif change == "caption-count":
            qc["caption_count"] = 1
        elif change == "word-units":
            qc["word_units"] = 1
        else:
            qc["errors"] = ["failed"]
        qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


@pytest.mark.parametrize("change", ["unrelated-word", "out-of-range-word", "inconsistent-result-text"])
def test_preparer_binds_raw_asr_words_to_caption_word_units_and_staged_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Would fail if unrelated or out-of-range ASR evidence could support renderer captions."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    asr_path = captions / "asr-result.json"
    asr = json.loads(asr_path.read_text(encoding="utf-8"))
    if change == "unrelated-word":
        asr["result"]["utterances"][0]["words"][0]["text"] = "无关"
    elif change == "out-of-range-word":
        asr["result"]["utterances"][0]["words"][1]["end_time"] = 2_500
    else:
        asr["result"]["text"] = "无关文本"
    asr_path.write_text(json.dumps(asr, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


@pytest.mark.parametrize(
    "change",
    [
        "negative-script-characters",
        "non-numeric-matched-characters",
        "matched-exceeds-script",
        "incoherent-coverage",
        "negative-asr-characters",
        "non-numeric-asr-characters",
        "negative-reading-rate",
        "non-finite-reading-rate",
    ],
)
def test_preparer_requires_coherent_canonical_qc_numeric_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Would fail if malformed QC counters could be detached from passed ASR evidence."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    qc_path = captions / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    if change == "negative-script-characters":
        qc["script_characters"] = -1
    elif change == "non-numeric-matched-characters":
        qc["matched_characters"] = "6"
    elif change == "matched-exceeds-script":
        qc["matched_characters"] = 7
    elif change == "incoherent-coverage":
        qc["alignment_coverage"] = 0.95
    elif change == "negative-asr-characters":
        qc["asr_characters"] = -1
    elif change == "non-numeric-asr-characters":
        qc["asr_characters"] = "6"
    elif change == "negative-reading-rate":
        qc["max_reading_units_per_second"] = -0.1
    else:
        qc["max_reading_units_per_second"] = float("nan")
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


@pytest.mark.parametrize("reported", [3.0, 12.001])
def test_preparer_requires_reported_reading_speed_to_match_validated_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reported: float
) -> None:
    """Would fail if a passed QC rate could be forged independently of caption text and duration."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    qc_path = captions / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["max_reading_units_per_second"] = reported
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


def test_preparer_rejects_a_forged_pass_when_validated_reading_speed_exceeds_twelve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a caption above the canonical hard speed limit could retain pass/empty errors."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    actual, reported = _set_caption_speed_fixture(captions, "甲乙丙丁戊己庚辛壬癸子丑寅")
    assert actual > 12.0
    qc_path = captions / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["max_reading_units_per_second"] = reported
    qc["status"] = "pass"
    qc["warnings"] = []
    qc["errors"] = []
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


@pytest.mark.parametrize("change", ["missing-canonical-warning", "wrong-warning", "unexpected-warning"])
def test_preparer_requires_canonical_reading_speed_warning_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Would fail if passed QC warnings did not match the canonical 9-to-12 speed band."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    qc_path = captions / "caption-qc.json"
    if change == "unexpected-warning":
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        qc["warnings"] = ["maximum reading speed 3.33 units/s exceeds preferred 9.0"]
    else:
        actual, _reported = _set_caption_speed_fixture(captions, "甲乙丙丁戊己庚辛壬癸")
        assert 9.0 < actual <= 12.0
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        qc["warnings"] = [] if change == "missing-canonical-warning" else ["wrong warning"]
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


def test_preparer_publishes_the_captured_bytes_when_active_inputs_change_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if publication reopened active files after their validated capture."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    expected = narration.read_bytes()
    captured: list[Path] = []

    def mutate_active_after_capture(stage: Path) -> None:
        if not captured:
            _write_wav(narration, seconds=2.0)
            captured.append(stage)

    monkeypatch.setattr(preparer, "_after_capture", mutate_active_after_capture)

    _prepare(workspace, narration, manifest, captions, output)

    assert captured
    assert narration.read_bytes() != expected
    assert (output / "media" / "narration.wav").read_bytes() == expected


def test_preparer_detects_source_mutation_during_capture_before_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a source replacement during one capture could pass as a stable snapshot."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)

    def mutate_during_narration_capture(source: Path) -> None:
        if Path(source) == narration:
            _write_wav(narration, seconds=2.0)

    monkeypatch.setattr(preparer, "_after_capture_read", mutate_during_narration_capture, raising=False)

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


def test_preparer_rejects_reparse_substitution_during_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a capture path became a reparse point between open and publication."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    backup = narration.with_name("narration-original.wav")

    def replace_with_reparse(source: Path) -> None:
        if Path(source) != narration:
            return
        narration.replace(backup)
        try:
            os.symlink(backup, narration)
        except OSError:
            result = subprocess.run(
                ["cmd", "/c", "mklink", str(narration), str(backup)],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode:
                backup.replace(narration)
                pytest.skip("reparse-point creation is unavailable in this Windows test environment")

    monkeypatch.setattr(preparer, "_after_capture_read", replace_with_reparse, raising=False)
    try:
        with pytest.raises(ValueError):
            _prepare(workspace, narration, manifest, captions, output)
    finally:
        if narration.exists() or narration.is_symlink():
            narration.unlink()
        if backup.exists():
            backup.replace(narration)

    assert not output.exists()


def test_preparer_rejects_caption_directory_junction_substitution_during_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a captured file could continue through a reparse parent after opening."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    original = captions.with_name("media-original")

    def replace_parent_with_junction(source: Path) -> None:
        if Path(source) != captions / "asr-result.json":
            return
        captions.replace(original)
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(captions), str(original)],
            capture_output=True,
            check=False,
            text=True,
        )
        if junction.returncode:
            original.replace(captions)
            pytest.skip("junction creation is unavailable in this Windows test environment")

    monkeypatch.setattr(preparer, "_after_capture_read", replace_parent_with_junction)
    try:
        with pytest.raises(ValueError):
            _prepare(workspace, narration, manifest, captions, output)
    finally:
        if captions.exists() or captions.is_symlink():
            os.rmdir(captions)
        if original.exists():
            original.replace(captions)

    assert not output.exists()


def test_preparer_preserves_a_foreign_destination_created_at_publish_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if concurrent destination creation could be overwritten or removed."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)

    def create_foreign_destination(destination: Path) -> None:
        destination.mkdir(parents=True)
        (destination / "foreign.txt").write_text("preserve", encoding="utf-8")

    monkeypatch.setattr(preparer, "_before_publish", create_foreign_destination, raising=False)

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert (output / "foreign.txt").read_text(encoding="utf-8") == "preserve"


def test_preparer_hides_partial_stage_until_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if renderer consumers could observe a partially assembled output directory."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    observed: list[Path] = []

    def inspect_stage(stage: Path, destination: Path) -> None:
        assert stage.is_dir()
        assert (stage / "index.html").is_file()
        assert not destination.exists()
        observed.append(stage)

    monkeypatch.setattr(preparer, "_after_stage_assembled", inspect_stage, raising=False)

    _prepare(workspace, narration, manifest, captions, output)

    assert observed
    assert output.is_dir()
    assert not observed[0].exists()


def test_failed_publication_preserves_private_stage_and_foreign_replacement_at_delete_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if cleanup deleted a replacement after its final identity check."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    output.parent.mkdir()
    foreign = workspace / "runtime" / "foreign-replacement"
    foreign.mkdir(parents=True)
    (foreign / "foreign.txt").write_text("preserve", encoding="utf-8")
    stages: list[Path] = []

    def remember_stage(stage: Path, _destination: Path) -> None:
        stages.append(stage)

    def create_foreign_destination(destination: Path) -> None:
        destination.mkdir()

    actual_rmtree = preparer.shutil.rmtree

    def replace_quarantine_then_remove(path: Path | str, *args: object, **kwargs: object) -> None:
        target = Path(path)
        target.replace(target.with_name("owned-stage-recovery"))
        foreign.replace(target)
        actual_rmtree(target, *args, **kwargs)

    monkeypatch.setattr(preparer, "_after_stage_assembled", remember_stage)
    monkeypatch.setattr(preparer, "_before_publish", create_foreign_destination)
    monkeypatch.setattr(preparer.shutil, "rmtree", replace_quarantine_then_remove)

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert len(stages) == 1
    assert stages[0].is_relative_to(workspace / "runtime")
    assert stages[0].is_dir()
    assert (foreign / "foreign.txt").read_text(encoding="utf-8") == "preserve"


def test_failed_publication_never_removes_a_foreign_cleanup_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if cleanup-root deletion could remove an adversarial empty directory."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    output.parent.mkdir()
    foreign_root = workspace / "runtime" / "foreign-cleanup-root"
    foreign_root.mkdir(parents=True)
    stages: list[Path] = []
    replacement_done = False

    def remember_stage(stage: Path, _destination: Path) -> None:
        stages.append(stage)

    def create_foreign_destination(destination: Path) -> None:
        destination.mkdir()

    actual_rmdir = Path.rmdir

    def replace_cleanup_root_then_remove(path: Path) -> None:
        nonlocal replacement_done
        if not replacement_done and ".cleanup-" in path.name:
            replacement_done = True
            path.replace(path.with_name("owned-cleanup-root"))
            foreign_root.replace(path)
        actual_rmdir(path)

    monkeypatch.setattr(preparer, "_after_stage_assembled", remember_stage)
    monkeypatch.setattr(preparer, "_before_publish", create_foreign_destination)
    monkeypatch.setattr(Path, "rmdir", replace_cleanup_root_then_remove)

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert len(stages) == 1
    assert stages[0].is_relative_to(workspace / "runtime")
    assert stages[0].is_dir()
    assert foreign_root.is_dir()


def test_render_project_resolves_its_local_gsap_dependency_and_keeps_cues_visible_at_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if the isolated renderer missed GSAP or faded a canonical cue inside its bounds."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    _prepare(workspace, narration, manifest, captions, output)
    dependency = output / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    assert dependency.is_file()
    assert subprocess.run(["node", "--check", str(dependency)], capture_output=True, check=False).returncode == 0
    runner = r'''
const fs = require("fs");
const root = process.argv[1];
const html = fs.readFileSync(root + "/index.html", "utf8");
const scripts = [...html.matchAll(/<script(?: src="[^"]+")?>([\s\S]*?)<\/script>/g)];
const inline = scripts[scripts.length - 1][1];
const fixtureCaptions = JSON.parse(fs.readFileSync(root + "/media/captions.json", "utf8"));
const nodes = {"caption-anchor": {appendChild() {}}};
global.window = {__timelines: {}};
global.document = {createElement() { return {}; }, getElementById(id) { return nodes[id] || (nodes[id] = {}); }};
global.XMLHttpRequest = function () { this.open = function () {}; this.send = function () { this.status = 200; this.responseText = JSON.stringify(this.path.includes("caption-qc") ? {status:"pass", timing_source:"volcengine-word-timestamps"} : fixtureCaptions); }; };
Object.defineProperty(global.XMLHttpRequest.prototype, "open", {value: function (_method, path) { this.path = path; }});
const events = [];
global.gsap = {timeline() { return {from(target, vars, at) { events.push(["from", target.id || target, vars, at]); return this; }, to(target, vars, at) { events.push(["to", target.id || target, vars, at]); return this; }, set(target, vars, at) { events.push(["set", target.id || target, vars, at]); return this; }}; }};
eval(inline);
console.log(JSON.stringify({captions: fixtureCaptions, events}));
''';
    result = subprocess.run(
        ["node", "-e", runner, str(output)], capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    timeline = json.loads(result.stdout.decode("utf-8"))
    for index, cue in enumerate(timeline["captions"]):
        events = [event for event in timeline["events"] if event[1] == f"caption-group-{index}"]
        assert ["set", f"caption-group-{index}", {"opacity": 1}, cue["start"]] in events
        hidden = [event for event in events if event[0] == "set" and event[2] == {"opacity": 0}]
        assert hidden and float(hidden[-1][3]) > float(cue["end"])


@pytest.mark.parametrize("change", ["invalid-word-schema", "srt-cue-mismatch", "vtt-cue-mismatch"])
def test_preparer_requires_the_existing_six_file_caption_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Would fail if a malformed word record or diverging text subtitle could render."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    if change == "invalid-word-schema":
        (captions / "captions_words.json").write_text(
            json.dumps([{"start": 0.2, "end": 1.1, "text": "第一句"}], ensure_ascii=False),
            encoding="utf-8",
        )
    elif change == "srt-cue-mismatch":
        (captions / "captions.srt").write_text(
            "1\n00:00:00,200 --> 00:00:01,100\n错误\n", encoding="utf-8"
        )
    else:
        (captions / "captions.vtt").write_text(
            "WEBVTT\n\n00:00:00.200 --> 00:00:01.100\n错误\n", encoding="utf-8"
        )

    with pytest.raises(ValueError):
        _prepare(workspace, narration, manifest, captions, output)

    assert not output.exists()


def test_preparer_rejects_reparse_caption_input_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a linked caption input could escape the local artifact boundary."""
    workspace, narration, manifest, captions, output = _make_inputs(tmp_path, monkeypatch)
    linked_captions = workspace / "linked-captions"
    try:
        os.symlink(captions, linked_captions, target_is_directory=True)
    except OSError:
        junction = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked_captions), str(captions)],
            capture_output=True,
            check=False,
            text=True,
        )
        if junction.returncode != 0:
            pytest.skip("reparse-point creation is unavailable in this Windows test environment")

    try:
        with pytest.raises(ValueError):
            _prepare(workspace, narration, manifest, linked_captions, output)
    finally:
        if linked_captions.exists() or linked_captions.is_symlink():
            os.rmdir(linked_captions)

    assert not output.exists()


def test_preparer_help_is_redacted_and_declares_offline_operation() -> None:
    """Would fail if the CLI implied network work or echoed supplied private paths."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "network" in result.stdout.lower()
    assert "narration.wav" not in result.stdout
    assert "voice_manifest.json" not in result.stdout

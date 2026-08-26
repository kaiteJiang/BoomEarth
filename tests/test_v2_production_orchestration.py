"""Offline production orchestration and archive-gate behavior tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys
import wave
from types import SimpleNamespace

import pytest

from boomearth.audio import indextts2
from boomearth.captions.align import reading_units


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "automation" / "scripts" / "run_v2_production_sample.py"
CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)


def _load_orchestrator():
    spec = importlib.util.spec_from_file_location("v2_orchestrator_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_caption_render_qc_accepts_registered_anchor_dark_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orchestrator = _load_orchestrator()
    project = tmp_path / "project"
    final_media = project / "成片" / "final.mp4"
    captions = project / "工程" / "media" / "captions" / "captions.json"
    output = project / "质检" / "caption-render-qc.json"
    final_media.parent.mkdir(parents=True)
    captions.parent.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    final_media.write_bytes(b"final-media")
    captions.write_text(
        json.dumps([{"start": 0.0, "end": 1.0, "text": "测试字幕"}]),
        encoding="utf-8",
    )
    frame = bytearray(b"\xff" * (1920 * 1080 * 3))
    panel_start = (1080 - 100) * 1920 * 3
    for index in range(panel_start, panel_start + 600 * 3, 3):
        frame[index : index + 3] = b"\x55\x55\x55"
    monkeypatch.setattr(
        orchestrator.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=bytes(frame)),
    )

    orchestrator._render_caption_qc(final_media, captions, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "pass"
    assert payload["frame_checks"][0]["dark_pixels"] == 600


def _write_production_caption_package(project: object) -> None:
    narration = project.narration_path
    narration.parent.mkdir(parents=True, exist_ok=True)
    narration.write_bytes(b"canonical-final-wav")
    project.manifest_path.write_text("{}\n", encoding="utf-8")
    for name in CAPTION_FILES[:-1]:
        value = [{"text": "fixture narration"}] if name == "captions.json" else []
        (project.captions_dir / name).write_text(json.dumps(value), encoding="utf-8")
    (project.captions_dir / "caption-qc.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "timing_source": "volcengine-word-timestamps",
                "alignment_coverage": 1.0,
                "source_media": "narration.wav",
                "asr_resource_id": "fixture-resource",
                "narration_sha256": hashlib.sha256(narration.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def _runtime_entries(workspace: Path) -> list[Path]:
    runtime = workspace / "runtime"
    return (
        [
            path
            for path in runtime.glob("v2-production-*")
            if len(path.name.removeprefix("v2-production-")) == 32
        ]
        if runtime.is_dir()
        else []
    )


def _prepared_runtime(preparer_calls: list[tuple[Path, Path, Path, Path]]) -> object:
    def prepare_project(**kwargs: object) -> object:
        narration = Path(kwargs["narration"])
        manifest = Path(kwargs["manifest"])
        captions_dir = Path(kwargs["captions_dir"])
        output_dir = Path(kwargs["output_dir"])
        preparer_calls.append((narration, manifest, captions_dir, output_dir))
        output_dir.mkdir(parents=True)
        snapshot_media = output_dir / "media"
        snapshot_media.mkdir()
        (snapshot_media / "narration.wav").write_bytes(narration.read_bytes())
        (output_dir / "index.html").write_text("offline renderer fixture", encoding="utf-8")
        return SimpleNamespace(output_dir=output_dir, duration_seconds=10.0)

    return prepare_project


def _write_authenticated_receipt(orchestrator: object, project: object, active: Path, handoff: Path) -> None:
    publication = orchestrator._capture_publication_manifest(active, project, handoff)
    (active / "delivery-report.json").write_text(
        json.dumps(
            {
                "status": "pass", "mode": "production",
                "rules": [{"id": "delivery-hard-gates", "status": "pass"}],
                "artifacts": {
                    "handoff": "交接稿.md", "narration": "工程/media/narration.wav", "final": "成片/",
                    "contact_sheet": "质检/contact-sheet.jpg", "contact_sheet_qc": "质检/contact-sheet-qc.json",
                },
                "artifact_sha256": publication, "media": {}, "skipped_stages": [],
            }
        ) + "\n",
        encoding="utf-8",
    )


def _make_directory_reparse(link: Path, target: Path) -> None:
    """Create a directory reparse point or skip only when this host forbids it."""

    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        pytest.skip("Windows directory reparse creation is unavailable")


def _write_synthetic_wav(path: Path, *, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\0\0" * round(8_000 * seconds))


def _format_cue(seconds: float, *, vtt: bool) -> str:
    milliseconds = round(seconds * 1_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"00:{minutes:02d}:{whole_seconds:02d}{'.' if vtt else ','}{milliseconds:03d}"


def _write_canonical_synthetic_inputs(project: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Build a generated-only fixture that passes the real Task 1 and delivery provenance contracts."""

    workspace = project.workspace_root
    synthetic_root = workspace / "synthetic-fixture"
    synthetic_root.mkdir()
    reference = synthetic_root / "reference.wav"
    _write_synthetic_wav(reference, seconds=0.2)
    interpreter = synthetic_root / "python.exe"
    cli_script = synthetic_root / "cli_v2.py"
    model_dir = synthetic_root / "models"
    ledger = synthetic_root / "provenance-ledger.json"
    interpreter.write_bytes(b"")
    cli_script.write_text("# generated integration fixture\n", encoding="utf-8")
    model_dir.mkdir()
    for name, value in (
        ("WORKSPACE_ROOT", workspace),
        ("LOCKED_PRIVATE_VOICE_DIRECTORY", synthetic_root),
        ("LOCKED_INTERPRETER_PATH", interpreter),
        ("LOCKED_CLI_SCRIPT_PATH", cli_script),
        ("LOCKED_MODEL_DIR", model_dir),
        ("LOCKED_PROVENANCE_LEDGER_PATH", ledger),
    ):
        monkeypatch.setattr(indextts2, name, value)

    config = workspace / "automation" / "config"
    config.mkdir(parents=True)
    reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
    (config / "tts-routing.json").write_text(
        json.dumps(
            {
                "schema_version": 1, "provider": "indextts2-local", "model": "IndexTTS2",
                "voice_id": indextts2.CURRENT_VOICE_ID, "reference_audio_path": str(reference),
                "reference_audio_sha256": reference_hash, "provenance_ledger_path": str(ledger),
                "interpreter_path": str(interpreter), "cli_script_path": str(cli_script),
                "model_dir": str(model_dir), "playback_speed": 1.12, "fp16": True,
                "deepspeed": False, "cuda_kernel": False, "accel": False,
                "torch_compile": False, "used_fallback": False,
            }
        ),
        encoding="utf-8",
    )
    _write_synthetic_wav(project.narration_path, seconds=2.4)
    narration_hash = hashlib.sha256(project.narration_path.read_bytes()).hexdigest()
    manifest = {
        "provider": "indextts2-local", "voice_id": indextts2.CURRENT_VOICE_ID, "model": "IndexTTS2",
        "reference_audio_path": str(reference), "reference_audio_sha256": reference_hash,
        "output_path": str(project.narration_path.resolve()), "output_sha256": narration_hash,
        "segment_contract_path": "synthetic-segments.jsonl", "segment_contract_sha256": "b" * 64,
        "segment_count": 1, "playback_speed": 1.12,
        "pronunciation_contract_path": "synthetic-pronunciation.json", "pronunciation_contract_sha256": "c" * 64,
        "used_fallback": False,
    }
    project.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_hash = hashlib.sha256(project.manifest_path.read_bytes()).hexdigest()
    ledger.write_text(
        json.dumps(
            {
                "schema_version": 1, "issued_output_sha256": [narration_hash],
                "canonical_reference_provenance": [{"voice_id": indextts2.CURRENT_VOICE_ID, "reference_audio_path": str(reference), "reference_audio_sha256": reference_hash}],
                "issued_manifest_sha256_by_output_sha256": {narration_hash: manifest_hash},
            }
        ),
        encoding="utf-8",
    )
    captions = [
        {"start": 0.2, "end": 1.1, "text": "SYNTHETIC ONE", "source": "volcengine-word-timestamps"},
        {"start": 1.3, "end": 2.2, "text": "SYNTHETIC TWO", "source": "volcengine-word-timestamps"},
    ]
    words = [{"start": item["start"], "end": item["end"], "text": item["text"], "isGap": False} for item in captions]
    (project.captions_dir / "captions.json").write_text(json.dumps(captions), encoding="utf-8")
    (project.captions_dir / "captions_words.json").write_text(json.dumps(words), encoding="utf-8")
    (project.captions_dir / "asr-result.json").write_text(
        json.dumps({"result": {"text": "SYNTHETIC ONESYNTHETIC TWO", "utterances": [{"words": [
            {"text": item["text"], "start_time": round(float(item["start"]) * 1_000), "end_time": round(float(item["end"]) * 1_000)} for item in captions
        ]}]}}),
        encoding="utf-8",
    )
    for extension, vtt in (("srt", False), ("vtt", True)):
        blocks = []
        for index, item in enumerate(captions, 1):
            prefix = "" if vtt else f"{index}\n"
            blocks.append(f"{prefix}{_format_cue(float(item['start']), vtt=vtt)} --> {_format_cue(float(item['end']), vtt=vtt)}\n{item['text']}")
        header = "WEBVTT\n\n" if vtt else ""
        (project.captions_dir / f"captions.{extension}").write_text(header + "\n\n".join(blocks) + "\n", encoding="utf-8")
    asr_text = "SYNTHETIC ONESYNTHETIC TWO"
    characters = sum(character.isalnum() for character in asr_text)
    maximum_speed = max(reading_units(str(item["text"])) / (float(item["end"]) - float(item["start"])) for item in captions)
    (project.captions_dir / "caption-qc.json").write_text(
        json.dumps(
            {
                "status": "pass", "timing_source": "volcengine-word-timestamps", "alignment_coverage": 1.0,
                "minimum_coverage": 0.90, "script_characters": characters, "matched_characters": characters,
                "asr_characters": characters, "word_units": 2, "caption_count": 2, "overlap_count": 0,
                "max_reading_units_per_second": round(maximum_speed, 3), "short_fragments": [],
                "split_connectors": [], "narration_sha256": narration_hash, "source_media": "narration.wav",
                "asr_resource_id": "generated-offline-fixture", "warnings": [], "errors": [],
            }
        ),
        encoding="utf-8",
    )


def test_initializer_creates_only_the_active_production_contract(tmp_path: Path) -> None:
    """Would fail if initialization exposed paths outside its dated active project."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="v2-safe"
    )

    assert project.active_dir.name.endswith("-v2-safe")
    assert project.narration_path == project.active_dir / "工程" / "media" / "narration.wav"
    assert project.manifest_path == project.active_dir / "工程" / "media" / "voice_manifest.json"
    assert project.captions_dir == project.active_dir / "工程" / "media" / "captions"
    assert project.render_path == project.active_dir / "成片" / "boomearth-v2-production.mp4"
    assert project.caption_render_qc_path == project.active_dir / "质检" / "caption-render-qc.json"
    assert project.handoff_path == project.active_dir / "交接稿.md"
    assert project.captions_dir.is_dir()
    assert "status: 制作中" in project.handoff_path.read_text(encoding="utf-8")


def test_loader_reconstructs_only_a_canonical_existing_active_project(tmp_path: Path) -> None:
    """Would fail if finalize required callers to construct trusted project paths themselves."""

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    created = orchestrator.initialize_production_project(
        workspace_root=workspace,
        archive_slug="existing-project",
    )

    loaded = orchestrator.load_production_project(
        workspace_root=workspace,
        active_project=created.active_dir.name,
    )

    assert loaded == created
    with pytest.raises(ValueError, match="production project load failed"):
        orchestrator.load_production_project(
            workspace_root=workspace,
            active_project=str(created.active_dir),
        )


def test_finalize_cli_requires_explicit_audio_approval_before_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Would fail if the operator CLI could render an existing project without its audio gate."""

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    project = orchestrator.initialize_production_project(
        workspace_root=workspace,
        archive_slug="cli-approval",
    )
    stale_report = project.active_dir / "delivery-report.json"
    stale_report.write_text("old-pass", encoding="utf-8")

    exit_code = orchestrator.main(
        [
            "finalize",
            "--workspace-root",
            str(workspace),
            "--active-project",
            project.active_dir.name,
            "--approved-narration-sha256",
            "0" * 64,
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().out.strip() == "rule=audio-approval-required"
    assert stale_report.read_text(encoding="utf-8") == "old-pass"
    assert not project.archive_dir.exists()


def test_initializer_rejects_active_or_archive_collisions(tmp_path: Path) -> None:
    """Would fail if a second production project could overwrite a dated project or archive."""

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    orchestrator.initialize_production_project(workspace_root=workspace, archive_slug="collision")

    with pytest.raises(ValueError):
        orchestrator.initialize_production_project(workspace_root=workspace, archive_slug="collision")


def test_handle_relative_archive_rename_cannot_follow_a_replaced_parent_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a parent replacement after handle open redirected archive publication."""

    orchestrator = _load_orchestrator()
    source = tmp_path / "active"
    archive_parent = tmp_path / "archive-parent"
    source.mkdir()
    archive_parent.mkdir()
    destination = archive_parent / "published"
    retained = tmp_path / "retained-parent"

    def replace_parent(_stage: Path, _destination: Path) -> None:
        archive_parent.rename(retained)
        archive_parent.mkdir()

    monkeypatch.setattr(orchestrator, "_after_rename_handles_open", replace_parent)
    orchestrator._move_directory_no_replace(
        source,
        destination,
        expected_source_identity=orchestrator._directory_identity(source),
        expected_parent_identity=orchestrator._directory_identity(archive_parent),
    )

    assert (retained / "published").is_dir()
    assert not (archive_parent / "published").exists()


def test_archive_move_rejects_source_swap_before_opening_approved_handles(tmp_path: Path) -> None:
    """Would fail if a path swap before handle acquisition could move an unapproved source tree."""

    orchestrator = _load_orchestrator()
    source = tmp_path / "active"
    parent = tmp_path / "archive"
    source.mkdir()
    parent.mkdir()
    approved_source = orchestrator._directory_identity(source)
    approved_parent = orchestrator._directory_identity(parent)
    retained = tmp_path / "retained"
    source.rename(retained)
    source.mkdir()

    with pytest.raises(RuntimeError):
        orchestrator._move_directory_no_replace(
            source,
            parent / "published",
            expected_source_identity=approved_source,
            expected_parent_identity=approved_parent,
        )

    assert retained.is_dir()
    assert source.is_dir()
    assert not (parent / "published").exists()


def test_final_publish_uses_opened_source_handle_for_approved_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if final publication re-opened a swapped stage pathname instead of its approved handle."""

    orchestrator = _load_orchestrator()
    source = tmp_path / "stage"
    parent = tmp_path / "archive"
    source.mkdir()
    parent.mkdir()
    (source / "evidence.txt").write_text("approved", encoding="utf-8")
    manifest = {"evidence.txt": hashlib.sha256(b"approved").hexdigest()}
    source_identity = orchestrator._directory_identity(source)
    parent_identity = orchestrator._directory_identity(parent)
    retained = tmp_path / "retained-stage"

    def swap_after_open(stage: Path, _destination: Path) -> None:
        stage.rename(retained)
        stage.mkdir()
        (stage / "evidence.txt").write_text("unapproved", encoding="utf-8")

    monkeypatch.setattr(orchestrator, "_after_rename_handles_open", swap_after_open)
    orchestrator._move_directory_no_replace(
        source,
        parent / "published",
        expected_source_identity=source_identity,
        expected_parent_identity=parent_identity,
        expected_source_manifest=manifest,
    )

    assert (parent / "published" / "evidence.txt").read_text(encoding="utf-8") == "approved"
    assert (source / "evidence.txt").read_text(encoding="utf-8") == "unapproved"


@pytest.mark.parametrize("move_number", [1, 2])
def test_finalizer_rejects_source_swap_before_each_archive_handle_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, move_number: int
) -> None:
    """Would fail if either orchestration archive boundary opened a swapped source pathname."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug=f"preopen-source-{move_number}"
    )
    _write_production_caption_package(project)
    seen = 0
    retained: list[Path] = []

    def swap_before_open(source: Path, _destination: Path) -> None:
        nonlocal seen
        seen += 1
        if seen != move_number:
            return
        saved = source.with_name(source.name + "-retained")
        source.rename(saved)
        source.mkdir()
        retained.append(saved)

    monkeypatch.setattr(orchestrator, "_before_archive_move", swap_before_open)

    def run_command(argv: list[str], **_: object) -> None:
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render")

    def mux(_render: Path, _narration: Path, output: Path, _duration: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def contact_sheet(_final: Path, output: Path, _duration: float) -> list[float]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"sheet")
        return [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]

    def checker(handoff: Path, active: Path, _final: Path, *, sample_mode: bool) -> object:
        _write_authenticated_receipt(orchestrator, project, active, handoff)
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",))

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact_sheet,
        caption_render_qc_fn=lambda _final, _captions, output: output.write_text("{}\n", encoding="utf-8"),
        check_delivery_fn=checker,
    )

    assert result.exit_code == 2
    assert seen == move_number
    assert retained and retained[0].is_dir()
    assert not project.archive_dir.exists()
    assert "status: 已完成" not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in retained[0].glob("交接稿.md")
    )


def test_active_leaf_creation_rejects_parent_swap_before_opening_approved_handle(tmp_path: Path) -> None:
    """Would fail if active creation trusted a parent pathname after its identity was approved."""

    orchestrator = _load_orchestrator()
    parent = tmp_path / "制作中"
    parent.mkdir()
    approved_parent = orchestrator._directory_identity(parent)
    retained = tmp_path / "retained-parent"
    parent.rename(retained)
    parent.mkdir()

    with pytest.raises(RuntimeError):
        orchestrator._create_active_leaf_no_replace(
            parent,
            "new-active",
            expected_parent_identity=approved_parent,
        )

    assert not (retained / "new-active").exists()
    assert not (parent / "new-active").exists()


def test_initializer_rejects_active_parent_replacement_after_handle_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if active-leaf creation followed a replaced ordinary parent path."""

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    active_parent = workspace / "01-内容生产" / "视频工作台" / "制作中"
    retained = tmp_path / "retained-active-parent"

    def replace_parent(parent: Path, _leaf: str) -> None:
        parent.rename(retained)
        parent.mkdir()

    monkeypatch.setattr(orchestrator, "_after_active_parent_handle_open", replace_parent)
    with pytest.raises(ValueError) as error:
        orchestrator.initialize_production_project(workspace_root=workspace, archive_slug="parent-race")

    assert str(error.value) == "production project initialization failed"
    assert not list(active_parent.iterdir())
    assert not (retained / f"{orchestrator.date.today().isoformat()}-parent-race" / "交接稿.md").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse semantics required")
@pytest.mark.parametrize("boundary", ["first-archive-move", "final-publish-move"])
def test_handle_relative_archive_moves_do_not_follow_replaced_junction_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Would fail if either archive move followed a junction installed after its handles opened."""

    orchestrator = _load_orchestrator()
    source_parent = tmp_path / f"{boundary}-source-parent"
    destination_parent = tmp_path / f"{boundary}-archive-parent"
    source = source_parent / "stage"
    destination = destination_parent / "published"
    retained = tmp_path / f"{boundary}-retained-parent"
    outside = tmp_path / f"{boundary}-outside"
    source.mkdir(parents=True)
    destination_parent.mkdir()
    outside.mkdir()

    def replace_parent(_stage: Path, _destination: Path) -> None:
        destination_parent.rename(retained)
        _make_directory_reparse(destination_parent, outside)

    monkeypatch.setattr(orchestrator, "_after_rename_handles_open", replace_parent)
    try:
        orchestrator._move_directory_no_replace(
            source,
            destination,
            expected_source_identity=orchestrator._directory_identity(source),
            expected_parent_identity=orchestrator._directory_identity(destination_parent),
        )
        assert (retained / "published").is_dir()
        assert not (outside / "published").exists()
        assert not (destination_parent / "published").exists()
    finally:
        if destination_parent.is_symlink() or orchestrator._is_reparse_point(destination_parent):
            os.rmdir(destination_parent)


def test_audio_approval_blocks_before_runtime_or_delivery_mutation(tmp_path: Path) -> None:
    """Would fail if rendering or report cleanup could begin without approval of the final WAV."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="approval"
    )
    stale_report = project.active_dir / "delivery-report.json"
    stale_report.write_text("old-pass", encoding="utf-8")
    calls: list[list[str]] = []

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=False,
        run_command=lambda argv, **_: calls.append(argv),
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=audio-approval-required",)
    assert calls == []
    assert stale_report.is_file()
    assert _runtime_entries(tmp_path / "workspace") == []


def test_production_caption_gate_rejects_missing_or_synthetic_evidence(tmp_path: Path) -> None:
    """Would fail if finalization accepted captions not produced from word timestamps for this WAV."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="caption-gate"
    )
    _write_production_caption_package(project)
    qc_path = project.captions_dir / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["timing_source"] = "synthetic-local-sample"
    qc_path.write_text(json.dumps(qc), encoding="utf-8")
    stale_report = project.active_dir / "delivery-report.json"
    stale_report.write_text("old-pass", encoding="utf-8")

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=production-caption-gate",)
    assert not stale_report.exists()
    assert project.active_dir.is_dir()
    assert not project.archive_dir.exists()


@pytest.mark.parametrize("field", ["narration_path", "render_path", "handoff_path", "delivery_report_path"])
def test_tampered_project_topology_has_no_finalizer_side_effects(tmp_path: Path, field: str) -> None:
    """Would fail if caller-controlled dataclass paths could redirect writes or subprocess inputs."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="topology"
    )
    _write_production_caption_package(project)
    stale = project.active_dir / "delivery-report.json"
    stale.write_text("existing", encoding="utf-8")
    tampered = replace(project, **{field: tmp_path / "outside" / field})
    calls: list[list[str]] = []

    result = orchestrator.finalize_production_sample(
        project=tampered,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        run_command=lambda argv, **_: calls.append(argv),
    )

    assert result.diagnostics == ("rule=production-project-state",)
    assert calls == []
    assert stale.is_file()


def test_failed_production_stage_cleans_only_its_runtime_and_stale_report(tmp_path: Path) -> None:
    """Would fail if a failed render leaked its disposable runtime or retained a passing report."""

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    task1_stage = workspace / "runtime" / "v2-production-stages" / "task1-recovery"
    task1_stage.mkdir(parents=True)
    sentinel = task1_stage / "keep.txt"
    sentinel.write_text("task-1-recovery", encoding="utf-8")
    project = orchestrator.initialize_production_project(workspace_root=workspace, archive_slug="cleanup")
    _write_production_caption_package(project)
    stale_report = project.active_dir / "delivery-report.json"
    stale_report.write_text("old-pass", encoding="utf-8")

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        run_command=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture failure")),
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=production-local-stage",)
    assert _runtime_entries(workspace) == []
    assert sentinel.read_text(encoding="utf-8") == "task-1-recovery"
    assert not stale_report.exists()
    assert project.active_dir.is_dir()
    assert not project.archive_dir.exists()


def test_successful_production_orchestration_checks_delivery_then_archives_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if production delivery were checked in sample mode or archived before its receipt."""

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    project = orchestrator.initialize_production_project(workspace_root=workspace, archive_slug="success")
    _write_production_caption_package(project)
    private_master = project.active_dir / "工程" / "avatar-master.mp4"
    private_master.write_bytes(b"private-avatar-master")
    preparer_calls: list[tuple[Path, Path, Path, Path]] = []
    commands: list[tuple[str, ...]] = []
    delivery_modes: list[bool] = []
    order: list[str] = []

    def run_command(argv: list[str], **_: object) -> None:
        commands.append(tuple(argv))
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render-fixture")

    def mux(render: Path, narration: Path, output: Path, duration_seconds: float) -> None:
        assert render.is_file()
        assert narration.read_bytes() == b"canonical-final-wav"
        assert duration_seconds == 10.0
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final-media-fixture")

    def contact_sheet(final_media: Path, output: Path, duration_seconds: float) -> list[float]:
        assert final_media.is_file()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"image-fixture")
        return [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]

    def check_delivery(handoff: Path, active: Path, final_media: Path, *, sample_mode: bool) -> object:
        delivery_modes.append(sample_mode)
        assert "status: 制作中" in handoff.read_text(encoding="utf-8")
        assert final_media.is_file()
        _write_authenticated_receipt(orchestrator, project, active, handoff)
        order.append("receipt")
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",))

    def caption_render_qc(final_media: Path, captions: Path, output: Path) -> None:
        assert final_media.is_file()
        assert captions.is_file()
        output.write_text(json.dumps({"status": "pass"}), encoding="utf-8")

    def preserve_private_inputs(active_project) -> None:
        assert active_project is project
        assert not (project.active_dir / "delivery-report.json").exists()
        assert project.delivery_report_path.is_file()
        private_master.unlink()
        order.append("private")

    monkeypatch.setattr(
        orchestrator,
        "_before_archive_move",
        lambda _source, _destination: order.append("archive"),
    )

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime(preparer_calls),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact_sheet,
        check_delivery_fn=check_delivery,
        caption_render_qc_fn=caption_render_qc,
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        pre_archive_private_inputs_fn=preserve_private_inputs,
    )

    archive = Path(result.archive_path)
    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=production",)
    assert delivery_modes == [False]
    assert order == ["receipt", "private", "archive", "archive"]
    assert preparer_calls == [
        (project.narration_path, project.manifest_path, project.captions_dir, preparer_calls[0][3])
    ]
    assert {"lint", "validate", "inspect", "render"} <= {
        command[2] for command in commands if len(command) > 2
    }
    assert (archive / "工程" / "media" / "narration.wav").read_bytes() == b"canonical-final-wav"
    assert (archive / "工程" / "delivery-report.json").is_file()
    assert not (archive / "工程" / "avatar-master.mp4").exists()
    assert json.loads((archive / "质检" / "caption-render-qc.json").read_text(encoding="utf-8"))["status"] == "pass"
    assert "status: 已完成" in (archive / "交接稿.md").read_text(encoding="utf-8")
    assert not project.active_dir.exists()
    assert _runtime_entries(workspace) == []


def test_pre_archive_private_input_failure_keeps_the_active_project_recoverable(
    tmp_path: Path,
) -> None:
    """Would fail if a private preservation failure could archive a project or discard its source."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="private-input-failure"
    )
    _write_production_caption_package(project)
    master = project.active_dir / "工程" / "avatar-master.mp4"
    master.write_bytes(b"private-avatar-master")

    def run_command(argv: list[str], **_: object) -> None:
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render")

    def mux(_render: Path, _narration: Path, output: Path, _duration: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def contact_sheet(_final: Path, output: Path, _duration: float) -> list[float]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"sheet")
        return [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]

    def checker(handoff: Path, active: Path, _final: Path, *, sample_mode: bool) -> object:
        assert sample_mode is False
        _write_authenticated_receipt(orchestrator, project, active, handoff)
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",))

    def fail_private_preservation(_project) -> None:
        assert not (project.active_dir / "delivery-report.json").exists()
        raise RuntimeError("private-preservation-failed")

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact_sheet,
        caption_render_qc_fn=lambda _final, _captions, output: output.write_text("{}\n", encoding="utf-8"),
        check_delivery_fn=checker,
        pre_archive_private_inputs_fn=fail_private_preservation,
    )

    assert result == orchestrator.ProductionSampleResult(2, ("rule=production-local-stage",))
    assert project.active_dir.is_dir()
    assert master.read_bytes() == b"private-avatar-master"
    assert not project.archive_dir.exists()


def test_post_check_artifact_mutation_cannot_enter_the_archive(tmp_path: Path) -> None:
    """Would fail if an artifact changed after delivery check could cross publication."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="post-check-mutation"
    )
    _write_production_caption_package(project)

    def run_command(argv: list[str], **_: object) -> None:
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render")

    def mux(_render: Path, _narration: Path, output: Path, _duration: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final-before-check")

    def contact_sheet(_final: Path, output: Path, _duration: float) -> list[float]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"sheet")
        return [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]

    def checker(_handoff: Path, active: Path, final: Path, *, sample_mode: bool) -> object:
        assert sample_mode is False
        _write_authenticated_receipt(orchestrator, project, active, _handoff)
        final.write_bytes(b"final-after-check")
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",))

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact_sheet,
        caption_render_qc_fn=lambda _final, _captions, output: output.write_text("{}\n", encoding="utf-8"),
        check_delivery_fn=checker,
    )

    assert result == orchestrator.ProductionSampleResult(2, ("rule=production-local-stage",))
    assert project.active_dir.is_dir()
    assert not project.archive_dir.exists()
    assert "status: 制作中" in project.handoff_path.read_text(encoding="utf-8")
    assert not (project.active_dir / "delivery-report.json").exists()
    assert not project.delivery_report_path.exists()


def test_receipt_replacement_after_validation_never_enters_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if publication copied a replacement checker receipt after validating its predecessor."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="receipt-copy-race"
    )
    _write_production_caption_package(project)
    expected_receipt = b""
    replacement_receipt = b'{"status":"forged"}\n'

    def run_command(argv: list[str], **_: object) -> None:
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render")

    def mux(_render: Path, _narration: Path, output: Path, _duration: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def contact_sheet(_final: Path, output: Path, _duration: float) -> list[float]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"sheet")
        return [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]

    def checker(handoff: Path, active: Path, _final: Path, *, sample_mode: bool) -> object:
        nonlocal expected_receipt
        assert sample_mode is False
        _write_authenticated_receipt(orchestrator, project, active, handoff)
        expected_receipt = (active / "delivery-report.json").read_bytes()
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",))

    def replace_after_capture(report: Path) -> None:
        report.write_bytes(replacement_receipt)

    monkeypatch.setattr(orchestrator, "_after_delivery_receipt_capture", replace_after_capture)
    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact_sheet,
        caption_render_qc_fn=lambda _final, _captions, output: output.write_text("{}\n", encoding="utf-8"),
        check_delivery_fn=checker,
    )

    assert result.exit_code == 0
    assert (Path(result.archive_path) / "工程" / "delivery-report.json").read_bytes() == expected_receipt
    assert replacement_receipt != expected_receipt


def test_forged_delivery_report_is_not_an_authenticated_publication_receipt(tmp_path: Path) -> None:
    """Would fail if an ordinary pass-shaped report could be accepted without checked artifact bindings."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="forged-receipt"
    )
    project.narration_path.write_bytes(b"fixture")
    for path in (
        project.manifest_path,
        *(project.captions_dir / name for name in CAPTION_FILES),
        project.render_path,
        project.contact_sheet_path,
        project.contact_sheet_qc_path,
        project.caption_render_qc_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    publication = orchestrator._capture_publication_manifest(project.active_dir, project, project.handoff_path)
    forged = {"status": "pass", "mode": "production", "rules": [{"id": "delivery-hard-gates", "status": "pass"}]}

    assert orchestrator._valid_delivery_receipt(forged, publication) is False


def test_final_move_failure_leaves_only_an_incomplete_recovery_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a failed final archive move preserved a pass receipt or completed handoff."""

    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    project = orchestrator.initialize_production_project(workspace_root=workspace, archive_slug="move-failure")
    _write_production_caption_package(project)
    moves = 0
    native_move = orchestrator._move_directory_no_replace

    def fail_second_move(source: Path, destination: Path, **kwargs: object) -> None:
        nonlocal moves
        moves += 1
        if moves == 2:
            raise RuntimeError("fixture move failure")
        native_move(source, destination, **kwargs)

    monkeypatch.setattr(orchestrator, "_move_directory_no_replace", fail_second_move)

    def run_command(argv: list[str], **_: object) -> None:
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render")

    def mux(_render: Path, _narration: Path, output: Path, _duration: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def contact_sheet(_final: Path, output: Path, _duration: float) -> list[float]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"sheet")
        return [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]

    def checker(_handoff: Path, active: Path, _final: Path, *, sample_mode: bool) -> object:
        assert sample_mode is False
        _write_authenticated_receipt(orchestrator, project, active, _handoff)
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",))

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact_sheet,
        caption_render_qc_fn=lambda _final, _captions, output: output.write_text("{}\n", encoding="utf-8"),
        check_delivery_fn=checker,
    )

    stages = list(project.archive_dir.parent.glob(f".{project.active_dir.name}.incomplete"))
    assert result == orchestrator.ProductionSampleResult(
        2, ("rule=production-local-stage",), recovery_stage=f".{project.active_dir.name}.incomplete"
    )
    assert moves == 2
    assert not project.archive_dir.exists()
    assert len(stages) == 1
    assert "status: 制作中" in (stages[0] / "交接稿.md").read_text(encoding="utf-8")
    assert not (stages[0] / "delivery-report.json").exists()
    assert not (stages[0] / "工程" / "delivery-report.json").exists()


def test_post_move_receipt_write_failure_is_recoverable_and_not_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a receipt write failure left a completed marker or pass report in staging."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="receipt-write-failure"
    )
    _write_production_caption_package(project)
    native_write_text = orchestrator._write_text

    def reject_completed(root: Path, path: Path, value: str) -> None:
        if "status: 已完成" in value:
            raise OSError("fixture receipt write failure")
        native_write_text(root, path, value)

    monkeypatch.setattr(orchestrator, "_write_text", reject_completed)

    def run_command(argv: list[str], **_: object) -> None:
        if "render" in argv:
            output = Path(argv[argv.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"render")

    def mux(_render: Path, _narration: Path, output: Path, _duration: float) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def contact_sheet(_final: Path, output: Path, _duration: float) -> list[float]:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"sheet")
        return [0.5, 2.5, 4.5, 5.5, 7.5, 9.5]

    def checker(_handoff: Path, active: Path, _final: Path, *, sample_mode: bool) -> object:
        assert sample_mode is False
        _write_authenticated_receipt(orchestrator, project, active, _handoff)
        return SimpleNamespace(exit_code=0, diagnostics=("status=pass mode=production",))

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=hashlib.sha256(project.narration_path.read_bytes()).hexdigest(),
        prepare_project_fn=_prepared_runtime([]),
        preflight_fn=lambda _: (REPO_ROOT / "node-fixture", REPO_ROOT / "hyperframes-fixture"),
        run_command=run_command,
        mux_fn=mux,
        contact_sheet_fn=contact_sheet,
        caption_render_qc_fn=lambda _final, _captions, output: output.write_text("{}\n", encoding="utf-8"),
        check_delivery_fn=checker,
    )

    stage = project.archive_dir
    assert result == orchestrator.ProductionSampleResult(2, ("rule=production-local-stage",), recovery_stage=stage.name)
    assert stage.is_dir()
    assert project.archive_dir.is_dir()
    handoff = (stage / "交接稿.md").read_text(encoding="utf-8")
    assert "status: 制作中" in handoff
    assert "status: 已完成" not in handoff
    assert "交付检查：pass" not in handoff
    assert not (stage / "delivery-report.json").exists()
    assert not (stage / "工程" / "delivery-report.json").exists()


def test_real_offline_synthetic_production_pipeline_uses_default_local_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real Task 1 preparation, HyperFrames, FFmpeg, caption frames, delivery, and archive offline."""

    requirements = {
        "node": shutil.which("node.exe") or shutil.which("node"),
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "hyperframes": REPO_ROOT / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs",
    }
    absent = [name for name, value in requirements.items() if not value or (isinstance(value, Path) and not value.is_file())]
    if absent:
        pytest.skip("required local offline tool is absent: " + ", ".join(absent))
    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug="real-offline-synthetic"
    )
    _write_canonical_synthetic_inputs(project, monkeypatch)
    narration_hash = hashlib.sha256(project.narration_path.read_bytes()).hexdigest()

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256=narration_hash,
    )

    assert result.exit_code == 0
    assert result.diagnostics == ("status=pass mode=production",)
    archive = Path(result.archive_path or "")
    assert archive.is_dir()
    template = (REPO_ROOT / "video-production-sample" / "index.template.html").read_text(encoding="utf-8")
    generated_renderer = (archive / "工程" / "render-project" / "index.html").read_text(encoding="utf-8")
    assert "data-boomearth-local-fonts" not in generated_renderer
    assert "data-layout-allow-" not in template
    assert "data-layout-allow-" not in generated_renderer
    assert (archive / "工程" / "publication-manifest.json").is_file()
    assert (archive / "质检" / "contact-sheet.jpg").is_file()
    assert (archive / "质检" / "caption-render-qc.json").is_file()
    assert (archive / "工程" / "delivery-report.json").is_file()
    assert "status: 已完成" in (archive / "交接稿.md").read_text(encoding="utf-8")
    assert hashlib.sha256((archive / "工程" / "media" / "narration.wav").read_bytes()).hexdigest() == narration_hash
    probe = subprocess.run(
        [str(requirements["ffprobe"]), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(archive / "成片" / "boomearth-v2-production.mp4")],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert probe.returncode == 0
    payload = json.loads(probe.stdout)
    video = next(stream for stream in payload["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in payload["streams"] if stream["codec_type"] == "audio")
    numerator, denominator = video["r_frame_rate"].split("/")
    assert (video["codec_name"], audio["codec_name"], int(video["width"]), int(video["height"])) == ("h264", "aac", 1920, 1080)
    assert float(numerator) / float(denominator) == pytest.approx(30.0)
    assert float(payload["format"]["duration"]) == pytest.approx(2.4, abs=0.15)


@pytest.mark.parametrize("replacement", ["file", "reparse"])
def test_finalizer_maps_active_replacement_to_a_fixed_redacted_result(
    tmp_path: Path, replacement: str
) -> None:
    """Would fail if a replaced active project exposed an OS path or chained failure."""

    orchestrator = _load_orchestrator()
    project = orchestrator.initialize_production_project(
        workspace_root=tmp_path / "workspace", archive_slug=f"active-{replacement}"
    )
    retained = project.active_dir.with_name(project.active_dir.name + "-retained")
    project.active_dir.rename(retained)
    if replacement == "file":
        project.active_dir.write_text("not a directory", encoding="utf-8")
    else:
        _make_directory_reparse(project.active_dir, retained)

    result = orchestrator.finalize_production_sample(
        project=project,
        audio_approved=True,
        approved_narration_sha256="0" * 64,
    )

    rendered = repr(result) + "\n" + "\n".join(result.diagnostics)
    assert result == orchestrator.ProductionSampleResult(2, ("rule=production-project-state",))
    assert str(tmp_path) not in rendered
    assert "0" * 64 not in rendered
    assert result.archive_path is None
    assert result.recovery_stage is None
    if replacement == "reparse" and (project.active_dir.is_symlink() or orchestrator._is_reparse_point(project.active_dir)):
        os.rmdir(project.active_dir)

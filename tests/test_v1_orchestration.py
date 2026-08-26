"""Offline tests for the V1 synthetic sample orchestrator."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "automation" / "scripts" / "run_v1_sample.py"


def _load_orchestrator():
    spec = importlib.util.spec_from_file_location("v1_orchestrator_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8_000)
        output.writeframes(b"\0\0" * 80_000)


def _runtime_entries(workspace: Path) -> list[Path]:
    runtime = workspace / "runtime"
    return list(runtime.glob("v1-sample-*")) if runtime.is_dir() else []


def _run_without_expensive_inspect(orchestrator):
    """Keep integration offline while the fixed inspect argv is covered separately."""
    def run(argv: list[str], **kwargs: object) -> None:
        if "inspect" in argv:
            return
        orchestrator._run_command(argv, **kwargs)

    return run


def test_audio_approval_blocks_before_any_subprocess_or_archive_mutation(tmp_path: Path) -> None:
    """Would fail if a render/archive could begin without an explicit audio approval."""
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)
    calls: list[list[str]] = []

    result = orchestrator.run_sample(
        source_wav=source,
        workspace_root=tmp_path / "workspace",
        archive_slug="safe-sample",
        audio_approved=False,
        run_command=lambda argv, **_: calls.append(argv),
    )

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=audio-approval-required",)
    assert calls == []
    assert not (tmp_path / "workspace" / "01-内容生产").exists()


def test_orchestrator_uses_only_fixed_local_commands_and_never_mutates_source(tmp_path: Path) -> None:
    """Would fail if default sample work invoked a live lane or changed its authorized WAV."""
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)
    before = source.read_bytes()
    calls: list[tuple[str, ...]] = []

    def fake_run(argv: list[str], **_: object) -> None:
        calls.append(tuple(argv))
        if "hf:render:sample" in argv:
            output = tmp_path / "workspace" / "video-sample" / "renders" / "boomearth-v1-sample.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"synthetic-render")

    result = orchestrator.run_sample(
        source_wav=source,
        workspace_root=tmp_path / "workspace",
        archive_slug="safe-sample",
        audio_approved=True,
        run_command=fake_run,
        check_delivery_fn=lambda *_, **__: type("Result", (), {"exit_code": 2, "diagnostics": ("rule=media-probe",)})(),
    )

    assert result.exit_code == 2
    assert source.read_bytes() == before
    flattened = " ".join(" ".join(call) for call in calls).lower()
    assert all(isinstance(argument, str) for call in calls for argument in call)
    assert "http" not in flattened
    assert "api" not in flattened
    assert "tts" not in flattened
    assert " inspect " in f" {flattened} "
    assert not (tmp_path / "workspace" / "01-内容生产" / "视频工作台" / "已制作").exists()


def test_successful_sample_isolates_shared_media_and_atomically_archives_completed_project(tmp_path: Path) -> None:
    """Would fail if a normal shared sample directory were touched or an active project survived success."""
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)
    workspace = tmp_path / "workspace"
    shared_media = workspace / "video-sample" / "media"
    shared_media.mkdir(parents=True)
    sentinel = shared_media / "narration.wav"
    sentinel.write_bytes(b"shared-media-must-stay-untouched")
    before = sentinel.read_bytes()

    first = orchestrator.run_sample(
        source_wav=source,
        workspace_root=workspace,
        archive_slug="safe-sample",
        audio_approved=True,
        run_command=_run_without_expensive_inspect(orchestrator),
    )

    assert first.exit_code == 0
    archive = Path(first.archive_path)
    assert archive.name.endswith("-safe-sample")
    assert (archive / "交接稿.md").is_file()
    assert (archive / "成片" / "boomearth-v1-sample.mp4").is_file()
    assert (archive / "质检" / "contact-sheet.jpg").is_file()
    contact_qc = json.loads((archive / "质检" / "contact-sheet-qc.json").read_text(encoding="utf-8"))
    assert contact_qc == {
        "frame_count": 6,
        "layout": "3x2",
        "times_s": [0.5, 2.5, 4.5, 5.5, 7.5, 9.5],
        "coverage": ["open", "early", "mid", "transition", "late", "near-end"],
    }
    assert (archive / "工程" / "delivery-report.json").is_file()
    assert (archive / "工程" / "media" / "captions.vtt").is_file()
    assert "status: 已完成" in (archive / "交接稿.md").read_text(encoding="utf-8")
    handoff = (archive / "交接稿.md").read_text(encoding="utf-8")
    assert "## 制作回执" in handoff
    assert "## QC结果" in handoff
    assert not (archive / "视频标题.md").exists()
    assert str(source) not in (archive / "交接稿.md").read_text(encoding="utf-8")
    assert sentinel.read_bytes() == before
    assert _runtime_entries(workspace) == []
    active_parent = workspace / "01-内容生产" / "视频工作台" / "制作中"
    assert not list(active_parent.glob("*-safe-sample"))

    second = orchestrator.run_sample(
        source_wav=source,
        workspace_root=workspace,
        archive_slug="safe-sample",
        audio_approved=True,
        run_command=_run_without_expensive_inspect(orchestrator),
    )
    assert second.exit_code == 2
    assert (archive / "成片" / "boomearth-v1-sample.mp4").is_file()


def test_normal_active_parent_with_missing_target_is_not_a_reparse_collision(tmp_path: Path) -> None:
    """Would fail if a normal 制作中 parent made a new active target look like a junction."""
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)
    workspace = tmp_path / "workspace"
    (workspace / "01-内容生产" / "视频工作台" / "制作中").mkdir(parents=True)

    result = orchestrator.run_sample(
        source_wav=source,
        workspace_root=workspace,
        archive_slug="normal-parent",
        audio_approved=True,
        run_command=_run_without_expensive_inspect(orchestrator),
    )

    assert result.exit_code == 0
    assert result.diagnostics != ("rule=active-project-collision",)


def test_failed_local_stage_removes_only_task_created_runtime_and_active_project(tmp_path: Path) -> None:
    """Would fail if a failing local render leaked an active project or runtime directory."""
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)
    workspace = tmp_path / "workspace"

    result = orchestrator.run_sample(
        source_wav=source,
        workspace_root=workspace,
        archive_slug="failing-sample",
        audio_approved=True,
        run_command=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("forced-local-failure")),
    )

    assert result.exit_code == 2
    assert _runtime_entries(workspace) == []
    active_parent = workspace / "01-内容生产" / "视频工作台" / "制作中"
    assert not list(active_parent.glob("*-failing-sample"))


def test_orchestrator_rejects_a_reparse_runtime_ancestor_before_creating_files(tmp_path: Path) -> None:
    """This unit boundary covers Windows where symlink privilege is unavailable."""
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime = workspace / "runtime"
    runtime.parent.mkdir()

    original_reparse = orchestrator._is_reparse_point
    orchestrator._is_reparse_point = lambda path: path == runtime or original_reparse(path)
    calls: list[list[str]] = []
    try:
        result = orchestrator.run_sample(
            source_wav=source,
            workspace_root=workspace,
            archive_slug="unsafe-runtime",
            audio_approved=True,
            run_command=lambda argv, **_: calls.append(argv),
        )
    finally:
        orchestrator._is_reparse_point = original_reparse

    assert result.exit_code == 2
    assert result.diagnostics == ("rule=workspace-safe-path",)
    assert calls == []
    assert list(outside.iterdir()) == []


def test_cleanup_refuses_a_task_tree_with_a_reparse_descendant(tmp_path: Path) -> None:
    """Would fail if cleanup could recursively cross a junction below its safe root."""
    orchestrator = _load_orchestrator()
    workspace = tmp_path / "workspace"
    runtime = workspace / "runtime" / "v1-sample-test"
    redirect = runtime / "redirect"
    redirect.mkdir(parents=True)

    original_reparse = orchestrator._is_reparse_point
    orchestrator._is_reparse_point = lambda path: path == redirect or original_reparse(path)
    try:
        orchestrator._remove_tree_if_safe(workspace, runtime)
    finally:
        orchestrator._is_reparse_point = original_reparse

    assert runtime.is_dir()


def test_sample_words_are_individual_synthetic_tokens_and_shared_with_asr(tmp_path: Path) -> None:
    """Would fail if sample word timing copied phrase records or divided an empty phrase."""
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)
    active = tmp_path / "workspace" / "active"
    source_media = tmp_path / "source-media"
    _write_wav(source_media / "narration.wav")
    (source_media / "captions.json").write_text(
        json.dumps(
            [
                {"start": 0.0, "end": 1.0, "text": "合成", "source": "synthetic-local-sample"},
                {"start": 1.0, "end": 2.0, "text": "样片", "source": "synthetic-local-sample"},
            ]
        ),
        encoding="utf-8",
    )
    (source_media / "caption-qc.json").write_text("{}", encoding="utf-8")

    orchestrator._write_sample_artifacts(source_media, active, source)

    media = active / "工程" / "media"
    words = json.loads((media / "captions_words.json").read_text(encoding="utf-8"))
    asr = json.loads((media / "asr-result.json").read_text(encoding="utf-8"))
    assert isinstance(words, list) and words
    assert all(len(word["text"]) == 1 for word in words)
    assert asr == {"synthetic": True, "words": words}


def test_empty_token_phrase_fails_safely_without_division_by_zero(tmp_path: Path) -> None:
    orchestrator = _load_orchestrator()
    source = tmp_path / "authorized" / "fixture.wav"
    _write_wav(source)

    with pytest.raises(ValueError, match="empty-tokens"):
        orchestrator._tokenize_caption_phrases(
            [{"start": 0.0, "end": 1.0, "text": "   ", "source": "synthetic-local-sample"}]
        )


def test_orchestrator_cli_help_documents_offline_sample_boundary() -> None:
    """Would fail if an operator could mistake the local sample for a live API workflow."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, check=False, text=True
    )
    assert result.returncode == 0
    assert "offline" in result.stdout.lower()
    assert "live APIs are not called" in result.stdout

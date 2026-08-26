from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation" / "scripts" / "resume_content_archive.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("resume_content_archive_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    active = workspace / "制作中" / "2026-08-15-v3"
    archive = workspace / "已制作" / "8月上旬" / active.name
    final = active / "成片" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"rendered-video")
    handoff = active / "交接稿.md"
    handoff.write_text("pending", encoding="utf-8")
    publication = active / "工程" / "publication-manifest.json"
    publication.parent.mkdir(parents=True)
    publication.write_text(
        json.dumps({"artifacts": {"成片/final.mp4": _sha256(final)}}),
        encoding="utf-8",
    )
    report = active / "delivery-report.json"
    report.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    project = SimpleNamespace(
        active_dir=active,
        archive_dir=archive,
        active_identity="active-identity",
        handoff_path=handoff,
        delivery_report_path=active / "工程" / "delivery-report.json",
        render_path=final,
    )
    return workspace, project


def _patch_success_path(module, monkeypatch: pytest.MonkeyPatch, project) -> None:
    timeline = SimpleNamespace(scenes=(object(),), duration_seconds=1.0)
    monkeypatch.setattr(module, "load_production_project", lambda **_kwargs: project)
    monkeypatch.setattr(
        module, "_content_contract", lambda _project: (timeline, None, "snapshot", None)
    )
    monkeypatch.setattr(module, "load_content_plan_snapshot", lambda **_kwargs: object())
    monkeypatch.setattr(module, "_publication_manifest_matches", lambda *_args: True)
    monkeypatch.setattr(module, "_read_production_caption_metadata", lambda _project: (1, 1))
    monkeypatch.setattr(
        module,
        "_handoff_renderer",
        lambda _snapshot: lambda *_args: "completed handoff",
    )
    monkeypatch.setattr(module, "_valid_delivery_receipt", lambda *_args: True)
    monkeypatch.setattr(
        module,
        "_write_exact_file_atomically",
        lambda _root, path, payload: (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(payload)),
    )
    monkeypatch.setattr(module, "_safe_mkdir", lambda _root, path: path.mkdir(parents=True))
    monkeypatch.setattr(module, "_is_reparse_point", lambda _path: False)
    monkeypatch.setattr(module, "_directory_identity", lambda path: str(path))
    monkeypatch.setattr(
        module,
        "_move_directory_no_replace",
        lambda source, target, **_kwargs: source.rename(target),
    )
    monkeypatch.setattr(
        module,
        "_write_publication_manifest",
        lambda root, payload: (root / "工程" / "publication-manifest.json").write_text(
            json.dumps({"artifacts": payload}), encoding="utf-8"
        ),
    )
    monkeypatch.setattr(
        module,
        "_write_text",
        lambda _root, path, text: path.write_text(text, encoding="utf-8"),
    )
    monkeypatch.setattr(module, "_safe_existing_directory", lambda path: path.is_dir())


def test_resume_content_archive_moves_only_after_delivery_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    workspace, project = _fixture(tmp_path)
    _patch_success_path(module, monkeypatch, project)
    monkeypatch.setattr(
        module, "check_delivery", lambda *_args, **_kwargs: SimpleNamespace(exit_code=0)
    )

    archived = module.resume_content_archive(
        workspace_root=workspace,
        active_project=project.active_dir.name,
    )

    assert archived == project.archive_dir
    assert not project.active_dir.exists()
    assert (archived / "交接稿.md").read_text(encoding="utf-8") == "completed handoff"
    assert (archived / "工程" / "delivery-report.json").is_file()


def test_resume_content_archive_leaves_active_project_when_delivery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    workspace, project = _fixture(tmp_path)
    _patch_success_path(module, monkeypatch, project)
    monkeypatch.setattr(
        module, "check_delivery", lambda *_args, **_kwargs: SimpleNamespace(exit_code=2)
    )

    with pytest.raises(module.ResumeArchiveError, match="resume-delivery"):
        module.resume_content_archive(
            workspace_root=workspace,
            active_project=project.active_dir.name,
        )

    assert project.active_dir.is_dir()
    assert not project.archive_dir.exists()

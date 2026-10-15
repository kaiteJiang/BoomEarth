from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from boomearth.video.artifacts import capture_regular_file


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


def _fixture(tmp_path: Path, module):
    workspace = tmp_path / "workspace"
    active = workspace / "制作中" / "2026-08-15-v3"
    archive = workspace / "已制作" / "8月上旬" / active.name
    final = active / "成片" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"rendered-video")
    handoff = active / "交接稿.md"
    handoff.write_text(
        "---\nstatus: 制作中\nduration_target_s: 9.000\nword_count: 3\n"
        "platform: local-v1\n---\n\n## 正文\n\n待保留的定稿。\n",
        encoding="utf-8",
    )
    publication = active / "工程" / "publication-manifest.json"
    publication.parent.mkdir(parents=True)
    artifacts = {"成片/final.mp4": _sha256(final), "交接稿.md": _sha256(handoff)}
    publication.write_text(
        json.dumps({"artifacts": artifacts}),
        encoding="utf-8",
    )
    report = active / "delivery-report.json"
    report.write_text(
        json.dumps(
            {
                "status": "pass",
                "mode": "production",
                "rules": [{"id": "delivery-hard-gates", "status": "pass"}],
                "artifacts": {
                    "handoff": "交接稿.md",
                    "narration": "工程/media/narration.wav",
                    "final": "成片/",
                    "contact_sheet": "质检/contact-sheet.jpg",
                    "contact_sheet_qc": "质检/contact-sheet-qc.json",
                },
                "artifact_sha256": artifacts,
                "media": {},
                "skipped_stages": [],
            }
        ),
        encoding="utf-8",
    )
    project = SimpleNamespace(
        active_dir=active,
        archive_dir=archive,
        active_identity=module._directory_identity(active),
        handoff_path=handoff,
        delivery_report_path=active / "工程" / "delivery-report.json",
        render_path=final,
    )
    return workspace, project


def _patch_success_path(module, monkeypatch: pytest.MonkeyPatch, project) -> None:
    timeline = SimpleNamespace(scenes=(object(),), duration_seconds=1.0)
    snapshot = capture_regular_file(project.handoff_path, within=project.active_dir)
    monkeypatch.setattr(module, "load_production_project", lambda **_kwargs: project)
    monkeypatch.setattr(
        module, "_content_contract", lambda _project: (timeline, None, snapshot, None, None)
    )
    monkeypatch.setattr(module, "load_content_plan_snapshot", lambda **_kwargs: object())
    monkeypatch.setattr(module, "_read_production_caption_metadata", lambda _project: (1, 1))


def test_resume_content_archive_moves_only_after_delivery_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    workspace, project = _fixture(tmp_path, module)
    receipt_bytes = (project.active_dir / "delivery-report.json").read_bytes()
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
    handoff = (archived / "交接稿.md").read_text(encoding="utf-8")
    assert "status: 已完成\n" in handoff
    assert "duration_target_s: 1.000\n" in handoff
    assert "word_count: 1\n" in handoff
    assert "待保留的定稿。" in handoff
    assert "## 制作回执" in handoff
    assert "## QC结果" in handoff
    assert (archived / "工程" / "delivery-report.json").read_bytes() == receipt_bytes
    retained_reports = list(archived.glob("delivery-report.retained-*.json"))
    assert len(retained_reports) == 1
    assert retained_reports[0].read_bytes() == receipt_bytes
    assert not (archived / "delivery-report.json").exists()
    manifest = json.loads((archived / "工程" / "publication-manifest.json").read_text("utf-8"))
    assert module._publication_manifest_matches(archived, manifest["artifacts"])
    assert manifest["artifacts"]["交接稿.md"] == _sha256(archived / "交接稿.md")


def test_resume_content_archive_leaves_active_project_when_delivery_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    workspace, project = _fixture(tmp_path, module)
    original_handoff = project.handoff_path.read_bytes()
    receipt_bytes = (project.active_dir / "delivery-report.json").read_bytes()
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
    assert project.handoff_path.read_bytes() == original_handoff
    assert (project.active_dir / "delivery-report.json").read_bytes() == receipt_bytes


@pytest.mark.parametrize("failure", ["manifest-write", "manifest-validation"])
def test_resume_content_archive_restores_pending_handoff_after_final_manifest_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    module = _load_module()
    workspace, project = _fixture(tmp_path, module)
    receipt_bytes = (project.active_dir / "delivery-report.json").read_bytes()
    _patch_success_path(module, monkeypatch, project)
    monkeypatch.setattr(
        module, "check_delivery", lambda *_args, **_kwargs: SimpleNamespace(exit_code=0)
    )
    original_write = module._write_publication_manifest
    original_matches = module._publication_manifest_matches

    def fail_final_manifest_write(root, manifest):
        if root == project.archive_dir:
            assert "status: 已完成\n" in (root / "交接稿.md").read_text("utf-8")
            raise OSError("injected final manifest write failure")
        original_write(root, manifest)

    def fail_final_manifest_validation(root, manifest):
        if root == project.archive_dir:
            assert "status: 已完成\n" in (root / "交接稿.md").read_text("utf-8")
            return False
        return original_matches(root, manifest)

    if failure == "manifest-write":
        monkeypatch.setattr(module, "_write_publication_manifest", fail_final_manifest_write)
        reason = "resume-local-stage"
    else:
        monkeypatch.setattr(module, "_publication_manifest_matches", fail_final_manifest_validation)
        reason = "resume-publication"

    with pytest.raises(module.ResumeArchiveError, match=reason):
        module.resume_content_archive(
            workspace_root=workspace,
            active_project=project.active_dir.name,
        )

    recovery = project.archive_dir
    assert not project.active_dir.exists()
    assert recovery.is_dir()
    handoff = (recovery / "交接稿.md").read_text("utf-8")
    assert "status: 制作中\n" in handoff
    assert "待保留的定稿。" in handoff
    assert "已完成" not in handoff
    assert "## 制作回执" not in handoff
    assert "## QC结果" not in handoff
    assert (recovery / "成片" / "final.mp4").read_bytes() == b"rendered-video"
    assert not (recovery / "delivery-report.json").exists()
    assert not (recovery / "工程" / "delivery-report.json").exists()
    for folder in (recovery, recovery / "工程"):
        retained = list(folder.glob("delivery-report.retained-*.json"))
        assert len(retained) == 1
        assert retained[0].read_bytes() == receipt_bytes
    manifest = json.loads((recovery / "工程" / "publication-manifest.json").read_text("utf-8"))
    assert not original_matches(recovery, manifest["artifacts"])


def test_resume_content_archive_rejects_handoff_changed_before_first_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    workspace, project = _fixture(tmp_path, module)
    _patch_success_path(module, monkeypatch, project)

    def mutate_handoff_before_render(_project):
        project.handoff_path.write_text("changed after contract validation", encoding="utf-8")
        return 1, 1

    monkeypatch.setattr(module, "_read_production_caption_metadata", mutate_handoff_before_render)
    monkeypatch.setattr(
        module,
        "check_delivery",
        lambda *_args, **_kwargs: pytest.fail("delivery must not run with a changed handoff"),
    )

    with pytest.raises(module.ResumeArchiveError, match="resume-local-stage"):
        module.resume_content_archive(
            workspace_root=workspace,
            active_project=project.active_dir.name,
        )

    assert project.active_dir.is_dir()
    assert not project.archive_dir.exists()
    assert (project.active_dir / "delivery-report.json").exists()

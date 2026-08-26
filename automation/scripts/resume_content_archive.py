#!/usr/bin/env python3
"""Archive an already-rendered content project after a corrected delivery gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from boomearth.video.content_plan import load_content_plan_snapshot
from check_delivery import check_delivery
from run_content_production import _content_contract, _handoff_renderer
from run_v2_production_sample import (
    _directory_identity,
    _is_reparse_point,
    _move_directory_no_replace,
    _publication_manifest_matches,
    _read_production_caption_metadata,
    _read_stable_file_bytes,
    _safe_existing_directory,
    _safe_mkdir,
    _sanitize_incomplete_stage,
    _sha256,
    _valid_delivery_receipt,
    _write_exact_file_atomically,
    _write_publication_manifest,
    _write_text,
    load_production_project,
)


class ResumeArchiveError(RuntimeError):
    """A fixed local archive-resume failure."""


def resume_content_archive(*, workspace_root: Path, active_project: str) -> Path:
    try:
        project = load_production_project(
            workspace_root=workspace_root,
            active_project=active_project,
        )
        timeline, _protected, handoff_snapshot, _motion, _cover_sources = _content_contract(project)
        load_content_plan_snapshot(project_root=project.active_dir)
        publication_path = project.active_dir / "工程" / "publication-manifest.json"
        publication_value = json.loads(publication_path.read_text(encoding="utf-8"))
        publication = publication_value["artifacts"]
        if (
            not isinstance(publication, dict)
            or not publication
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in publication.items())
            or not _publication_manifest_matches(project.active_dir, publication)
        ):
            raise ResumeArchiveError("resume-publication")
        caption_count, word_count = _read_production_caption_metadata(project)
        if caption_count <= 0 or not timeline.scenes:
            raise ResumeArchiveError("resume-content")
        duration_seconds = timeline.duration_seconds
        completed_handoff = _handoff_renderer(handoff_snapshot)(
            project,
            "已完成",
            duration_seconds,
            word_count,
        )
        checked = check_delivery(
            project.handoff_path,
            project.active_dir,
            project.render_path,
            sample_mode=False,
        )
        if checked.exit_code != 0:
            raise ResumeArchiveError("resume-delivery")
        report = project.active_dir / "delivery-report.json"
        receipt_bytes = _read_stable_file_bytes(report)
        receipt = json.loads(receipt_bytes.decode("utf-8"))
        if not _valid_delivery_receipt(receipt, publication):
            raise ResumeArchiveError("resume-delivery")
        _write_exact_file_atomically(
            project.active_dir,
            project.delivery_report_path,
            receipt_bytes,
        )
        report.unlink()
        completion = dict(publication)
        report_relative = project.delivery_report_path.relative_to(
            project.active_dir
        ).as_posix()
        completion[report_relative] = _sha256(project.delivery_report_path)
        if not _publication_manifest_matches(project.active_dir, completion):
            raise ResumeArchiveError("resume-publication")
    except ResumeArchiveError:
        raise
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise ResumeArchiveError("resume-local-stage") from None

    stage: Path | None = None
    final_recovery: Path | None = None
    try:
        _safe_mkdir(Path(workspace_root), project.archive_dir.parent)
        if project.archive_dir.exists() or _is_reparse_point(project.archive_dir):
            raise ResumeArchiveError("resume-archive-collision")
        parent_identity = _directory_identity(project.archive_dir.parent)
        stage = project.archive_dir.parent / f".{project.active_dir.name}.incomplete"
        if stage.exists() or _is_reparse_point(stage):
            raise ResumeArchiveError("resume-archive-collision")
        _move_directory_no_replace(
            project.active_dir,
            stage,
            expected_source_identity=project.active_identity,
            expected_parent_identity=parent_identity,
            expected_source_manifest=completion,
        )
        stage_identity = _directory_identity(stage)
        _write_publication_manifest(stage, completion)
        _move_directory_no_replace(
            stage,
            project.archive_dir,
            expected_source_identity=stage_identity,
            expected_parent_identity=parent_identity,
            expected_source_manifest=completion,
        )
        final_recovery = project.archive_dir
        final_handoff = project.archive_dir / project.handoff_path.relative_to(
            project.active_dir
        )
        _write_text(project.archive_dir, final_handoff, completed_handoff)
        completed_manifest = dict(completion)
        completed_manifest[
            project.handoff_path.relative_to(project.active_dir).as_posix()
        ] = _sha256(final_handoff)
        _write_publication_manifest(project.archive_dir, completed_manifest)
        if not _publication_manifest_matches(
            project.archive_dir, completed_manifest
        ):
            raise ResumeArchiveError("resume-publication")
        final_recovery = None
        return project.archive_dir
    except ResumeArchiveError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ResumeArchiveError("resume-local-stage") from None
    finally:
        recovery = stage if stage is not None and _safe_existing_directory(stage) else final_recovery
        if recovery is not None and _safe_existing_directory(recovery):
            try:
                _sanitize_incomplete_stage(
                    recovery,
                    project,
                    duration_seconds,
                    word_count,
                    _handoff_renderer(handoff_snapshot),
                )
            except (OSError, RuntimeError):
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--active-project", required=True)
    args = parser.parse_args(argv)
    try:
        archive = resume_content_archive(
            workspace_root=args.workspace_root,
            active_project=args.active_project,
        )
    except ResumeArchiveError as error:
        print(f"CONTENT_ARCHIVE_RESUME=FAIL reason={error}", file=sys.stderr)
        return 2
    print(f"CONTENT_ARCHIVE_RESUME=PASS archive={archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

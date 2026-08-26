"""Finalize one canonical content-driven production; no provider is called."""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from boomearth.video.artifacts import (
    ArtifactError,
    capture_regular_file,
    load_json_snapshot,
    snapshot_matches,
)
from boomearth.video.avatar_composite import (
    plan_circle_avatar,
    probe_video_size,
    render_circle_avatar,
)
from boomearth.video.content_plan import ContentPlanError, load_content_plan_snapshot
from boomearth.video.cover_contracts import (
    DEFAULT_PUNK_COVER_CONTRACT,
    LEGACY_PLATFORM_COVER_CONTRACT,
)
from boomearth.video.illustration_manifest import (
    IllustrationManifestError,
    load_illustration_manifest_snapshot,
)
from boomearth.video.render_project import prepare_content_render_project
from boomearth.video.motion_plan import MotionPlanError, load_motion_plan_snapshot
from boomearth.video.platform_covers import (
    cover_artifact_paths,
    render_platform_covers,
)
from boomearth.video.scene_qc import (
    create_scene_contact_sheet,
    render_scene_qc,
    scene_contact_sheet_qc,
    motion_preview_times,
    publish_motion_preview_qc,
)
from boomearth.video.scene_timeline import SceneTimelineError, validate_scene_timeline
from run_v2_production_sample import (
    ProductionProject,
    ProductionSampleResult,
    finalize_production_sample,
    load_production_project,
    _is_contained,
    _is_reparse_point,
    _close_handle,
    _project_paths,
    _safe_mkdir,
    _safe_existing_file,
    _sha256,
)


def _content_contract(project: ProductionProject):
    root = project.active_dir
    plan_snapshot = load_content_plan_snapshot(project_root=root)
    timeline_path = root / "工程" / "scene-timeline.json"
    timeline_value, _timeline_snapshot = load_json_snapshot(timeline_path, within=root)
    timeline = validate_scene_timeline(timeline_value, project_root=root)
    assets: list[Path] = []
    for scene in plan_snapshot.plan.scenes:
        if scene.visual_asset is None:
            continue
        asset = root / Path(*scene.visual_asset.split("/"))
        assets.append(asset)
    if not assets and plan_snapshot.plan.visual_system != "semantic-handdrawn-v3":
        raise ContentPlanError("content visual assets are invalid")
    motion_snapshot = None
    illustration_paths: tuple[Path, ...] = ()
    cover_sources: tuple[Path, ...] = ()
    if plan_snapshot.plan.visual_system in {
        "editorial-motion-v2",
        "semantic-handdrawn-v3",
        "profiled-illustration-v4",
    }:
        motion_snapshot = load_motion_plan_snapshot(root)
        illustration_snapshot = load_illustration_manifest_snapshot(root)
        illustration_paths = (
            illustration_snapshot.path,
            *(snapshot.path for snapshot in illustration_snapshot.prompt_snapshots),
            *(snapshot.path for snapshot in illustration_snapshot.candidate_snapshots),
            *((illustration_snapshot.semantic_qc_snapshot.path,) if illustration_snapshot.semantic_qc_snapshot is not None else ()),
        )
        if illustration_snapshot.semantic_qc_snapshot is not None:
            approved_relatives = {
                path
                for item in illustration_snapshot.manifest.assets
                if item.qc_status == "pass"
                for path in (
                    getattr(item, "selected_asset_path", None)
                    or getattr(item, "asset_path", None),
                )
                if isinstance(path, str)
            }
            cover_sources = tuple(
                asset
                for asset in assets
                if asset.relative_to(root).as_posix() in approved_relatives
            )
    protected = (
        plan_snapshot.path,
        timeline_path,
        *((motion_snapshot.path,) if motion_snapshot is not None else ()),
        *assets,
        *illustration_paths,
    )
    return (
        timeline,
        protected,
        plan_snapshot.handoff_snapshot,
        motion_snapshot,
        cover_sources,
    )


def _handoff_renderer(snapshot):
    try:
        original = snapshot.payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ContentPlanError("public handoff is invalid") from None
    lines = original.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    delimiters = [index for index, line in enumerate(lines) if line == "---"]
    if len(delimiters) < 2 or delimiters[0] != 0:
        raise ContentPlanError("public handoff is invalid")
    second = delimiters[1]
    fields: list[tuple[str, str]] = []
    for line in lines[1:second]:
        if not line:
            continue
        key, separator, value = line.partition(":")
        if separator != ":" or not key.strip():
            raise ContentPlanError("public handoff is invalid")
        fields.append((key.strip(), value.strip()))
    required = {"status", "duration_target_s", "word_count"}
    if not required <= {key for key, _value in fields}:
        raise ContentPlanError("public handoff is invalid")
    body = "\n".join(lines[second + 1 :]).strip()
    body = re.sub(
        r"(?:^|\n)## (?:制作回执|QC结果)\n.*?(?=\n## |\Z)",
        "",
        body,
        flags=re.DOTALL,
    ).strip()
    first_active_render = True

    def render(
        _project: ProductionProject,
        status: str,
        duration_seconds: float,
        word_count: int,
    ) -> str:
        nonlocal first_active_render
        if status not in {"制作中", "已完成"}:
            raise RuntimeError("content-handoff")
        if first_active_render:
            if not snapshot_matches(snapshot):
                raise RuntimeError("content-handoff")
            first_active_render = False
        replacements = {
            "status": status,
            "duration_target_s": f"{duration_seconds:.3f}",
            "word_count": str(word_count),
        }
        frontmatter = "\n".join(
            f"{key}: {replacements.get(key, value)}" for key, value in fields
        )
        receipt = ""
        if status == "已完成":
            receipt = (
                "\n\n## 制作回执\n\n"
                "- 交付检查：pass\n"
                "- 归档状态：已完成\n\n"
                "## QC结果\n\n"
                "- 逐场景预览：pass\n"
                "- 联系表：覆盖全部场景中点\n"
                "- 字幕：最终 WAV 的词级时间戳"
            )
        return f"---\n{frontmatter}\n---\n\n{body}{receipt}\n"

    return render


def _validated_avatar_master(
    project: ProductionProject, avatar_master: Path | None
) -> Path | None:
    if avatar_master is None:
        return None
    candidate = Path(avatar_master)
    if ".." in candidate.parts:
        raise ContentPlanError("avatar master is invalid")
    path = candidate.absolute()
    if not _is_contained(project.active_dir, path) or not _safe_existing_file(path):
        raise ContentPlanError("avatar master is invalid")
    return path


def _avatar_master_snapshot(path: Path) -> tuple[tuple[int, int, int, int], str]:
    if not _safe_existing_file(path):
        raise ContentPlanError("avatar master is invalid")
    before = path.lstat()
    digest = _sha256(path)
    after = path.lstat()
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ContentPlanError("avatar master is invalid")
    return identity, digest


def _open_private_target_lock(path: Path) -> int | None:
    """Prevent replacement of a verified private target during source unlink on Windows."""

    if os.name != "nt":
        return None
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x80000000,
        0x00000001,
        None,
        3,
        0x00200000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise RuntimeError("private-avatar-master")
    return int(handle)


def _preserve_avatar_master(
    project: ProductionProject,
    master: Path,
    master_identity: tuple[int, int, int, int],
    master_sha256: str,
) -> None:
    workspace = Path(project.workspace_root).absolute()
    expected_active = _project_paths(
        workspace, project.archive_slug, project.project_date
    )["active_dir"]
    if project.active_dir != expected_active:
        raise RuntimeError("private-avatar-master")
    workbench = expected_active.parent.parent
    private_directory = (
        workbench
        / ".internal"
        / "heygen"
        / "archived-masters"
        / project.active_dir.name
    )
    target = private_directory / f"{master_sha256}.mp4"
    if (
        not _is_contained(project.active_dir, master)
        or not _safe_existing_file(master)
        or not _is_contained(workspace, workbench)
        or not _is_contained(workbench, private_directory)
        or _is_reparse_point(target)
    ):
        raise RuntimeError("private-avatar-master")
    current = master.lstat()
    if (
        (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        != master_identity
        or _sha256(master) != master_sha256
    ):
        raise RuntimeError("private-avatar-master")
    _safe_mkdir(workbench, private_directory)
    if not target.exists():
        try:
            os.link(master, target)
        except FileExistsError:
            pass
        except OSError:
            raise RuntimeError("private-avatar-master") from None
    if not _safe_existing_file(target):
        raise RuntimeError("private-avatar-master")
    target_before = target.lstat()
    target_identity = (
        target_before.st_dev,
        target_before.st_ino,
        target_before.st_size,
        target_before.st_mtime_ns,
    )
    if _sha256(target) != master_sha256:
        raise RuntimeError("private-avatar-master")
    target_after = target.lstat()
    if target_identity != (
        target_after.st_dev,
        target_after.st_ino,
        target_after.st_size,
        target_after.st_mtime_ns,
    ):
        raise RuntimeError("private-avatar-master")
    target_lock = _open_private_target_lock(target)
    if target_lock is None:
        raise RuntimeError("private-avatar-master")
    try:
        if not _safe_existing_file(target):
            raise RuntimeError("private-avatar-master")
        target_current = target.lstat()
        if (
            target_identity
            != (
                target_current.st_dev,
                target_current.st_ino,
                target_current.st_size,
                target_current.st_mtime_ns,
            )
            or _sha256(target) != master_sha256
        ):
            raise RuntimeError("private-avatar-master")
        target_current = target.lstat()
        if target_identity != (
            target_current.st_dev,
            target_current.st_ino,
            target_current.st_size,
            target_current.st_mtime_ns,
        ):
            raise RuntimeError("private-avatar-master")
        current = master.lstat()
        if (
            (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
            != master_identity
            or _sha256(master) != master_sha256
        ):
            raise RuntimeError("private-avatar-master")
        try:
            master.unlink()
        except OSError:
            raise RuntimeError("private-avatar-master") from None
    finally:
        _close_handle(target_lock)


def _cover_handoff(snapshot) -> tuple[bool, str | None]:
    try:
        text = (
            snapshot.payload.decode("utf-8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
    except UnicodeDecodeError:
        raise ContentPlanError("public handoff is invalid") from None
    lines = text.split("\n")
    delimiters = [index for index, line in enumerate(lines) if line == "---"]
    if len(delimiters) < 2 or delimiters[0] != 0:
        raise ContentPlanError("public handoff is invalid")
    fields: dict[str, str] = {}
    for line in lines[1 : delimiters[1]]:
        if not line:
            continue
        key, separator, value = line.partition(":")
        if separator != ":" or not key.strip() or key.strip() in fields:
            raise ContentPlanError("public handoff is invalid")
        normalized = value.strip()
        if (
            len(normalized) > 1
            and normalized[0] == normalized[-1]
            and normalized[0] in "'\""
        ):
            normalized = normalized[1:-1]
        fields[key.strip()] = normalized
    marker = fields.get("covers")
    if marker is None:
        return False, None
    if marker == DEFAULT_PUNK_COVER_CONTRACT:
        return False, None
    if marker != LEGACY_PLATFORM_COVER_CONTRACT:
        raise ContentPlanError("public handoff is invalid")
    body = lines[delimiters[1] + 1 :]
    try:
        heading = next(
            index for index, line in enumerate(body) if line.strip() == "## 标题候选"
        )
    except StopIteration:
        raise ContentPlanError("public handoff is invalid") from None
    for line in body[heading + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("- ") and stripped[2:].strip():
            return True, stripped[2:].strip()
    raise ContentPlanError("public handoff is invalid")


def _validated_cover_source(
    project: ProductionProject,
    cover_background: Path | None,
    assets: tuple[Path, ...],
) -> Path | None:
    if cover_background is None:
        return assets[0] if assets else None
    supplied = Path(cover_background)
    if ".." in supplied.parts:
        raise ContentPlanError("cover source is invalid")
    candidate = (
        supplied.absolute()
        if supplied.is_absolute()
        else (project.active_dir / supplied).absolute()
    )
    if (
        candidate not in assets
        or not _is_contained(project.active_dir, candidate)
        or not _safe_existing_file(candidate)
    ):
        raise ContentPlanError("cover source is invalid")
    current = candidate
    while current != project.active_dir:
        if _is_reparse_point(current):
            raise ContentPlanError("cover source is invalid")
        current = current.parent
    try:
        candidate.resolve(strict=True).relative_to(project.active_dir.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        raise ContentPlanError("cover source is invalid") from None
    return candidate


_SCENE_QC_KEYS = {
    "schema_version",
    "status",
    "final_video_sha256",
    "content_plan_sha256",
    "scene_timeline_sha256",
    "evidence",
}
_SCENE_EVIDENCE_KEYS = {
    "scene_id",
    "time_s",
    "kind",
    "frame_sha256",
    "preview_sha256",
    "foreground_pixels",
    "dark_caption_pixels",
    "bright_caption_pixels",
}


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _verified_final_frame_source(project: ProductionProject, timeline) -> Path:
    """Select one final-MP4 frame only through exact passing local QC evidence."""

    root = project.active_dir
    timeline_path = root / "工程" / "scene-timeline.json"
    plan_path = root / "工程" / "content-plan.json"
    scene_qc_path = root / "质检" / "scene-preview-qc.json"
    contact_qc_path = project.contact_sheet_qc_path
    try:
        final_snapshot = capture_regular_file(project.render_path, within=root)
        plan_snapshot = capture_regular_file(plan_path, within=root)
        timeline_snapshot = capture_regular_file(timeline_path, within=root)
        contact_snapshot = capture_regular_file(project.contact_sheet_path, within=root)
        scene_qc, scene_qc_snapshot = load_json_snapshot(scene_qc_path, within=root)
        contact_qc, contact_qc_snapshot = load_json_snapshot(
            contact_qc_path, within=root
        )
        if (
            not isinstance(scene_qc, dict)
            or set(scene_qc) != _SCENE_QC_KEYS
            or scene_qc.get("schema_version") != 1
            or scene_qc.get("status") != "pass"
            or scene_qc.get("final_video_sha256") != final_snapshot.sha256
            or scene_qc.get("content_plan_sha256") != plan_snapshot.sha256
            or scene_qc.get("scene_timeline_sha256") != timeline_snapshot.sha256
            or not isinstance(scene_qc.get("evidence"), list)
        ):
            raise ContentPlanError("cover source is invalid")
        if not isinstance(contact_qc, dict):
            raise ContentPlanError("cover source is invalid")
        raw_times = contact_qc.get("times_s")
        if not isinstance(raw_times, list):
            raise ContentPlanError("cover source is invalid")
        expected_contact_qc = scene_contact_sheet_qc(
            scene_timeline=timeline_path,
            times=tuple(raw_times),
        )
        if contact_qc != expected_contact_qc:
            raise ContentPlanError("cover source is invalid")

        expected_pairs = {
            (scene.id, kind)
            for scene in timeline.scenes
            for kind in ("settle", "midpoint", "late")
        }
        evidence_by_pair: dict[tuple[str, str], object] = {}
        preview_snapshots = []
        for raw in scene_qc["evidence"]:
            if not isinstance(raw, dict) or set(raw) != _SCENE_EVIDENCE_KEYS:
                raise ContentPlanError("cover source is invalid")
            pair = (raw.get("scene_id"), raw.get("kind"))
            if (
                pair not in expected_pairs
                or pair in evidence_by_pair
                or not _valid_digest(raw.get("frame_sha256"))
                or not _valid_digest(raw.get("preview_sha256"))
            ):
                raise ContentPlanError("cover source is invalid")
            preview = root / "质检" / "scene-previews" / f"{pair[0]}-{pair[1]}.png"
            preview_snapshot = capture_regular_file(preview, within=root)
            if preview_snapshot.sha256 != raw["preview_sha256"]:
                raise ContentPlanError("cover source is invalid")
            evidence_by_pair[pair] = preview_snapshot
            preview_snapshots.append(preview_snapshot)
        if set(evidence_by_pair) != expected_pairs:
            raise ContentPlanError("cover source is invalid")
        selected = evidence_by_pair[(timeline.scenes[-1].id, "late")]
        snapshots = (
            final_snapshot,
            plan_snapshot,
            timeline_snapshot,
            contact_snapshot,
            scene_qc_snapshot,
            contact_qc_snapshot,
            *preview_snapshots,
        )
        if not all(snapshot_matches(snapshot) for snapshot in snapshots):
            raise ContentPlanError("cover source is invalid")
        return selected.path
    except (ArtifactError, ContentPlanError, OSError, TypeError, ValueError):
        raise ContentPlanError("cover source is invalid") from None


def finalize_content_production(
    *,
    project: ProductionProject,
    audio_approved: bool,
    approved_narration_sha256: str | None,
    avatar_master: Path | None = None,
    cover_background: Path | None = None,
    cover_headline: str | None = None,
    finalize_fn: Callable[..., ProductionSampleResult] = finalize_production_sample,
    repo_root: Path = ROOT,
) -> ProductionSampleResult:
    """Wire content-only renderer and QC hooks into the proven P0 finalizer."""

    try:
        validated_avatar_master = _validated_avatar_master(project, avatar_master)
        avatar_master_identity, avatar_master_sha256 = (
            _avatar_master_snapshot(validated_avatar_master)
            if validated_avatar_master is not None
            else (None, None)
        )
        timeline, protected, handoff_snapshot, motion_snapshot, assets = _content_contract(project)
        handoff_text_fn = _handoff_renderer(handoff_snapshot)
        covers_enabled, default_cover_headline = _cover_handoff(handoff_snapshot)
        cover_source = (
            _validated_cover_source(project, cover_background, assets)
            if covers_enabled
            else None
        )
        selected_cover_headline = (
            cover_headline if cover_headline is not None else default_cover_headline
        )
        if covers_enabled and not isinstance(selected_cover_headline, str):
            raise ContentPlanError("cover headline is invalid")
    except (
        ArtifactError,
        ContentPlanError,
        IllustrationManifestError,
        SceneTimelineError,
        MotionPlanError,
        OSError,
        ValueError,
    ):
        return ProductionSampleResult(2, ("rule=content-production-contract",))
    root = project.active_dir
    timeline_path = root / "工程" / "scene-timeline.json"
    midpoints = [
        round((scene.start + scene.end) / 2.0, 6) for scene in timeline.scenes
    ]
    if not midpoints or any(not math.isfinite(value) for value in midpoints):
        return ProductionSampleResult(2, ("rule=content-production-contract",))

    def prepare(**kwargs: object):
        return prepare_content_render_project(
            project_root=root,
            output_dir=Path(kwargs["output_dir"]),
            repo_root=Path(repo_root),
        )

    def contact_sheet(final_mp4: Path, output: Path, _duration: float) -> list[float]:
        return list(
            create_scene_contact_sheet(
                final_mp4=final_mp4,
                scene_timeline=timeline_path,
                output=output,
            )
        )

    def contact_payload(times: list[float]) -> dict[str, object]:
        return scene_contact_sheet_qc(
            scene_timeline=timeline_path,
            times=tuple(times),
        )

    def post_render_qc(active_project: ProductionProject, _duration: float) -> None:
        render_scene_qc(
            final_mp4=active_project.render_path,
            content_plan=root / "工程" / "content-plan.json",
            scene_timeline=timeline_path,
            qc_root=root / "质检",
        )
        if motion_snapshot is not None:
            publish_motion_preview_qc(active_project.render_path, root)
        if covers_enabled:
            selected_source = cover_source or _verified_final_frame_source(
                active_project, timeline
            )
            render_platform_covers(
                root,
                selected_source,
                selected_cover_headline,
            )

    def publication_paths(_active_project: ProductionProject) -> tuple[Path, ...]:
        previews = root / "质检" / "scene-previews"
        expected_previews = tuple(
            previews / f"{scene.id}-{kind}.png"
            for scene in timeline.scenes
            for kind in ("settle", "midpoint", "late")
        )
        motion_paths: tuple[Path, ...] = ()
        if motion_snapshot is not None:
            motion_previews = root / "质检" / "motion-previews"
            motion_paths = (
                root / "质检" / "motion-preview-qc.json",
                root / "质检" / "motion-contact-sheet.jpg",
                *(
                    motion_previews / f"{scene_id}-{kind}.png"
                    for scene_id, values in motion_preview_times(motion_snapshot.plan).items()
                    for kind in values
                ),
            )
        return (
            *protected,
            root / "质检" / "scene-preview-qc.json",
            *expected_previews,
            *motion_paths,
            *(cover_artifact_paths(root) if covers_enabled else ()),
        )

    finalizer_kwargs = {
        "project": project,
        "audio_approved": audio_approved,
        "approved_narration_sha256": approved_narration_sha256,
        "prepare_project_fn": prepare,
        "contact_sheet_fn": contact_sheet,
        "extra_input_paths": (
            *protected,
            *((validated_avatar_master,) if validated_avatar_master is not None else ()),
            *((cover_source,) if covers_enabled and cover_source not in protected else ()),
        ),
        "extra_publication_paths_fn": publication_paths,
        "post_render_qc_fn": post_render_qc,
        "expected_contact_sheet_times_fn": lambda _duration: list(midpoints),
        "contact_sheet_qc_payload_fn": contact_payload,
        "handoff_text_fn": handoff_text_fn,
        "repo_root": Path(repo_root),
    }
    if validated_avatar_master is not None:
        def mux(
            background: Path, narration: Path, output: Path, _duration: float
        ) -> None:
            plan = plan_circle_avatar(
                source_size=probe_video_size(validated_avatar_master), occupied=()
            )
            render_circle_avatar(
                background, validated_avatar_master, narration, output, plan
            )

        finalizer_kwargs["mux_fn"] = mux
        def preserve_private_inputs(active_project: ProductionProject) -> None:
            if (
                avatar_master_identity is None
                or avatar_master_sha256 is None
            ):
                raise RuntimeError("private-avatar-master")
            _preserve_avatar_master(
                active_project,
                validated_avatar_master,
                avatar_master_identity,
                avatar_master_sha256,
            )

        finalizer_kwargs["pre_archive_private_inputs_fn"] = preserve_private_inputs
    return finalize_fn(**finalizer_kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--workspace-root", required=True, type=Path)
    finalize.add_argument("--active-project", required=True)
    finalize.add_argument("--audio-approved", action="store_true")
    finalize.add_argument("--approved-narration-sha256", required=True)
    finalize.add_argument("--avatar-master", type=Path)
    finalize.add_argument("--cover-background", type=Path)
    finalize.add_argument("--cover-headline")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project = load_production_project(
            workspace_root=args.workspace_root,
            active_project=args.active_project,
        )
    except (OSError, TypeError, ValueError):
        print("rule=content-production-project")
        return 2
    result = finalize_content_production(
        project=project,
        audio_approved=args.audio_approved,
        approved_narration_sha256=args.approved_narration_sha256,
        avatar_master=args.avatar_master,
        cover_background=args.cover_background,
        cover_headline=args.cover_headline,
        repo_root=ROOT,
    )
    for diagnostic in result.diagnostics:
        print(diagnostic)
    if result.exit_code == 0 and result.archive_path is not None:
        try:
            relative = Path(result.archive_path).absolute().relative_to(
                Path(args.workspace_root).absolute()
            )
        except ValueError:
            print("rule=content-production-archive")
            return 2
        print(f"archive={relative.as_posix()}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

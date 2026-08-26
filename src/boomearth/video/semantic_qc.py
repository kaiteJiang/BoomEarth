"""Fail-closed semantic review contract for Semantic Handdrawn V3 assets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from types import MappingProxyType

from boomearth.video.artifacts import (
    ArtifactError,
    FileSnapshot,
    capture_regular_file,
    load_json_snapshot,
    snapshot_matches,
)
from boomearth.video.content_plan import ContentPlan
from boomearth.video.illustration_themes import (
    PROFILED_VISUAL_SYSTEM,
    IllustrationTheme,
    IllustrationThemeError,
    get_theme,
)
from boomearth.workbench.handoff import validate_public_handoff


_TOP_FIELDS = {
    "schema_version",
    "visual_system",
    "project_id",
    "content_plan_sha256",
    "reviewer_type",
    "scenes",
}
_PROFILED_TOP_FIELDS = _TOP_FIELDS | {"visual_theme"}
_SCENE_FIELDS = {
    "scene_id",
    "contract_path",
    "contract_sha256",
    "selected_asset_path",
    "selected_asset_sha256",
    "relevance_rationale",
    "checks",
    "status",
}
_CHECK_FIELDS = {
    "subject_match",
    "action_match",
    "evidence_complete",
    "claim_readable",
    "non_generic",
    "forbidden_absent",
    "mobile_readable",
    "caption_safe",
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SemanticQCError(RuntimeError):
    """A fixed, redacted semantic-QC contract failure."""


@dataclass(frozen=True, slots=True)
class SemanticSceneQC:
    scene_id: str
    contract_path: str
    contract_sha256: str
    selected_asset_path: str | None
    selected_asset_sha256: str | None
    relevance_rationale: str
    checks: Mapping[str, bool]
    status: str


@dataclass(frozen=True, slots=True)
class SemanticQCReport:
    schema_version: int
    visual_system: str
    project_id: str
    content_plan_sha256: str
    reviewer_type: str
    scenes: tuple[SemanticSceneQC, ...]
    visual_theme: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticQCReportSnapshot:
    path: Path
    report: SemanticQCReport
    snapshot: FileSnapshot
    contract_snapshots: tuple[FileSnapshot, ...]
    asset_snapshots: tuple[FileSnapshot, ...]


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _safe_relative_path(value: object, *, expected: str) -> str:
    if (
        not isinstance(value, str)
        or value != expected
        or "\\" in value
        or ":" in value
    ):
        raise SemanticQCError("semantic QC is invalid")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or "." in pure.parts
        or ".." in pure.parts
        or pure.as_posix() != value
    ):
        raise SemanticQCError("semantic QC is invalid")
    return value


def _capture_bound_file(
    root: Path,
    relative: str,
    expected_sha256: object,
) -> FileSnapshot:
    if not _is_sha256(expected_sha256):
        raise SemanticQCError("semantic QC is invalid")
    try:
        snapshot = capture_regular_file(
            root / Path(*PurePosixPath(relative).parts),
            within=root,
        )
    except ArtifactError:
        raise SemanticQCError("semantic QC is invalid") from None
    if snapshot.sha256 != expected_sha256:
        raise SemanticQCError("semantic QC is invalid")
    return snapshot


def _validate_semantic_qc(
    candidate: object,
    *,
    project_root: Path,
    content_plan: ContentPlan,
    verify_files: bool,
) -> tuple[
    SemanticQCReport,
    tuple[FileSnapshot, ...],
    tuple[FileSnapshot, ...],
]:
    root = Path(project_root).absolute()
    is_profiled = (
        content_plan.schema_version == 4
        and content_plan.visual_system == PROFILED_VISUAL_SYSTEM
    )
    theme: IllustrationTheme | None = None
    if is_profiled:
        try:
            theme = get_theme(content_plan.visual_theme)  # type: ignore[arg-type]
        except IllustrationThemeError:
            raise SemanticQCError("semantic QC is invalid") from None
        contract_valid = (
            isinstance(candidate, dict)
            and set(candidate) == _PROFILED_TOP_FIELDS
            and candidate.get("schema_version") == 2
            and not isinstance(candidate.get("schema_version"), bool)
            and candidate.get("visual_system") == PROFILED_VISUAL_SYSTEM
            and candidate.get("visual_theme") == theme.id
        )
    else:
        contract_valid = (
            isinstance(candidate, dict)
            and set(candidate) == _TOP_FIELDS
            and candidate.get("schema_version") == 1
            and not isinstance(candidate.get("schema_version"), bool)
            and candidate.get("visual_system") == "semantic-handdrawn-v3"
            and content_plan.schema_version == 3
            and content_plan.visual_system == "semantic-handdrawn-v3"
        )
    if (
        not contract_valid
        or not isinstance(candidate, dict)
        or candidate.get("project_id") != content_plan.project_id
        or candidate.get("project_id") != root.name
        or candidate.get("reviewer_type") != "multimodal-review"
        or not _is_sha256(candidate.get("content_plan_sha256"))
        or not isinstance(candidate.get("scenes"), list)
        or len(candidate["scenes"]) != len(content_plan.scenes)
    ):
        raise SemanticQCError("semantic QC is invalid")

    contract_snapshots: list[FileSnapshot] = []
    asset_snapshots: list[FileSnapshot] = []
    if verify_files:
        plan_snapshot = _capture_bound_file(
            root,
            "工程/content-plan.json",
            candidate["content_plan_sha256"],
        )
        if plan_snapshot.sha256 != candidate["content_plan_sha256"]:
            raise SemanticQCError("semantic QC is invalid")

    results: list[SemanticSceneQC] = []
    for value, scene in zip(candidate["scenes"], content_plan.scenes):
        if (
            not isinstance(value, dict)
            or set(value) != _SCENE_FIELDS
            or value.get("scene_id") != scene.id
            or value.get("status") != "pass"
        ):
            raise SemanticQCError("semantic QC is invalid")
        if theme is not None:
            contract_relative = (
                "工程/assets/profiled-illustrations/"
                f"{theme.directory}/prompts/{scene.id}.md"
            )
        else:
            contract_relative = (
                f"工程/assets/semantic-handdrawn/type-led/{scene.id}.json"
                if scene.visual_mode == "type-led"
                else f"工程/assets/semantic-handdrawn/prompts/{scene.id}.md"
            )
        contract_path = _safe_relative_path(
            value.get("contract_path"), expected=contract_relative
        )
        if not _is_sha256(value.get("contract_sha256")):
            raise SemanticQCError("semantic QC is invalid")

        selected_path = value.get("selected_asset_path")
        selected_hash = value.get("selected_asset_sha256")
        if theme is None and scene.visual_mode == "type-led":
            if selected_path is not None or selected_hash is not None or scene.visual_asset is not None:
                raise SemanticQCError("semantic QC is invalid")
        else:
            if scene.visual_asset is None:
                raise SemanticQCError("semantic QC is invalid")
            selected_path = _safe_relative_path(
                selected_path,
                expected=scene.visual_asset,
            )
            if not _is_sha256(selected_hash):
                raise SemanticQCError("semantic QC is invalid")

        rationale = value.get("relevance_rationale")
        if (
            not isinstance(rationale, str)
            or rationale != rationale.strip()
            or not rationale
            or len(rationale) > 160
            or validate_public_handoff(rationale)
        ):
            raise SemanticQCError("semantic QC is invalid")
        checks = value.get("checks")
        expected_checks = (
            _CHECK_FIELDS | set(theme.required_qc)
            if theme is not None
            else _CHECK_FIELDS
        )
        if (
            not isinstance(checks, dict)
            or set(checks) != expected_checks
            or any(type(checks[name]) is not bool for name in expected_checks)
            or not all(checks[name] for name in expected_checks)
        ):
            raise SemanticQCError("semantic QC is invalid")

        if verify_files:
            contract_snapshots.append(
                _capture_bound_file(root, contract_path, value["contract_sha256"])
            )
            if isinstance(selected_path, str):
                asset_snapshots.append(
                    _capture_bound_file(root, selected_path, selected_hash)
                )
        results.append(
            SemanticSceneQC(
                scene_id=scene.id,
                contract_path=contract_path,
                contract_sha256=str(value["contract_sha256"]),
                selected_asset_path=(
                    selected_path if isinstance(selected_path, str) else None
                ),
                selected_asset_sha256=(
                    selected_hash if isinstance(selected_hash, str) else None
                ),
                relevance_rationale=rationale,
                checks=MappingProxyType(dict(checks)),
                status="pass",
            )
        )
    report = SemanticQCReport(
        schema_version=2 if theme is not None else 1,
        visual_system=(
            PROFILED_VISUAL_SYSTEM
            if theme is not None
            else "semantic-handdrawn-v3"
        ),
        project_id=root.name,
        content_plan_sha256=str(candidate["content_plan_sha256"]),
        reviewer_type="multimodal-review",
        scenes=tuple(results),
        visual_theme=theme.id if theme is not None else None,
    )
    return report, tuple(contract_snapshots), tuple(asset_snapshots)


def validate_semantic_qc(
    candidate: object,
    *,
    project_root: Path,
    content_plan: ContentPlan,
    verify_files: bool = True,
) -> SemanticQCReport:
    report, _, _ = _validate_semantic_qc(
        candidate,
        project_root=project_root,
        content_plan=content_plan,
        verify_files=verify_files,
    )
    return report


def load_semantic_qc_snapshot(
    project_root: Path,
    *,
    content_plan: ContentPlan,
    prearchive_project_root: Path | None = None,
) -> SemanticQCReportSnapshot:
    root = Path(project_root).absolute()
    if (
        content_plan.schema_version == 4
        and content_plan.visual_system == PROFILED_VISUAL_SYSTEM
    ):
        try:
            theme = get_theme(content_plan.visual_theme)  # type: ignore[arg-type]
        except IllustrationThemeError:
            raise SemanticQCError("semantic QC is invalid") from None
        qc_path = (
            root
            / "工程"
            / "assets"
            / "profiled-illustrations"
            / theme.directory
            / "semantic-qc.json"
        )
    else:
        qc_path = root / "工程" / "assets" / "semantic-handdrawn" / "semantic-qc.json"
    try:
        candidate, snapshot = load_json_snapshot(qc_path, within=root)
    except ArtifactError:
        raise SemanticQCError("semantic QC is invalid") from None
    report, contract_snapshots, asset_snapshots = _validate_semantic_qc(
        candidate,
        project_root=root,
        content_plan=content_plan,
        verify_files=True,
    )
    all_snapshots = (snapshot, *contract_snapshots, *asset_snapshots)
    if not all(snapshot_matches(item) for item in all_snapshots):
        raise SemanticQCError("semantic QC is invalid")
    return SemanticQCReportSnapshot(
        path=qc_path,
        report=report,
        snapshot=snapshot,
        contract_snapshots=contract_snapshots,
        asset_snapshots=asset_snapshots,
    )

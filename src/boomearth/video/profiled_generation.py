from __future__ import annotations

import hashlib
import io
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from boomearth.video.artifacts import (
    ArtifactError,
    capture_regular_file,
    load_json_snapshot,
)
from boomearth.video.illustration_themes import IllustrationThemeError, get_theme
from boomearth.workbench.source_artifacts import (
    SourceContractError,
    publish_json_exclusive,
    verify_private_relative,
)


GENERATION_ARTIFACT_KEYS = frozenset(
    {
        "path",
        "sha256",
        "generation_prompt_path",
        "generation_prompt_sha256",
        "generation_intent_path",
        "generation_intent_sha256",
        "generation_receipt_path",
        "generation_receipt_sha256",
    }
)
_INTENT_KEYS = frozenset(
    {
        "schema_version",
        "event",
        "sequence",
        "provider",
        "call_id",
        "scene_id",
        "visual_theme",
        "prompt_path",
        "prompt_sha256",
        "candidate_path",
        "receipt_path",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "event",
        "sequence",
        "provider",
        "call_id",
        "scene_id",
        "visual_theme",
        "intent_path",
        "intent_sha256",
        "prompt_path",
        "prompt_sha256",
        "candidate_path",
        "candidate_sha256",
    }
)
_RUNTIME_ADAPTED_RECEIPT_KEYS = _RECEIPT_KEYS | frozenset(
    {
        "source_original_path",
        "source_original_sha256",
        "source_original_width",
        "source_original_height",
        "transform",
    }
)
_REUSE_ARTIFACT_KEYS = frozenset(
    {
        "path",
        "sha256",
        "generation_prompt_path",
        "generation_prompt_sha256",
        "reuse_receipt_path",
        "reuse_receipt_sha256",
    }
)
HISTORY_ADAPTATION_ARTIFACT_KEYS = frozenset(
    {
        "path",
        "sha256",
        "generation_prompt_path",
        "generation_prompt_sha256",
        "adaptation_receipt_path",
        "adaptation_receipt_sha256",
    }
)
_REUSE_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "event",
        "provider",
        "scene_id",
        "visual_theme",
        "prompt_path",
        "prompt_sha256",
        "candidate_path",
        "candidate_sha256",
        "source_project_path",
        "source_scene_id",
        "source_candidate_path",
        "source_candidate_sha256",
        "source_prompt_path",
        "source_prompt_sha256",
        "source_intent_path",
        "source_intent_sha256",
        "source_receipt_path",
        "source_receipt_sha256",
        "reuse_reason",
    }
)
_ADAPTATION_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "event",
        "provider",
        "scene_id",
        "visual_theme",
        "prompt_path",
        "prompt_sha256",
        "candidate_path",
        "candidate_sha256",
        "source_origin",
        "source_original_path",
        "source_original_sha256",
        "source_original_width",
        "source_original_height",
        "transform",
        "approval_basis",
    }
)
_SCENE_ID = re.compile(r"scene-[0-9]{2}")
_MAX_PROMPT_BYTES = 256 * 1024


class ProfiledGenerationError(RuntimeError):
    """A fixed, source-redacted profiled-image generation failure."""


@dataclass(frozen=True, slots=True)
class ProfiledGenerationTicket:
    sequence: int
    call_id: str
    scene_id: str
    visual_theme: str
    candidate_index: int
    prompt_path: str
    prompt_sha256: str
    candidate_path: str
    intent_path: str
    intent_sha256: str
    receipt_path: str

    def payload(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "call_id": self.call_id,
            "scene_id": self.scene_id,
            "visual_theme": self.visual_theme,
            "candidate_index": self.candidate_index,
            "prompt_path": self.prompt_path,
            "prompt_sha256": self.prompt_sha256,
            "candidate_path": self.candidate_path,
            "intent_path": self.intent_path,
            "intent_sha256": self.intent_sha256,
            "receipt_path": self.receipt_path,
        }


def _relative_paths(
    theme_id: str,
    scene_id: str,
    candidate_index: int,
) -> dict[str, str]:
    try:
        get_theme(theme_id)
    except IllustrationThemeError:
        raise ProfiledGenerationError("profiled-generation-input-invalid") from None
    if (
        _SCENE_ID.fullmatch(scene_id) is None
        or isinstance(candidate_index, bool)
        or candidate_index not in {1, 2}
    ):
        raise ProfiledGenerationError("profiled-generation-input-invalid")
    base = f"工程/assets/profiled-illustrations/{theme_id}"
    stem = f"{scene_id}-candidate-{candidate_index:02d}"
    prompt_name = (
        f"{scene_id}.md"
        if candidate_index == 1
        else f"{scene_id}-repair-02.md"
    )
    return {
        "prompt": f"{base}/prompts/{prompt_name}",
        "candidate": f"{base}/candidates/{stem}.png",
        "intent": f"{base}/generation/{stem}-intent.json",
        "receipt": f"{base}/generation/{stem}-receipt.json",
    }


def _reuse_receipt_path(theme_id: str, scene_id: str, candidate_index: int) -> str:
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    stem = f"{scene_id}-candidate-{candidate_index:02d}-reuse-receipt.json"
    return str(Path(paths["receipt"]).with_name(stem)).replace("\\", "/")


def _adaptation_receipt_path(
    theme_id: str, scene_id: str, candidate_index: int
) -> str:
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    stem = f"{scene_id}-candidate-{candidate_index:02d}-user-adaptation-receipt.json"
    return str(Path(paths["receipt"]).with_name(stem)).replace("\\", "/")


def _adaptation_original_path(
    theme_id: str, scene_id: str, candidate_index: int
) -> str:
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    name = f"{scene_id}-candidate-{candidate_index:02d}-source.png"
    return str(Path(paths["candidate"]).parent.parent / "originals" / name).replace(
        "\\", "/"
    )


def _runtime_original_path(
    theme_id: str, scene_id: str, candidate_index: int
) -> str:
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    name = f"{scene_id}-candidate-{candidate_index:02d}-source.png"
    return str(Path(paths["candidate"]).parent.parent / "originals" / name).replace(
        "\\", "/"
    )


def _write_exclusive(root: Path, relative: str, payload: bytes) -> Path:
    try:
        target = verify_private_relative(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = verify_private_relative(root, relative)
        with target.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return target
    except (OSError, SourceContractError):
        raise ProfiledGenerationError("profiled-generation-publication-failed") from None


def _safe_workspace_relative(workspace_root: Path, path: Path) -> str:
    try:
        return Path(path).absolute().relative_to(Path(workspace_root).absolute()).as_posix()
    except ValueError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None


def _capture_source_file(path: Path, *, source_project_root: Path) -> object:
    try:
        return capture_regular_file(path, within=source_project_root)
    except ArtifactError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None


def _validated_prompt(payload: bytes) -> str:
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > _MAX_PROMPT_BYTES
        or b"\x00" in payload
    ):
        raise ProfiledGenerationError("profiled-generation-input-invalid")
    try:
        return payload.decode("utf-8")
    except UnicodeError:
        raise ProfiledGenerationError("profiled-generation-input-invalid") from None


def _validated_png(payload: bytes) -> None:
    if not isinstance(payload, bytes) or not payload:
        raise ProfiledGenerationError("profiled-generation-result-invalid")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.format != "PNG" or image.size != (3840, 2160):
                raise ProfiledGenerationError("profiled-generation-result-invalid")
    except ProfiledGenerationError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ProfiledGenerationError("profiled-generation-result-invalid") from None


def _adapt_history_png(payload: bytes) -> tuple[bytes, int, int]:
    if not isinstance(payload, bytes) or not payload:
        raise ProfiledGenerationError("profiled-generation-result-invalid")
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            if image.format != "PNG":
                raise ProfiledGenerationError("profiled-generation-result-invalid")
            width, height = image.size
            if width < 1 or height < 1:
                raise ProfiledGenerationError("profiled-generation-result-invalid")
            rendered = ImageOps.fit(
                image.convert("RGB"),
                (3840, 2160),
                method=Image.Resampling.LANCZOS,
            )
            output = io.BytesIO()
            rendered.save(output, format="PNG")
            return output.getvalue(), width, height
    except ProfiledGenerationError:
        raise
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ProfiledGenerationError("profiled-generation-result-invalid") from None


def _adapt_runtime_native_png(payload: bytes) -> tuple[bytes, int, int]:
    adapted, width, height = _adapt_history_png(payload)
    if (
        width < 1280
        or height < 720
        or abs((width / height) - (16 / 9)) > 0.01
    ):
        raise ProfiledGenerationError("profiled-generation-result-invalid")
    return adapted, width, height


def _canonical_call_id(call_id: str | None) -> str:
    try:
        return str(UUID(call_id)) if call_id is not None else str(uuid4())
    except (TypeError, ValueError, AttributeError):
        raise ProfiledGenerationError("profiled-generation-input-invalid") from None


def prepare_profiled_generation_call(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    prompt_payload: bytes,
    candidate_index: int,
    call_id: str | None = None,
) -> ProfiledGenerationTicket:
    """Publish one immutable prompt plus its sequence-1 external-call intent."""

    root = Path(project_root).absolute()
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    _validated_prompt(prompt_payload)
    canonical_call_id = _canonical_call_id(call_id)
    _write_exclusive(root, paths["prompt"], prompt_payload)
    prompt_sha256 = hashlib.sha256(prompt_payload).hexdigest()
    intent_path = verify_private_relative(root, paths["intent"])
    receipt_path = verify_private_relative(root, paths["receipt"])
    intent_value: dict[str, object] = {
        "schema_version": 1,
        "event": "imagegen-call-prepared",
        "sequence": 1,
        "provider": "imagegen",
        "call_id": canonical_call_id,
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "prompt_path": paths["prompt"],
        "prompt_sha256": prompt_sha256,
        "candidate_path": paths["candidate"],
        "receipt_path": paths["receipt"],
    }
    try:
        intent_sha256 = publish_json_exclusive(root, intent_path, intent_value)
    except SourceContractError:
        raise ProfiledGenerationError("profiled-generation-publication-failed") from None
    return ProfiledGenerationTicket(
        sequence=1,
        call_id=canonical_call_id,
        scene_id=scene_id,
        visual_theme=theme_id,
        candidate_index=candidate_index,
        prompt_path=paths["prompt"],
        prompt_sha256=prompt_sha256,
        candidate_path=paths["candidate"],
        intent_path=paths["intent"],
        intent_sha256=intent_sha256,
        receipt_path=paths["receipt"],
    )


def _recover_ticket(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    candidate_index: int,
    call_id: str,
    intent_sha256: str,
) -> ProfiledGenerationTicket:
    root = Path(project_root).absolute()
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    canonical_call_id = _canonical_call_id(call_id)
    if not isinstance(intent_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", intent_sha256
    ) is None:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    try:
        prompt_snapshot = capture_regular_file(
            root / Path(*Path(paths["prompt"]).parts), within=root
        )
        intent, intent_snapshot = load_json_snapshot(
            root / Path(*Path(paths["intent"]).parts), within=root
        )
    except ArtifactError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    expected_intent = {
        "schema_version": 1,
        "event": "imagegen-call-prepared",
        "sequence": 1,
        "provider": "imagegen",
        "call_id": canonical_call_id,
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "prompt_path": paths["prompt"],
        "prompt_sha256": prompt_snapshot.sha256,
        "candidate_path": paths["candidate"],
        "receipt_path": paths["receipt"],
    }
    if (
        intent_snapshot.sha256 != intent_sha256
        or not isinstance(intent, dict)
        or intent != expected_intent
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    return ProfiledGenerationTicket(
        sequence=1,
        call_id=canonical_call_id,
        scene_id=scene_id,
        visual_theme=theme_id,
        candidate_index=candidate_index,
        prompt_path=paths["prompt"],
        prompt_sha256=prompt_snapshot.sha256,
        candidate_path=paths["candidate"],
        intent_path=paths["intent"],
        intent_sha256=intent_snapshot.sha256,
        receipt_path=paths["receipt"],
    )


def complete_profiled_generation_call(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    candidate_index: int,
    call_id: str,
    intent_sha256: str,
    candidate_payload: bytes,
    adapt_runtime_native: bool = False,
) -> dict[str, str]:
    """Validate sequence 1, then publish the candidate and sequence-2 receipt."""

    root = Path(project_root).absolute()
    ticket = _recover_ticket(
        project_root=root,
        theme_id=theme_id,
        scene_id=scene_id,
        candidate_index=candidate_index,
        call_id=call_id,
        intent_sha256=intent_sha256,
    )
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    receipt_path = verify_private_relative(root, paths["receipt"])

    if not isinstance(adapt_runtime_native, bool):
        raise ProfiledGenerationError("profiled-generation-input-invalid")
    source_evidence: dict[str, object] = {}
    published_payload = candidate_payload
    if adapt_runtime_native:
        published_payload, source_width, source_height = _adapt_runtime_native_png(
            candidate_payload
        )
        source_path = _runtime_original_path(theme_id, scene_id, candidate_index)
        _write_exclusive(root, source_path, candidate_payload)
        source_evidence = {
            "source_original_path": source_path,
            "source_original_sha256": hashlib.sha256(candidate_payload).hexdigest(),
            "source_original_width": source_width,
            "source_original_height": source_height,
            "transform": "ImageOps.fit RGB 3840x2160 LANCZOS",
        }
    else:
        _validated_png(candidate_payload)
    _write_exclusive(root, paths["candidate"], published_payload)
    candidate_sha256 = hashlib.sha256(published_payload).hexdigest()
    receipt_value: dict[str, object] = {
        "schema_version": 1,
        "event": "imagegen-call-completed",
        "sequence": 2,
        "provider": "imagegen",
        "call_id": ticket.call_id,
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "intent_path": paths["intent"],
        "intent_sha256": ticket.intent_sha256,
        "prompt_path": paths["prompt"],
        "prompt_sha256": ticket.prompt_sha256,
        "candidate_path": paths["candidate"],
        "candidate_sha256": candidate_sha256,
        **source_evidence,
    }
    try:
        receipt_sha256 = publish_json_exclusive(root, receipt_path, receipt_value)
    except SourceContractError:
        raise ProfiledGenerationError("profiled-generation-publication-failed") from None
    return {
        "path": paths["candidate"],
        "sha256": candidate_sha256,
        "generation_prompt_path": paths["prompt"],
        "generation_prompt_sha256": ticket.prompt_sha256,
        "generation_intent_path": paths["intent"],
        "generation_intent_sha256": ticket.intent_sha256,
        "generation_receipt_path": paths["receipt"],
        "generation_receipt_sha256": receipt_sha256,
    }


def import_profiled_generation_reuse(
    *,
    project_root: Path,
    workspace_root: Path,
    theme_id: str,
    scene_id: str,
    candidate_index: int,
    prompt_payload: bytes,
    source_project_root: Path,
    source_scene_id: str,
    source_candidate_path: Path,
    source_prompt_path: Path,
    source_intent_path: Path,
    source_receipt_path: Path,
    reuse_reason: str,
) -> dict[str, str]:
    """Copy one prior reviewed PNG and bind it to immutable source evidence."""

    root = Path(project_root).absolute()
    workspace = Path(workspace_root).absolute()
    source_root = Path(source_project_root).absolute()
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    if (
        _SCENE_ID.fullmatch(source_scene_id) is None
        or not isinstance(reuse_reason, str)
        or not reuse_reason.strip()
        or len(reuse_reason) > 200
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    _validated_prompt(prompt_payload)
    try:
        target_prompt = capture_regular_file(
            root / Path(*Path(paths["prompt"]).parts), within=root
        )
    except ArtifactError:
        _write_exclusive(root, paths["prompt"], prompt_payload)
    else:
        if target_prompt.payload != prompt_payload:
            raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    source_candidate = _capture_source_file(
        source_candidate_path, source_project_root=source_root
    )
    source_prompt = _capture_source_file(
        source_prompt_path, source_project_root=source_root
    )
    source_intent = _capture_source_file(
        source_intent_path, source_project_root=source_root
    )
    source_receipt = _capture_source_file(
        source_receipt_path, source_project_root=source_root
    )
    source_paths = _relative_paths(theme_id, source_scene_id, candidate_index)
    try:
        source_candidate_relative = source_candidate.path.relative_to(source_root).as_posix()  # type: ignore[attr-defined]
        source_prompt_relative = source_prompt.path.relative_to(source_root).as_posix()  # type: ignore[attr-defined]
        source_intent_relative = source_intent.path.relative_to(source_root).as_posix()  # type: ignore[attr-defined]
        source_receipt_relative = source_receipt.path.relative_to(source_root).as_posix()  # type: ignore[attr-defined]
    except ValueError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    source_artifact = {
        "path": source_candidate_relative,
        "sha256": source_candidate.sha256,  # type: ignore[attr-defined]
        "generation_prompt_path": source_prompt_relative,
        "generation_prompt_sha256": source_prompt.sha256,  # type: ignore[attr-defined]
        "generation_intent_path": source_intent_relative,
        "generation_intent_sha256": source_intent.sha256,  # type: ignore[attr-defined]
        "generation_receipt_path": source_receipt_relative,
        "generation_receipt_sha256": source_receipt.sha256,  # type: ignore[attr-defined]
    }
    if (
        source_candidate_relative != source_paths["candidate"]
        or source_prompt_relative != source_paths["prompt"]
        or source_intent_relative != source_paths["intent"]
        or source_receipt_relative != source_paths["receipt"]
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    validate_profiled_generation_evidence(
        project_root=source_root,
        theme_id=theme_id,
        scene_id=source_scene_id,
        candidate_index=candidate_index,
        prompt_path=source_prompt_relative,
        prompt_sha256=source_prompt.sha256,  # type: ignore[attr-defined]
        candidate_path=source_candidate_relative,
        candidate_sha256=source_candidate.sha256,  # type: ignore[attr-defined]
        artifact=source_artifact,
    )
    _validated_png(source_candidate.payload)  # type: ignore[attr-defined]
    _write_exclusive(root, paths["candidate"], source_candidate.payload)  # type: ignore[attr-defined]
    prompt_sha256 = hashlib.sha256(prompt_payload).hexdigest()
    receipt_relative = _reuse_receipt_path(theme_id, scene_id, candidate_index)
    receipt_path = verify_private_relative(root, receipt_relative)
    receipt_value: dict[str, object] = {
        "schema_version": 1,
        "event": "profiled-illustration-reuse-imported",
        "provider": "imagegen",
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "prompt_path": paths["prompt"],
        "prompt_sha256": prompt_sha256,
        "candidate_path": paths["candidate"],
        "candidate_sha256": source_candidate.sha256,  # type: ignore[attr-defined]
        "source_project_path": _safe_workspace_relative(workspace, source_root),
        "source_scene_id": source_scene_id,
        "source_candidate_path": _safe_workspace_relative(workspace, source_candidate.path),  # type: ignore[attr-defined]
        "source_candidate_sha256": source_candidate.sha256,  # type: ignore[attr-defined]
        "source_prompt_path": _safe_workspace_relative(workspace, source_prompt.path),  # type: ignore[attr-defined]
        "source_prompt_sha256": source_prompt.sha256,  # type: ignore[attr-defined]
        "source_intent_path": _safe_workspace_relative(workspace, source_intent.path),  # type: ignore[attr-defined]
        "source_intent_sha256": source_intent.sha256,  # type: ignore[attr-defined]
        "source_receipt_path": _safe_workspace_relative(workspace, source_receipt.path),  # type: ignore[attr-defined]
        "source_receipt_sha256": source_receipt.sha256,  # type: ignore[attr-defined]
        "reuse_reason": reuse_reason,
    }
    try:
        receipt_sha256 = publish_json_exclusive(root, receipt_path, receipt_value)
    except SourceContractError:
        raise ProfiledGenerationError("profiled-generation-publication-failed") from None
    return {
        "path": paths["candidate"],
        "sha256": source_candidate.sha256,  # type: ignore[attr-defined]
        "generation_prompt_path": paths["prompt"],
        "generation_prompt_sha256": prompt_sha256,
        "reuse_receipt_path": receipt_relative,
        "reuse_receipt_sha256": receipt_sha256,
    }


def validate_profiled_reuse_evidence(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    candidate_index: int,
    prompt_path: str,
    prompt_sha256: str,
    candidate_path: str,
    candidate_sha256: str,
    artifact: Mapping[str, object],
) -> None:
    """Verify a local reused candidate is unchanged and source-bound by receipt."""

    paths = _relative_paths(theme_id, scene_id, candidate_index)
    receipt_relative = _reuse_receipt_path(theme_id, scene_id, candidate_index)
    if (
        set(artifact) != _REUSE_ARTIFACT_KEYS
        or prompt_path != paths["prompt"]
        or candidate_path != paths["candidate"]
        or artifact.get("path") != candidate_path
        or artifact.get("sha256") != candidate_sha256
        or artifact.get("generation_prompt_path") != prompt_path
        or artifact.get("generation_prompt_sha256") != prompt_sha256
        or artifact.get("reuse_receipt_path") != receipt_relative
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    root = Path(project_root).absolute()
    try:
        prompt = capture_regular_file(root / Path(*Path(prompt_path).parts), within=root)
        candidate = capture_regular_file(root / Path(*Path(candidate_path).parts), within=root)
        receipt, receipt_snapshot = load_json_snapshot(
            root / Path(*Path(receipt_relative).parts), within=root
        )
    except ArtifactError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    if (
        prompt.sha256 != prompt_sha256
        or candidate.sha256 != candidate_sha256
        or receipt_snapshot.sha256 != artifact.get("reuse_receipt_sha256")
        or not isinstance(receipt, dict)
        or set(receipt) != _REUSE_RECEIPT_KEYS
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    expected = {
        "schema_version": 1,
        "event": "profiled-illustration-reuse-imported",
        "provider": "imagegen",
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha256,
        "candidate_path": candidate_path,
        "candidate_sha256": candidate_sha256,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    for key in (
        "source_project_path",
        "source_scene_id",
        "source_candidate_path",
        "source_candidate_sha256",
        "source_prompt_path",
        "source_prompt_sha256",
        "source_intent_path",
        "source_intent_sha256",
        "source_receipt_path",
        "source_receipt_sha256",
        "reuse_reason",
    ):
        if not isinstance(receipt.get(key), str) or not receipt[key]:
            raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    try:
        workspace = root.parents[1]
        source_root = workspace / Path(str(receipt["source_project_path"]))
        source_candidate = capture_regular_file(
            workspace / Path(str(receipt["source_candidate_path"])),
            within=source_root,
        )
        source_prompt = capture_regular_file(
            workspace / Path(str(receipt["source_prompt_path"])),
            within=source_root,
        )
        source_intent = capture_regular_file(
            workspace / Path(str(receipt["source_intent_path"])),
            within=source_root,
        )
        source_receipt = capture_regular_file(
            workspace / Path(str(receipt["source_receipt_path"])),
            within=source_root,
        )
    except (ArtifactError, IndexError):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    if (
        source_candidate.sha256 != receipt["source_candidate_sha256"]
        or source_prompt.sha256 != receipt["source_prompt_sha256"]
        or source_intent.sha256 != receipt["source_intent_sha256"]
        or source_receipt.sha256 != receipt["source_receipt_sha256"]
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")


def import_user_approved_history_adaptation(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    candidate_index: int,
    prompt_payload: bytes,
    source_original_path: Path,
    source_origin: str,
    approval_basis: str,
) -> dict[str, str]:
    """Publish a 4K render copy of a user-approved, locally held historical PNG."""

    root = Path(project_root).absolute()
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    original_relative = _adaptation_original_path(theme_id, scene_id, candidate_index)
    receipt_relative = _adaptation_receipt_path(theme_id, scene_id, candidate_index)
    if (
        not isinstance(source_origin, str)
        or not source_origin.strip()
        or len(source_origin) > 200
        or not isinstance(approval_basis, str)
        or not approval_basis.strip()
        or len(approval_basis) > 300
    ):
        raise ProfiledGenerationError("profiled-generation-input-invalid")
    _validated_prompt(prompt_payload)
    try:
        target_prompt = capture_regular_file(
            root / Path(*Path(paths["prompt"]).parts), within=root
        )
    except ArtifactError:
        _write_exclusive(root, paths["prompt"], prompt_payload)
        prompt_sha256 = hashlib.sha256(prompt_payload).hexdigest()
    else:
        if target_prompt.payload != prompt_payload:
            raise ProfiledGenerationError("profiled-generation-evidence-invalid")
        prompt_sha256 = target_prompt.sha256
    try:
        original = capture_regular_file(source_original_path)
    except ArtifactError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    adapted_payload, original_width, original_height = _adapt_history_png(original.payload)
    _write_exclusive(root, original_relative, original.payload)
    _write_exclusive(root, paths["candidate"], adapted_payload)
    candidate_sha256 = hashlib.sha256(adapted_payload).hexdigest()
    receipt_value: dict[str, object] = {
        "schema_version": 1,
        "event": "profiled-illustration-user-approved-history-adapted",
        "provider": "local-image-adaptation",
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "prompt_path": paths["prompt"],
        "prompt_sha256": prompt_sha256,
        "candidate_path": paths["candidate"],
        "candidate_sha256": candidate_sha256,
        "source_origin": source_origin,
        "source_original_path": original_relative,
        "source_original_sha256": original.sha256,
        "source_original_width": original_width,
        "source_original_height": original_height,
        "transform": "ImageOps.fit RGB 3840x2160 LANCZOS",
        "approval_basis": approval_basis,
    }
    receipt_path = verify_private_relative(root, receipt_relative)
    try:
        receipt_sha256 = publish_json_exclusive(root, receipt_path, receipt_value)
    except SourceContractError:
        raise ProfiledGenerationError("profiled-generation-publication-failed") from None
    return {
        "path": paths["candidate"],
        "sha256": candidate_sha256,
        "generation_prompt_path": paths["prompt"],
        "generation_prompt_sha256": prompt_sha256,
        "adaptation_receipt_path": receipt_relative,
        "adaptation_receipt_sha256": receipt_sha256,
    }


def validate_user_approved_history_adaptation_evidence(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    candidate_index: int,
    prompt_path: str,
    prompt_sha256: str,
    candidate_path: str,
    candidate_sha256: str,
    artifact: Mapping[str, object],
) -> None:
    """Verify a user-approved historical original and its fixed 4K adaptation."""

    root = Path(project_root).absolute()
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    original_relative = _adaptation_original_path(theme_id, scene_id, candidate_index)
    receipt_relative = _adaptation_receipt_path(theme_id, scene_id, candidate_index)
    if (
        set(artifact) != HISTORY_ADAPTATION_ARTIFACT_KEYS
        or prompt_path != paths["prompt"]
        or candidate_path != paths["candidate"]
        or artifact.get("path") != candidate_path
        or artifact.get("sha256") != candidate_sha256
        or artifact.get("generation_prompt_path") != prompt_path
        or artifact.get("generation_prompt_sha256") != prompt_sha256
        or artifact.get("adaptation_receipt_path") != receipt_relative
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    try:
        prompt = capture_regular_file(root / Path(*Path(prompt_path).parts), within=root)
        candidate = capture_regular_file(
            root / Path(*Path(candidate_path).parts), within=root
        )
        original = capture_regular_file(
            root / Path(*Path(original_relative).parts), within=root
        )
        receipt, receipt_snapshot = load_json_snapshot(
            root / Path(*Path(receipt_relative).parts), within=root
        )
    except ArtifactError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    if (
        prompt.sha256 != prompt_sha256
        or candidate.sha256 != candidate_sha256
        or receipt_snapshot.sha256 != artifact.get("adaptation_receipt_sha256")
        or not isinstance(receipt, dict)
        or set(receipt) != _ADAPTATION_RECEIPT_KEYS
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    expected = {
        "schema_version": 1,
        "event": "profiled-illustration-user-approved-history-adapted",
        "provider": "local-image-adaptation",
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha256,
        "candidate_path": candidate_path,
        "candidate_sha256": candidate_sha256,
        "source_original_path": original_relative,
        "source_original_sha256": original.sha256,
        "transform": "ImageOps.fit RGB 3840x2160 LANCZOS",
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    if (
        not isinstance(receipt.get("source_origin"), str)
        or not receipt["source_origin"]
        or not isinstance(receipt.get("approval_basis"), str)
        or not receipt["approval_basis"]
        or not isinstance(receipt.get("source_original_width"), int)
        or not isinstance(receipt.get("source_original_height"), int)
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    try:
        adapted_payload, width, height = _adapt_history_png(original.payload)
    except ProfiledGenerationError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    if (
        width != receipt["source_original_width"]
        or height != receipt["source_original_height"]
        or hashlib.sha256(adapted_payload).hexdigest() != candidate.sha256
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")


def run_profiled_generation_call(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    prompt_payload: bytes,
    candidate_index: int,
    invoke: Callable[[str, str], bytes],
    call_id: str | None = None,
    adapt_runtime_native: bool = False,
) -> dict[str, str]:
    """Run the in-process form of the same prepare/invoke/complete state machine."""

    prompt_text = _validated_prompt(prompt_payload)
    ticket = prepare_profiled_generation_call(
        project_root=project_root,
        theme_id=theme_id,
        scene_id=scene_id,
        prompt_payload=prompt_payload,
        candidate_index=candidate_index,
        call_id=call_id,
    )
    provider_failed = False
    candidate_payload = b""
    try:
        candidate_payload = invoke(prompt_text, ticket.call_id)
    except Exception:
        provider_failed = True
    if provider_failed:
        raise ProfiledGenerationError("profiled-generation-provider-failed") from None
    return complete_profiled_generation_call(
        project_root=project_root,
        theme_id=theme_id,
        scene_id=scene_id,
        candidate_index=candidate_index,
        call_id=ticket.call_id,
        intent_sha256=ticket.intent_sha256,
        candidate_payload=candidate_payload,
        adapt_runtime_native=adapt_runtime_native,
    )


def validate_profiled_generation_evidence(
    *,
    project_root: Path,
    theme_id: str,
    scene_id: str,
    candidate_index: int,
    prompt_path: str,
    prompt_sha256: str,
    candidate_path: str,
    candidate_sha256: str,
    artifact: Mapping[str, object],
) -> None:
    """Verify the exact pre-call intent and post-call receipt hash chain."""

    if set(artifact) != GENERATION_ARTIFACT_KEYS:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    paths = _relative_paths(theme_id, scene_id, candidate_index)
    if (
        prompt_path != paths["prompt"]
        or candidate_path != paths["candidate"]
        or artifact.get("path") != candidate_path
        or artifact.get("sha256") != candidate_sha256
        or artifact.get("generation_prompt_path") != prompt_path
        or artifact.get("generation_prompt_sha256") != prompt_sha256
        or artifact.get("generation_intent_path") != paths["intent"]
        or artifact.get("generation_receipt_path") != paths["receipt"]
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    root = Path(project_root).absolute()
    try:
        prompt_snapshot = capture_regular_file(
            root / Path(*Path(paths["prompt"]).parts), within=root
        )
        candidate_snapshot = capture_regular_file(
            root / Path(*Path(paths["candidate"]).parts), within=root
        )
        intent, intent_snapshot = load_json_snapshot(
            root / Path(*Path(paths["intent"]).parts), within=root
        )
        receipt, receipt_snapshot = load_json_snapshot(
            root / Path(*Path(paths["receipt"]).parts), within=root
        )
    except ArtifactError:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    if (
        not isinstance(intent, dict)
        or prompt_snapshot.sha256 != prompt_sha256
        or candidate_snapshot.sha256 != candidate_sha256
        or set(intent) != _INTENT_KEYS
        or not isinstance(receipt, dict)
        or set(receipt) not in {_RECEIPT_KEYS, _RUNTIME_ADAPTED_RECEIPT_KEYS}
        or artifact.get("generation_intent_sha256") != intent_snapshot.sha256
        or artifact.get("generation_receipt_sha256") != receipt_snapshot.sha256
        or not isinstance(intent.get("call_id"), str)
    ):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")
    try:
        canonical_call_id = str(UUID(intent["call_id"]))
    except (TypeError, ValueError, AttributeError):
        raise ProfiledGenerationError("profiled-generation-evidence-invalid") from None
    expected_intent = {
        "schema_version": 1,
        "event": "imagegen-call-prepared",
        "sequence": 1,
        "provider": "imagegen",
        "call_id": canonical_call_id,
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha256,
        "candidate_path": candidate_path,
        "receipt_path": paths["receipt"],
    }
    expected_receipt = {
        "schema_version": 1,
        "event": "imagegen-call-completed",
        "sequence": 2,
        "provider": "imagegen",
        "call_id": canonical_call_id,
        "scene_id": scene_id,
        "visual_theme": theme_id,
        "intent_path": paths["intent"],
        "intent_sha256": intent_snapshot.sha256,
        "prompt_path": prompt_path,
        "prompt_sha256": prompt_sha256,
        "candidate_path": candidate_path,
        "candidate_sha256": candidate_sha256,
    }
    if set(receipt) == _RUNTIME_ADAPTED_RECEIPT_KEYS:
        original_relative = _runtime_original_path(
            theme_id, scene_id, candidate_index
        )
        try:
            original = capture_regular_file(
                root / Path(*Path(original_relative).parts), within=root
            )
            adapted_payload, width, height = _adapt_runtime_native_png(
                original.payload
            )
        except (ArtifactError, ProfiledGenerationError):
            raise ProfiledGenerationError(
                "profiled-generation-evidence-invalid"
            ) from None
        expected_receipt.update(
            {
                "source_original_path": original_relative,
                "source_original_sha256": original.sha256,
                "source_original_width": width,
                "source_original_height": height,
                "transform": "ImageOps.fit RGB 3840x2160 LANCZOS",
            }
        )
        if hashlib.sha256(adapted_payload).hexdigest() != candidate_sha256:
            raise ProfiledGenerationError(
                "profiled-generation-evidence-invalid"
            )
    if intent != expected_intent or receipt != expected_receipt:
        raise ProfiledGenerationError("profiled-generation-evidence-invalid")

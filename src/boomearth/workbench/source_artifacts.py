"""Strict, source-redacted contracts for private P2 work artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4


SourceKind = Literal["local", "url", "x-article", "github-skill"]
SourceStage = Literal[
    "source_registered",
    "github_skill_planned",
    "github_skill_ready",
    "article_ready",
    "media_ready",
    "audio_ready",
    "transcript_ready",
    "rewrite_ready",
    "handoff_ready",
    "production_started",
    "production_archived",
    "skipped",
]

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_HASH_CHUNK_SIZE = 1024 * 1024
_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "request_count",
        "network_required",
        "fee_possible",
        "no_retry",
        "no_fallback",
    }
)
_APPROVAL_KEYS = frozenset(
    {
        "approved",
        "plan_sha256",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "request_count",
        "no_retry",
        "no_fallback",
    }
)


class SourceContractError(ValueError):
    """A fixed-message private artifact contract failure."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ActionPlan:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    request_count: int
    network_required: bool
    fee_possible: bool
    no_retry: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "ActionPlan(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ArtifactRecord:
    relative_path: str
    sha256: str
    size_bytes: int
    path: Path = field(repr=False)

    def __repr__(self) -> str:
        return "ArtifactRecord(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class SourceWorkOrder:
    work_id: str
    source_kind: SourceKind
    authorized: bool
    created_at: str
    stage: SourceStage
    source_input_sha256: str
    private_root: Path = field(repr=False)

    def __repr__(self) -> str:
        return "SourceWorkOrder(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ApprovalReceipt:
    approved: bool
    plan_sha256: str
    work_id: str
    provider: str
    action: str
    input_sha256: str
    request_count: int
    no_retry: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "ApprovalReceipt(<redacted>)"


def new_work_id() -> str:
    """Return a random source-opaque UUID4 work identifier."""

    return str(uuid4())


def _is_reparse_stat(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        int(getattr(value, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _absolute_without_resolving(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError):
        raise SourceContractError("private-path-invalid") from None


def _ensure_no_reparse_components(path: Path) -> None:
    current = path
    pending: list[Path] = []
    while True:
        pending.append(current)
        if current == current.parent:
            break
        current = current.parent
    for component in reversed(pending):
        try:
            component_stat = component.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise SourceContractError("private-path-invalid") from None
        if _is_reparse_stat(component_stat):
            raise SourceContractError("private-path-invalid")


def verify_private_relative(root: Path, relative: str) -> Path:
    """Return one non-escaping private path without following reparse points."""

    if not isinstance(relative, str) or not relative.strip():
        raise SourceContractError("private-path-invalid")
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or bool(candidate.drive)
        or candidate in {Path("."), Path("")}
        or ".." in candidate.parts
    ):
        raise SourceContractError("private-path-invalid")

    absolute_root = _absolute_without_resolving(Path(root))
    absolute_candidate = _absolute_without_resolving(absolute_root / candidate)
    try:
        if os.path.commonpath((absolute_root, absolute_candidate)) != str(absolute_root):
            raise SourceContractError("private-path-invalid")
    except (OSError, ValueError):
        raise SourceContractError("private-path-invalid") from None

    _ensure_no_reparse_components(absolute_root)
    _ensure_no_reparse_components(absolute_candidate)
    return absolute_candidate


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise SourceContractError("json-value-invalid")
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SourceContractError("json-value-invalid")
            _validate_json_value(item)
        return
    raise SourceContractError("json-value-invalid")


def canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    """Serialize a strict JSON mapping deterministically as UTF-8."""

    if not isinstance(value, Mapping):
        raise SourceContractError("json-value-invalid")
    _validate_json_value(value)
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise SourceContractError("json-value-invalid") from None
    return (serialized + "\n").encode("utf-8")


def _relative_from_root(root: Path, target: Path) -> str:
    absolute_root = _absolute_without_resolving(root)
    absolute_target = _absolute_without_resolving(target)
    try:
        relative = absolute_target.relative_to(absolute_root)
    except ValueError:
        raise SourceContractError("private-path-invalid") from None
    return relative.as_posix()


def publish_json_exclusive(
    root: Path,
    target: Path,
    value: Mapping[str, object],
) -> str:
    """Fsync and atomically publish canonical JSON without intentional overwrite."""

    relative = _relative_from_root(Path(root), Path(target))
    formal = verify_private_relative(Path(root), relative)
    if formal.exists() or formal.is_symlink():
        raise SourceContractError("artifact-exists")
    payload = canonical_json_bytes(value)
    temporary: Path | None = None
    try:
        formal.parent.mkdir(parents=True, exist_ok=True)
        verify_private_relative(Path(root), relative)
        if formal.exists() or formal.is_symlink():
            raise SourceContractError("artifact-exists")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{formal.name}.",
            suffix=".tmp",
            dir=formal.parent,
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(payload)
            sink.flush()
            os.fsync(sink.fileno())
        if formal.exists() or formal.is_symlink():
            raise SourceContractError("artifact-exists")
        try:
            os.link(temporary, formal)
        except FileExistsError:
            raise SourceContractError("artifact-exists") from None
        temporary.unlink()
        temporary = None
    except SourceContractError:
        raise
    except OSError:
        raise SourceContractError("artifact-unavailable") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash one ordinary, non-reparse file with bounded memory."""

    source = _absolute_without_resolving(Path(path))
    try:
        _ensure_no_reparse_components(source)
        source_stat = source.lstat()
        if not stat.S_ISREG(source_stat.st_mode) or _is_reparse_stat(source_stat):
            raise SourceContractError("artifact-unavailable")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(_HASH_CHUNK_SIZE), b""):
                digest.update(block)
        return digest.hexdigest()
    except SourceContractError:
        raise
    except OSError:
        raise SourceContractError("artifact-unavailable") from None


def load_exact_json(
    path: Path,
    required_keys: frozenset[str],
) -> dict[str, object]:
    """Load one exact-key JSON object using fixed redacted failures."""

    try:
        raw = Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SourceContractError("json-unavailable") from None
    if not isinstance(value, dict) or set(value) != set(required_keys):
        raise SourceContractError("json-schema-invalid")
    try:
        _validate_json_value(value)
    except SourceContractError:
        raise SourceContractError("json-schema-invalid") from None
    return value


def _is_uuid4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _is_token(value: object) -> bool:
    return isinstance(value, str) and _SAFE_TOKEN_RE.fullmatch(value) is not None


def _action_plan_dict(plan: ActionPlan) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
        "provider": plan.provider,
        "action": plan.action,
        "input_sha256": plan.input_sha256,
        "request_count": plan.request_count,
        "network_required": plan.network_required,
        "fee_possible": plan.fee_possible,
        "no_retry": plan.no_retry,
        "no_fallback": plan.no_fallback,
    }


def load_action_plan(path: Path) -> ActionPlan:
    """Load and validate the exact version-1 network action plan schema."""

    try:
        value = load_exact_json(path, _PLAN_KEYS)
    except SourceContractError:
        raise SourceContractError("action-plan-invalid") from None
    if not (
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and _is_uuid4(value["work_id"])
        and _is_token(value["provider"])
        and _is_token(value["action"])
        and _is_digest(value["input_sha256"])
        and type(value["request_count"]) is int
        and value["request_count"] == 1
        and type(value["network_required"]) is bool
        and type(value["fee_possible"]) is bool
        and type(value["no_retry"]) is bool
        and type(value["no_fallback"]) is bool
        and value["no_retry"] is True
        and value["no_fallback"] is True
    ):
        raise SourceContractError("action-plan-invalid")
    return ActionPlan(**value)  # type: ignore[arg-type]


def _load_approval(path: Path) -> ApprovalReceipt:
    try:
        value = load_exact_json(path, _APPROVAL_KEYS)
    except SourceContractError:
        raise SourceContractError("approval-invalid") from None
    if not (
        type(value["approved"]) is bool
        and _is_digest(value["plan_sha256"])
        and _is_uuid4(value["work_id"])
        and _is_token(value["provider"])
        and _is_token(value["action"])
        and _is_digest(value["input_sha256"])
        and type(value["request_count"]) is int
        and value["request_count"] > 0
        and type(value["no_retry"]) is bool
        and type(value["no_fallback"]) is bool
    ):
        raise SourceContractError("approval-invalid")
    return ApprovalReceipt(**value)  # type: ignore[arg-type]


def verify_approval(plan: ActionPlan, receipt_path: Path) -> ApprovalReceipt:
    """Require one receipt to approve exactly one immutable action plan."""

    if not isinstance(plan, ActionPlan):
        raise SourceContractError("action-plan-invalid")
    return verify_approval_payload(
        _action_plan_dict(plan), receipt_path, request_count=plan.request_count
    )


def verify_approval_payload(
    plan_value: Mapping[str, object],
    receipt_path: Path,
    *,
    request_count: int,
) -> ApprovalReceipt:
    """Require one receipt to bind the common scope of an exact plan mapping."""

    if (
        not isinstance(plan_value, Mapping)
        or type(request_count) is not int
        or request_count <= 0
    ):
        raise SourceContractError("action-plan-invalid")
    required = {
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "no_retry",
        "no_fallback",
    }
    if not required.issubset(plan_value):
        raise SourceContractError("action-plan-invalid")
    receipt = _load_approval(receipt_path)
    plan_sha256 = hashlib.sha256(canonical_json_bytes(plan_value)).hexdigest()
    if receipt.plan_sha256 != plan_sha256:
        raise SourceContractError("approval-plan-mismatch")
    if not (
        receipt.approved is True
        and receipt.work_id == plan_value["work_id"]
        and receipt.provider == plan_value["provider"]
        and receipt.action == plan_value["action"]
        and receipt.input_sha256 == plan_value["input_sha256"]
        and receipt.request_count == request_count
        and plan_value["no_retry"] is True
        and plan_value["no_fallback"] is True
        and receipt.no_retry is True
        and receipt.no_fallback is True
    ):
        raise SourceContractError("approval-scope-mismatch")
    return receipt


__all__ = [
    "ActionPlan",
    "ApprovalReceipt",
    "ArtifactRecord",
    "SourceContractError",
    "SourceKind",
    "SourceStage",
    "SourceWorkOrder",
    "canonical_json_bytes",
    "load_action_plan",
    "load_exact_json",
    "new_work_id",
    "publish_json_exclusive",
    "sha256_file",
    "verify_approval",
    "verify_approval_payload",
    "verify_private_relative",
]

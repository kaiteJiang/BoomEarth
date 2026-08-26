"""Authorized, private source work-order intake."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.source_artifacts import (
    SourceContractError,
    SourceWorkOrder,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_private_relative,
)
from boomearth.workbench.source_ledger import StageEvent, WashEventLedger


_INTAKE_KEYS = frozenset(
    {
        "authorized",
        "created_at",
        "schema_version",
        "source_input_sha256",
        "source_kind",
        "source_locator",
        "stage",
        "work_id",
    }
)
_MAX_URL_BYTES = 16 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_X_ARTICLE_URL_RE = re.compile(
    r"https://x\.com/[A-Za-z0-9_]{1,15}/status/[0-9]+"
)
_GITHUB_SKILL_URL_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/"
    r"[A-Za-z0-9_.-]{1,100}(?:/(?:tree|blob)/[^?#]+)?"
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _work_id(factory: Callable[[], UUID]) -> str:
    try:
        value = factory()
    except Exception:
        raise SourceContractError("work-id-invalid") from None
    if not isinstance(value, UUID) or value.version != 4:
        raise SourceContractError("work-id-invalid")
    return str(value)


def _timestamp(factory: Callable[[], datetime]) -> str:
    try:
        value = factory()
    except Exception:
        raise SourceContractError("source-clock-invalid") from None
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise SourceContractError("source-clock-invalid")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        int(getattr(value, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _regular_file(path: Path) -> Path:
    try:
        source = Path(os.path.abspath(os.fspath(path)))
        value = source.lstat()
        if not stat.S_ISREG(value.st_mode) or _is_reparse(value) or value.st_size <= 0:
            raise SourceContractError("source-input-invalid")
        sha256_file(source)
        return source
    except SourceContractError:
        raise SourceContractError("source-input-invalid") from None
    except (OSError, TypeError, ValueError):
        raise SourceContractError("source-input-invalid") from None


def _contained(parent: Path, child: Path) -> bool:
    try:
        return os.path.commonpath((str(parent), str(child))) == str(parent)
    except (OSError, ValueError):
        return False


def _reject_public_source(root: Path, source: Path) -> None:
    paths = WorkbenchPaths(root)
    absolute = Path(os.path.abspath(os.fspath(source)))
    public_roots = tuple(
        Path(os.path.abspath(os.fspath(item)))
        for item in (paths.pending, paths.active, paths.archived)
    )
    if any(_contained(public, absolute) for public in public_roots):
        raise SourceContractError("source-input-invalid")


def _url_bytes(path: Path) -> bytes:
    source = _regular_file(path)
    try:
        payload = source.read_bytes()
        if len(payload) > _MAX_URL_BYTES or b"\0" in payload:
            raise SourceContractError("source-input-invalid")
        text = payload.decode("utf-8")
        lines = text.splitlines()
        if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
            raise SourceContractError("source-input-invalid")
        parsed = urlsplit(lines[0])
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise SourceContractError("source-input-invalid")
        return payload
    except SourceContractError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise SourceContractError("source-input-invalid") from None


def _x_article_url_bytes(path: Path) -> bytes:
    payload = _url_bytes(path)
    try:
        value = payload.decode("utf-8").splitlines()[0]
    except (UnicodeError, IndexError):
        raise SourceContractError("source-input-invalid") from None
    if _X_ARTICLE_URL_RE.fullmatch(value) is None:
        raise SourceContractError("source-input-invalid")
    return payload


def _github_skill_url_bytes(path: Path) -> bytes:
    payload = _url_bytes(path)
    try:
        value = payload.decode("utf-8").splitlines()[0]
    except (UnicodeError, IndexError):
        raise SourceContractError("source-input-invalid") from None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or _GITHUB_SKILL_URL_RE.fullmatch(value) is None
    ):
        raise SourceContractError("source-input-invalid")
    return payload


def _write_bytes_exclusive(root: Path, relative: str, payload: bytes) -> Path:
    target = verify_private_relative(root, relative)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise SourceContractError("artifact-exists") from None
    except OSError:
        raise SourceContractError("artifact-unavailable") from None
    return target


def _remove_created_work(private_root: Path, created: bool) -> None:
    if not created:
        return
    try:
        if private_root.is_dir() and not private_root.is_symlink():
            shutil.rmtree(private_root)
    except OSError:
        pass


def _create_intake(
    root: Path,
    *,
    source_kind: str,
    source_locator: str,
    source_input_sha256: str,
    authorized: bool,
    created_at: str,
    work_id: str,
    url_payload: bytes | None,
) -> SourceWorkOrder:
    if authorized is not True:
        raise SourceContractError("source-authorization-required")
    try:
        paths = WorkbenchPaths(root)
        private_root = paths.private_source(work_id)
    except (OSError, TypeError, ValueError):
        raise SourceContractError("source-workspace-invalid") from None
    created = False
    try:
        paths.private_wash.mkdir(parents=True, exist_ok=True)
        private_root.mkdir(parents=False, exist_ok=False)
        created = True
        if url_payload is not None:
            _write_bytes_exclusive(private_root, "source-input.txt", url_payload)
        intake = {
            "authorized": True,
            "created_at": created_at,
            "schema_version": 1,
            "source_input_sha256": source_input_sha256,
            "source_kind": source_kind,
            "source_locator": source_locator,
            "stage": "source_registered",
            "work_id": work_id,
        }
        intake_sha256 = publish_json_exclusive(
            private_root,
            private_root / "intake.json",
            intake,
        )
        WashEventLedger(root).append(
            StageEvent(
                schema_version=2,
                work_id=work_id,
                source_id=source_input_sha256,
                source_kind=source_kind,
                stage="source_registered",
                result="ok",
                artifact_label="intake-manifest",
                artifact_sha256=intake_sha256,
                timestamp=created_at,
                previous_event_sha256=None,
            )
        )
        return SourceWorkOrder(
            work_id=work_id,
            source_kind=source_kind,  # type: ignore[arg-type]
            authorized=True,
            created_at=created_at,
            stage="source_registered",
            source_input_sha256=source_input_sha256,
            private_root=private_root,
        )
    except FileExistsError:
        raise SourceContractError("work-order-exists") from None
    except SourceContractError:
        _remove_created_work(private_root, created)
        raise
    except OSError:
        _remove_created_work(private_root, created)
        raise SourceContractError("work-order-unavailable") from None


def create_local_intake(
    root: Path,
    source: Path,
    *,
    authorized: bool,
    now: Callable[[], datetime] = _utc_now,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> SourceWorkOrder:
    if authorized is not True:
        raise SourceContractError("source-authorization-required")
    local_source = _regular_file(source)
    _reject_public_source(Path(root), local_source)
    source_digest = sha256_file(local_source)
    return _create_intake(
        Path(root),
        source_kind="local",
        source_locator=str(local_source),
        source_input_sha256=source_digest,
        authorized=True,
        created_at=_timestamp(now),
        work_id=_work_id(uuid_factory),
        url_payload=None,
    )


def create_url_intake(
    root: Path,
    input_file: Path,
    *,
    authorized: bool,
    now: Callable[[], datetime] = _utc_now,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> SourceWorkOrder:
    if authorized is not True:
        raise SourceContractError("source-authorization-required")
    payload = _url_bytes(input_file)
    source_digest = hashlib.sha256(payload).hexdigest()
    return _create_intake(
        Path(root),
        source_kind="url",
        source_locator="source-input.txt",
        source_input_sha256=source_digest,
        authorized=True,
        created_at=_timestamp(now),
        work_id=_work_id(uuid_factory),
        url_payload=payload,
    )


def create_x_article_intake(
    root: Path,
    input_file: Path,
    *,
    authorized: bool,
    now: Callable[[], datetime] = _utc_now,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> SourceWorkOrder:
    if authorized is not True:
        raise SourceContractError("source-authorization-required")
    payload = _x_article_url_bytes(input_file)
    source_digest = hashlib.sha256(payload).hexdigest()
    return _create_intake(
        Path(root),
        source_kind="x-article",
        source_locator="source-input.txt",
        source_input_sha256=source_digest,
        authorized=True,
        created_at=_timestamp(now),
        work_id=_work_id(uuid_factory),
        url_payload=payload,
    )


def create_github_skill_intake(
    root: Path,
    input_file: Path,
    *,
    authorized: bool,
    now: Callable[[], datetime] = _utc_now,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> SourceWorkOrder:
    if authorized is not True:
        raise SourceContractError("source-authorization-required")
    payload = _github_skill_url_bytes(input_file)
    source_digest = hashlib.sha256(payload).hexdigest()
    return _create_intake(
        Path(root),
        source_kind="github-skill",
        source_locator="source-input.txt",
        source_input_sha256=source_digest,
        authorized=True,
        created_at=_timestamp(now),
        work_id=_work_id(uuid_factory),
        url_payload=payload,
    )


def load_work_order(root: Path, work_id: str) -> SourceWorkOrder:
    try:
        parsed = UUID(work_id)
        if parsed.version != 4 or str(parsed) != work_id:
            raise ValueError
        private_root = WorkbenchPaths(root).private_source(work_id)
        value = load_exact_json(private_root / "intake.json", _INTAKE_KEYS)
        if not (
            value["schema_version"] == 1
            and value["work_id"] == work_id
            and value["source_kind"] in {"local", "url", "x-article", "github-skill"}
            and value["authorized"] is True
            and value["stage"] == "source_registered"
            and _valid_timestamp(value["created_at"])
            and _valid_digest(value["source_input_sha256"])
            and isinstance(value["source_locator"], str)
            and bool(value["source_locator"])
        ):
            raise ValueError
        if value["source_kind"] in {"url", "x-article", "github-skill"}:
            if value["source_locator"] != "source-input.txt":
                raise ValueError
            copied = verify_private_relative(private_root, "source-input.txt")
            if sha256_file(copied) != value["source_input_sha256"]:
                raise ValueError
        elif not Path(value["source_locator"]).is_absolute():
            raise ValueError
        return SourceWorkOrder(
            work_id=work_id,
            source_kind=value["source_kind"],  # type: ignore[arg-type]
            authorized=True,
            created_at=value["created_at"],  # type: ignore[arg-type]
            stage="source_registered",
            source_input_sha256=value["source_input_sha256"],  # type: ignore[arg-type]
            private_root=private_root,
        )
    except (SourceContractError, OSError, TypeError, ValueError):
        raise SourceContractError("work-order-invalid") from None


def load_source_locator(root: Path, work_id: str) -> Path:
    """Return the private local-source locator without exposing it in reprs."""

    order = load_work_order(root, work_id)
    if order.source_kind != "local":
        raise SourceContractError("source-kind-invalid")
    try:
        value = load_exact_json(order.private_root / "intake.json", _INTAKE_KEYS)
        locator = value["source_locator"]
        if not isinstance(locator, str) or not locator:
            raise ValueError
        path = Path(locator)
        if not path.is_absolute():
            raise ValueError
        return path
    except (SourceContractError, TypeError, ValueError):
        raise SourceContractError("work-order-invalid") from None


__all__ = [
    "create_github_skill_intake",
    "create_local_intake",
    "create_url_intake",
    "create_x_article_intake",
    "load_source_locator",
    "load_work_order",
]

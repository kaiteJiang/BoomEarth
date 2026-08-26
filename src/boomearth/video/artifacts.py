"""Stable, no-clobber artifact I/O for local content production."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_REPARSE_POINT = 0x0400
_CHUNK_SIZE = 1024 * 1024


class ArtifactError(RuntimeError):
    """Raised with fixed, redacted messages for formal artifact failures."""


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str
    payload: bytes


def _absolute(path: Path) -> Path:
    return Path(path).absolute()


def _is_reparse(stat_result: os.stat_result) -> bool:
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        int(getattr(stat_result, "st_file_attributes", 0)) & _REPARSE_POINT
    )


def _identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(stat_result.st_dev),
        int(stat_result.st_ino),
        int(stat_result.st_size),
        int(stat_result.st_mtime_ns),
    )


def _is_contained(root: Path, candidate: Path) -> bool:
    root = _absolute(root)
    candidate = _absolute(candidate)
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate != root


def _safe_existing_ancestors(path: Path, *, stop: Path | None = None) -> bool:
    current = _absolute(path)
    boundary = _absolute(stop) if stop is not None else None
    while True:
        try:
            entry = current.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            return False
        else:
            if _is_reparse(entry):
                return False
        if boundary is not None and current == boundary:
            return True
        if current == current.parent:
            return boundary is None
        current = current.parent


def _same_identity(*entries: os.stat_result) -> bool:
    return bool(entries) and all(
        _identity(entry) == _identity(entries[0]) for entry in entries[1:]
    )


def _same_snapshot_identity(left: FileSnapshot, right: FileSnapshot) -> bool:
    return (
        left.device,
        left.inode,
        left.size,
        left.mtime_ns,
        left.sha256,
    ) == (
        right.device,
        right.inode,
        right.size,
        right.mtime_ns,
        right.sha256,
    )


def capture_regular_file(
    path: Path,
    *,
    within: Path | None = None,
) -> FileSnapshot:
    """Read and hash exactly one stable ordinary file without resolving links."""

    source = _absolute(path)
    boundary = _absolute(within) if within is not None else None
    if boundary is not None and (
        not _is_contained(boundary, source)
        or not _safe_existing_ancestors(boundary)
    ):
        raise ArtifactError("artifact source is unavailable")
    if not _safe_existing_ancestors(source, stop=boundary):
        raise ArtifactError("artifact source is unavailable")
    try:
        path_before = source.lstat()
        if _is_reparse(path_before) or not stat.S_ISREG(path_before.st_mode):
            raise ArtifactError("artifact source is unavailable")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        with source.open("rb") as handle:
            handle_before = os.fstat(handle.fileno())
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                if not chunk:
                    break
                chunks.append(chunk)
                digest.update(chunk)
            handle_after = os.fstat(handle.fileno())
        path_after = source.lstat()
    except ArtifactError:
        raise
    except OSError:
        raise ArtifactError("artifact source is unavailable") from None
    payload = b"".join(chunks)
    if (
        _is_reparse(path_after)
        or not stat.S_ISREG(path_after.st_mode)
        or not _same_identity(
            path_before,
            handle_before,
            handle_after,
            path_after,
        )
        or len(payload) != path_before.st_size
        or not _safe_existing_ancestors(source, stop=boundary)
    ):
        raise ArtifactError("artifact source changed during capture")
    device, inode, size, mtime_ns = _identity(path_before)
    return FileSnapshot(
        path=source,
        device=device,
        inode=inode,
        size=size,
        mtime_ns=mtime_ns,
        sha256=digest.hexdigest(),
        payload=payload,
    )


def snapshot_matches(snapshot: FileSnapshot) -> bool:
    """Return whether the same ordinary path still has the captured identity and bytes."""

    try:
        current = capture_regular_file(snapshot.path)
    except ArtifactError:
        return False
    return (
        current.device,
        current.inode,
        current.size,
        current.mtime_ns,
        current.sha256,
    ) == (
        snapshot.device,
        snapshot.inode,
        snapshot.size,
        snapshot.mtime_ns,
        snapshot.sha256,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError("artifact JSON is invalid")
        result[key] = value
    return result


def load_json_snapshot(
    path: Path,
    *,
    within: Path | None = None,
) -> tuple[object, FileSnapshot]:
    """Parse strict UTF-8 JSON from the same bytes represented by its snapshot."""

    snapshot = capture_regular_file(path, within=within)
    try:
        value = json.loads(
            snapshot.payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ArtifactError("artifact JSON is invalid")
            ),
        )
    except ArtifactError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ArtifactError("artifact JSON is invalid") from None
    return value, snapshot


def encode_canonical_json(value: object) -> bytes:
    """Serialize one strict deterministic JSON artifact."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError):
        raise ArtifactError("artifact JSON is invalid") from None
    return (encoded + "\n").encode("utf-8")


def _prepare_target(destination: Path, boundary: Path) -> tuple[Path, Path]:
    target = _absolute(destination)
    root = _absolute(boundary)
    if (
        not _is_contained(root, target)
        or not _safe_existing_ancestors(root)
        or not root.is_dir()
    ):
        raise ArtifactError("artifact target is unavailable")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        parent_entry = target.parent.lstat()
    except OSError:
        raise ArtifactError("artifact target is unavailable") from None
    if (
        _is_reparse(parent_entry)
        or not stat.S_ISDIR(parent_entry.st_mode)
        or not _safe_existing_ancestors(target.parent, stop=root)
        or target.exists()
    ):
        raise ArtifactError("artifact target is unavailable")
    try:
        target.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        raise ArtifactError("artifact target is unavailable") from None
    else:
        raise ArtifactError("artifact target is unavailable")
    return target, root


def publish_bytes_no_clobber(
    destination: Path,
    payload: bytes,
    *,
    within: Path,
) -> FileSnapshot:
    """Atomically publish bytes once without replacing an existing formal artifact."""

    if not isinstance(payload, bytes):
        raise ArtifactError("artifact target is unavailable")
    target, root = _prepare_target(destination, within)
    temporary = target.parent / f".{target.name}-{uuid.uuid4().hex}.tmp"
    published = False
    completed = False
    staged: FileSnapshot | None = None
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        staged = capture_regular_file(temporary, within=root)
        if staged.payload != payload or target.exists():
            raise ArtifactError("artifact target is unavailable")
        os.link(temporary, target)
        published = True
        temporary.unlink()
        result = capture_regular_file(target, within=root)
        if not _same_snapshot_identity(result, staged):
            raise ArtifactError("artifact target is unavailable")
        completed = True
        return result
    except ArtifactError:
        raise
    except OSError:
        raise ArtifactError("artifact target is unavailable") from None
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
        if published and not completed and staged is not None and target.exists():
            try:
                current = capture_regular_file(target, within=root)
            except ArtifactError:
                current = None
            if current is not None and _same_snapshot_identity(current, staged):
                try:
                    target.unlink()
                except OSError:
                    pass

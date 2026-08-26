"""Redacted recovery and single-use retrieval for approved HeyGen jobs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import TextIO
from urllib.parse import urlsplit


_CHUNK_BYTES = 1024 * 1024
MAX_PRIVATE_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
_RECORDED_MEDIA_FACTS = frozenset(
    {"container", "duration_seconds", "width", "height", "codec", "codec_type"}
)


class HeyGenRecoveryError(RuntimeError):
    """A state contradiction expressed as a fixed, redacted category."""

    def __repr__(self) -> str:
        return "HeyGenRecoveryError(<redacted>)"


class PrivateVideoDownloadError(RuntimeError):
    """A private-download failure expressed as a fixed, redacted category."""

    def __repr__(self) -> str:
        return "PrivateVideoDownloadError(<redacted>)"


@dataclass(frozen=True, slots=True)
class HeyGenRecoveryState:
    generation_approved: bool
    job_created: bool
    job_completed: bool
    retrieval_approved: bool
    expected_master_sha256: str | None = None
    status_read_approved: bool = False


def _recovery_invalid() -> HeyGenRecoveryError:
    return HeyGenRecoveryError("recovery-evidence-invalid")


def _valid_sha256(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def next_heygen_action(state: HeyGenRecoveryState, master_path: Path) -> str:
    """Return the only safe next action without contacting the provider."""
    if not isinstance(state, HeyGenRecoveryState) or any(
        type(value) is not bool
        for value in (
            state.generation_approved,
            state.job_created,
            state.job_completed,
            state.retrieval_approved,
            state.status_read_approved,
        )
    ):
        raise _recovery_invalid()
    if state.expected_master_sha256 is not None and not _valid_sha256(
        state.expected_master_sha256
    ):
        raise _recovery_invalid()
    if (
        (state.job_created and not state.generation_approved)
        or (state.job_completed and not state.job_created)
        or (state.retrieval_approved and not state.job_completed)
        or (state.status_read_approved and not state.job_created)
    ):
        raise _recovery_invalid()

    master = Path(master_path)
    if master.exists() and state.expected_master_sha256 is not None:
        if not master.is_file() or _sha256(master) != state.expected_master_sha256:
            raise _recovery_invalid()
        return "compose-locally"

    if not state.generation_approved:
        return "request-generation-approval"
    if not state.job_created:
        return "generate-once"
    if not state.job_completed:
        if not state.status_read_approved:
            return "request-status-read-approval"
        return "read-status"
    if not state.retrieval_approved:
        return "request-retrieval-approval"
    return "download-completed-job"


def _read_signed_https_url(url_stream: TextIO) -> str:
    try:
        lines = url_stream.read().splitlines()
    except Exception:
        raise PrivateVideoDownloadError("download-url-rejected") from None
    if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
        raise PrivateVideoDownloadError("download-url-rejected")
    value = lines[0]
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise PrivateVideoDownloadError("download-url-rejected") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.query
    ):
        raise PrivateVideoDownloadError("download-url-rejected")
    return value


def _prepare_target(path: Path, category: str) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() or path.is_symlink():
            raise PrivateVideoDownloadError(category)
        return path
    except PrivateVideoDownloadError:
        raise
    except (OSError, TypeError, ValueError):
        raise PrivateVideoDownloadError("download-target-rejected") from None


def _canonical_target(path: Path) -> Path:
    try:
        return Path(path).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        raise PrivateVideoDownloadError("download-target-rejected") from None


def _normalise_media_facts(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise PrivateVideoDownloadError("download-probe-failed")
    facts = {
        key: value[key]
        for key in _RECORDED_MEDIA_FACTS
        if key in value
        and isinstance(value[key], (str, int, float, bool))
        and not isinstance(value[key], bytes)
    }
    if (
        facts.get("codec_type") != "video"
        or type(facts.get("width")) is not int
        or int(facts["width"]) <= 0
        or type(facts.get("height")) is not int
        or int(facts["height"]) <= 0
    ):
        raise PrivateVideoDownloadError("download-probe-failed")
    return facts


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        value = path.lstat()
    except OSError:
        return None
    return (value.st_dev, value.st_ino)


def _publish_no_replace(source: Path, target: Path, exists_category: str) -> None:
    try:
        os.link(source, target)
    except FileExistsError:
        raise PrivateVideoDownloadError(exists_category) from None
    except OSError:
        raise PrivateVideoDownloadError("download-publish-failed") from None


def _open_source_lock(path: Path, *, delete_access: bool = False) -> int | None:
    """On Windows, deny source writes/deletes while still allowing probe reads."""

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
        0x80000000 | (0x00010000 if delete_access else 0),
        0x00000001,
        None,
        3,
        0x00200000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise PrivateVideoDownloadError("download-publish-failed")
    return int(handle)


def _close_source_lock(handle: int | None) -> bool:
    if handle is None:
        return True
    try:
        close_handle = ctypes.windll.kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        return bool(close_handle(ctypes.c_void_p(handle)))
    except (AttributeError, OSError):
        return False


@dataclass(frozen=True, slots=True)
class _LockedPath:
    source: Path
    identity: tuple[int, int]
    handle: int | None


@dataclass(frozen=True, slots=True)
class _LockedSnapshot:
    source: Path
    identity: tuple[int, int]
    sha256: str
    handle: int | None
    delete_capable: bool


def _lock_owned_path(
    source: Path, source_identity: tuple[int, int]
) -> _LockedPath:
    """Lock one path and prove it still names the captured file."""

    source_lock = _open_source_lock(source, delete_access=True)
    try:
        if _file_identity(source) != source_identity:
            raise PrivateVideoDownloadError("download-publish-failed")
    except BaseException:
        _close_source_lock(source_lock)
        raise
    return _LockedPath(source, source_identity, source_lock)


def _sha256_locked_source(path: Path, handle: int | None) -> str:
    if os.name != "nt" or handle is None:
        return _sha256(path)
    try:
        set_pointer = ctypes.windll.kernel32.SetFilePointerEx
        set_pointer.argtypes = (
            ctypes.c_void_p,
            ctypes.c_longlong,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        set_pointer.restype = ctypes.c_int
        if not set_pointer(ctypes.c_void_p(handle), 0, None, 0):
            raise PrivateVideoDownloadError("download-publish-failed")
        read_file = ctypes.windll.kernel32.ReadFile
        read_file.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        )
        read_file.restype = ctypes.c_int
        digest = hashlib.sha256()
        buffer = ctypes.create_string_buffer(_CHUNK_BYTES)
        bytes_read = ctypes.c_uint32()
        while True:
            if not read_file(
                ctypes.c_void_p(handle),
                ctypes.byref(buffer),
                len(buffer),
                ctypes.byref(bytes_read),
                None,
            ):
                raise PrivateVideoDownloadError("download-publish-failed")
            if bytes_read.value == 0:
                return digest.hexdigest()
            digest.update(buffer.raw[: bytes_read.value])
    except PrivateVideoDownloadError:
        raise
    except (AttributeError, OSError, ValueError):
        raise PrivateVideoDownloadError("download-publish-failed") from None


def _lock_verified_snapshot(
    source: Path,
    source_identity: tuple[int, int],
    source_sha256: str,
    *,
    delete_access: bool = False,
) -> _LockedSnapshot:
    """Bind a source lock to an already captured identity and digest."""

    source_lock = _open_source_lock(source, delete_access=delete_access)
    try:
        if (
            _file_identity(source) != source_identity
            or _sha256_locked_source(source, source_lock) != source_sha256
        ):
            raise PrivateVideoDownloadError("download-publish-failed")
    except BaseException:
        _close_source_lock(source_lock)
        raise
    return _LockedSnapshot(
        source, source_identity, source_sha256, source_lock, delete_access
    )


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = (("delete_file", ctypes.c_int),)


def _dispose_locked_path(locked_path: _LockedPath | _LockedSnapshot) -> bool:
    """Delete the exact opened temp link, never a later pathname occupant."""

    if os.name != "nt":
        _close_source_lock(locked_path.handle)
        return True
    if locked_path.handle is None:
        return False
    marked_for_deletion = False
    try:
        set_file_information = ctypes.windll.kernel32.SetFileInformationByHandle
        set_file_information.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        set_file_information.restype = ctypes.c_int
        disposition = _FileDispositionInfo(1)
        marked_for_deletion = bool(
            set_file_information(
                ctypes.c_void_p(locked_path.handle),
                4,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            )
        )
    except (AttributeError, OSError, ValueError):
        marked_for_deletion = False
    closed = _close_source_lock(locked_path.handle)
    return marked_for_deletion and closed


def _publish_locked_no_replace(
    snapshot: _LockedSnapshot,
    target: Path,
    exists_category: str,
) -> None:
    """Publish one locked snapshot and verify the resulting hard link."""

    if (
        (
            os.name == "nt"
            and (snapshot.handle is None or not snapshot.delete_capable)
        )
        or _file_identity(snapshot.source) != snapshot.identity
        or _sha256_locked_source(snapshot.source, snapshot.handle) != snapshot.sha256
    ):
        raise PrivateVideoDownloadError("download-publish-failed")
    _publish_no_replace(snapshot.source, target, exists_category)
    if (
        _file_identity(snapshot.source) != snapshot.identity
        or _file_identity(target) != snapshot.identity
        or _sha256_locked_source(snapshot.source, snapshot.handle) != snapshot.sha256
    ):
        raise PrivateVideoDownloadError("download-publish-failed")


def _close(response: object) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def download_private_video(
    url_stream: TextIO,
    output: Path,
    receipt: Path,
    *,
    opener: Callable[[str], object],
    probe: Callable[[Path], Mapping[str, object]],
) -> Path:
    """Download one approved HTTPS URL without retaining private URL evidence."""
    target = _prepare_target(_canonical_target(output), "download-target-exists")
    receipt_path = _prepare_target(
        _canonical_target(receipt), "download-receipt-exists"
    )
    if target == receipt_path:
        raise PrivateVideoDownloadError("download-target-rejected")
    url = _read_signed_https_url(url_stream)
    try:
        response = opener(url)
    except Exception:
        raise PrivateVideoDownloadError("download-request-failed") from None

    temporary: Path | None = None
    receipt_temporary: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    receipt_temporary_identity: tuple[int, int] | None = None
    source_snapshot: _LockedSnapshot | None = None
    receipt_snapshot: _LockedSnapshot | None = None
    completed = False
    descriptor = -1
    try:
        try:
            reader = getattr(response, "read")
        except AttributeError:
            raise PrivateVideoDownloadError("download-stream-failed") from None
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        temporary_identity = _file_identity(temporary)
        if temporary_identity is None:
            raise PrivateVideoDownloadError("download-stream-failed")
        digest = hashlib.sha256()
        size_bytes = 0
        with os.fdopen(descriptor, "wb") as sink:
            descriptor = -1
            while True:
                try:
                    block = reader(_CHUNK_BYTES)
                except Exception:
                    raise PrivateVideoDownloadError("download-stream-failed") from None
                if not block:
                    break
                if isinstance(block, str):
                    block = block.encode("utf-8")
                if not isinstance(block, bytes):
                    raise PrivateVideoDownloadError("download-stream-failed")
                size_bytes += len(block)
                if size_bytes > MAX_PRIVATE_VIDEO_BYTES:
                    raise PrivateVideoDownloadError("download-size-exceeded")
                digest.update(block)
                sink.write(block)
            sink.flush()
            os.fsync(sink.fileno())
        if _file_identity(temporary) != temporary_identity or size_bytes <= 0:
            raise PrivateVideoDownloadError("download-stream-failed")
        downloaded_sha256 = digest.hexdigest()
        source_snapshot = _lock_verified_snapshot(
            temporary, temporary_identity, downloaded_sha256
        )
        try:
            media = _normalise_media_facts(probe(temporary))
        except PrivateVideoDownloadError:
            raise
        except Exception:
            raise PrivateVideoDownloadError("download-probe-failed") from None
        document = {
            "status": "downloaded",
            "download_count": 1,
            "sha256": downloaded_sha256,
            "size_bytes": size_bytes,
            "media": media,
        }
        try:
            receipt_payload = json.dumps(
                document, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise PrivateVideoDownloadError("download-probe-failed") from None
        receipt_descriptor, receipt_name = tempfile.mkstemp(
            prefix=f".{receipt_path.stem}.", suffix=".tmp", dir=receipt_path.parent
        )
        receipt_temporary = Path(receipt_name)
        with os.fdopen(receipt_descriptor, "wb") as sink:
            sink.write(receipt_payload)
            sink.flush()
            os.fsync(sink.fileno())
        receipt_temporary_identity = _file_identity(receipt_temporary)
        if receipt_temporary_identity is None:
            raise PrivateVideoDownloadError("download-publish-failed")
        receipt_sha256 = hashlib.sha256(receipt_payload).hexdigest()
        receipt_snapshot = _lock_verified_snapshot(
            receipt_temporary,
            receipt_temporary_identity,
            receipt_sha256,
            delete_access=True,
        )
        if source_snapshot is None or not _close_source_lock(source_snapshot.handle):
            raise PrivateVideoDownloadError("download-publish-failed")
        source_snapshot = None
        source_snapshot = _lock_verified_snapshot(
            temporary,
            temporary_identity,
            downloaded_sha256,
            delete_access=True,
        )
        _publish_locked_no_replace(
            source_snapshot, target, "download-target-exists"
        )
        _publish_locked_no_replace(
            receipt_snapshot,
            receipt_path,
            "download-receipt-exists",
        )
        completed = True
        return target
    except PrivateVideoDownloadError:
        raise
    except OSError:
        raise PrivateVideoDownloadError("download-publish-failed") from None
    finally:
        _close(response)
        if descriptor != -1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        cleanup_failed = False
        source_cleanup: _LockedPath | _LockedSnapshot | None = source_snapshot
        receipt_cleanup: _LockedPath | _LockedSnapshot | None = receipt_snapshot
        if (
            source_cleanup is None
            and temporary is not None
            and temporary_identity is not None
        ):
            try:
                source_cleanup = _lock_owned_path(temporary, temporary_identity)
            except (OSError, PrivateVideoDownloadError):
                source_cleanup = None
        if (
            receipt_cleanup is None
            and receipt_temporary is not None
            and receipt_temporary_identity is not None
        ):
            try:
                receipt_cleanup = _lock_owned_path(
                    receipt_temporary, receipt_temporary_identity
                )
            except (OSError, PrivateVideoDownloadError):
                receipt_cleanup = None
        if receipt_cleanup is not None and not _dispose_locked_path(receipt_cleanup):
            cleanup_failed = True
        if source_cleanup is not None and not _dispose_locked_path(source_cleanup):
            cleanup_failed = True
        if completed and (source_cleanup is None or receipt_cleanup is None):
            cleanup_failed = True
        if completed and cleanup_failed:
            raise PrivateVideoDownloadError("download-publish-failed")


__all__ = [
    "HeyGenRecoveryError",
    "HeyGenRecoveryState",
    "MAX_PRIVATE_VIDEO_BYTES",
    "PrivateVideoDownloadError",
    "download_private_video",
    "next_heygen_action",
]

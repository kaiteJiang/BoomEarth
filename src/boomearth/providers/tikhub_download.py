"""Bounded credential-free download of one TikHub-resolved private media URL."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    verify_private_relative,
)


MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TikHubDownloadError(RuntimeError):
    """A fixed-message private media download failure."""

    def __repr__(self) -> str:
        return "TikHubDownloadError(<redacted>)"


def _safe_url(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise TikHubDownloadError("media-download-rejected")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise TikHubDownloadError("media-download-rejected") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise TikHubDownloadError("media-download-rejected")
    return value


def _target_path(target: Path) -> Path:
    try:
        parent = Path(target).parent
        formal = verify_private_relative(parent, Path(target).name)
        if formal.exists() or formal.is_symlink():
            raise TikHubDownloadError("media-download-exists")
        parent.mkdir(parents=True, exist_ok=True)
        formal = verify_private_relative(parent, Path(target).name)
        if formal.exists() or formal.is_symlink():
            raise TikHubDownloadError("media-download-exists")
        return formal
    except TikHubDownloadError:
        raise
    except (SourceContractError, OSError, TypeError, ValueError):
        raise TikHubDownloadError("media-download-rejected") from None


class PrivateMediaDownloader:
    """Stream exactly one non-redirected HTTPS body into an immutable file."""

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client

    def download(self, url: str, target: Path) -> ArtifactRecord:
        media_url = _safe_url(url)
        formal = _target_path(Path(target))
        temporary: Path | None = None
        client = self._client or httpx.Client(
            timeout=_TIMEOUT,
            follow_redirects=False,
        )
        owns_client = self._client is None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{formal.stem}.", suffix=".tmp", dir=formal.parent
            )
            temporary = Path(temporary_name)
            request = client.build_request("GET", media_url)
            request.headers["Accept-Encoding"] = "identity"
            for header in ("authorization", "x-api-key", "cookie", "proxy-authorization"):
                request.headers.pop(header, None)
            response = client.send(
                request,
                stream=True,
                follow_redirects=False,
                auth=None,
            )
            try:
                if response.status_code != 200:
                    raise TikHubDownloadError("media-download-rejected")
                raw_length = response.headers.get("Content-Length")
                try:
                    declared_length = int(raw_length) if raw_length is not None else 0
                except ValueError:
                    raise TikHubDownloadError("media-download-rejected") from None
                if declared_length <= 0 or declared_length > MAX_MEDIA_BYTES:
                    raise TikHubDownloadError("media-download-rejected")
                total = 0
                digest = hashlib.sha256()
                with os.fdopen(descriptor, "wb") as sink:
                    descriptor = -1
                    for block in response.iter_bytes(_CHUNK_SIZE):
                        total += len(block)
                        if total > declared_length or total > MAX_MEDIA_BYTES:
                            raise TikHubDownloadError("media-download-too-large")
                        digest.update(block)
                        sink.write(block)
                    if total != declared_length:
                        raise TikHubDownloadError("media-download-failed")
                    sink.flush()
                    os.fsync(sink.fileno())
            finally:
                response.close()
            try:
                os.link(temporary, formal)
            except FileExistsError:
                raise TikHubDownloadError("media-download-exists") from None
            temporary.unlink()
            temporary = None
            return ArtifactRecord(
                relative_path=formal.name,
                sha256=digest.hexdigest(),
                size_bytes=total,
                path=formal,
            )
        except TikHubDownloadError:
            raise
        except (httpx.HTTPError, OSError):
            raise TikHubDownloadError("media-download-failed") from None
        finally:
            if "descriptor" in locals() and descriptor != -1:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            if owns_client:
                client.close()


__all__ = ["MAX_MEDIA_BYTES", "PrivateMediaDownloader", "TikHubDownloadError"]

"""Bounded, credential-free Bilibili CDN media downloading."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import os
from pathlib import Path
import socket
import tempfile
from typing import Callable, Sequence
from urllib.parse import urljoin, urlsplit

import httpx

from boomearth.workbench.bilibili_play_info import BilibiliAsset
from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    verify_private_relative,
)


MAX_MEDIA_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 307, 308})
_SAFE_PORTS = frozenset({443})
_HEADERS = {
    "Accept-Encoding": "identity",
    "User-Agent": "BoomEarth-Bilibili/1",
    "Referer": "https://www.bilibili.com/",
}

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Sequence[IPAddress]]


class BilibiliMediaDownloadError(RuntimeError):
    """A fixed-message download failure without private URL details."""

    def __repr__(self) -> str:
        return "BilibiliMediaDownloadError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DownloadedBilibiliAsset:
    kind: str
    artifact: ArtifactRecord
    http_request_count: int
    redirect_hops: int
    final_host_sha256: str

    def __repr__(self) -> str:
        return "DownloadedBilibiliAsset(<redacted>)"


def safe_media_url(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise BilibiliMediaDownloadError("media-url-rejected")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise BilibiliMediaDownloadError("media-url-rejected") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (port is not None and port not in _SAFE_PORTS)
        or parsed.hostname.lower() == "localhost"
    ):
        raise BilibiliMediaDownloadError("media-url-rejected")
    return value


def resolve_public_addresses(host: str, port: int) -> Sequence[IPAddress]:
    try:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return tuple(ipaddress.ip_address(row[4][0]) for row in rows)
    except (OSError, ValueError):
        raise BilibiliMediaDownloadError("media-host-rejected") from None


def require_public_media_host(url: str, resolver: Resolver) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname
    if host is None:
        raise BilibiliMediaDownloadError("media-host-rejected")
    port = parsed.port or 443
    try:
        addresses = tuple(resolver(host, port))
    except BilibiliMediaDownloadError:
        raise
    except Exception:
        raise BilibiliMediaDownloadError("media-host-rejected") from None
    if not addresses or any(
        not isinstance(address, (ipaddress.IPv4Address, ipaddress.IPv6Address))
        or not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_private
        for address in addresses
    ):
        raise BilibiliMediaDownloadError("media-host-rejected")


def send_one_credential_free_request(
    client: httpx.Client, url: str
) -> httpx.Response:
    request = httpx.Request("GET", url, headers=_HEADERS)
    for header in ("authorization", "x-api-key", "cookie", "proxy-authorization"):
        request.headers.pop(header, None)
    try:
        return client.send(
            request,
            stream=True,
            follow_redirects=False,
            auth=None,
        )
    except httpx.HTTPError:
        raise BilibiliMediaDownloadError("media-request-failed") from None


def redirected_https_url(current: str, response: httpx.Response) -> str:
    location = response.headers.get("Location")
    if not location:
        raise BilibiliMediaDownloadError("media-redirect-rejected")
    return safe_media_url(urljoin(current, location))


def sha256_host(url: str) -> str:
    host = urlsplit(url).hostname
    if host is None:
        raise BilibiliMediaDownloadError("media-url-rejected")
    return hashlib.sha256(host.lower().encode("utf-8")).hexdigest()


def _formal_target(target: Path) -> Path:
    try:
        parent = Path(target).parent
        parent.mkdir(parents=True, exist_ok=True)
        formal = verify_private_relative(parent, Path(target).name)
        if formal.exists() or formal.is_symlink():
            raise BilibiliMediaDownloadError("media-target-exists")
        return formal
    except BilibiliMediaDownloadError:
        raise
    except (SourceContractError, OSError, TypeError, ValueError):
        raise BilibiliMediaDownloadError("media-target-rejected") from None


def publish_media_response(
    response: httpx.Response,
    target: Path,
) -> ArtifactRecord:
    formal = _formal_target(target)
    temporary: Path | None = None
    descriptor = -1
    try:
        if response.status_code != 200:
            raise BilibiliMediaDownloadError("media-response-rejected")
        raw_length = response.headers.get("Content-Length")
        try:
            declared = int(raw_length) if raw_length is not None else 0
        except ValueError:
            raise BilibiliMediaDownloadError("media-response-rejected") from None
        if declared <= 0 or declared > MAX_MEDIA_BYTES:
            raise BilibiliMediaDownloadError("media-response-rejected")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{formal.stem}.", suffix=".tmp", dir=formal.parent
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(descriptor, "wb") as sink:
            descriptor = -1
            try:
                for block in response.iter_bytes(_CHUNK_BYTES):
                    total += len(block)
                    if total > declared or total > MAX_MEDIA_BYTES:
                        raise BilibiliMediaDownloadError("media-body-size-mismatch")
                    digest.update(block)
                    sink.write(block)
            except BilibiliMediaDownloadError:
                raise
            except httpx.HTTPError:
                raise BilibiliMediaDownloadError("media-body-read-failed") from None
            if total != declared:
                raise BilibiliMediaDownloadError("media-body-size-mismatch")
            sink.flush()
            os.fsync(sink.fileno())
        try:
            os.link(temporary, formal)
        except FileExistsError:
            raise BilibiliMediaDownloadError("media-target-exists") from None
        temporary.unlink()
        temporary = None
        return ArtifactRecord(
            relative_path=formal.name,
            sha256=digest.hexdigest(),
            size_bytes=total,
            path=formal,
        )
    except BilibiliMediaDownloadError:
        raise
    except OSError:
        raise BilibiliMediaDownloadError("media-publish-failed") from None
    finally:
        response.close()
        if descriptor != -1:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class BilibiliMediaDownloader:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        resolver: Resolver = resolve_public_addresses,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(900.0, connect=15.0),
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._resolver = resolver

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def download(
        self,
        asset: BilibiliAsset,
        target: Path,
        *,
        max_redirect_hops: int = 3,
    ) -> DownloadedBilibiliAsset:
        current = safe_media_url(asset.private_url)
        formal_target = _formal_target(Path(target))
        if type(max_redirect_hops) is not int or max_redirect_hops < 0:
            raise BilibiliMediaDownloadError("media-redirect-limit")
        http_request_count = 0
        for redirect_hops in range(max_redirect_hops + 1):
            require_public_media_host(current, self._resolver)
            response = send_one_credential_free_request(self._client, current)
            http_request_count += 1
            if response.status_code in _REDIRECT_STATUSES:
                try:
                    if redirect_hops == max_redirect_hops:
                        raise BilibiliMediaDownloadError("media-redirect-limit")
                    current = redirected_https_url(current, response)
                finally:
                    response.close()
                continue
            artifact = publish_media_response(response, formal_target)
            return DownloadedBilibiliAsset(
                kind=asset.kind,
                artifact=artifact,
                http_request_count=http_request_count,
                redirect_hops=redirect_hops,
                final_host_sha256=sha256_host(current),
            )
        raise BilibiliMediaDownloadError("media-redirect-limit")


__all__ = [
    "BilibiliMediaDownloadError",
    "BilibiliMediaDownloader",
    "DownloadedBilibiliAsset",
    "MAX_MEDIA_BYTES",
    "redirected_https_url",
    "require_public_media_host",
    "resolve_public_addresses",
    "safe_media_url",
    "send_one_credential_free_request",
    "sha256_host",
]

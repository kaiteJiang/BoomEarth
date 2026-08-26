from __future__ import annotations

import hashlib
import ipaddress
from pathlib import Path

import httpx
import pytest

import boomearth.providers as providers
from boomearth.providers.bilibili_media_download import (
    MAX_MEDIA_BYTES,
    BilibiliMediaDownloadError,
    BilibiliMediaDownloader,
)
from boomearth.workbench.bilibili_play_info import BilibiliAsset


MEDIA_URL = "https://media.example.invalid/video.m4s"
PUBLIC_IP = ipaddress.ip_address("93.184.216.34")


def _asset(url: str = MEDIA_URL) -> BilibiliAsset:
    return BilibiliAsset(
        kind="video",
        private_url=url,
        mime_type="video/mp4",
        codecs="avc1.640028",
        bandwidth=1000,
        size_bytes=None,
        width=1920,
        height=1080,
        frame_rate="30",
    )


def _resolver(_host: str, _port: int):
    return [PUBLIC_IP]


def test_one_response_streams_to_immutable_target_with_safe_result(
    tmp_path: Path,
) -> None:
    body = b"synthetic-media-bytes"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(body))},
            content=body,
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    downloader = BilibiliMediaDownloader(client=client, resolver=_resolver)
    target = tmp_path / "video.m4s"

    result = downloader.download(_asset(), target)

    assert target.read_bytes() == body
    assert result.kind == "video"
    assert result.artifact.sha256 == hashlib.sha256(body).hexdigest()
    assert result.artifact.size_bytes == len(body)
    assert result.artifact.relative_path == "video.m4s"
    assert result.http_request_count == 1
    assert result.redirect_hops == 0
    assert len(result.final_host_sha256) == 64
    assert seen[0].headers["accept-encoding"] == "identity"
    assert seen[0].headers["user-agent"] == "BoomEarth-Bilibili/1"
    assert seen[0].headers["referer"] == "https://www.bilibili.com/"
    for name in ("authorization", "x-api-key", "cookie", "proxy-authorization"):
        assert name not in seen[0].headers


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_https_redirect_is_followed_manually_once(
    tmp_path: Path, status: int
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(status, headers={"Location": "/final.m4s"})
        return httpx.Response(200, headers={"Content-Length": "2"}, content=b"ok")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = BilibiliMediaDownloader(client=client, resolver=_resolver)

    result = downloader.download(_asset(), tmp_path / "video.m4s")

    assert seen == [MEDIA_URL, "https://media.example.invalid/final.m4s"]
    assert result.http_request_count == 2
    assert result.redirect_hops == 1


def test_fourth_redirect_fails_without_fifth_request(tmp_path: Path) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": f"/hop-{calls}.m4s"})

    downloader = BilibiliMediaDownloader(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_resolver,
    )

    with pytest.raises(BilibiliMediaDownloadError, match="^media-redirect-limit$"):
        downloader.download(_asset(), tmp_path / "video.m4s")

    assert calls == 4
    assert not (tmp_path / "video.m4s").exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.invalid/video.m4s",
        "https://user:pass@media.example.invalid/video.m4s",
        "https://media.example.invalid/video.m4s#fragment",
        "https://media.example.invalid:22/video.m4s",
        "https://localhost/video.m4s",
    ],
)
def test_unsafe_initial_url_fails_before_request(tmp_path: Path, url: str) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"unexpected")

    downloader = BilibiliMediaDownloader(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_resolver,
    )

    with pytest.raises(BilibiliMediaDownloadError):
        downloader.download(_asset(url), tmp_path / "video.m4s")

    assert calls == 0


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "0.0.0.0",
        "::1",
    ],
)
def test_nonpublic_resolution_fails_before_request(
    tmp_path: Path, address: str
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"unexpected")

    downloader = BilibiliMediaDownloader(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda _host, _port: [ipaddress.ip_address(address)],
    )

    with pytest.raises(BilibiliMediaDownloadError, match="^media-host-rejected$"):
        downloader.download(_asset(), tmp_path / "video.m4s")

    assert calls == 0


class _BodyStream(httpx.SyncByteStream):
    def __init__(self, blocks: list[bytes], *, fail: bool = False) -> None:
        self.blocks = blocks
        self.fail = fail

    def __iter__(self):
        yield from self.blocks
        if self.fail:
            raise httpx.ReadError("private body failure")


@pytest.mark.parametrize(
    ("headers", "stream"),
    [
        ({}, _BodyStream([b"a"])),
        ({"Content-Length": "0"}, _BodyStream([])),
        ({"Content-Length": "invalid"}, _BodyStream([b"a"])),
        ({"Content-Length": str(MAX_MEDIA_BYTES + 1)}, _BodyStream([b"a"])),
        ({"Content-Length": "2"}, _BodyStream([b"a"])),
        ({"Content-Length": "1"}, _BodyStream([b"ab"])),
        ({"Content-Length": "2"}, _BodyStream([b"a"], fail=True)),
    ],
)
def test_invalid_or_failed_body_cleans_temporary_files(
    tmp_path: Path,
    headers: dict[str, str],
    stream: httpx.SyncByteStream,
) -> None:
    downloader = BilibiliMediaDownloader(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, headers=headers, stream=stream)
            )
        ),
        resolver=_resolver,
    )
    target = tmp_path / "video.m4s"

    with pytest.raises(BilibiliMediaDownloadError):
        downloader.download(_asset(), target)

    assert not target.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_existing_target_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "video.m4s"
    target.write_bytes(b"existing")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"Content-Length": "2"}, content=b"ok")

    downloader = BilibiliMediaDownloader(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=_resolver,
    )

    with pytest.raises(BilibiliMediaDownloadError, match="^media-target-exists$"):
        downloader.download(_asset(), target)

    assert calls == 0
    assert target.read_bytes() == b"existing"


def test_bilibili_media_downloader_is_exported_publicly() -> None:
    assert providers.BilibiliMediaDownloader is BilibiliMediaDownloader
    assert providers.BilibiliMediaDownloadError is BilibiliMediaDownloadError
    assert providers.BILIBILI_MAX_MEDIA_BYTES == MAX_MEDIA_BYTES

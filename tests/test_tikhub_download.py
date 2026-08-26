from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from boomearth.providers.tikhub_download import (
    MAX_MEDIA_BYTES,
    PrivateMediaDownloader,
    TikHubDownloadError,
)


def test_tikhub_download_streams_one_https_response_without_credentials(
    tmp_path: Path,
) -> None:
    payload = b"synthetic-media"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(payload))},
            content=payload,
        )

    target = tmp_path / "download.mp4"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = PrivateMediaDownloader(client=client).download(
            "https://media.example.invalid/private.mp4", target
        )

    assert len(requests) == 1
    assert "authorization" not in requests[0].headers
    assert "x-api-key" not in requests[0].headers
    assert target.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.size_bytes == len(payload)
    assert result.relative_path == "download.mp4"
    assert "private.mp4" not in repr(result)


def test_tikhub_download_rejects_redirect_without_second_request(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            302,
            headers={"Location": "https://other.example.invalid/next"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        downloader = PrivateMediaDownloader(client=client)
        with pytest.raises(TikHubDownloadError, match="^media-download-rejected$"):
            downloader.download(
                "https://media.example.invalid/private.mp4",
                tmp_path / "download.mp4",
            )

    assert calls == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.invalid/private.mp4",
        "https://user:pass@media.example.invalid/private.mp4",
        "https:///private.mp4",
    ],
)
def test_tikhub_download_rejects_unsafe_url_before_request(
    tmp_path: Path, url: str
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"Content-Length": "1"}, content=b"x")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TikHubDownloadError, match="^media-download-rejected$"):
            PrivateMediaDownloader(client=client).download(
                url, tmp_path / "download.mp4"
            )
    assert calls == 0


@pytest.mark.parametrize("length", [None, "invalid", "0", str(MAX_MEDIA_BYTES + 1)])
def test_tikhub_download_rejects_missing_invalid_or_excessive_length(
    tmp_path: Path, length: str | None
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        if length is None:
            return httpx.Response(200, stream=httpx.ByteStream(b"x"))
        return httpx.Response(
            200, headers={"Content-Length": length}, stream=httpx.ByteStream(b"x")
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TikHubDownloadError, match="^media-download-rejected$"):
            PrivateMediaDownloader(client=client).download(
                "https://media.example.invalid/private.mp4",
                tmp_path / "download.mp4",
            )


def test_tikhub_download_cleans_partial_file_on_stream_failure(tmp_path: Path) -> None:
    class FailingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("private url")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "20"},
            stream=FailingStream(),
        )

    target = tmp_path / "download.mp4"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TikHubDownloadError) as raised:
            PrivateMediaDownloader(client=client).download(
                "https://media.example.invalid/private.mp4", target
            )

    assert str(raised.value) == "media-download-failed"
    assert "private url" not in repr(raised.value)
    assert not target.exists()
    assert not list(tmp_path.glob(".download.*.tmp"))


def test_tikhub_download_never_overwrites_target(tmp_path: Path) -> None:
    target = tmp_path / "download.mp4"
    target.write_bytes(b"foreign")
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"Content-Length": "1"}, content=b"x")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TikHubDownloadError, match="^media-download-exists$"):
            PrivateMediaDownloader(client=client).download(
                "https://media.example.invalid/private.mp4", target
            )
    assert calls == 0
    assert target.read_bytes() == b"foreign"


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadTimeout])
def test_tikhub_download_maps_timeout_without_retry(
    tmp_path: Path, failure: type[httpx.TimeoutException]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise failure("timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TikHubDownloadError, match="^media-download-failed$"):
            PrivateMediaDownloader(client=client).download(
                "https://media.example.invalid/private.mp4",
                tmp_path / "download.mp4",
            )
    assert calls == 1

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import boomearth.providers as providers
from boomearth.providers.tikhub_bilibili import (
    TikHubBilibiliClient,
    TikHubBilibiliCredentialError,
    TikHubBilibiliHTTPError,
    TikHubBilibiliPayloadError,
    TikHubBilibiliTimeoutError,
    TikHubBilibiliTransportError,
)


SYNTHETIC_TOKEN = "synthetic-tikhub-bilibili-token"
SYNTHETIC_URL = "https://www.bilibili.com/video/BV1ab411c7De?t=473.1"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(tikhub_api_key=SYNTHETIC_TOKEN)


def test_fetch_play_info_calls_only_the_fixed_official_endpoint_once() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": "synthetic-request",
                "data": {"kind": "synthetic-play-info"},
            },
        )

    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    result = client.fetch_play_info(SYNTHETIC_URL)
    client.close()

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.scheme == "https"
    assert seen[0].url.host == "api.tikhub.io"
    assert seen[0].url.path == "/api/v1/bilibili/web/fetch_video_play_info"
    assert seen[0].url.params["url"] == SYNTHETIC_URL
    assert seen[0].headers["Authorization"] == f"Bearer {SYNTHETIC_TOKEN}"
    assert result.http_status == 200
    assert result.request_id == "synthetic-request"
    assert result.payload["data"] == {"kind": "synthetic-play-info"}
    assert SYNTHETIC_TOKEN not in repr(result)
    assert SYNTHETIC_URL not in repr(result)


def test_fetch_playurl_uses_only_private_identifiers_on_the_fixed_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "request_id": "synthetic-playurl-request",
                "data": {"kind": "synthetic-playurl"},
            },
        )

    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    result = client.fetch_playurl("BV1ab411c7De", 123456789)
    client.close()

    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.host == "api.tikhub.io"
    assert seen[0].url.path == "/api/v1/bilibili/web/fetch_video_playurl"
    assert dict(seen[0].url.params) == {
        "bv_id": "BV1ab411c7De",
        "cid": "123456789",
    }
    assert result.request_id == "synthetic-playurl-request"
    assert "BV1ab411c7De" not in repr(result)


@pytest.mark.parametrize("credential", [None, "", "   "])
def test_client_rejects_missing_credential_without_request(
    credential: str | None,
) -> None:
    with pytest.raises(TikHubBilibiliCredentialError):
        TikHubBilibiliClient(SimpleNamespace(tikhub_api_key=credential))


@pytest.mark.parametrize("status", [301, 302, 401, 402, 403, 429, 500, 503])
def test_http_rejection_is_redacted_and_never_retried(status: int) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="private source title and response")

    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(TikHubBilibiliHTTPError) as raised:
            client.fetch_play_info(SYNTHETIC_URL)
    finally:
        client.close()

    assert calls == 1
    assert raised.value.status_code == status
    assert "private source title" not in repr(raised.value)
    assert SYNTHETIC_URL not in repr(raised.value)
    assert SYNTHETIC_TOKEN not in repr(raised.value)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectTimeout, TikHubBilibiliTimeoutError),
        (httpx.ReadTimeout, TikHubBilibiliTimeoutError),
        (httpx.ConnectError, TikHubBilibiliTransportError),
    ],
)
def test_transport_failure_is_typed_redacted_and_never_retried(
    failure: type[httpx.RequestError],
    expected: type[Exception],
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise failure("private source title", request=request)

    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(expected) as raised:
            client.fetch_play_info(SYNTHETIC_URL)
    finally:
        client.close()

    assert calls == 1
    assert "private source title" not in repr(raised.value)
    assert SYNTHETIC_URL not in repr(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"code": 500, "data": {}}),
        httpx.Response(200, json={"code": 200, "success": False, "data": {}}),
    ],
)
def test_invalid_or_unsuccessful_payload_is_redacted(
    response: httpx.Response,
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(TikHubBilibiliPayloadError) as raised:
            client.fetch_play_info(SYNTHETIC_URL)
    finally:
        client.close()

    assert calls == 1
    assert SYNTHETIC_URL not in repr(raised.value)
    assert SYNTHETIC_TOKEN not in repr(raised.value)


def test_successful_null_data_is_preserved_for_offline_schema_review() -> None:
    payload = {
        "code": 200,
        "request_id": "synthetic-null-data",
        "data": None,
    }
    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=payload)
        ),
    )
    try:
        result = client.fetch_play_info(SYNTHETIC_URL)
    finally:
        client.close()

    assert result.payload == payload
    assert result.request_id == "synthetic-null-data"


def test_response_larger_than_sixteen_mib_is_rejected_before_json_decode() -> None:
    body = (
        b'{"code":200,"data":"'
        + b"x" * (16 * 1024 * 1024)
        + b'"}'
    )
    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=body)
        ),
    )
    try:
        with pytest.raises(TikHubBilibiliPayloadError):
            client.fetch_play_info(SYNTHETIC_URL)
    finally:
        client.close()


def test_response_that_echoes_the_exact_api_key_is_rejected() -> None:
    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {"unsafe_echo": SYNTHETIC_TOKEN},
                },
            )
        ),
    )
    try:
        with pytest.raises(TikHubBilibiliPayloadError) as raised:
            client.fetch_play_info(SYNTHETIC_URL)
    finally:
        client.close()

    assert SYNTHETIC_TOKEN not in repr(raised.value)


def test_stream_read_failure_is_mapped_without_retry() -> None:
    calls = 0

    class FailingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"code":200,'
            raise httpx.ReadError("private response body")

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=FailingStream())

    client = TikHubBilibiliClient(
        _settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(TikHubBilibiliTransportError) as raised:
            client.fetch_play_info(SYNTHETIC_URL)
    finally:
        client.close()

    assert calls == 1
    assert "private response body" not in repr(raised.value)


def test_dedicated_bilibili_provider_is_exported_publicly() -> None:
    assert providers.TikHubBilibiliClient is TikHubBilibiliClient
    assert providers.TikHubBilibiliCredentialError is TikHubBilibiliCredentialError
    assert providers.TikHubBilibiliHTTPError is TikHubBilibiliHTTPError
    assert providers.TikHubBilibiliPayloadError is TikHubBilibiliPayloadError
    assert providers.TikHubBilibiliTimeoutError is TikHubBilibiliTimeoutError
    assert providers.TikHubBilibiliTransportError is TikHubBilibiliTransportError

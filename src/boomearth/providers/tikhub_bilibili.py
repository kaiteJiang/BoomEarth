"""Fixed-origin, redacted access to TikHub's Bilibili API."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

import httpx

from boomearth.config import Settings
from boomearth.workbench.bilibili_contracts import (
    BILIBILI_API_ORIGIN,
    DISCOVERY_ENDPOINT,
    PLAYURL_ENDPOINT,
)


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_READ_CHUNK_BYTES = 64 * 1024


class TikHubBilibiliError(RuntimeError):
    """Base class for safe dedicated-provider failures."""

    def __repr__(self) -> str:
        return "TikHubBilibiliError(<redacted>)"


class TikHubBilibiliCredentialError(TikHubBilibiliError):
    def __init__(self) -> None:
        super().__init__("TikHub Bilibili credential unavailable")


class TikHubBilibiliResponseError(TikHubBilibiliError):
    """Base class for safe provider response and transport failures."""


class TikHubBilibiliHTTPError(TikHubBilibiliResponseError):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__("TikHub Bilibili HTTP failure")


class TikHubBilibiliTransportError(TikHubBilibiliResponseError):
    def __init__(self) -> None:
        super().__init__("TikHub Bilibili transport unavailable")


class TikHubBilibiliTimeoutError(TikHubBilibiliTransportError):
    pass


class TikHubBilibiliPayloadError(TikHubBilibiliResponseError):
    def __init__(self) -> None:
        super().__init__("TikHub Bilibili response payload invalid")


@dataclass(frozen=True, slots=True, repr=False)
class TikHubBilibiliResponse:
    payload: dict[str, Any] = field(repr=False)
    http_status: int
    request_id: str | None

    def __repr__(self) -> str:
        return "TikHubBilibiliResponse(<redacted>)"


class TikHubBilibiliClient:
    """One fixed-origin synchronous client with no redirect behavior."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        api_key = getattr(settings, "tikhub_api_key", None)
        if not isinstance(api_key, str) or not api_key.strip():
            raise TikHubBilibiliCredentialError()
        self._api_key = api_key.strip()
        self._http = httpx.Client(
            base_url=BILIBILI_API_ORIGIN,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
            transport=transport,
        )

    def fetch_play_info(self, url: str) -> TikHubBilibiliResponse:
        return self._request(DISCOVERY_ENDPOINT, {"url": url})

    def fetch_playurl(self, bv_id: str, cid: int) -> TikHubBilibiliResponse:
        return self._request(
            PLAYURL_ENDPOINT,
            {"bv_id": bv_id, "cid": str(cid)},
        )

    def _request(
        self,
        endpoint: str,
        params: dict[str, str],
    ) -> TikHubBilibiliResponse:
        try:
            request = self._http.build_request("GET", endpoint, params=params)
            response = self._http.send(
                request,
                stream=True,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise TikHubBilibiliTimeoutError() from None
        except httpx.RequestError:
            raise TikHubBilibiliTransportError() from None
        try:
            if not 200 <= response.status_code <= 299:
                raise TikHubBilibiliHTTPError(response.status_code)
            chunks: list[bytes] = []
            total = 0
            try:
                for block in response.iter_bytes(_READ_CHUNK_BYTES):
                    total += len(block)
                    if total > MAX_RESPONSE_BYTES:
                        raise TikHubBilibiliPayloadError()
                    chunks.append(block)
            except TikHubBilibiliPayloadError:
                raise
            except httpx.TimeoutException:
                raise TikHubBilibiliTimeoutError() from None
            except httpx.RequestError:
                raise TikHubBilibiliTransportError() from None
            body = b"".join(chunks)
        finally:
            response.close()
        if self._api_key.encode("utf-8") in body:
            raise TikHubBilibiliPayloadError()
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise TikHubBilibiliPayloadError() from None
        if (
            not isinstance(payload, dict)
            or str(payload.get("code")) != "200"
            or payload.get("success") is False
        ):
            raise TikHubBilibiliPayloadError()
        raw_request_id = payload.get("request_id")
        request_id = (
            raw_request_id.strip()
            if isinstance(raw_request_id, str) and raw_request_id.strip()
            else None
        )
        return TikHubBilibiliResponse(
            payload=dict(payload),
            http_status=response.status_code,
            request_id=request_id,
        )

    def close(self) -> None:
        self._http.close()


__all__ = [
    "TikHubBilibiliClient",
    "TikHubBilibiliCredentialError",
    "TikHubBilibiliError",
    "TikHubBilibiliHTTPError",
    "TikHubBilibiliPayloadError",
    "TikHubBilibiliResponse",
    "TikHubBilibiliResponseError",
    "TikHubBilibiliTimeoutError",
    "TikHubBilibiliTransportError",
]

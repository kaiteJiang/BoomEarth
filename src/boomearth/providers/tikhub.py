"""Authorized, redacted TikHub access for the BoomEarth V1 boundary."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any
from urllib.parse import urlsplit

import httpx

from boomearth.config import Settings


OFFICIAL_TIKHUB_BASE = "https://api.tikhub.dev"
USER_INFO_ENDPOINT = "/api/v1/tikhub/user/get_user_info"
VIDEO_DATA_ENDPOINT = "/api/v1/hybrid/video_data"
_TIMEOUT_SECONDS = 10.0
_CONNECT_TIMEOUT_SECONDS = 5.0


class TikHubError(RuntimeError):
    """Base class for safe, non-sensitive TikHub failures."""

    def __repr__(self) -> str:
        return "TikHubFailure(<redacted>)"


class InvalidTikHubBaseError(TikHubError):
    """Raised when the configured base is not the official V1 base."""

    def __init__(self) -> None:
        super().__init__("TikHub API base is not the official base")


class TikHubCredentialError(TikHubError):
    """Raised when the client is constructed without a usable API key."""

    def __init__(self) -> None:
        super().__init__("TikHub API credential is unavailable")


class AuthorizationRequiredError(TikHubError):
    """Raised when the caller has not explicitly authorized source use."""

    def __init__(self) -> None:
        super().__init__("TikHub source authorization is required")


class UnsupportedSourceURLError(TikHubError):
    """Raised for URLs outside the supported HTTPS source boundary."""

    def __init__(self) -> None:
        super().__init__("TikHub source URL is not supported")


class TikHubResponseError(TikHubError):
    """Base class for redacted response and transport failures."""


class TikHubServiceError(TikHubResponseError):
    """Base class for provider-side TikHub service failures."""


class TikHubHTTPError(TikHubServiceError):
    """Raised for an HTTP response that is not a successful API response."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"TikHub request returned HTTP {status_code}")


class TikHubUnauthorizedError(TikHubHTTPError):
    """Raised for HTTP 401."""

    def __init__(self, status_code: int = 401) -> None:
        super().__init__(status_code)
        self.args = ("TikHub authentication failed",)


class TikHubPaymentRequiredError(TikHubHTTPError):
    """Raised for HTTP 402."""

    def __init__(self, status_code: int = 402) -> None:
        super().__init__(status_code)
        self.args = ("TikHub account payment is required",)


class TikHubForbiddenError(TikHubHTTPError):
    """Raised for HTTP 403."""

    def __init__(self, status_code: int = 403) -> None:
        super().__init__(status_code)
        self.args = ("TikHub request forbidden",)


class TikHubRateLimitError(TikHubHTTPError):
    """Raised for HTTP 429."""

    def __init__(self, status_code: int = 429) -> None:
        super().__init__(status_code)
        self.args = ("TikHub request is rate limited",)


class TikHubServerError(TikHubHTTPError):
    """Raised for HTTP 5xx."""

    def __init__(self, status_code: int) -> None:
        super().__init__(status_code)
        self.args = ("TikHub server unavailable",)


class TikHubTransportError(TikHubResponseError):
    """Raised when httpx cannot complete the request."""

    def __init__(self) -> None:
        super().__init__("TikHub transport unavailable")


class TikHubTimeoutError(TikHubTransportError):
    """Raised when TikHub does not respond before the client timeout."""

    def __init__(self) -> None:
        super().__init__()


class TikHubPayloadError(TikHubServiceError):
    """Base class for malformed or unsuccessful JSON payloads."""


class TikHubMalformedPayloadError(TikHubPayloadError):
    """Raised when a successful HTTP response has an unusable shape."""

    def __init__(self) -> None:
        super().__init__("TikHub response payload invalid")


class TikHubNonSuccessPayloadError(TikHubPayloadError):
    """Raised when the API body reports failure despite a successful HTTP code."""

    def __init__(self) -> None:
        super().__init__("TikHub response unsuccessful")


@dataclass(frozen=True, slots=True)
class AccountStatus:
    """Safe account state required by local diagnostics."""

    active: bool
    quota_present: bool

    @property
    def account_active(self) -> bool:
        return self.active

    @property
    def has_quota(self) -> bool:
        return self.quota_present

    def public_summary(self) -> dict[str, bool]:
        return {
            "account_active": self.active,
            "quota_present": self.quota_present,
        }


@dataclass(frozen=True, slots=True, repr=False)
class SourceMedia:
    """Normalized media for trusted internal callers.

    The private media URL is intentionally available as a field for internal
    consumers, while the custom repr and public summary omit it and all source
    metadata.
    """

    platform: str
    public_work_id: str
    duration_s: float
    private_media_url: str

    @property
    def work_id(self) -> str:
        return self.public_work_id

    @property
    def duration(self) -> float:
        return self.duration_s

    @property
    def media_url(self) -> str:
        return self.private_media_url

    def public_summary(self) -> dict[str, str | float]:
        return {
            "platform": self.platform,
            "public_work_id": self.public_work_id,
            "duration_s": self.duration_s,
        }

    def __repr__(self) -> str:
        return (
            "SourceMedia("
            f"platform={self.platform!r}, "
            f"public_work_id={self.public_work_id!r}, "
            f"duration_s={self.duration_s!r})"
        )


class TikHubClient:
    """Small synchronous TikHub client with an explicit authorized resolver."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        configured_base = getattr(settings, "tikhub_api_base", None)
        if configured_base != OFFICIAL_TIKHUB_BASE:
            raise InvalidTikHubBaseError()

        api_key = getattr(settings, "tikhub_api_key", None)
        if not isinstance(api_key, str) or not api_key.strip():
            raise TikHubCredentialError()

        self._api_key = api_key
        self._last_http_status: int | None = None
        self._http = httpx.Client(
            base_url=OFFICIAL_TIKHUB_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(
                _TIMEOUT_SECONDS,
                connect=_CONNECT_TIMEOUT_SECONDS,
            ),
            follow_redirects=False,
            transport=transport,
        )

    @property
    def last_http_status(self) -> int | None:
        """Return the most recent response status for the opt-in smoke CLI."""

        return self._last_http_status

    def check_account(self) -> AccountStatus:
        payload = self._request("GET", USER_INFO_ENDPOINT)
        api_key_data = payload.get("api_key_data")
        user_data = payload.get("user_data")
        if not isinstance(api_key_data, Mapping) or not isinstance(user_data, Mapping):
            raise TikHubMalformedPayloadError()

        _validate_account_model(api_key_data, user_data)
        active = (
            user_data["is_active"] is True
            and user_data["account_disabled"] is False
            and api_key_data["api_key_status"] == 1
        )
        quota_present = _has_official_quota(user_data)
        return AccountStatus(active=active, quota_present=quota_present)

    def resolve_authorized_url(
        self,
        url: str,
        *,
        authorized: bool = False,
    ) -> SourceMedia:
        if authorized is not True:
            raise AuthorizationRequiredError()

        platform, source_url = _normalize_source_url(url, self._api_key)
        payload = self._request(
            "GET",
            VIDEO_DATA_ENDPOINT,
            params={"url": source_url},
        )
        data = _payload_data(payload)
        work_id = _extract_work_id(data)
        duration_s = _extract_duration_s(data)
        private_media_url = _extract_private_media_url(data)
        if work_id is None or duration_s is None or private_media_url is None:
            raise TikHubMalformedPayloadError()

        return SourceMedia(
            platform=platform,
            public_work_id=work_id,
            duration_s=duration_s,
            private_media_url=private_media_url,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TikHubClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:
        try:
            response = self._http.request(method, endpoint, params=params)
        except httpx.TimeoutException:
            raise TikHubTimeoutError() from None
        except httpx.RequestError:
            raise TikHubTransportError() from None

        self._last_http_status = response.status_code
        status_code = response.status_code
        if status_code == 401:
            raise TikHubUnauthorizedError(status_code)
        if status_code == 402:
            raise TikHubPaymentRequiredError(status_code)
        if status_code == 403:
            raise TikHubForbiddenError(status_code)
        if status_code == 429:
            raise TikHubRateLimitError(status_code)
        if 500 <= status_code <= 599:
            raise TikHubServerError(status_code)
        if not 200 <= status_code <= 299:
            raise TikHubHTTPError(status_code)

        try:
            payload = response.json()
        except (TypeError, UnicodeError, ValueError):
            raise TikHubMalformedPayloadError() from None

        if not isinstance(payload, Mapping):
            raise TikHubMalformedPayloadError()

        code = payload.get("code")
        if code is not None and str(code) != "200":
            raise TikHubNonSuccessPayloadError()
        if payload.get("success") is False:
            raise TikHubNonSuccessPayloadError()
        status = payload.get("status")
        if isinstance(status, str) and status.strip().lower() in {
            "error",
            "failed",
            "failure",
        }:
            raise TikHubNonSuccessPayloadError()
        return payload


def _normalize_source_url(url: object, api_key: str) -> tuple[str, str]:
    if not isinstance(url, str):
        raise UnsupportedSourceURLError()

    candidate = url.strip()
    if api_key and api_key in candidate:
        raise UnsupportedSourceURLError()

    try:
        parsed = urlsplit(candidate)
        host = parsed.hostname
        parsed.port
    except ValueError:
        raise UnsupportedSourceURLError() from None

    if parsed.scheme.lower() != "https" or not host:
        raise UnsupportedSourceURLError()
    if parsed.username is not None or parsed.password is not None:
        raise UnsupportedSourceURLError()

    normalized_host = host.lower()
    if normalized_host == "douyin.com" or normalized_host.endswith(".douyin.com"):
        return "douyin", candidate
    if normalized_host == "tiktok.com" or normalized_host.endswith(".tiktok.com"):
        return "tiktok", candidate
    raise UnsupportedSourceURLError()


def _payload_data(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if "data" not in payload:
        return payload
    data = payload["data"]
    if not isinstance(data, Mapping):
        raise TikHubMalformedPayloadError()
    return data


def _walk_mappings(value: object) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_mappings(child)


def _first_value(root: Mapping[str, Any], keys: tuple[str, ...]) -> object | None:
    for mapping in _walk_mappings(root):
        for key in keys:
            value = mapping.get(key)
            if value is not None:
                return value
    return None


_API_KEY_MODEL_FIELDS = (
    "api_key_name",
    "api_key_scopes",
    "created_at",
    "expires_at",
    "api_key_status",
)
_USER_MODEL_FIELDS = (
    "email",
    "balance",
    "free_credit",
    "email_verified",
    "account_disabled",
    "is_active",
)


def _validate_account_model(
    api_key_data: Mapping[str, Any],
    user_data: Mapping[str, Any],
) -> None:
    if any(field not in api_key_data for field in _API_KEY_MODEL_FIELDS):
        raise TikHubMalformedPayloadError()
    if any(field not in user_data for field in _USER_MODEL_FIELDS):
        raise TikHubMalformedPayloadError()

    api_key_status = api_key_data["api_key_status"]
    if (
        isinstance(api_key_status, bool)
        or not isinstance(api_key_status, int)
        or api_key_status not in (0, 1)
    ):
        raise TikHubMalformedPayloadError()

    if not isinstance(user_data["is_active"], bool):
        raise TikHubMalformedPayloadError()
    if not isinstance(user_data["account_disabled"], bool):
        raise TikHubMalformedPayloadError()


def _has_official_quota(user_data: Mapping[str, Any]) -> bool:
    for field in ("balance", "free_credit"):
        value = user_data[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isfinite(float(value)):
            return True
    return False


def _extract_work_id(root: Mapping[str, Any]) -> str | None:
    value = _first_value(
        root,
        ("public_work_id", "aweme_id", "video_id", "item_id", "work_id", "id"),
    )
    if isinstance(value, bool) or value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _extract_duration_s(root: Mapping[str, Any]) -> float | None:
    duration_ms = _as_nonnegative_number(_first_value(root, ("duration_ms",)))
    if duration_ms is not None:
        return duration_ms / 1000.0

    duration_s = _as_nonnegative_number(_first_value(root, ("duration_s",)))
    if duration_s is not None:
        return duration_s

    duration = _as_nonnegative_number(_first_value(root, ("duration",)))
    if duration is None:
        return None
    return duration / 1000.0 if duration >= 1000.0 else duration


def _as_nonnegative_number(value: object | None) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number) or number < 0:
        return None
    return number


def _extract_private_media_url(root: Mapping[str, Any]) -> str | None:
    for mapping in _walk_mappings(root):
        for key in ("private_media_url", "video_url", "download_url", "play_url"):
            if key in mapping:
                value = _find_http_url(mapping[key])
                if value is not None:
                    return value
        for key in ("play_addr", "download_addr", "url_list"):
            if key in mapping:
                value = _find_http_url(mapping[key])
                if value is not None:
                    return value
    return None


def _find_http_url(value: object) -> str | None:
    if isinstance(value, str):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
        return None
    if isinstance(value, list):
        for child in value:
            found = _find_http_url(child)
            if found is not None:
                return found
        return None
    if isinstance(value, Mapping):
        for key in ("url_list", "url", "uri"):
            if key in value:
                found = _find_http_url(value[key])
                if found is not None:
                    return found
    return None


__all__ = [
    "AccountStatus",
    "AuthorizationRequiredError",
    "InvalidTikHubBaseError",
    "SourceMedia",
    "TikHubClient",
    "TikHubCredentialError",
    "TikHubError",
    "TikHubForbiddenError",
    "TikHubHTTPError",
    "TikHubMalformedPayloadError",
    "TikHubNonSuccessPayloadError",
    "TikHubPaymentRequiredError",
    "TikHubPayloadError",
    "TikHubRateLimitError",
    "TikHubResponseError",
    "TikHubServerError",
    "TikHubTransportError",
    "TikHubUnauthorizedError",
    "UnsupportedSourceURLError",
]

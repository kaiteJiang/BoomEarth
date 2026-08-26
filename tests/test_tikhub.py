"""Behavioral tests for the authorized TikHub source boundary."""

from __future__ import annotations

import importlib.util
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import httpx
import pytest

import boomearth.providers.tikhub as tikhub
from boomearth.config import Settings
from boomearth.providers.tikhub import (
    AccountStatus,
    AuthorizationRequiredError,
    InvalidTikHubBaseError,
    SourceMedia,
    TikHubClient,
    TikHubForbiddenError,
    TikHubHTTPError,
    TikHubMalformedPayloadError,
    TikHubNonSuccessPayloadError,
    TikHubPaymentRequiredError,
    TikHubRateLimitError,
    TikHubServerError,
    TikHubTransportError,
    TikHubUnauthorizedError,
    UnsupportedSourceURLError,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOKEN = "synthetic-tikhub-token-never-real"
SOURCE_URL = "https://www.douyin.com/video/9000000000000000001"
PRIVATE_MEDIA_URL = (
    "https://media.example.invalid/synthetic-private-video.mp4?sig=synthetic-signature"
)
SOURCE_TITLE = "synthetic source title must stay private"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _settings() -> Settings:
    return Settings(
        tikhub_api_key=TOKEN,
        dashscope_api_key="synthetic-dashscope-token",
        volcengine_api_key="synthetic-volcengine-token",
        tikhub_api_base="https://api.tikhub.dev",
        paraformer_model="synthetic-paraformer-model",
        volcengine_resource_id="synthetic-resource-id",
        indextts2_root=ROOT,
        indextts2_python=ROOT / "synthetic-python.exe",
        indextts2_reference_audio=ROOT / "synthetic-reference.wav",
        yt_dlp_path=ROOT / "synthetic-yt-dlp.exe",
        voice_playback_speed=1.0,
    )


def _client_for(
    handler: Callable[[httpx.Request], httpx.Response],
) -> TikHubClient:
    return TikHubClient(_settings(), transport=httpx.MockTransport(handler))


def _json_handler(
    payload: object,
    *,
    status_code: int = 200,
    seen: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status_code, json=payload, request=request)

    return handler


def _load_smoke_module() -> ModuleType:
    path = ROOT / "automation" / "scripts" / "api_smoke.py"
    spec = importlib.util.spec_from_file_location("boomearth_api_smoke_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_account_normalizes_official_root_user_info_without_exposing_amounts() -> None:
    client = _client_for(_json_handler(_fixture("tikhub_user_ok.json")))
    try:
        status = client.check_account()
    finally:
        client.close()

    assert status == AccountStatus(active=True, quota_present=True)
    assert status.account_active is True
    assert status.has_quota is True
    rendered = repr(status) + repr(status.public_summary())
    assert "owner@example.invalid" not in rendered
    assert "123.45" not in rendered
    assert "17.0" not in rendered
    assert TOKEN not in rendered


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("user_data", "is_active", False),
        ("user_data", "account_disabled", True),
        ("api_key_data", "api_key_status", 0),
    ],
    ids=["user-inactive", "account-disabled", "api-key-inactive"],
)
def test_check_account_marks_each_official_health_veto_inactive(
    section: str,
    field: str,
    value: object,
) -> None:
    payload = deepcopy(_fixture("tikhub_user_ok.json"))
    payload[section][field] = value
    client = _client_for(_json_handler(payload))
    try:
        status = client.check_account()
    finally:
        client.close()

    assert status == AccountStatus(active=False, quota_present=True)


def test_check_account_marks_combined_official_health_veto_inactive() -> None:
    payload = deepcopy(_fixture("tikhub_user_ok.json"))
    payload["user_data"]["is_active"] = False
    payload["user_data"]["account_disabled"] = True
    payload["api_key_data"]["api_key_status"] = 0
    client = _client_for(_json_handler(payload))
    try:
        status = client.check_account()
    finally:
        client.close()

    assert status == AccountStatus(active=False, quota_present=True)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("api_key_data", "api_key_status", None),
        ("api_key_data", "api_key_status", "1"),
        ("user_data", "is_active", None),
        ("user_data", "is_active", "true"),
        ("user_data", "account_disabled", None),
        ("user_data", "account_disabled", 0),
    ],
    ids=[
        "missing-api-key-status",
        "string-api-key-status",
        "missing-user-active",
        "string-user-active",
        "missing-account-disabled",
        "integer-account-disabled",
    ],
)
def test_check_account_rejects_missing_or_malformed_official_status_fields(
    section: str,
    field: str,
    value: object,
) -> None:
    payload = deepcopy(_fixture("tikhub_user_ok.json"))
    if value is None:
        payload[section].pop(field)
    else:
        payload[section][field] = value
    client = _client_for(_json_handler(payload))
    try:
        with pytest.raises(TikHubMalformedPayloadError):
            client.check_account()
    finally:
        client.close()


@pytest.mark.parametrize(
    ("balance", "free_credit", "expected"),
    [
        (0, 0, True),
        (123.45, None, True),
        ("not-a-number", "also-not-a-number", False),
    ],
    ids=["zero-is-present", "one-valid-field", "all-invalid-fields"],
)
def test_check_account_quota_presence_uses_finite_official_numeric_fields(
    balance: object,
    free_credit: object,
    expected: bool,
) -> None:
    payload = deepcopy(_fixture("tikhub_user_ok.json"))
    payload["user_data"]["balance"] = balance
    payload["user_data"]["free_credit"] = free_credit
    client = _client_for(_json_handler(payload))
    try:
        status = client.check_account()
    finally:
        client.close()

    assert status.active is True
    assert status.quota_present is expected


def test_check_account_rejects_obsolete_nested_account_model() -> None:
    fixture = _fixture("tikhub_user_ok.json")
    payload = {
        "code": fixture["code"],
        "message": "synthetic nested wrapper",
        "data": {
            "api_key_data": fixture["api_key_data"],
            "user_data": fixture["user_data"],
        },
    }
    client = _client_for(_json_handler(payload))
    try:
        with pytest.raises(TikHubMalformedPayloadError):
            client.check_account()
    finally:
        client.close()


def test_resolver_sends_bearer_header_to_exact_official_video_endpoint() -> None:
    seen: list[httpx.Request] = []
    client = _client_for(
        _json_handler(_fixture("tikhub_video_ok.json"), seen=seen)
    )
    try:
        media = client.resolve_authorized_url(SOURCE_URL, authorized=True)
    finally:
        client.close()

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == "api.tikhub.dev"
    assert request.url.path == "/api/v1/hybrid/video_data"
    assert request.url.params["url"] == SOURCE_URL
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)
    assert TOKEN not in request.content.decode("utf-8")
    assert media.platform == "douyin"


def test_client_rejects_changed_base_before_any_credentialed_request() -> None:
    seen: list[httpx.Request] = []
    settings = replace(_settings(), tikhub_api_base="https://api.tikhub.dev.evil.invalid")

    with pytest.raises(InvalidTikHubBaseError) as raised:
        TikHubClient(settings, transport=httpx.MockTransport(_json_handler({}, seen=seen)))

    assert seen == []
    assert TOKEN not in str(raised.value)
    assert TOKEN not in repr(raised.value)


@pytest.mark.parametrize("authorized", [None, False])
def test_resolver_requires_explicit_authorization_without_transport_call(
    authorized: bool | None,
) -> None:
    seen: list[httpx.Request] = []
    client = _client_for(
        _json_handler(_fixture("tikhub_video_ok.json"), seen=seen)
    )
    try:
        if authorized is None:
            with pytest.raises(AuthorizationRequiredError):
                client.resolve_authorized_url(SOURCE_URL)
        else:
            with pytest.raises(AuthorizationRequiredError):
                client.resolve_authorized_url(SOURCE_URL, authorized=authorized)
    finally:
        client.close()

    assert seen == []


@pytest.mark.parametrize(
    "url",
    [
        "http://www.douyin.com/video/9000000000000000001",
        "https://douyin.com.evil.invalid/video/9000000000000000001",
        "https://evil.invalid@www.douyin.com/video/9000000000000000001",
        "https:///video/9000000000000000001",
        "https://example.invalid/video/9000000000000000001",
        "not-a-url",
    ],
)
def test_resolver_rejects_unsupported_source_urls_without_transport_call(
    url: str,
) -> None:
    seen: list[httpx.Request] = []
    client = _client_for(
        _json_handler(_fixture("tikhub_video_ok.json"), seen=seen)
    )
    try:
        with pytest.raises(UnsupportedSourceURLError) as raised:
            client.resolve_authorized_url(url, authorized=True)
    finally:
        client.close()

    assert seen == []
    assert url not in str(raised.value)
    assert url not in repr(raised.value)
    assert TOKEN not in str(raised.value)


@pytest.mark.parametrize(
    ("url", "expected_platform"),
    [
        ("https://DOUYIN.com/video/9000000000000000001", "douyin"),
        ("https://creator.douyin.com/video/9000000000000000001", "douyin"),
        ("https://www.tiktok.com/@synthetic/video/9000000000000000001", "tiktok"),
        ("https://m.tiktok.com/video/9000000000000000001", "tiktok"),
    ],
)
def test_resolver_normalizes_supported_host_platforms(
    url: str,
    expected_platform: str,
) -> None:
    client = _client_for(_json_handler(_fixture("tikhub_video_ok.json")))
    try:
        media = client.resolve_authorized_url(url, authorized=True)
    finally:
        client.close()

    assert media.platform == expected_platform


def test_source_media_exposes_internal_media_but_public_views_are_redacted() -> None:
    client = _client_for(_json_handler(_fixture("tikhub_video_ok.json")))
    try:
        media = client.resolve_authorized_url(SOURCE_URL, authorized=True)
    finally:
        client.close()

    assert media == SourceMedia(
        platform="douyin",
        public_work_id="9000000000000000001",
        duration_s=12.345,
        private_media_url=PRIVATE_MEDIA_URL,
    )
    assert media.work_id == "9000000000000000001"
    assert media.duration == 12.345
    assert media.media_url == PRIVATE_MEDIA_URL
    assert media.public_summary() == {
        "platform": "douyin",
        "public_work_id": "9000000000000000001",
        "duration_s": 12.345,
    }

    public_render = repr(media) + repr(media.public_summary())
    assert PRIVATE_MEDIA_URL not in public_render
    assert SOURCE_URL not in public_render
    assert SOURCE_TITLE not in public_render


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, TikHubUnauthorizedError),
        (402, TikHubPaymentRequiredError),
        (403, TikHubForbiddenError),
        (429, TikHubRateLimitError),
        (500, TikHubServerError),
        (503, TikHubServerError),
    ],
)
def test_required_http_statuses_raise_typed_redacted_errors(
    status_code: int,
    error_type: type[Exception],
) -> None:
    response_body = {
        "error": TOKEN,
        "source": SOURCE_URL,
        "media": PRIVATE_MEDIA_URL,
    }
    client = _client_for(_json_handler(response_body, status_code=status_code))
    try:
        with pytest.raises(error_type) as raised:
            client.check_account()
    finally:
        client.close()

    assert isinstance(raised.value, TikHubHTTPError)
    assert raised.value.status_code == status_code
    rendered = str(raised.value) + repr(raised.value)
    assert TOKEN not in rendered
    assert SOURCE_URL not in rendered
    assert PRIVATE_MEDIA_URL not in rendered
    assert "error" not in rendered.lower()


def test_redirects_are_not_followed_and_location_is_not_leaked() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            302,
            headers={"location": "https://redirect.example.invalid/private"},
            request=request,
        )

    client = _client_for(handler)
    try:
        with pytest.raises(TikHubHTTPError) as raised:
            client.check_account()
    finally:
        client.close()

    assert len(calls) == 1
    assert calls[0].url.host == "api.tikhub.dev"
    assert "redirect.example.invalid" not in str(raised.value)


def test_transport_failure_is_typed_and_does_not_echo_exception_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"transport detail {TOKEN} {SOURCE_URL}", request=request
        )

    client = _client_for(handler)
    try:
        with pytest.raises(TikHubTransportError) as raised:
            client.check_account()
    finally:
        client.close()

    rendered = str(raised.value) + repr(raised.value)
    assert TOKEN not in rendered
    assert SOURCE_URL not in rendered
    assert "transport detail" not in rendered


def test_malformed_json_is_typed_and_does_not_echo_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=f"malformed {TOKEN} {PRIVATE_MEDIA_URL}".encode("utf-8"),
            request=request,
        )

    client = _client_for(handler)
    try:
        with pytest.raises(TikHubMalformedPayloadError) as raised:
            client.check_account()
    finally:
        client.close()

    rendered = str(raised.value) + repr(raised.value)
    assert TOKEN not in rendered
    assert PRIVATE_MEDIA_URL not in rendered
    assert "malformed" not in rendered.lower()


def test_non_success_json_payload_is_typed_and_redacted() -> None:
    payload = {
        "code": 401,
        "message": f"{TOKEN} {SOURCE_URL}",
        "data": {"email": "owner@example.invalid"},
    }
    client = _client_for(_json_handler(payload))
    try:
        with pytest.raises(TikHubNonSuccessPayloadError) as raised:
            client.check_account()
    finally:
        client.close()

    rendered = str(raised.value) + repr(raised.value)
    assert TOKEN not in rendered
    assert SOURCE_URL not in rendered
    assert "owner@example.invalid" not in rendered


def test_success_payload_with_missing_video_fields_is_typed_and_redacted() -> None:
    payload = {
        "code": 200,
        "data": {"video_data": {"desc": SOURCE_TITLE, "url": PRIVATE_MEDIA_URL}},
    }
    client = _client_for(_json_handler(payload))
    try:
        with pytest.raises(TikHubMalformedPayloadError) as raised:
            client.resolve_authorized_url(SOURCE_URL, authorized=True)
    finally:
        client.close()

    rendered = str(raised.value) + repr(raised.value)
    assert SOURCE_URL not in rendered
    assert SOURCE_TITLE not in rendered
    assert PRIVATE_MEDIA_URL not in rendered


def test_smoke_module_import_has_no_automatic_request() -> None:
    module = _load_smoke_module()
    assert hasattr(module, "main")


def test_smoke_account_prints_only_safe_status_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load_smoke_module()
    calls: list[object] = []

    class FakeClient:
        last_http_status = 200

        def __init__(self, settings: object) -> None:
            calls.append(settings)

        def check_account(self) -> AccountStatus:
            return AccountStatus(active=True, quota_present=True)

        def close(self) -> None:
            pass

    monkeypatch.setattr(module.Settings, "load", lambda root: object())

    exit_code = module.main(
        ["tikhub-account"],
        root=tmp_path,
        client_factory=FakeClient,
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert len(calls) == 1
    assert output.out.splitlines() == [
        "status=OK",
        "http_status=200",
        "account_active=true",
        "quota_present=true",
    ]
    assert output.err == ""
    assert TOKEN not in output.out
    assert "owner@example.invalid" not in output.out
    assert "999.99" not in output.out
    assert "raw" not in output.out.lower()


def test_smoke_account_classifies_config_failure_without_constructing_client(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Would fail if configuration errors reached the account client or lost their status."""

    module = _load_smoke_module()
    client_calls: list[object] = []

    def missing_settings(root: Path) -> object:
        raise module.MissingSettingsError(("PRIVATE",))

    class ForbiddenClient:
        def __init__(self, settings: object) -> None:
            client_calls.append(settings)
            raise AssertionError("config failure must block account client construction")

    monkeypatch.setattr(module.Settings, "load", missing_settings)

    exit_code = module.main(
        ["tikhub-account"],
        root=tmp_path,
        client_factory=ForbiddenClient,
    )
    output = capsys.readouterr()

    assert exit_code == 2
    assert client_calls == []
    assert output.out.splitlines() == [
        "status=CONFIG_ERROR",
        "http_status=UNAVAILABLE",
        "account_active=false",
        "quota_present=false",
    ]
    assert "PRIVATE" not in output.out + output.err


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_exit_code"),
    [
        ("TikHubCredentialError", "CONFIG_ERROR", 2),
        ("AuthorizationRequiredError", "AUTHORIZATION_REQUIRED", 2),
        ("TikHubTransportError", "NETWORK_ERROR", 1),
        ("TikHubHTTPError", "SERVICE_ERROR", 1),
        ("RuntimeError", "INTERNAL_ERROR", 1),
    ],
)
def test_api_smoke_main_maps_injected_tikhub_typed_errors_to_exact_exit_contract(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    error: str,
    expected_status: str,
    expected_exit_code: int,
) -> None:
    """Would fail if real api_smoke.main mapped a TikHub error outside its 0/1/2 contract."""

    module = _load_smoke_module()
    errors = {
        "TikHubCredentialError": module.TikHubCredentialError(),
        "AuthorizationRequiredError": module.AuthorizationRequiredError(),
        "TikHubTransportError": module.TikHubTransportError(),
        "TikHubHTTPError": TikHubHTTPError(500),
        "RuntimeError": RuntimeError("synthetic runtime failure"),
    }
    client_calls: list[object] = []

    class FakeClient:
        last_http_status = None

        def __init__(self, settings: object) -> None:
            client_calls.append(settings)

        def check_account(self) -> AccountStatus:
            raise errors[error]

        def close(self) -> None:
            pass

    exit_code = module.main(
        ["tikhub-account"],
        root=tmp_path,
        settings_loader=lambda root: object(),
        client_factory=FakeClient,
    )
    output = capsys.readouterr()

    assert exit_code == expected_exit_code
    assert len(client_calls) == 1
    assert output.out.splitlines() == [
        f"status={expected_status}",
        "http_status=UNAVAILABLE",
        "account_active=false",
        "quota_present=false",
    ]
    assert "synthetic runtime failure" not in output.out + output.err


def test_tikhub_timeout_is_a_narrow_provider_error_and_smoke_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Would fail if a real TikHub timeout became a generic transport failure."""

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("PRIVATE", request=request)

    client = _client_for(timeout_handler)
    try:
        with pytest.raises(TikHubTransportError) as raised:
            client.check_account()
    finally:
        client.close()

    assert type(raised.value).__name__ == "TikHubTimeoutError"

    module = _load_smoke_module()
    timeout_error = type(raised.value)()

    class FailingClient:
        last_http_status = None

        def __init__(self, settings: object) -> None:
            pass

        def check_account(self) -> AccountStatus:
            raise timeout_error

        def close(self) -> None:
            pass

    monkeypatch.setattr(module.Settings, "load", lambda root: object())
    exit_code = module.main(["tikhub-account"], root=tmp_path, client_factory=FailingClient)
    output = capsys.readouterr()

    assert exit_code == 1
    assert output.out.splitlines()[0] == "status=TIMEOUT"
    assert "PRIVATE" not in output.out + output.err


def test_tikhub_service_errors_share_a_narrow_provider_service_base() -> None:
    """Would fail if real TikHub HTTP rejections lacked a stable service class."""

    client = _client_for(_json_handler({}, status_code=500))
    try:
        with pytest.raises(TikHubHTTPError) as raised:
            client.check_account()
    finally:
        client.close()

    service_base = getattr(tikhub, "TikHubServiceError", ())
    assert isinstance(raised.value, service_base)


def test_smoke_account_unknown_settings_error_is_internal_without_factory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Would fail if an unexpected settings error escaped or constructed a client."""

    module = _load_smoke_module()
    calls: list[object] = []

    def broken_settings(root: Path) -> object:
        raise RuntimeError("PRIVATE")

    class ForbiddenClient:
        def __init__(self, settings: object) -> None:
            calls.append(settings)

    monkeypatch.setattr(module.Settings, "load", broken_settings)
    exit_code = module.main(["tikhub-account"], root=tmp_path, client_factory=ForbiddenClient)
    output = capsys.readouterr()

    assert exit_code == 1
    assert calls == []
    assert output.out.splitlines()[0] == "status=INTERNAL_ERROR"
    assert "PRIVATE" not in output.out + output.err


def test_smoke_status_output_enforces_the_status_whitelist(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Would fail if an accidental status value could reach smoke diagnostics."""

    module = _load_smoke_module()
    module._print_account_status(
        status="PRIVATE_NOT_A_STATUS",
        http_status="UNAVAILABLE",
        account_active=False,
        quota_present=False,
    )
    output = capsys.readouterr()

    assert output.out.splitlines()[0] == "status=INTERNAL_ERROR"
    assert "PRIVATE" not in output.out + output.err


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TikHubTransportError(), "NETWORK_ERROR"),
        (TikHubHTTPError(500), "SERVICE_ERROR"),
    ],
)
def test_smoke_account_classifies_typed_failures_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    error: BaseException,
    expected: str,
) -> None:
    """Would fail if transport or provider failures were collapsed into a generic status."""

    module = _load_smoke_module()

    class FakeClient:
        last_http_status = None

        def __init__(self, settings: object) -> None:
            pass

        def check_account(self) -> AccountStatus:
            raise error

        def close(self) -> None:
            pass

    monkeypatch.setattr(module.Settings, "load", lambda root: object())

    exit_code = module.main(
        ["tikhub-account"],
        root=tmp_path,
        client_factory=FakeClient,
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert output.out.splitlines() == [
        f"status={expected}",
        "http_status=UNAVAILABLE",
        "account_active=false",
        "quota_present=false",
    ]
    assert "PRIVATE" not in output.out + output.err

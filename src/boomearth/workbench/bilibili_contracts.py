"""Strict private contracts for dedicated Bilibili source acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from boomearth.workbench.source_artifacts import (
    SourceContractError,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_approval_payload,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import SourceLedgerError, WashEventLedger


BILIBILI_API_ORIGIN = "https://api.tikhub.io"
DISCOVERY_ENDPOINT = "/api/v1/bilibili/web/fetch_video_play_info"
PLAYURL_ENDPOINT = "/api/v1/bilibili/web/fetch_video_playurl"
DISCOVERY_PLAN_FILE = "tikhub-bilibili-discovery-plan.json"
DISCOVERY_APPROVAL_FILE = "tikhub-bilibili-discovery-approval.json"
DISCOVERY_EXECUTION_STARTED_FILE = (
    "tikhub-bilibili-discovery-execution-started.json"
)
DISCOVERY_RESPONSE_FILE = "tikhub-bilibili-play-info-response.json"
DISCOVERY_RESULT_FILE = "tikhub-bilibili-discovery-result.json"
DISCOVERY_RECOVERY_PLAN_FILE = "tikhub-bilibili-discovery-recovery-plan.json"
DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE = (
    "tikhub-bilibili-discovery-recovery-execution-started.json"
)
DISCOVERY_RECOVERY_RESULT_FILE = "tikhub-bilibili-discovery-recovery-result.json"
PLAYURL_PLAN_FILE = "tikhub-bilibili-playurl-plan.json"
PLAYURL_APPROVAL_FILE = "tikhub-bilibili-playurl-approval.json"
PLAYURL_EXECUTION_STARTED_FILE = "tikhub-bilibili-playurl-execution-started.json"
PLAYURL_RESPONSE_FILE = "tikhub-bilibili-playurl-response.json"
PLAYURL_RESULT_FILE = "tikhub-bilibili-playurl-result.json"
MEDIA_PLAN_FILE = "tikhub-bilibili-media-plan.json"
MEDIA_APPROVAL_FILE = "tikhub-bilibili-media-approval.json"
MEDIA_EXECUTION_STARTED_FILE = "tikhub-bilibili-media-execution-started.json"
MEDIA_DOWNLOAD_RESULT_FILE = "tikhub-bilibili-media-download-result.json"

_VIDEO_PATH_RE = re.compile(r"^/video/(BV[0-9A-Za-z]{10})/?$")
_POSITIVE_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_NONNEGATIVE_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_DISCOVERY_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "api_origin",
        "endpoint",
        "method",
        "paid_api_request_count",
        "media_asset_count",
        "network_required",
        "fee_possible",
        "provider_credential_required",
        "source_site_credentials",
        "follow_redirects",
        "timeout_seconds",
        "no_retry",
        "no_fallback",
    }
)
_PLAYURL_PLAN_KEYS = _DISCOVERY_PLAN_KEYS | frozenset(
    {"discovery_response_sha256", "bv_id", "cid"}
)
_DISCOVERY_RECOVERY_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "discovery_plan_sha256",
        "response_sha256",
        "failed_result_sha256",
        "network_required",
        "provider_credential_required",
        "paid_api_request_count",
        "media_asset_count",
        "no_key_read",
        "no_retry",
        "no_fallback",
        "preserve_originals",
    }
)


class BilibiliContractError(ValueError):
    """A fixed-message failure that never carries private source data."""

    def __repr__(self) -> str:
        return "BilibiliContractError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliDiscoveryPlan:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    api_origin: str
    endpoint: str
    method: str
    paid_api_request_count: int
    media_asset_count: int
    network_required: bool
    fee_possible: bool
    provider_credential_required: bool
    source_site_credentials: bool
    follow_redirects: bool
    timeout_seconds: int
    no_retry: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "BilibiliDiscoveryPlan(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliPlayurlPlan:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    api_origin: str
    endpoint: str
    method: str
    discovery_response_sha256: str
    bv_id: str
    cid: int
    paid_api_request_count: int
    media_asset_count: int
    network_required: bool
    fee_possible: bool
    provider_credential_required: bool
    source_site_credentials: bool
    follow_redirects: bool
    timeout_seconds: int
    no_retry: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "BilibiliPlayurlPlan(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliDiscoveryRecoveryPlan:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    discovery_plan_sha256: str
    response_sha256: str
    failed_result_sha256: str
    network_required: bool
    provider_credential_required: bool
    paid_api_request_count: int
    media_asset_count: int
    no_key_read: bool
    no_retry: bool
    no_fallback: bool
    preserve_originals: bool

    def __repr__(self) -> str:
        return "BilibiliDiscoveryRecoveryPlan(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliMediaPlan:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    upstream_response_sha256: str
    selection_sha256: str
    assets: tuple[dict[str, object], ...]
    paid_api_request_count: int
    media_asset_count: int
    max_bytes_per_asset: int
    max_total_bytes: int
    connect_timeout_seconds: int
    asset_timeout_seconds: int
    max_redirect_hops_per_asset: int
    follow_https_redirects: bool
    no_retry: bool
    no_backup_url: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "BilibiliMediaPlan(<redacted>)"


def normalize_bilibili_video_url(value: object) -> str:
    """Return one direct Bilibili video URL without revealing it on failure."""

    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value.startswith("https://")
    ):
        raise BilibiliContractError("bilibili-source-url-invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        raise BilibiliContractError("bilibili-source-url-invalid") from None
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (port is not None and port != 443)
        or not (host.lower() == "bilibili.com" or host.lower().endswith(".bilibili.com"))
        or _VIDEO_PATH_RE.fullmatch(parsed.path) is None
        or not set(query).issubset({"p", "t"})
    ):
        raise BilibiliContractError("bilibili-source-url-invalid")
    if "p" in query and (
        len(query["p"]) != 1 or _POSITIVE_INTEGER_RE.fullmatch(query["p"][0]) is None
    ):
        raise BilibiliContractError("bilibili-source-url-invalid")
    if "t" in query and (
        len(query["t"]) != 1
        or _NONNEGATIVE_DECIMAL_RE.fullmatch(query["t"][0]) is None
    ):
        raise BilibiliContractError("bilibili-source-url-invalid")
    return value


def discovery_plan_value(plan: BilibiliDiscoveryPlan) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
        "provider": plan.provider,
        "action": plan.action,
        "input_sha256": plan.input_sha256,
        "api_origin": plan.api_origin,
        "endpoint": plan.endpoint,
        "method": plan.method,
        "paid_api_request_count": plan.paid_api_request_count,
        "media_asset_count": plan.media_asset_count,
        "network_required": plan.network_required,
        "fee_possible": plan.fee_possible,
        "provider_credential_required": plan.provider_credential_required,
        "source_site_credentials": plan.source_site_credentials,
        "follow_redirects": plan.follow_redirects,
        "timeout_seconds": plan.timeout_seconds,
        "no_retry": plan.no_retry,
        "no_fallback": plan.no_fallback,
    }


def playurl_plan_value(plan: BilibiliPlayurlPlan) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
        "provider": plan.provider,
        "action": plan.action,
        "input_sha256": plan.input_sha256,
        "api_origin": plan.api_origin,
        "endpoint": plan.endpoint,
        "method": plan.method,
        "discovery_response_sha256": plan.discovery_response_sha256,
        "bv_id": plan.bv_id,
        "cid": plan.cid,
        "paid_api_request_count": plan.paid_api_request_count,
        "media_asset_count": plan.media_asset_count,
        "network_required": plan.network_required,
        "fee_possible": plan.fee_possible,
        "provider_credential_required": plan.provider_credential_required,
        "source_site_credentials": plan.source_site_credentials,
        "follow_redirects": plan.follow_redirects,
        "timeout_seconds": plan.timeout_seconds,
        "no_retry": plan.no_retry,
        "no_fallback": plan.no_fallback,
    }


def load_discovery_plan(path: Path) -> BilibiliDiscoveryPlan:
    """Load only the exact fixed discovery policy."""

    try:
        value = load_exact_json(path, _DISCOVERY_PLAN_KEYS)
        work_id = value["work_id"]
        if not isinstance(work_id, str):
            raise ValueError
        parsed_work_id = UUID(work_id)
        if not (
            parsed_work_id.version == 4
            and str(parsed_work_id) == work_id
            and type(value["schema_version"]) is int
            and value["schema_version"] == 1
            and value["provider"] == "tikhub-bilibili"
            and value["action"] == "bilibili-play-info-discovery"
            and isinstance(value["input_sha256"], str)
            and _DIGEST_RE.fullmatch(value["input_sha256"]) is not None
            and value["api_origin"] == BILIBILI_API_ORIGIN
            and value["endpoint"] == DISCOVERY_ENDPOINT
            and value["method"] == "GET"
            and type(value["paid_api_request_count"]) is int
            and value["paid_api_request_count"] == 1
            and type(value["media_asset_count"]) is int
            and value["media_asset_count"] == 0
            and value["network_required"] is True
            and value["fee_possible"] is True
            and value["provider_credential_required"] is True
            and value["source_site_credentials"] is False
            and value["follow_redirects"] is False
            and type(value["timeout_seconds"]) is int
            and value["timeout_seconds"] == 30
            and value["no_retry"] is True
            and value["no_fallback"] is True
        ):
            raise ValueError
        return BilibiliDiscoveryPlan(**value)  # type: ignore[arg-type]
    except (SourceContractError, TypeError, ValueError):
        raise BilibiliContractError("bilibili-discovery-plan-invalid") from None


def load_playurl_plan(path: Path) -> BilibiliPlayurlPlan:
    """Load only the exact optional playurl policy."""

    try:
        value = load_exact_json(path, _PLAYURL_PLAN_KEYS)
        work_id = value["work_id"]
        if not isinstance(work_id, str):
            raise ValueError
        parsed_work_id = UUID(work_id)
        if not (
            parsed_work_id.version == 4
            and str(parsed_work_id) == work_id
            and type(value["schema_version"]) is int
            and value["schema_version"] == 1
            and value["provider"] == "tikhub-bilibili"
            and value["action"] == "bilibili-playurl-resolution"
            and isinstance(value["input_sha256"], str)
            and _DIGEST_RE.fullmatch(value["input_sha256"]) is not None
            and value["api_origin"] == BILIBILI_API_ORIGIN
            and value["endpoint"] == PLAYURL_ENDPOINT
            and value["method"] == "GET"
            and isinstance(value["discovery_response_sha256"], str)
            and _DIGEST_RE.fullmatch(value["discovery_response_sha256"])
            is not None
            and isinstance(value["bv_id"], str)
            and _VIDEO_PATH_RE.fullmatch(f"/video/{value['bv_id']}") is not None
            and type(value["cid"]) is int
            and value["cid"] > 0
            and type(value["paid_api_request_count"]) is int
            and value["paid_api_request_count"] == 1
            and type(value["media_asset_count"]) is int
            and value["media_asset_count"] == 0
            and value["network_required"] is True
            and value["fee_possible"] is True
            and value["provider_credential_required"] is True
            and value["source_site_credentials"] is False
            and value["follow_redirects"] is False
            and type(value["timeout_seconds"]) is int
            and value["timeout_seconds"] == 30
            and value["no_retry"] is True
            and value["no_fallback"] is True
        ):
            raise ValueError
        return BilibiliPlayurlPlan(**value)  # type: ignore[arg-type]
    except (SourceContractError, TypeError, ValueError):
        raise BilibiliContractError("bilibili-playurl-plan-invalid") from None


def media_plan_value(plan: BilibiliMediaPlan) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
        "provider": plan.provider,
        "action": plan.action,
        "input_sha256": plan.input_sha256,
        "upstream_response_sha256": plan.upstream_response_sha256,
        "selection_sha256": plan.selection_sha256,
        "assets": [dict(asset) for asset in plan.assets],
        "paid_api_request_count": plan.paid_api_request_count,
        "media_asset_count": plan.media_asset_count,
        "max_bytes_per_asset": plan.max_bytes_per_asset,
        "max_total_bytes": plan.max_total_bytes,
        "connect_timeout_seconds": plan.connect_timeout_seconds,
        "asset_timeout_seconds": plan.asset_timeout_seconds,
        "max_redirect_hops_per_asset": plan.max_redirect_hops_per_asset,
        "follow_https_redirects": plan.follow_https_redirects,
        "no_retry": plan.no_retry,
        "no_backup_url": plan.no_backup_url,
        "no_fallback": plan.no_fallback,
    }


def discovery_recovery_plan_value(
    plan: BilibiliDiscoveryRecoveryPlan,
) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
        "provider": plan.provider,
        "action": plan.action,
        "input_sha256": plan.input_sha256,
        "discovery_plan_sha256": plan.discovery_plan_sha256,
        "response_sha256": plan.response_sha256,
        "failed_result_sha256": plan.failed_result_sha256,
        "network_required": plan.network_required,
        "provider_credential_required": plan.provider_credential_required,
        "paid_api_request_count": plan.paid_api_request_count,
        "media_asset_count": plan.media_asset_count,
        "no_key_read": plan.no_key_read,
        "no_retry": plan.no_retry,
        "no_fallback": plan.no_fallback,
        "preserve_originals": plan.preserve_originals,
    }


def load_discovery_recovery_plan(path: Path) -> BilibiliDiscoveryRecoveryPlan:
    try:
        value = load_exact_json(path, _DISCOVERY_RECOVERY_PLAN_KEYS)
        work_id = value["work_id"]
        if not isinstance(work_id, str):
            raise ValueError
        parsed_work_id = UUID(work_id)
        if not (
            parsed_work_id.version == 4
            and str(parsed_work_id) == work_id
            and type(value["schema_version"]) is int
            and value["schema_version"] == 1
            and value["provider"] == "local-bilibili-recovery"
            and value["action"] == "bilibili-discovery-offline-recovery"
            and all(
                isinstance(value[name], str)
                and _DIGEST_RE.fullmatch(value[name]) is not None
                for name in (
                    "input_sha256",
                    "discovery_plan_sha256",
                    "response_sha256",
                    "failed_result_sha256",
                )
            )
            and value["network_required"] is False
            and value["provider_credential_required"] is False
            and type(value["paid_api_request_count"]) is int
            and value["paid_api_request_count"] == 0
            and type(value["media_asset_count"]) is int
            and value["media_asset_count"] == 0
            and value["no_key_read"] is True
            and value["no_retry"] is True
            and value["no_fallback"] is True
            and value["preserve_originals"] is True
        ):
            raise ValueError
        return BilibiliDiscoveryRecoveryPlan(**value)  # type: ignore[arg-type]
    except (SourceContractError, TypeError, ValueError):
        raise BilibiliContractError(
            "bilibili-discovery-recovery-plan-invalid"
        ) from None


def load_media_plan(path: Path) -> BilibiliMediaPlan:
    keys = frozenset(
        {
            "schema_version",
            "work_id",
            "provider",
            "action",
            "input_sha256",
            "upstream_response_sha256",
            "selection_sha256",
            "assets",
            "paid_api_request_count",
            "media_asset_count",
            "max_bytes_per_asset",
            "max_total_bytes",
            "connect_timeout_seconds",
            "asset_timeout_seconds",
            "max_redirect_hops_per_asset",
            "follow_https_redirects",
            "no_retry",
            "no_backup_url",
            "no_fallback",
        }
    )
    try:
        value = load_exact_json(path, keys)
        raw_assets = value["assets"]
        if not isinstance(raw_assets, list) or len(raw_assets) not in {1, 2}:
            raise ValueError
        assets: list[dict[str, object]] = []
        labels: list[str] = []
        for item in raw_assets:
            if not isinstance(item, dict) or set(item) != {
                "label",
                "url_sha256",
                "initial_host_sha256",
                "expected_size",
            }:
                raise ValueError
            label = item["label"]
            if label not in {"progressive", "video", "audio"}:
                raise ValueError
            if not all(
                isinstance(item[name], str)
                and _DIGEST_RE.fullmatch(item[name]) is not None
                for name in ("url_sha256", "initial_host_sha256")
            ):
                raise ValueError
            if item["expected_size"] is not None and (
                type(item["expected_size"]) is not int
                or item["expected_size"] <= 0
            ):
                raise ValueError
            labels.append(label)
            assets.append(dict(item))
        if labels not in (["progressive"], ["video", "audio"]):
            raise ValueError
        work_id = value["work_id"]
        if not isinstance(work_id, str):
            raise ValueError
        parsed_work_id = UUID(work_id)
        if not (
            parsed_work_id.version == 4
            and str(parsed_work_id) == work_id
            and type(value["schema_version"]) is int
            and value["schema_version"] == 1
            and value["provider"] == "tikhub-bilibili-media"
            and value["action"] == "bilibili-media-download"
            and all(
                isinstance(value[name], str)
                and _DIGEST_RE.fullmatch(value[name]) is not None
                for name in (
                    "input_sha256",
                    "upstream_response_sha256",
                    "selection_sha256",
                )
            )
            and type(value["paid_api_request_count"]) is int
            and value["paid_api_request_count"] == 0
            and type(value["media_asset_count"]) is int
            and value["media_asset_count"] == len(assets)
            and type(value["max_bytes_per_asset"]) is int
            and value["max_bytes_per_asset"] == 2 * 1024 * 1024 * 1024
            and type(value["max_total_bytes"]) is int
            and value["max_total_bytes"] == 4 * 1024 * 1024 * 1024
            and type(value["connect_timeout_seconds"]) is int
            and value["connect_timeout_seconds"] == 15
            and type(value["asset_timeout_seconds"]) is int
            and value["asset_timeout_seconds"] == 900
            and type(value["max_redirect_hops_per_asset"]) is int
            and value["max_redirect_hops_per_asset"] == 3
            and value["follow_https_redirects"] is True
            and value["no_retry"] is True
            and value["no_backup_url"] is True
            and value["no_fallback"] is True
        ):
            raise ValueError
        value["assets"] = tuple(assets)
        return BilibiliMediaPlan(**value)  # type: ignore[arg-type]
    except (SourceContractError, TypeError, ValueError):
        raise BilibiliContractError("bilibili-media-plan-invalid") from None


def _discovery_input(root: Path, work_id: str) -> tuple[Path, str, Path]:
    try:
        order = load_work_order(root, work_id)
        if order.source_kind != "url":
            raise ValueError
        if WashEventLedger(root).status(work_id) != "source_registered":
            raise ValueError
        source_input = verify_private_relative(order.private_root, "source-input.txt")
        digest = sha256_file(source_input)
        if digest != order.source_input_sha256:
            raise ValueError
        lines = source_input.read_text(encoding="utf-8").splitlines()
        if len(lines) != 1:
            raise ValueError
        normalize_bilibili_video_url(lines[0])
        return source_input, digest, order.private_root
    except (
        BilibiliContractError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        raise BilibiliContractError("bilibili-discovery-input-invalid") from None


def plan_bilibili_discovery(root: Path, work_id: str) -> BilibiliDiscoveryPlan:
    """Publish one immutable, offline plan for a paid discovery request."""

    _, digest, private_root = _discovery_input(Path(root), work_id)
    plan = BilibiliDiscoveryPlan(
        schema_version=1,
        work_id=work_id,
        provider="tikhub-bilibili",
        action="bilibili-play-info-discovery",
        input_sha256=digest,
        api_origin=BILIBILI_API_ORIGIN,
        endpoint=DISCOVERY_ENDPOINT,
        method="GET",
        paid_api_request_count=1,
        media_asset_count=0,
        network_required=True,
        fee_possible=True,
        provider_credential_required=True,
        source_site_credentials=False,
        follow_redirects=False,
        timeout_seconds=30,
        no_retry=True,
        no_fallback=True,
    )
    try:
        target = verify_private_relative(private_root, DISCOVERY_PLAN_FILE)
        publish_json_exclusive(private_root, target, discovery_plan_value(plan))
    except SourceContractError:
        raise BilibiliContractError("bilibili-discovery-plan-unavailable") from None
    return plan


def verify_discovery_approval(
    root: Path,
    work_id: str,
    approval: Path,
) -> tuple[BilibiliDiscoveryPlan, Path, Path]:
    """Revalidate an unused discovery plan and its exact approval receipt."""

    try:
        source_input, digest, private_root = _discovery_input(Path(root), work_id)
        plan_path = verify_private_relative(private_root, DISCOVERY_PLAN_FILE)
        plan = load_discovery_plan(plan_path)
        if plan.work_id != work_id or plan.input_sha256 != digest:
            raise SourceContractError("approval-scope-mismatch")
        for relative in (
            DISCOVERY_EXECUTION_STARTED_FILE,
            DISCOVERY_RESPONSE_FILE,
            DISCOVERY_RESULT_FILE,
        ):
            artifact = verify_private_relative(private_root, relative)
            if artifact.exists() or artifact.is_symlink():
                raise SourceContractError("approval-scope-mismatch")
        verify_approval_payload(
            discovery_plan_value(plan),
            Path(approval),
            request_count=plan.paid_api_request_count,
        )
        if sha256_file(source_input) != plan.input_sha256:
            raise SourceContractError("approval-scope-mismatch")
        return plan, source_input, private_root
    except (
        BilibiliContractError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        raise BilibiliContractError("bilibili-discovery-not-approved") from None


__all__ = [
    "BILIBILI_API_ORIGIN",
    "BilibiliContractError",
    "BilibiliDiscoveryPlan",
    "BilibiliDiscoveryRecoveryPlan",
    "BilibiliPlayurlPlan",
    "BilibiliMediaPlan",
    "DISCOVERY_APPROVAL_FILE",
    "DISCOVERY_ENDPOINT",
    "DISCOVERY_EXECUTION_STARTED_FILE",
    "DISCOVERY_PLAN_FILE",
    "DISCOVERY_RESPONSE_FILE",
    "DISCOVERY_RESULT_FILE",
    "DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE",
    "DISCOVERY_RECOVERY_PLAN_FILE",
    "DISCOVERY_RECOVERY_RESULT_FILE",
    "PLAYURL_ENDPOINT",
    "PLAYURL_APPROVAL_FILE",
    "PLAYURL_EXECUTION_STARTED_FILE",
    "PLAYURL_PLAN_FILE",
    "PLAYURL_RESPONSE_FILE",
    "PLAYURL_RESULT_FILE",
    "MEDIA_APPROVAL_FILE",
    "MEDIA_DOWNLOAD_RESULT_FILE",
    "MEDIA_EXECUTION_STARTED_FILE",
    "MEDIA_PLAN_FILE",
    "discovery_plan_value",
    "discovery_recovery_plan_value",
    "load_discovery_plan",
    "load_discovery_recovery_plan",
    "load_playurl_plan",
    "load_media_plan",
    "media_plan_value",
    "normalize_bilibili_video_url",
    "plan_bilibili_discovery",
    "playurl_plan_value",
    "verify_discovery_approval",
]

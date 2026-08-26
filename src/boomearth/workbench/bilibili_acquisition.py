"""Separately approved orchestration for dedicated Bilibili acquisition."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping

from boomearth.config import Settings
from boomearth.providers.tikhub_bilibili import (
    TikHubBilibiliClient,
    TikHubBilibiliError,
)
from boomearth.providers.bilibili_media_download import (
    BilibiliMediaDownloadError,
    BilibiliMediaDownloader,
    DownloadedBilibiliAsset,
    MAX_MEDIA_BYTES,
    safe_media_url,
    sha256_host,
)
from boomearth.workbench.bilibili_contracts import (
    BilibiliContractError,
    DISCOVERY_APPROVAL_FILE,
    DISCOVERY_EXECUTION_STARTED_FILE,
    DISCOVERY_PLAN_FILE,
    DISCOVERY_RESPONSE_FILE,
    DISCOVERY_RESULT_FILE,
    DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE,
    DISCOVERY_RECOVERY_PLAN_FILE,
    DISCOVERY_RECOVERY_RESULT_FILE,
    BILIBILI_API_ORIGIN,
    PLAYURL_ENDPOINT,
    PLAYURL_EXECUTION_STARTED_FILE,
    PLAYURL_PLAN_FILE,
    PLAYURL_RESPONSE_FILE,
    PLAYURL_RESULT_FILE,
    MEDIA_DOWNLOAD_RESULT_FILE,
    MEDIA_EXECUTION_STARTED_FILE,
    MEDIA_PLAN_FILE,
    BilibiliMediaPlan,
    BilibiliDiscoveryRecoveryPlan,
    BilibiliPlayurlPlan,
    load_discovery_plan,
    load_discovery_recovery_plan,
    load_playurl_plan,
    load_media_plan,
    media_plan_value,
    discovery_recovery_plan_value,
    discovery_plan_value,
    playurl_plan_value,
    verify_discovery_approval,
)
from boomearth.workbench.bilibili_play_info import (
    BilibiliPlayInfoError,
    interpret_play_info,
    selection_value,
)
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


MEDIA_SELECTION_FILE = "bilibili-media-selection.json"
ClientFactory = Callable[[Settings], TikHubBilibiliClient]
DownloaderFactory = Callable[[], BilibiliMediaDownloader]
_DISCOVERY_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "action",
        "work_id",
        "plan_sha256",
        "response_sha256",
        "paid_api_requests",
        "next_action",
        "selection_sha256",
        "request_id_sha256",
    }
)
_DISCOVERY_RECOVERY_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "action",
        "work_id",
        "plan_sha256",
        "response_sha256",
        "original_result_sha256",
        "paid_api_requests",
        "next_action",
        "selection_sha256",
        "media_asset_count",
        "network_requests",
        "provider_credentials_read",
        "original_artifacts_unchanged",
    }
)
_DISCOVERY_EXECUTION_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "input_sha256",
        "plan_sha256",
        "action",
        "started_at",
    }
)


class BilibiliAcquisitionError(RuntimeError):
    """A fixed-message acquisition failure without private provider details."""

    def __repr__(self) -> str:
        return "BilibiliAcquisitionError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliDiscoveryResult:
    action: str
    work_id: str
    plan_sha256: str
    response_sha256: str | None
    paid_api_requests: int
    next_action: str
    selection_sha256: str | None
    request_id_sha256: str | None

    def __repr__(self) -> str:
        return "BilibiliDiscoveryResult(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliMediaDownloadResult:
    action: str
    work_id: str
    plan_sha256: str
    upstream_response_sha256: str
    selection_sha256: str
    status: str
    paid_api_requests: int
    media_assets_attempted: int
    media_assets_completed: int
    http_request_count: int
    redirect_hops: int
    assets: tuple[DownloadedBilibiliAsset, ...] = field(repr=False)

    def __repr__(self) -> str:
        return "BilibiliMediaDownloadResult(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliDiscoveryRecoveryResult:
    action: str
    work_id: str
    plan_sha256: str
    response_sha256: str
    original_result_sha256: str
    paid_api_requests: int
    next_action: str
    selection_sha256: str
    media_asset_count: int

    def __repr__(self) -> str:
        return "BilibiliDiscoveryRecoveryResult(<redacted>)"


def _result_value(result: BilibiliDiscoveryResult) -> dict[str, object]:
    return {
        "schema_version": 1,
        "action": result.action,
        "work_id": result.work_id,
        "plan_sha256": result.plan_sha256,
        "response_sha256": result.response_sha256,
        "paid_api_requests": result.paid_api_requests,
        "next_action": result.next_action,
        "selection_sha256": result.selection_sha256,
        "request_id_sha256": result.request_id_sha256,
    }


def _request_id_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _publish_failure(
    private_root: Path,
    *,
    work_id: str,
    plan_sha256: str,
    paid_api_requests: int,
) -> None:
    result = BilibiliDiscoveryResult(
        action="bilibili-play-info-discovery",
        work_id=work_id,
        plan_sha256=plan_sha256,
        response_sha256=None,
        paid_api_requests=paid_api_requests,
        next_action="discovery-failed",
        selection_sha256=None,
        request_id_sha256=None,
    )
    target = verify_private_relative(private_root, DISCOVERY_RESULT_FILE)
    publish_json_exclusive(private_root, target, _result_value(result))


def _failed_discovery_context(
    root: Path,
    work_id: str,
    *,
    expected_response_sha256: str,
    expected_failed_result_sha256: str,
    recovery_plan_expected: bool,
):
    try:
        order = load_work_order(root, work_id)
        if (
            order.source_kind != "url"
            or WashEventLedger(root).status(work_id) != "source_registered"
        ):
            raise ValueError
        private_root = order.private_root
        source_input = verify_private_relative(private_root, "source-input.txt")
        input_sha256 = sha256_file(source_input)
        if input_sha256 != order.source_input_sha256:
            raise ValueError
        discovery_plan_path = verify_private_relative(
            private_root, DISCOVERY_PLAN_FILE
        )
        discovery_plan = load_discovery_plan(discovery_plan_path)
        discovery_plan_sha256 = sha256_file(discovery_plan_path)
        if (
            discovery_plan.work_id != work_id
            or discovery_plan.input_sha256 != input_sha256
        ):
            raise ValueError
        approval_path = verify_private_relative(private_root, DISCOVERY_APPROVAL_FILE)
        execution_path = verify_private_relative(
            private_root, DISCOVERY_EXECUTION_STARTED_FILE
        )
        if not approval_path.is_file() or not execution_path.is_file():
            raise ValueError
        verify_approval_payload(
            discovery_plan_value(discovery_plan),
            approval_path,
            request_count=discovery_plan.paid_api_request_count,
        )
        execution = load_exact_json(execution_path, _DISCOVERY_EXECUTION_KEYS)
        if not (
            execution["schema_version"] == 1
            and execution["work_id"] == work_id
            and execution["input_sha256"] == input_sha256
            and execution["plan_sha256"] == discovery_plan_sha256
            and execution["action"] == discovery_plan.action
            and isinstance(execution["started_at"], str)
            and bool(execution["started_at"])
        ):
            raise ValueError
        response_path = verify_private_relative(private_root, DISCOVERY_RESPONSE_FILE)
        result_path = verify_private_relative(private_root, DISCOVERY_RESULT_FILE)
        if not response_path.is_file() or not result_path.is_file():
            raise ValueError
        if (
            sha256_file(response_path) != expected_response_sha256
            or sha256_file(result_path) != expected_failed_result_sha256
        ):
            raise ValueError
        failed_result = load_exact_json(result_path, _DISCOVERY_RESULT_KEYS)
        if not (
            failed_result["schema_version"] == 1
            and failed_result["action"] == "bilibili-play-info-discovery"
            and failed_result["work_id"] == work_id
            and failed_result["plan_sha256"] == discovery_plan_sha256
            and failed_result["response_sha256"] is None
            and type(failed_result["paid_api_requests"]) is int
            and failed_result["paid_api_requests"] == 1
            and failed_result["next_action"] == "discovery-failed"
            and failed_result["selection_sha256"] is None
            and failed_result["request_id_sha256"] is None
        ):
            raise ValueError
        selection_path = verify_private_relative(private_root, MEDIA_SELECTION_FILE)
        if selection_path.exists() or selection_path.is_symlink():
            raise ValueError
        recovery_plan_path = verify_private_relative(
            private_root, DISCOVERY_RECOVERY_PLAN_FILE
        )
        if recovery_plan_expected:
            recovery_plan = load_discovery_recovery_plan(recovery_plan_path)
            if not (
                recovery_plan.work_id == work_id
                and recovery_plan.input_sha256 == input_sha256
                and recovery_plan.discovery_plan_sha256 == discovery_plan_sha256
                and recovery_plan.response_sha256 == expected_response_sha256
                and recovery_plan.failed_result_sha256
                == expected_failed_result_sha256
            ):
                raise ValueError
        else:
            if recovery_plan_path.exists() or recovery_plan_path.is_symlink():
                raise ValueError
            recovery_plan = None
        for relative in (
            DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE,
            DISCOVERY_RECOVERY_RESULT_FILE,
            PLAYURL_PLAN_FILE,
            PLAYURL_EXECUTION_STARTED_FILE,
            PLAYURL_RESPONSE_FILE,
            PLAYURL_RESULT_FILE,
            MEDIA_PLAN_FILE,
            MEDIA_EXECUTION_STARTED_FILE,
            MEDIA_DOWNLOAD_RESULT_FILE,
        ):
            artifact = verify_private_relative(private_root, relative)
            if artifact.exists() or artifact.is_symlink():
                raise ValueError
        return (
            private_root,
            input_sha256,
            discovery_plan_sha256,
            response_path,
            result_path,
            recovery_plan_path,
            recovery_plan,
        )
    except (
        BilibiliContractError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        UnicodeError,
        TypeError,
        ValueError,
    ):
        raise BilibiliAcquisitionError(
            "bilibili-discovery-recovery-context-invalid"
        ) from None


def plan_bilibili_discovery_recovery(
    root: Path,
    work_id: str,
    *,
    expected_response_sha256: str,
    expected_failed_result_sha256: str,
) -> BilibiliDiscoveryRecoveryPlan:
    """Bind an immutable, zero-network repair to saved discovery artifacts."""

    try:
        (
            private_root,
            input_sha256,
            discovery_plan_sha256,
            _response_path,
            _result_path,
            recovery_plan_path,
            _existing,
        ) = _failed_discovery_context(
            Path(root),
            work_id,
            expected_response_sha256=expected_response_sha256,
            expected_failed_result_sha256=expected_failed_result_sha256,
            recovery_plan_expected=False,
        )
        plan = BilibiliDiscoveryRecoveryPlan(
            schema_version=1,
            work_id=work_id,
            provider="local-bilibili-recovery",
            action="bilibili-discovery-offline-recovery",
            input_sha256=input_sha256,
            discovery_plan_sha256=discovery_plan_sha256,
            response_sha256=expected_response_sha256,
            failed_result_sha256=expected_failed_result_sha256,
            network_required=False,
            provider_credential_required=False,
            paid_api_request_count=0,
            media_asset_count=0,
            no_key_read=True,
            no_retry=True,
            no_fallback=True,
            preserve_originals=True,
        )
        publish_json_exclusive(
            private_root, recovery_plan_path, discovery_recovery_plan_value(plan)
        )
        return plan
    except (BilibiliAcquisitionError, SourceContractError, OSError, ValueError):
        raise BilibiliAcquisitionError(
            "bilibili-discovery-recovery-plan-unavailable"
        ) from None


def _recovery_result_value(
    result: BilibiliDiscoveryRecoveryResult,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "action": result.action,
        "work_id": result.work_id,
        "plan_sha256": result.plan_sha256,
        "response_sha256": result.response_sha256,
        "original_result_sha256": result.original_result_sha256,
        "paid_api_requests": result.paid_api_requests,
        "next_action": result.next_action,
        "selection_sha256": result.selection_sha256,
        "media_asset_count": result.media_asset_count,
        "network_requests": 0,
        "provider_credentials_read": False,
        "original_artifacts_unchanged": True,
    }


def run_bilibili_discovery_recovery(
    root: Path, work_id: str
) -> BilibiliDiscoveryRecoveryResult:
    """Interpret one saved response offline without touching original history."""

    try:
        order = load_work_order(Path(root), work_id)
        recovery_plan_path = verify_private_relative(
            order.private_root, DISCOVERY_RECOVERY_PLAN_FILE
        )
        plan = load_discovery_recovery_plan(recovery_plan_path)
        (
            private_root,
            _input_sha256,
            _discovery_plan_sha256,
            response_path,
            original_result_path,
            _plan_path,
            _loaded_plan,
        ) = _failed_discovery_context(
            Path(root),
            work_id,
            expected_response_sha256=plan.response_sha256,
            expected_failed_result_sha256=plan.failed_result_sha256,
            recovery_plan_expected=True,
        )
        original_names = (
            DISCOVERY_PLAN_FILE,
            DISCOVERY_APPROVAL_FILE,
            DISCOVERY_EXECUTION_STARTED_FILE,
            DISCOVERY_RESPONSE_FILE,
            DISCOVERY_RESULT_FILE,
        )
        original_hashes = {
            name: sha256_file(verify_private_relative(private_root, name))
            for name in original_names
        }
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError
        interpretation = interpret_play_info(payload)
        if (
            interpretation.next_action != "media-download-ready"
            or interpretation.selection is None
        ):
            raise ValueError
        recovery_plan_sha256 = sha256_file(recovery_plan_path)
        marker_path = verify_private_relative(
            private_root, DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE
        )
        publish_json_exclusive(
            private_root,
            marker_path,
            {
                "schema_version": 1,
                "work_id": work_id,
                "action": plan.action,
                "plan_sha256": recovery_plan_sha256,
                "response_sha256": plan.response_sha256,
                "original_result_sha256": plan.failed_result_sha256,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        selection_path = verify_private_relative(private_root, MEDIA_SELECTION_FILE)
        selection_sha256 = publish_json_exclusive(
            private_root,
            selection_path,
            selection_value(interpretation.selection),
        )
        if any(
            sha256_file(verify_private_relative(private_root, name)) != digest
            for name, digest in original_hashes.items()
        ):
            raise ValueError
        result = BilibiliDiscoveryRecoveryResult(
            action=plan.action,
            work_id=work_id,
            plan_sha256=recovery_plan_sha256,
            response_sha256=plan.response_sha256,
            original_result_sha256=sha256_file(original_result_path),
            paid_api_requests=0,
            next_action="media-download-ready",
            selection_sha256=selection_sha256,
            media_asset_count=len(interpretation.selection.assets),
        )
        result_path = verify_private_relative(
            private_root, DISCOVERY_RECOVERY_RESULT_FILE
        )
        publish_json_exclusive(
            private_root, result_path, _recovery_result_value(result)
        )
        return result
    except (
        BilibiliAcquisitionError,
        BilibiliContractError,
        BilibiliPlayInfoError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise BilibiliAcquisitionError(
            "bilibili-discovery-recovery-unavailable"
        ) from None


def run_bilibili_discovery(
    root: Path,
    work_id: str,
    approval: Path,
    *,
    settings: Settings | None = None,
    client_factory: ClientFactory = TikHubBilibiliClient,
) -> BilibiliDiscoveryResult:
    """Execute exactly one approved play-info request and stop before media."""

    try:
        plan, source_input, private_root = verify_discovery_approval(
            Path(root), work_id, Path(approval)
        )
    except BilibiliContractError:
        raise BilibiliAcquisitionError("bilibili-discovery-not-approved") from None
    plan_path = verify_private_relative(private_root, DISCOVERY_PLAN_FILE)
    plan_sha256 = sha256_file(plan_path)
    marker = verify_private_relative(
        private_root, DISCOVERY_EXECUTION_STARTED_FILE
    )
    try:
        publish_json_exclusive(
            private_root,
            marker,
            {
                "schema_version": 1,
                "work_id": work_id,
                "input_sha256": plan.input_sha256,
                "plan_sha256": plan_sha256,
                "action": plan.action,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except SourceContractError:
        raise BilibiliAcquisitionError("bilibili-discovery-not-approved") from None

    paid_api_requests = 0
    client: TikHubBilibiliClient | None = None
    try:
        active_settings = settings if settings is not None else Settings.load(Path(root))
        source_lines = source_input.read_text(encoding="utf-8").splitlines()
        if len(source_lines) != 1 or not source_lines[0]:
            raise BilibiliAcquisitionError("bilibili-discovery-failed")
        client = client_factory(active_settings)
        paid_api_requests = 1
        response = client.fetch_play_info(source_lines[0])
        if sha256_file(source_input) != plan.input_sha256:
            raise BilibiliAcquisitionError("bilibili-discovery-failed")
        response_path = verify_private_relative(private_root, DISCOVERY_RESPONSE_FILE)
        response_sha256 = publish_json_exclusive(
            private_root, response_path, response.payload
        )
        interpretation = interpret_play_info(response.payload)
        selection_sha256: str | None = None
        if interpretation.selection is not None:
            selection_path = verify_private_relative(private_root, MEDIA_SELECTION_FILE)
            selection_sha256 = publish_json_exclusive(
                private_root,
                selection_path,
                selection_value(interpretation.selection),
            )
        result = BilibiliDiscoveryResult(
            action=plan.action,
            work_id=work_id,
            plan_sha256=plan_sha256,
            response_sha256=response_sha256,
            paid_api_requests=paid_api_requests,
            next_action=interpretation.next_action,
            selection_sha256=selection_sha256,
            request_id_sha256=_request_id_hash(response.request_id),
        )
        result_path = verify_private_relative(private_root, DISCOVERY_RESULT_FILE)
        publish_json_exclusive(private_root, result_path, _result_value(result))
        return result
    except (
        BilibiliAcquisitionError,
        BilibiliPlayInfoError,
        SourceContractError,
        TikHubBilibiliError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        try:
            _publish_failure(
                private_root,
                work_id=work_id,
                plan_sha256=plan_sha256,
                paid_api_requests=paid_api_requests,
            )
        except SourceContractError:
            pass
        raise BilibiliAcquisitionError("bilibili-discovery-failed") from None
    except Exception:
        try:
            _publish_failure(
                private_root,
                work_id=work_id,
                plan_sha256=plan_sha256,
                paid_api_requests=paid_api_requests,
            )
        except SourceContractError:
            pass
        raise BilibiliAcquisitionError("bilibili-discovery-failed") from None
    finally:
        if client is not None:
            client.close()


def _playurl_context(
    root: Path,
    work_id: str,
    *,
    plan_expected: bool,
) -> tuple[Path, Path, str, str, int, BilibiliPlayurlPlan | None]:
    try:
        order = load_work_order(root, work_id)
        if (
            order.source_kind != "url"
            or WashEventLedger(root).status(work_id) != "source_registered"
        ):
            raise ValueError
        private_root = order.private_root
        source_input = verify_private_relative(private_root, "source-input.txt")
        input_sha256 = sha256_file(source_input)
        if input_sha256 != order.source_input_sha256:
            raise ValueError
        discovery_plan_path = verify_private_relative(
            private_root, DISCOVERY_PLAN_FILE
        )
        discovery_plan = load_discovery_plan(discovery_plan_path)
        if (
            discovery_plan.work_id != work_id
            or discovery_plan.input_sha256 != input_sha256
        ):
            raise ValueError
        result_path = verify_private_relative(private_root, DISCOVERY_RESULT_FILE)
        result = load_exact_json(result_path, _DISCOVERY_RESULT_KEYS)
        response_sha256 = result["response_sha256"]
        if not (
            result["schema_version"] == 1
            and result["action"] == "bilibili-play-info-discovery"
            and result["work_id"] == work_id
            and result["plan_sha256"] == sha256_file(discovery_plan_path)
            and type(result["paid_api_requests"]) is int
            and result["paid_api_requests"] == 1
            and result["next_action"] == "playurl-required"
            and result["selection_sha256"] is None
            and isinstance(response_sha256, str)
        ):
            raise ValueError
        response_path = verify_private_relative(private_root, DISCOVERY_RESPONSE_FILE)
        if sha256_file(response_path) != response_sha256:
            raise ValueError
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError
        interpretation = interpret_play_info(payload)
        if (
            interpretation.next_action != "playurl-required"
            or interpretation.bv_id is None
            or interpretation.cid is None
        ):
            raise ValueError
        selection_path = verify_private_relative(private_root, MEDIA_SELECTION_FILE)
        if selection_path.exists() or selection_path.is_symlink():
            raise ValueError
        plan_path = verify_private_relative(private_root, PLAYURL_PLAN_FILE)
        if plan_expected:
            plan = load_playurl_plan(plan_path)
            if not (
                plan.work_id == work_id
                and plan.input_sha256 == input_sha256
                and plan.discovery_response_sha256 == response_sha256
                and plan.bv_id == interpretation.bv_id
                and plan.cid == interpretation.cid
            ):
                raise ValueError
        else:
            if plan_path.exists() or plan_path.is_symlink():
                raise ValueError
            plan = None
        for relative in (
            PLAYURL_EXECUTION_STARTED_FILE,
            PLAYURL_RESPONSE_FILE,
            PLAYURL_RESULT_FILE,
        ):
            artifact = verify_private_relative(private_root, relative)
            if artifact.exists() or artifact.is_symlink():
                raise ValueError
        return (
            private_root,
            source_input,
            input_sha256,
            response_sha256,
            interpretation.cid,
            plan,
        )
    except (
        BilibiliContractError,
        BilibiliPlayInfoError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise BilibiliAcquisitionError("bilibili-playurl-context-invalid") from None


def plan_bilibili_playurl(root: Path, work_id: str) -> BilibiliPlayurlPlan:
    """Publish an optional second paid request only after identifiers-only discovery."""

    try:
        (
            private_root,
            _source_input,
            input_sha256,
            response_sha256,
            cid,
            _existing,
        ) = _playurl_context(Path(root), work_id, plan_expected=False)
        response_path = verify_private_relative(private_root, DISCOVERY_RESPONSE_FILE)
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError
        interpretation = interpret_play_info(payload)
        if interpretation.bv_id is None:
            raise ValueError
        plan = BilibiliPlayurlPlan(
            schema_version=1,
            work_id=work_id,
            provider="tikhub-bilibili",
            action="bilibili-playurl-resolution",
            input_sha256=input_sha256,
            api_origin=BILIBILI_API_ORIGIN,
            endpoint=PLAYURL_ENDPOINT,
            method="GET",
            discovery_response_sha256=response_sha256,
            bv_id=interpretation.bv_id,
            cid=cid,
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
        target = verify_private_relative(private_root, PLAYURL_PLAN_FILE)
        publish_json_exclusive(private_root, target, playurl_plan_value(plan))
        return plan
    except (
        BilibiliAcquisitionError,
        BilibiliPlayInfoError,
        SourceContractError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise BilibiliAcquisitionError("bilibili-playurl-plan-unavailable") from None


def _approved_playurl(
    root: Path, work_id: str, approval: Path
) -> tuple[BilibiliPlayurlPlan, Path, Path]:
    try:
        private_root, source_input, _, _, _, plan = _playurl_context(
            root, work_id, plan_expected=True
        )
        if plan is None:
            raise ValueError
        verify_approval_payload(
            playurl_plan_value(plan),
            approval,
            request_count=plan.paid_api_request_count,
        )
        if sha256_file(source_input) != plan.input_sha256:
            raise ValueError
        return plan, source_input, private_root
    except (
        BilibiliAcquisitionError,
        BilibiliContractError,
        SourceContractError,
        OSError,
        ValueError,
    ):
        raise BilibiliAcquisitionError("bilibili-playurl-not-approved") from None


def run_bilibili_playurl(
    root: Path,
    work_id: str,
    approval: Path,
    *,
    settings: Settings | None = None,
    client_factory: ClientFactory = TikHubBilibiliClient,
) -> BilibiliDiscoveryResult:
    """Execute the separately approved playurl request once, never discovery."""

    plan, source_input, private_root = _approved_playurl(
        Path(root), work_id, Path(approval)
    )
    plan_path = verify_private_relative(private_root, PLAYURL_PLAN_FILE)
    plan_sha256 = sha256_file(plan_path)
    try:
        marker = verify_private_relative(
            private_root, PLAYURL_EXECUTION_STARTED_FILE
        )
        publish_json_exclusive(
            private_root,
            marker,
            {
                "schema_version": 1,
                "work_id": work_id,
                "input_sha256": plan.input_sha256,
                "plan_sha256": plan_sha256,
                "action": plan.action,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except SourceContractError:
        raise BilibiliAcquisitionError("bilibili-playurl-not-approved") from None
    paid_api_requests = 0
    client: TikHubBilibiliClient | None = None
    try:
        active_settings = settings if settings is not None else Settings.load(Path(root))
        client = client_factory(active_settings)
        paid_api_requests = 1
        response = client.fetch_playurl(plan.bv_id, plan.cid)
        if sha256_file(source_input) != plan.input_sha256:
            raise BilibiliAcquisitionError("bilibili-playurl-failed")
        response_path = verify_private_relative(private_root, PLAYURL_RESPONSE_FILE)
        response_sha256 = publish_json_exclusive(
            private_root, response_path, response.payload
        )
        interpretation = interpret_play_info(response.payload)
        selection_sha256: str | None = None
        if interpretation.selection is not None:
            selection_path = verify_private_relative(private_root, MEDIA_SELECTION_FILE)
            selection_sha256 = publish_json_exclusive(
                private_root,
                selection_path,
                selection_value(interpretation.selection),
            )
        result = BilibiliDiscoveryResult(
            action=plan.action,
            work_id=work_id,
            plan_sha256=plan_sha256,
            response_sha256=response_sha256,
            paid_api_requests=paid_api_requests,
            next_action=interpretation.next_action,
            selection_sha256=selection_sha256,
            request_id_sha256=_request_id_hash(response.request_id),
        )
        result_path = verify_private_relative(private_root, PLAYURL_RESULT_FILE)
        publish_json_exclusive(private_root, result_path, _result_value(result))
        return result
    except (
        BilibiliAcquisitionError,
        BilibiliPlayInfoError,
        SourceContractError,
        TikHubBilibiliError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        failure = BilibiliDiscoveryResult(
            action=plan.action,
            work_id=work_id,
            plan_sha256=plan_sha256,
            response_sha256=None,
            paid_api_requests=paid_api_requests,
            next_action="playurl-failed",
            selection_sha256=None,
            request_id_sha256=None,
        )
        try:
            result_path = verify_private_relative(private_root, PLAYURL_RESULT_FILE)
            publish_json_exclusive(
                private_root, result_path, _result_value(failure)
            )
        except SourceContractError:
            pass
        raise BilibiliAcquisitionError("bilibili-playurl-failed") from None
    finally:
        if client is not None:
            client.close()


def _media_context(
    root: Path,
    work_id: str,
    *,
    plan_expected: bool,
):
    try:
        order = load_work_order(root, work_id)
        if (
            order.source_kind != "url"
            or WashEventLedger(root).status(work_id) != "source_registered"
        ):
            raise ValueError
        private_root = order.private_root
        source_input = verify_private_relative(private_root, "source-input.txt")
        input_sha256 = sha256_file(source_input)
        if input_sha256 != order.source_input_sha256:
            raise ValueError
        selection_path = verify_private_relative(private_root, MEDIA_SELECTION_FILE)
        selection_sha256 = sha256_file(selection_path)
        selection_payload = json.loads(selection_path.read_text(encoding="utf-8"))
        if not isinstance(selection_payload, dict):
            raise ValueError
        candidates = (
            (
                DISCOVERY_RECOVERY_RESULT_FILE,
                DISCOVERY_RESPONSE_FILE,
                _DISCOVERY_RECOVERY_RESULT_KEYS,
            ),
            (DISCOVERY_RESULT_FILE, DISCOVERY_RESPONSE_FILE, _DISCOVERY_RESULT_KEYS),
            (PLAYURL_RESULT_FILE, PLAYURL_RESPONSE_FILE, _DISCOVERY_RESULT_KEYS),
        )
        upstream_response_sha256: str | None = None
        interpretation = None
        for result_name, response_name, result_keys in candidates:
            result_path = verify_private_relative(private_root, result_name)
            response_path = verify_private_relative(private_root, response_name)
            if not result_path.is_file() or not response_path.is_file():
                continue
            result_value = load_exact_json(result_path, result_keys)
            if not (
                result_value["work_id"] == work_id
                and result_value["next_action"] == "media-download-ready"
                and result_value["selection_sha256"] == selection_sha256
                and isinstance(result_value["response_sha256"], str)
                and sha256_file(response_path) == result_value["response_sha256"]
            ):
                continue
            if result_name == DISCOVERY_RECOVERY_RESULT_FILE:
                recovery_plan_path = verify_private_relative(
                    private_root, DISCOVERY_RECOVERY_PLAN_FILE
                )
                recovery_plan = load_discovery_recovery_plan(recovery_plan_path)
                original_result_path = verify_private_relative(
                    private_root, DISCOVERY_RESULT_FILE
                )
                if not (
                    result_value["action"]
                    == "bilibili-discovery-offline-recovery"
                    and result_value["plan_sha256"]
                    == sha256_file(recovery_plan_path)
                    and result_value["response_sha256"]
                    == recovery_plan.response_sha256
                    and result_value["original_result_sha256"]
                    == sha256_file(original_result_path)
                    and result_value["original_result_sha256"]
                    == recovery_plan.failed_result_sha256
                    and result_value["paid_api_requests"] == 0
                    and result_value["network_requests"] == 0
                    and result_value["provider_credentials_read"] is False
                    and result_value["original_artifacts_unchanged"] is True
                ):
                    continue
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError
            candidate = interpret_play_info(payload)
            if (
                candidate.selection is None
                or selection_value(candidate.selection) != selection_payload
            ):
                raise ValueError
            if (
                result_name == DISCOVERY_RECOVERY_RESULT_FILE
                and result_value["media_asset_count"]
                != len(candidate.selection.assets)
            ):
                raise ValueError
            interpretation = candidate
            upstream_response_sha256 = result_value["response_sha256"]
            break
        if interpretation is None or upstream_response_sha256 is None:
            raise ValueError
        plan_path = verify_private_relative(private_root, MEDIA_PLAN_FILE)
        if plan_expected:
            plan = load_media_plan(plan_path)
            if not (
                plan.work_id == work_id
                and plan.input_sha256 == input_sha256
                and plan.upstream_response_sha256 == upstream_response_sha256
                and plan.selection_sha256 == selection_sha256
                and plan.media_asset_count == len(interpretation.selection.assets)
            ):
                raise ValueError
        else:
            if plan_path.exists() or plan_path.is_symlink():
                raise ValueError
            plan = None
        for relative in (MEDIA_EXECUTION_STARTED_FILE, MEDIA_DOWNLOAD_RESULT_FILE):
            artifact = verify_private_relative(private_root, relative)
            if artifact.exists() or artifact.is_symlink():
                raise ValueError
        return (
            private_root,
            source_input,
            input_sha256,
            upstream_response_sha256,
            selection_sha256,
            interpretation.selection,
            plan,
        )
    except (
        BilibiliContractError,
        BilibiliPlayInfoError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise BilibiliAcquisitionError("bilibili-media-context-invalid") from None


def _media_asset_plan(asset) -> dict[str, object]:
    url = safe_media_url(asset.private_url)
    return {
        "label": asset.kind,
        "url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "initial_host_sha256": sha256_host(url),
        "expected_size": asset.size_bytes,
    }


def plan_bilibili_media(root: Path, work_id: str) -> BilibiliMediaPlan:
    """Publish an immutable zero-paid-API plan for selected media assets."""

    try:
        (
            private_root,
            _source_input,
            input_sha256,
            response_sha256,
            selection_sha256,
            selection,
            _existing,
        ) = _media_context(Path(root), work_id, plan_expected=False)
        assets = tuple(_media_asset_plan(asset) for asset in selection.assets)
        plan = BilibiliMediaPlan(
            schema_version=1,
            work_id=work_id,
            provider="tikhub-bilibili-media",
            action="bilibili-media-download",
            input_sha256=input_sha256,
            upstream_response_sha256=response_sha256,
            selection_sha256=selection_sha256,
            assets=assets,
            paid_api_request_count=0,
            media_asset_count=len(assets),
            max_bytes_per_asset=MAX_MEDIA_BYTES,
            max_total_bytes=2 * MAX_MEDIA_BYTES,
            connect_timeout_seconds=15,
            asset_timeout_seconds=900,
            max_redirect_hops_per_asset=3,
            follow_https_redirects=True,
            no_retry=True,
            no_backup_url=True,
            no_fallback=True,
        )
        target = verify_private_relative(private_root, MEDIA_PLAN_FILE)
        publish_json_exclusive(private_root, target, media_plan_value(plan))
        return plan
    except (
        BilibiliAcquisitionError,
        BilibiliMediaDownloadError,
        SourceContractError,
        OSError,
        ValueError,
    ):
        raise BilibiliAcquisitionError("bilibili-media-plan-unavailable") from None


def _approved_media(root: Path, work_id: str, approval: Path):
    try:
        context = _media_context(root, work_id, plan_expected=True)
        plan = context[-1]
        if not isinstance(plan, BilibiliMediaPlan):
            raise ValueError
        verify_approval_payload(
            media_plan_value(plan),
            approval,
            request_count=plan.media_asset_count,
        )
        return context
    except (
        BilibiliAcquisitionError,
        BilibiliContractError,
        SourceContractError,
        OSError,
        ValueError,
    ):
        raise BilibiliAcquisitionError("bilibili-media-not-approved") from None


def _download_result_value(result: BilibiliMediaDownloadResult) -> dict[str, object]:
    return {
        "schema_version": 1,
        "action": result.action,
        "work_id": result.work_id,
        "plan_sha256": result.plan_sha256,
        "upstream_response_sha256": result.upstream_response_sha256,
        "selection_sha256": result.selection_sha256,
        "status": result.status,
        "paid_api_requests": result.paid_api_requests,
        "media_assets_attempted": result.media_assets_attempted,
        "media_assets_completed": result.media_assets_completed,
        "http_request_count": result.http_request_count,
        "redirect_hops": result.redirect_hops,
        "assets": [
            {
                "kind": item.kind,
                "relative_path": item.artifact.relative_path,
                "sha256": item.artifact.sha256,
                "size_bytes": item.artifact.size_bytes,
                "http_request_count": item.http_request_count,
                "redirect_hops": item.redirect_hops,
                "final_host_sha256": item.final_host_sha256,
            }
            for item in result.assets
        ],
    }


def run_bilibili_media_download(
    root: Path,
    work_id: str,
    approval: Path,
    *,
    downloader_factory: DownloaderFactory = BilibiliMediaDownloader,
) -> BilibiliMediaDownloadResult:
    """Download each hash-bound selected asset once and stop before local QC."""

    (
        private_root,
        source_input,
        _input_sha256,
        response_sha256,
        selection_sha256,
        selection,
        plan,
    ) = _approved_media(Path(root), work_id, Path(approval))
    if not isinstance(plan, BilibiliMediaPlan):
        raise BilibiliAcquisitionError("bilibili-media-not-approved")
    plan_path = verify_private_relative(private_root, MEDIA_PLAN_FILE)
    plan_sha256 = sha256_file(plan_path)
    try:
        marker = verify_private_relative(private_root, MEDIA_EXECUTION_STARTED_FILE)
        publish_json_exclusive(
            private_root,
            marker,
            {
                "schema_version": 1,
                "work_id": work_id,
                "input_sha256": plan.input_sha256,
                "plan_sha256": plan_sha256,
                "action": plan.action,
                "media_asset_count": plan.media_asset_count,
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except SourceContractError:
        raise BilibiliAcquisitionError("bilibili-media-not-approved") from None
    downloader = None
    completed: list[DownloadedBilibiliAsset] = []
    attempted = 0
    http_requests = 0
    redirects = 0
    try:
        downloader = downloader_factory()
        output_dir = verify_private_relative(private_root, "source-acquisition")
        output_dir.mkdir(parents=False, exist_ok=False)
        names = {
            "progressive": "progressive.mp4",
            "video": "video.m4s",
            "audio": "audio.m4a",
        }
        for index, asset in enumerate(selection.assets):
            expected = plan.assets[index]
            if _media_asset_plan(asset) != expected:
                raise BilibiliAcquisitionError("bilibili-media-download-failed")
            attempted += 1
            target = verify_private_relative(
                private_root, f"source-acquisition/{names[asset.kind]}"
            )
            item = downloader.download(
                asset,
                target,
                max_redirect_hops=plan.max_redirect_hops_per_asset,
            )
            if (
                expected["expected_size"] is not None
                and item.artifact.size_bytes != expected["expected_size"]
            ):
                raise BilibiliAcquisitionError(
                    "bilibili-media-download-failed"
                )
            completed.append(item)
            http_requests += item.http_request_count
            redirects += item.redirect_hops
        if sha256_file(source_input) != plan.input_sha256:
            raise BilibiliAcquisitionError("bilibili-media-download-failed")
        result = BilibiliMediaDownloadResult(
            action=plan.action,
            work_id=work_id,
            plan_sha256=plan_sha256,
            upstream_response_sha256=response_sha256,
            selection_sha256=selection_sha256,
            status="succeeded",
            paid_api_requests=0,
            media_assets_attempted=attempted,
            media_assets_completed=len(completed),
            http_request_count=http_requests,
            redirect_hops=redirects,
            assets=tuple(completed),
        )
        result_path = verify_private_relative(private_root, MEDIA_DOWNLOAD_RESULT_FILE)
        publish_json_exclusive(
            private_root, result_path, _download_result_value(result)
        )
        return result
    except Exception:
        failure = BilibiliMediaDownloadResult(
            action=plan.action,
            work_id=work_id,
            plan_sha256=plan_sha256,
            upstream_response_sha256=response_sha256,
            selection_sha256=selection_sha256,
            status="failed",
            paid_api_requests=0,
            media_assets_attempted=attempted,
            media_assets_completed=len(completed),
            http_request_count=http_requests,
            redirect_hops=redirects,
            assets=tuple(completed),
        )
        try:
            result_path = verify_private_relative(private_root, MEDIA_DOWNLOAD_RESULT_FILE)
            publish_json_exclusive(
                private_root, result_path, _download_result_value(failure)
            )
        except SourceContractError:
            pass
        raise BilibiliAcquisitionError("bilibili-media-download-failed") from None
    finally:
        if downloader is not None:
            downloader.close()


__all__ = [
    "BilibiliAcquisitionError",
    "BilibiliDiscoveryRecoveryResult",
    "BilibiliDiscoveryResult",
    "BilibiliMediaDownloadResult",
    "MEDIA_SELECTION_FILE",
    "plan_bilibili_discovery_recovery",
    "plan_bilibili_playurl",
    "plan_bilibili_media",
    "run_bilibili_discovery",
    "run_bilibili_discovery_recovery",
    "run_bilibili_playurl",
    "run_bilibili_media_download",
]

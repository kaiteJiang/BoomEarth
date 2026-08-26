"""Plan and execute one explicitly approved private source acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from boomearth.config import Settings
from boomearth.media.source_snapshot import (
    SourceSnapshotError,
    snapshot_acquired_source,
)
from boomearth.providers.ytdlp import YtDlpError, YtDlpProvider
from boomearth.providers.tikhub import TikHubClient, TikHubError
from boomearth.providers.tikhub_download import (
    PrivateMediaDownloader,
    TikHubDownloadError,
)
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.source_artifacts import (
    ActionPlan,
    ArtifactRecord,
    SourceContractError,
    load_action_plan,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_approval,
    verify_approval_payload,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import SourceLedgerError, WashEventLedger


ProviderName = Literal["yt-dlp", "tikhub"]
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_PLAN_V2_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "request_count",
        "request_unit",
        "internal_http_request_limit",
        "network_required",
        "fee_possible",
        "follow_redirects",
        "redirect_policy",
        "timeout_seconds",
        "playlist_item_limit",
        "browser_cookies",
        "credentials",
        "no_retry",
        "no_fallback",
    }
)
_MEDIA_SUFFIXES = frozenset(
    {".wav", ".mp3", ".mp4", ".m4a", ".mov", ".mkv", ".webm", ".avi", ".ogg", ".flac"}
)


class SourceAcquisitionError(RuntimeError):
    """A fixed-message approved-acquisition failure."""

    def __repr__(self) -> str:
        return "SourceAcquisitionError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class SourceAcquisitionPlanV2:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    request_count: int
    request_unit: str
    internal_http_request_limit: None
    network_required: bool
    fee_possible: bool
    follow_redirects: bool
    redirect_policy: str
    timeout_seconds: int
    playlist_item_limit: int
    browser_cookies: bool
    credentials: bool
    no_retry: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "SourceAcquisitionPlanV2(<redacted>)"


def _plan_value(plan: ActionPlan) -> dict[str, object]:
    return {
        "action": plan.action,
        "fee_possible": plan.fee_possible,
        "input_sha256": plan.input_sha256,
        "network_required": plan.network_required,
        "no_fallback": plan.no_fallback,
        "no_retry": plan.no_retry,
        "provider": plan.provider,
        "request_count": plan.request_count,
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
    }


def _source_plan_value(plan: SourceAcquisitionPlanV2) -> dict[str, object]:
    return {
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
        "provider": plan.provider,
        "action": plan.action,
        "input_sha256": plan.input_sha256,
        "request_count": plan.request_count,
        "request_unit": plan.request_unit,
        "internal_http_request_limit": plan.internal_http_request_limit,
        "network_required": plan.network_required,
        "fee_possible": plan.fee_possible,
        "follow_redirects": plan.follow_redirects,
        "redirect_policy": plan.redirect_policy,
        "timeout_seconds": plan.timeout_seconds,
        "playlist_item_limit": plan.playlist_item_limit,
        "browser_cookies": plan.browser_cookies,
        "credentials": plan.credentials,
        "no_retry": plan.no_retry,
        "no_fallback": plan.no_fallback,
    }


def _load_source_acquisition_plan(path: Path) -> SourceAcquisitionPlanV2:
    try:
        value = load_exact_json(path, _SOURCE_PLAN_V2_KEYS)
        work_id = value["work_id"]
        if not isinstance(work_id, str):
            raise ValueError
        parsed_work_id = UUID(work_id)
        if not (
            parsed_work_id.version == 4
            and str(parsed_work_id) == work_id
            and type(value["schema_version"]) is int
            and value["schema_version"] == 2
            and value["provider"] == "yt-dlp"
            and value["action"] == "source-acquisition"
            and isinstance(value["input_sha256"], str)
            and _DIGEST_RE.fullmatch(value["input_sha256"]) is not None
            and type(value["request_count"]) is int
            and value["request_count"] == 1
            and value["request_unit"] == "provider-invocation"
            and value["internal_http_request_limit"] is None
            and value["network_required"] is True
            and value["fee_possible"] is False
            and value["follow_redirects"] is True
            and value["redirect_policy"]
            == "yt-dlp-extractor-and-media-cdn-managed"
            and type(value["timeout_seconds"]) is int
            and value["timeout_seconds"] == 300
            and type(value["playlist_item_limit"]) is int
            and value["playlist_item_limit"] == 1
            and value["browser_cookies"] is False
            and value["credentials"] is False
            and value["no_retry"] is True
            and value["no_fallback"] is True
        ):
            raise ValueError
        return SourceAcquisitionPlanV2(**value)  # type: ignore[arg-type]
    except (SourceContractError, TypeError, ValueError):
        raise SourceAcquisitionError("source-acquisition-plan-invalid") from None


def _url_input(root: Path, work_id: str) -> tuple[Path, str, Path]:
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
        return source_input, digest, order.private_root
    except (SourceContractError, SourceLedgerError, OSError, ValueError):
        raise SourceAcquisitionError("source-acquisition-input-invalid") from None


def plan_source_acquisition(
    root: Path, work_id: str, provider: ProviderName
) -> ActionPlan | SourceAcquisitionPlanV2:
    """Publish one immutable network action plan without starting a request."""

    if provider not in {"yt-dlp", "tikhub"}:
        raise SourceAcquisitionError("source-acquisition-provider-invalid")
    _, digest, private_root = _url_input(Path(root), work_id)
    if provider == "yt-dlp":
        plan: ActionPlan | SourceAcquisitionPlanV2 = SourceAcquisitionPlanV2(
            schema_version=2,
            work_id=work_id,
            provider=provider,
            action="source-acquisition",
            input_sha256=digest,
            request_count=1,
            request_unit="provider-invocation",
            internal_http_request_limit=None,
            network_required=True,
            fee_possible=False,
            follow_redirects=True,
            redirect_policy="yt-dlp-extractor-and-media-cdn-managed",
            timeout_seconds=300,
            playlist_item_limit=1,
            browser_cookies=False,
            credentials=False,
            no_retry=True,
            no_fallback=True,
        )
        plan_value = _source_plan_value(plan)
    else:
        plan = ActionPlan(
            schema_version=1,
            work_id=work_id,
            provider=provider,
            action="source-acquisition",
            input_sha256=digest,
            request_count=1,
            network_required=True,
            fee_possible=True,
            no_retry=True,
            no_fallback=True,
        )
        plan_value = _plan_value(plan)
    try:
        path = verify_private_relative(private_root, "source-acquisition-plan.json")
        publish_json_exclusive(private_root, path, plan_value)
        return plan
    except SourceContractError:
        raise SourceAcquisitionError("source-acquisition-plan-unavailable") from None


def _approved_plan(
    root: Path,
    work_id: str,
    provider: str,
    approval: Path,
) -> tuple[ActionPlan | SourceAcquisitionPlanV2, Path, Path]:
    try:
        private_root = WorkbenchPaths(root).private_source(work_id)
        plan_path = verify_private_relative(private_root, "source-acquisition-plan.json")
        if provider == "yt-dlp":
            plan = _load_source_acquisition_plan(plan_path)
        else:
            plan = load_action_plan(plan_path)
        if (
            plan.work_id != work_id
            or plan.provider != provider
            or plan.action != "source-acquisition"
            or plan.request_count != 1
            or plan.network_required is not True
            or plan.no_retry is not True
            or plan.no_fallback is not True
        ):
            raise SourceContractError("approval-scope-mismatch")
        if isinstance(plan, SourceAcquisitionPlanV2):
            verify_approval_payload(
                _source_plan_value(plan),
                approval,
                request_count=plan.request_count,
            )
        else:
            verify_approval(plan, approval)
        current = WashEventLedger(root).current(work_id)
        if current.stage != "source_registered" or current.source_kind != "url":
            raise SourceContractError("approval-scope-mismatch")
        source_input = verify_private_relative(private_root, "source-input.txt")
        digest = sha256_file(source_input)
    except (
        SourceAcquisitionError,
        SourceContractError,
        SourceLedgerError,
        OSError,
        ValueError,
    ):
        raise SourceAcquisitionError("source-acquisition-not-approved") from None
    if digest != plan.input_sha256:
        raise SourceAcquisitionError("source-acquisition-input-changed")
    return plan, source_input, private_root


def run_source_acquisition(
    root: Path,
    work_id: str,
    provider: str,
    approval: Path,
    *,
    settings: Settings | None = None,
) -> ArtifactRecord:
    """Execute the approved provider once; never retry or switch providers."""

    if provider not in {"yt-dlp", "tikhub"}:
        raise SourceAcquisitionError("source-acquisition-provider-invalid")
    plan, source_input, private_root = _approved_plan(
        Path(root), work_id, provider, Path(approval)
    )
    try:
        active_settings = settings if settings is not None else Settings.load(Path(root))
        output_dir = verify_private_relative(private_root, "source-acquisition")
        if provider == "yt-dlp":
            provider_client = YtDlpProvider(active_settings.yt_dlp_path)
            result = provider_client.download(source_input, output_dir)
            acquired_path = result.downloaded_path
        else:
            source_url = source_input.read_text(encoding="utf-8").splitlines()
            if len(source_url) != 1 or not source_url[0]:
                raise SourceAcquisitionError("source-acquisition-input-changed")
            resolver = TikHubClient(active_settings)
            try:
                media = resolver.resolve_authorized_url(source_url[0], authorized=True)
            finally:
                resolver.close()
            suffix = Path(urlsplit(media.private_media_url).path).suffix.lower()
            if suffix not in _MEDIA_SUFFIXES:
                suffix = ".mp4"
            output_dir.mkdir(parents=False, exist_ok=False)
            verify_private_relative(private_root, "source-acquisition")
            target = verify_private_relative(
                private_root, f"source-acquisition/download{suffix}"
            )
            result = PrivateMediaDownloader().download(
                media.private_media_url, target
            )
            acquired_path = result.path
        if sha256_file(source_input) != plan.input_sha256:
            raise SourceAcquisitionError("source-acquisition-input-changed")
        return snapshot_acquired_source(root, work_id, acquired_path)
    except SourceAcquisitionError:
        raise
    except (
        YtDlpError,
        TikHubError,
        TikHubDownloadError,
        SourceSnapshotError,
        SourceContractError,
        AttributeError,
        OSError,
        UnicodeError,
        ValueError,
    ):
        raise SourceAcquisitionError("source-acquisition-failed") from None
    except Exception:
        raise SourceAcquisitionError("source-acquisition-failed") from None


__all__ = [
    "SourceAcquisitionError",
    "plan_source_acquisition",
    "run_source_acquisition",
]

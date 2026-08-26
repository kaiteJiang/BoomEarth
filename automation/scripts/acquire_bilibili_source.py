"""Redacted work-ID-only CLI for dedicated Bilibili acquisition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.media.bilibili_source import (
    BilibiliSourceError,
    finalize_bilibili_source,
)
from boomearth.workbench.bilibili_acquisition import (
    BilibiliAcquisitionError,
    MEDIA_SELECTION_FILE,
    plan_bilibili_discovery_recovery,
    plan_bilibili_media,
    plan_bilibili_playurl,
    run_bilibili_discovery,
    run_bilibili_discovery_recovery,
    run_bilibili_media_download,
    run_bilibili_playurl,
)
from boomearth.workbench.bilibili_contracts import (
    BilibiliContractError,
    DISCOVERY_PLAN_FILE,
    DISCOVERY_RESULT_FILE,
    DISCOVERY_RECOVERY_PLAN_FILE,
    DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE,
    DISCOVERY_RECOVERY_RESULT_FILE,
    MEDIA_DOWNLOAD_RESULT_FILE,
    MEDIA_PLAN_FILE,
    PLAYURL_PLAN_FILE,
    load_discovery_plan,
    load_discovery_recovery_plan,
    load_media_plan,
    load_playurl_plan,
    plan_bilibili_discovery,
)
from boomearth.workbench.source_artifacts import sha256_file
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import SourceLedgerError, WashEventLedger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Acquire one authorized Bilibili work item")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan-discovery", "plan-playurl", "plan-media", "finalize", "status"):
        command = commands.add_parser(name)
        command.add_argument("work_id")
    recovery_plan = commands.add_parser("plan-recovery")
    recovery_plan.add_argument("work_id")
    recovery_plan.add_argument("--response-sha256", required=True)
    recovery_plan.add_argument("--failed-result-sha256", required=True)
    recovery_run = commands.add_parser("run-recovery")
    recovery_run.add_argument("work_id")
    for name in ("run-discovery", "run-playurl", "run-media"):
        command = commands.add_parser(name)
        command.add_argument("work_id")
        command.add_argument("--approval", required=True, type=Path)
    return parser


def _safe_work(work_id: str) -> str:
    return work_id[:12] if isinstance(work_id, str) else "invalid-work"


def _print(
    work_id: str,
    *,
    action: str,
    status: str,
    stage: str,
    paid: int = 0,
    media: int = 0,
) -> None:
    print(
        f"work={_safe_work(work_id)} action={action} status={status} "
        f"stage={stage} paid_api_count={paid} media_asset_count={media}"
    )


def _status(root: Path, work_id: str) -> str:
    order = load_work_order(root, work_id)
    stage = WashEventLedger(root).status(work_id)
    if stage in {"audio_ready", "media_ready"}:
        return stage
    private_root = order.private_root
    media_result = private_root / MEDIA_DOWNLOAD_RESULT_FILE
    media_plan = private_root / MEDIA_PLAN_FILE
    if media_result.is_file() and media_plan.is_file():
        plan = load_media_plan(media_plan)
        value = json.loads(media_result.read_text(encoding="utf-8"))
        if (
            isinstance(value, dict)
            and value.get("status") == "succeeded"
            and value.get("plan_sha256") == sha256_file(media_plan)
            and value.get("selection_sha256") == plan.selection_sha256
        ):
            return "media_downloaded"
    if media_plan.is_file():
        load_media_plan(media_plan)
        return "media_plan_ready"
    recovery_result = private_root / DISCOVERY_RECOVERY_RESULT_FILE
    recovery_plan = private_root / DISCOVERY_RECOVERY_PLAN_FILE
    if recovery_result.is_file():
        try:
            plan = load_discovery_recovery_plan(recovery_plan)
            selection_path = private_root / MEDIA_SELECTION_FILE
            value = json.loads(recovery_result.read_text(encoding="utf-8"))
            if (
                isinstance(value, dict)
                and value.get("action") == "bilibili-discovery-offline-recovery"
                and value.get("work_id") == work_id
                and value.get("plan_sha256") == sha256_file(recovery_plan)
                and value.get("response_sha256") == plan.response_sha256
                and value.get("original_result_sha256")
                == plan.failed_result_sha256
                and value.get("next_action") == "media-download-ready"
                and value.get("paid_api_requests") == 0
                and value.get("network_requests") == 0
                and value.get("provider_credentials_read") is False
                and value.get("original_artifacts_unchanged") is True
                and selection_path.is_file()
                and value.get("selection_sha256") == sha256_file(selection_path)
            ):
                return "media_plan_ready"
        except (BilibiliContractError, OSError, UnicodeError, ValueError):
            pass
        return "discovery_recovery_failed"
    recovery_marker = private_root / DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE
    if recovery_marker.exists() or recovery_marker.is_symlink():
        return "discovery_recovery_failed"
    if recovery_plan.is_file():
        load_discovery_recovery_plan(recovery_plan)
        return "discovery_recovery_ready"
    playurl_plan = private_root / PLAYURL_PLAN_FILE
    if playurl_plan.is_file():
        load_playurl_plan(playurl_plan)
        return "playurl_required"
    discovery_result = private_root / DISCOVERY_RESULT_FILE
    if discovery_result.is_file():
        value = json.loads(discovery_result.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            if value.get("next_action") == "discovery-failed":
                return "discovery_failed"
            if value.get("next_action") == "playurl-required":
                return "playurl_required"
            if value.get("next_action") == "media-download-ready":
                return "media_plan_ready"
    discovery_plan = private_root / DISCOVERY_PLAN_FILE
    if discovery_plan.is_file():
        load_discovery_plan(discovery_plan)
        return "discovery_ready"
    return stage


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = SCRIPT_ROOT if root is None else Path(root)
    work_id = args.work_id
    try:
        if args.command == "plan-discovery":
            plan_bilibili_discovery(workspace, work_id)
            _print(work_id, action="bilibili-discovery", status="planned", stage="discovery_ready", paid=1)
        elif args.command == "run-discovery":
            result = run_bilibili_discovery(workspace, work_id, args.approval)
            _print(work_id, action="bilibili-discovery", status="completed", stage=result.next_action, paid=result.paid_api_requests)
        elif args.command == "plan-recovery":
            plan_bilibili_discovery_recovery(
                workspace,
                work_id,
                expected_response_sha256=args.response_sha256,
                expected_failed_result_sha256=args.failed_result_sha256,
            )
            _print(work_id, action="bilibili-recovery", status="planned", stage="discovery_recovery_ready")
        elif args.command == "run-recovery":
            result = run_bilibili_discovery_recovery(workspace, work_id)
            _print(work_id, action="bilibili-recovery", status="completed", stage="media_plan_ready", media=result.media_asset_count)
        elif args.command == "plan-playurl":
            plan_bilibili_playurl(workspace, work_id)
            _print(work_id, action="bilibili-playurl", status="planned", stage="playurl_required", paid=1)
        elif args.command == "run-playurl":
            result = run_bilibili_playurl(workspace, work_id, args.approval)
            _print(work_id, action="bilibili-playurl", status="completed", stage=result.next_action, paid=result.paid_api_requests)
        elif args.command == "plan-media":
            plan = plan_bilibili_media(workspace, work_id)
            _print(work_id, action="bilibili-media", status="planned", stage="media_plan_ready", media=plan.media_asset_count)
        elif args.command == "run-media":
            result = run_bilibili_media_download(workspace, work_id, args.approval)
            _print(work_id, action="bilibili-media", status=result.status, stage="media_downloaded", media=result.media_assets_completed)
        elif args.command == "finalize":
            finalize_bilibili_source(workspace, work_id)
            _print(work_id, action="bilibili-finalize", status="completed", stage="audio_ready")
        else:
            stage = _status(workspace, work_id)
            _print(work_id, action="bilibili-status", status="ok", stage=stage)
        return 0
    except (
        BilibiliAcquisitionError,
        BilibiliContractError,
        BilibiliSourceError,
        SourceLedgerError,
        OSError,
        UnicodeError,
        ValueError,
    ) as error:
        if isinstance(
            error,
            (
                BilibiliAcquisitionError,
                BilibiliContractError,
                BilibiliSourceError,
                SourceLedgerError,
            ),
        ):
            code = str(error)
        else:
            code = "bilibili-command-failed"
        if not code or any(character.isspace() for character in code):
            code = "bilibili-command-failed"
        print(f"error={code}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

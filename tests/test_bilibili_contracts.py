from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.workbench.bilibili_contracts import (
    BilibiliContractError,
    DISCOVERY_EXECUTION_STARTED_FILE,
    DISCOVERY_PLAN_FILE,
    load_discovery_plan,
    normalize_bilibili_video_url,
    plan_bilibili_discovery,
    verify_discovery_approval,
)
from boomearth.workbench.source_artifacts import (
    canonical_json_bytes,
    publish_json_exclusive,
    sha256_file,
)
from boomearth.workbench.source_intake import create_url_intake


WORK_ID = "00000000-0000-4000-8000-000000000014"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 16, 6, 7, 8, tzinfo=timezone.utc)
SYNTHETIC_URL = "https://www.bilibili.com/video/BV1ab411c7De?t=473.1"


def _work_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def _bilibili_url_order(root: Path, *, url: str = SYNTHETIC_URL) -> None:
    source = root / "private-url.txt"
    source.write_text(url + "\n", encoding="utf-8")
    create_url_intake(
        root,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )


def _planned_discovery(root: Path) -> Path:
    _bilibili_url_order(root)
    plan_bilibili_discovery(root, WORK_ID)
    return _work_root(root) / DISCOVERY_PLAN_FILE


def _discovery_approval(root: Path) -> Path:
    plan_path = _work_root(root) / DISCOVERY_PLAN_FILE
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    receipt = {
        "action": plan["action"],
        "approved": True,
        "input_sha256": plan["input_sha256"],
        "no_fallback": True,
        "no_retry": True,
        "plan_sha256": sha256_file(plan_path),
        "provider": plan["provider"],
        "request_count": 1,
        "work_id": WORK_ID,
    }
    approval = _work_root(root) / "tikhub-bilibili-discovery-approval.json"
    publish_json_exclusive(_work_root(root), approval, receipt)
    return approval


def test_discovery_plan_binds_exact_private_input_and_network_policy(
    tmp_path: Path,
) -> None:
    _bilibili_url_order(tmp_path)

    plan = plan_bilibili_discovery(tmp_path, WORK_ID)

    plan_path = _work_root(tmp_path) / DISCOVERY_PLAN_FILE
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    assert value == {
        "action": "bilibili-play-info-discovery",
        "api_origin": "https://api.tikhub.io",
        "endpoint": "/api/v1/bilibili/web/fetch_video_play_info",
        "fee_possible": True,
        "follow_redirects": False,
        "input_sha256": sha256_file(_work_root(tmp_path) / "source-input.txt"),
        "media_asset_count": 0,
        "method": "GET",
        "network_required": True,
        "no_fallback": True,
        "no_retry": True,
        "paid_api_request_count": 1,
        "provider": "tikhub-bilibili",
        "provider_credential_required": True,
        "schema_version": 1,
        "source_site_credentials": False,
        "timeout_seconds": 30,
        "work_id": WORK_ID,
    }
    assert canonical_json_bytes(value) == plan_path.read_bytes()
    assert "BV1" not in repr(plan)


@pytest.mark.parametrize(
    "url",
    [
        "https://bilibili.com/video/BV1ab411c7De",
        "https://www.bilibili.com/video/BV1ab411c7De/",
        "https://www.bilibili.com/video/BV1ab411c7De?t=0",
        "https://www.bilibili.com/video/BV1ab411c7De?p=1",
        "https://www.bilibili.com/video/BV1ab411c7De?p=2&t=473.1",
        "https://www.bilibili.com:443/video/BV1ab411c7De",
    ],
)
def test_direct_bilibili_video_url_is_preserved_byte_for_byte(url: str) -> None:
    assert normalize_bilibili_video_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "HTTPS://www.bilibili.com/video/BV1ab411c7De",
        "http://www.bilibili.com/video/BV1ab411c7De",
        "https://user:pass@www.bilibili.com/video/BV1ab411c7De",
        "https://www.bilibili.com/video/BV1ab411c7De#fragment",
        "https://b23.tv/BV1ab411c7De",
        "https://www.bilibili.com/list/watchlater",
        "https://www.bilibili.com/medialist/play/1",
        "https://www.bilibili.com/video/BV1ab411c7De?p=1&p=2",
        "https://www.bilibili.com/video/BV1ab411c7De?spm_id_from=333.1",
        "https://www.bilibili.com/video/BV1ab411c7De?p=0",
        "https://www.bilibili.com/video/BV1ab411c7De?t=-1",
        "https://www.bilibili.com/video/BV1ab411c7De?t=nan",
        "https://www.bilibili.com:444/video/BV1ab411c7De",
        " https://www.bilibili.com/video/BV1ab411c7De",
        "https://www.bilibili.com/video/BV1ab411c7De\n",
    ],
)
def test_noncanonical_or_out_of_scope_bilibili_url_is_rejected(url: str) -> None:
    with pytest.raises(
        BilibiliContractError,
        match="^bilibili-source-url-invalid$",
        ):
        normalize_bilibili_video_url(url)


def test_discovery_plan_loads_only_the_exact_approved_contract(
    tmp_path: Path,
) -> None:
    path = _planned_discovery(tmp_path)

    plan = load_discovery_plan(path)

    assert plan.work_id == WORK_ID
    assert plan.paid_api_request_count == 1
    assert plan.media_asset_count == 0
    assert plan.follow_redirects is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("work_id", "not-a-uuid"),
        ("provider", "tikhub"),
        ("action", "source-acquisition"),
        ("input_sha256", "0" * 63),
        ("api_origin", "https://example.invalid"),
        ("endpoint", "/api/v1/hybrid/video_data"),
        ("method", "POST"),
        ("paid_api_request_count", 2),
        ("paid_api_request_count", True),
        ("media_asset_count", 1),
        ("network_required", False),
        ("fee_possible", False),
        ("provider_credential_required", False),
        ("source_site_credentials", True),
        ("follow_redirects", True),
        ("timeout_seconds", 31),
        ("timeout_seconds", True),
        ("no_retry", False),
        ("no_fallback", False),
    ],
)
def test_discovery_plan_rejects_policy_mutation(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = _planned_discovery(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        BilibiliContractError,
        match="^bilibili-discovery-plan-invalid$",
    ):
        load_discovery_plan(path)


@pytest.mark.parametrize("change", ["missing", "unknown"])
def test_discovery_plan_rejects_key_set_mutation(
    tmp_path: Path,
    change: str,
) -> None:
    path = _planned_discovery(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if change == "missing":
        del payload["timeout_seconds"]
    else:
        payload["unexpected"] = False
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        BilibiliContractError,
        match="^bilibili-discovery-plan-invalid$",
    ):
        load_discovery_plan(path)


def test_discovery_approval_revalidates_plan_input_and_empty_execution_state(
    tmp_path: Path,
) -> None:
    _planned_discovery(tmp_path)
    approval = _discovery_approval(tmp_path)

    plan, source_input, private_root = verify_discovery_approval(
        tmp_path,
        WORK_ID,
        approval,
    )

    assert plan.work_id == WORK_ID
    assert source_input == _work_root(tmp_path) / "source-input.txt"
    assert private_root == _work_root(tmp_path)


def test_discovery_approval_rejects_plan_hash_mismatch(tmp_path: Path) -> None:
    _planned_discovery(tmp_path)
    approval = _discovery_approval(tmp_path)
    value = json.loads(approval.read_text(encoding="utf-8"))
    value["plan_sha256"] = "0" * 64
    approval.write_bytes(canonical_json_bytes(value))

    with pytest.raises(
        BilibiliContractError,
        match="^bilibili-discovery-not-approved$",
    ):
        verify_discovery_approval(tmp_path, WORK_ID, approval)


def test_discovery_approval_rejects_private_input_drift(tmp_path: Path) -> None:
    _planned_discovery(tmp_path)
    approval = _discovery_approval(tmp_path)
    (_work_root(tmp_path) / "source-input.txt").write_text(
        "https://www.bilibili.com/video/BV1xy411c7Fg\n",
        encoding="utf-8",
    )

    with pytest.raises(
        BilibiliContractError,
        match="^bilibili-discovery-not-approved$",
    ):
        verify_discovery_approval(tmp_path, WORK_ID, approval)


def test_discovery_approval_rejects_existing_execution_start(tmp_path: Path) -> None:
    _planned_discovery(tmp_path)
    approval = _discovery_approval(tmp_path)
    marker = _work_root(tmp_path) / DISCOVERY_EXECUTION_STARTED_FILE
    publish_json_exclusive(_work_root(tmp_path), marker, {"schema_version": 1})

    with pytest.raises(
        BilibiliContractError,
        match="^bilibili-discovery-not-approved$",
    ):
        verify_discovery_approval(tmp_path, WORK_ID, approval)

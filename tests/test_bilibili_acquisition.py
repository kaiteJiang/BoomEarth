from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest

from boomearth.providers.tikhub_bilibili import (
    TikHubBilibiliResponse,
    TikHubBilibiliTimeoutError,
)
from boomearth.providers.bilibili_media_download import DownloadedBilibiliAsset
from boomearth.workbench.bilibili_acquisition import (
    BilibiliAcquisitionError,
    plan_bilibili_discovery_recovery,
    plan_bilibili_media,
    plan_bilibili_playurl,
    run_bilibili_discovery_recovery,
    run_bilibili_discovery,
    run_bilibili_media_download,
    run_bilibili_playurl,
)
from boomearth.workbench.bilibili_contracts import (
    BilibiliContractError,
    DISCOVERY_EXECUTION_STARTED_FILE,
    DISCOVERY_PLAN_FILE,
    DISCOVERY_RESPONSE_FILE,
    DISCOVERY_RESULT_FILE,
    DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE,
    DISCOVERY_RECOVERY_PLAN_FILE,
    DISCOVERY_RECOVERY_RESULT_FILE,
    PLAYURL_EXECUTION_STARTED_FILE,
    PLAYURL_PLAN_FILE,
    PLAYURL_RESPONSE_FILE,
    PLAYURL_RESULT_FILE,
    MEDIA_DOWNLOAD_RESULT_FILE,
    MEDIA_EXECUTION_STARTED_FILE,
    MEDIA_PLAN_FILE,
    load_discovery_recovery_plan,
    load_playurl_plan,
    plan_bilibili_discovery,
)
from boomearth.workbench.source_artifacts import publish_json_exclusive, sha256_file
from boomearth.workbench.source_artifacts import ArtifactRecord
from boomearth.workbench.source_intake import create_url_intake
from boomearth.workbench.source_ledger import WashEventLedger


WORK_ID = "00000000-0000-4000-8000-000000000015"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 16, 7, 8, 9, tzinfo=timezone.utc)
SYNTHETIC_URL = "https://www.bilibili.com/video/BV1ab411c7De?t=473.1"
FIXTURES = Path(__file__).parent / "fixtures"


def _work_root(root: Path) -> Path:
    return root / "01-内容生产" / "视频工作台" / ".internal" / "洗稿" / WORK_ID


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _planned_and_approved(root: Path) -> Path:
    source = root / "private-url.txt"
    source.write_text(SYNTHETIC_URL + "\n", encoding="utf-8")
    create_url_intake(
        root,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    plan_bilibili_discovery(root, WORK_ID)
    plan_path = _work_root(root) / DISCOVERY_PLAN_FILE
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    approval = _work_root(root) / "tikhub-bilibili-discovery-approval.json"
    publish_json_exclusive(
        _work_root(root),
        approval,
        {
            "action": plan["action"],
            "approved": True,
            "input_sha256": plan["input_sha256"],
            "no_fallback": True,
            "no_retry": True,
            "plan_sha256": sha256_file(plan_path),
            "provider": plan["provider"],
            "request_count": 1,
            "work_id": WORK_ID,
        },
    )
    return approval


class _FakeClient:
    def __init__(
        self,
        root: Path,
        payload: dict[str, object],
        calls: dict[str, int],
        *,
        failure: Exception | None = None,
    ) -> None:
        assert (_work_root(root) / DISCOVERY_EXECUTION_STARTED_FILE).is_file()
        calls["construct"] += 1
        self._payload = payload
        self._calls = calls
        self._failure = failure

    def fetch_play_info(self, url: str) -> TikHubBilibiliResponse:
        self._calls["play_info"] += 1
        assert url == SYNTHETIC_URL
        if self._failure is not None:
            raise self._failure
        return TikHubBilibiliResponse(
            payload=self._payload,
            http_status=200,
            request_id=str(self._payload.get("request_id")),
        )

    def fetch_playurl(self, bv_id: str, cid: int) -> TikHubBilibiliResponse:
        self._calls["playurl"] += 1
        raise AssertionError("playurl must be a separately approved action")

    def close(self) -> None:
        self._calls["close"] += 1


def _factory(
    root: Path,
    payload: dict[str, object],
    calls: dict[str, int],
    *,
    failure: Exception | None = None,
):
    def build(_settings):
        return _FakeClient(root, payload, calls, failure=failure)

    return build


def _calls() -> dict[str, int]:
    return {"construct": 0, "play_info": 0, "playurl": 0, "close": 0}


def _offline_failed_saved_discovery(root: Path) -> tuple[str, str]:
    _planned_and_approved(root)
    private_root = _work_root(root)
    plan_path = private_root / DISCOVERY_PLAN_FILE
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    publish_json_exclusive(
        private_root,
        private_root / DISCOVERY_EXECUTION_STARTED_FILE,
        {
            "schema_version": 1,
            "work_id": WORK_ID,
            "input_sha256": plan["input_sha256"],
            "plan_sha256": sha256_file(plan_path),
            "action": plan["action"],
            "started_at": FIXED_NOW.isoformat(),
        },
    )
    response_sha256 = publish_json_exclusive(
        private_root,
        private_root / DISCOVERY_RESPONSE_FILE,
        _fixture("tikhub_bilibili_play_info_real_schema.json"),
    )
    publish_json_exclusive(
        private_root,
        private_root / DISCOVERY_RESULT_FILE,
        {
            "schema_version": 1,
            "action": "bilibili-play-info-discovery",
            "work_id": WORK_ID,
            "plan_sha256": sha256_file(plan_path),
            "response_sha256": None,
            "paid_api_requests": 1,
            "next_action": "discovery-failed",
            "selection_sha256": None,
            "request_id_sha256": None,
        },
    )
    return response_sha256, sha256_file(private_root / DISCOVERY_RESULT_FILE)


def test_discovery_calls_once_persists_response_and_stops_before_media(
    tmp_path: Path,
) -> None:
    approval = _planned_and_approved(tmp_path)
    calls = _calls()
    payload = _fixture("tikhub_bilibili_play_info_dash.json")

    result = run_bilibili_discovery(
        tmp_path,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=_factory(tmp_path, payload, calls),
    )

    assert calls == {"construct": 1, "play_info": 1, "playurl": 0, "close": 1}
    assert result.next_action == "media-download-ready"
    assert result.paid_api_requests == 1
    response_path = _work_root(tmp_path) / DISCOVERY_RESPONSE_FILE
    assert json.loads(response_path.read_text(encoding="utf-8")) == payload
    assert result.response_sha256 == sha256_file(response_path)
    selection_path = _work_root(tmp_path) / "bilibili-media-selection.json"
    assert selection_path.is_file()
    assert result.selection_sha256 == sha256_file(selection_path)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"
    serialized = (_work_root(tmp_path) / DISCOVERY_RESULT_FILE).read_text(
        encoding="utf-8"
    )
    assert SYNTHETIC_URL not in serialized
    assert "BV1ab411c7De" not in serialized
    assert "synthetic-request-dash" not in serialized
    assert result.request_id_sha256 == hashlib.sha256(
        b"synthetic-request-dash"
    ).hexdigest()


def test_identifiers_only_stops_for_separate_playurl_approval(tmp_path: Path) -> None:
    approval = _planned_and_approved(tmp_path)
    calls = _calls()

    result = run_bilibili_discovery(
        tmp_path,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=_factory(
            tmp_path,
            _fixture("tikhub_bilibili_identifiers_only.json"),
            calls,
        ),
    )

    assert result.next_action == "playurl-required"
    assert calls["playurl"] == 0
    assert not (_work_root(tmp_path) / "bilibili-media-selection.json").exists()


def test_unknown_success_response_is_saved_then_stops_for_offline_review(
    tmp_path: Path,
) -> None:
    approval = _planned_and_approved(tmp_path)
    calls = _calls()
    payload = {"code": 200, "request_id": "synthetic-unknown", "data": None}

    result = run_bilibili_discovery(
        tmp_path,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=_factory(tmp_path, payload, calls),
    )

    assert result.next_action == "offline-schema-review"
    assert (_work_root(tmp_path) / DISCOVERY_RESPONSE_FILE).is_file()
    assert not (_work_root(tmp_path) / "bilibili-media-selection.json").exists()


def test_discovery_failure_is_locked_recorded_and_never_retried(tmp_path: Path) -> None:
    approval = _planned_and_approved(tmp_path)
    calls = _calls()
    factory = _factory(
        tmp_path,
        {},
        calls,
        failure=TikHubBilibiliTimeoutError(),
    )

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-discovery-failed$"):
        run_bilibili_discovery(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(tikhub_api_key="synthetic"),
            client_factory=factory,
        )

    assert calls == {"construct": 1, "play_info": 1, "playurl": 0, "close": 1}
    assert (_work_root(tmp_path) / DISCOVERY_EXECUTION_STARTED_FILE).is_file()
    assert not (_work_root(tmp_path) / DISCOVERY_RESPONSE_FILE).exists()
    failure = json.loads(
        (_work_root(tmp_path) / DISCOVERY_RESULT_FILE).read_text(encoding="utf-8")
    )
    assert failure["next_action"] == "discovery-failed"
    assert failure["paid_api_requests"] == 1
    assert failure["response_sha256"] is None
    assert "Timeout" not in json.dumps(failure)

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-discovery-not-approved$"):
        run_bilibili_discovery(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(tikhub_api_key="synthetic"),
            client_factory=factory,
        )
    assert calls["play_info"] == 1


def test_discovery_refuses_same_approval_after_success(tmp_path: Path) -> None:
    approval = _planned_and_approved(tmp_path)
    calls = _calls()
    factory = _factory(
        tmp_path,
        _fixture("tikhub_bilibili_play_info_dash.json"),
        calls,
    )
    run_bilibili_discovery(
        tmp_path,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=factory,
    )

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-discovery-not-approved$"):
        run_bilibili_discovery(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(tikhub_api_key="synthetic"),
            client_factory=factory,
        )

    assert calls["play_info"] == 1


def _run_identifier_discovery(root: Path) -> None:
    approval = _planned_and_approved(root)
    calls = _calls()
    run_bilibili_discovery(
        root,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=_factory(
            root,
            _fixture("tikhub_bilibili_identifiers_only.json"),
            calls,
        ),
    )


def _playurl_approval(root: Path) -> Path:
    plan_path = _work_root(root) / PLAYURL_PLAN_FILE
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    approval = _work_root(root) / "tikhub-bilibili-playurl-approval.json"
    publish_json_exclusive(
        _work_root(root),
        approval,
        {
            "action": plan["action"],
            "approved": True,
            "input_sha256": plan["input_sha256"],
            "no_fallback": True,
            "no_retry": True,
            "plan_sha256": sha256_file(plan_path),
            "provider": plan["provider"],
            "request_count": 1,
            "work_id": WORK_ID,
        },
    )
    return approval


def test_playurl_plan_is_offline_exact_and_hash_binds_discovery(tmp_path: Path) -> None:
    _run_identifier_discovery(tmp_path)

    plan = plan_bilibili_playurl(tmp_path, WORK_ID)

    plan_path = _work_root(tmp_path) / PLAYURL_PLAN_FILE
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    assert value == {
        "action": "bilibili-playurl-resolution",
        "api_origin": "https://api.tikhub.io",
        "bv_id": "BV1ab411c7De",
        "cid": 123456789,
        "discovery_response_sha256": sha256_file(
            _work_root(tmp_path) / DISCOVERY_RESPONSE_FILE
        ),
        "endpoint": "/api/v1/bilibili/web/fetch_video_playurl",
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
    assert load_playurl_plan(plan_path) == plan
    assert "BV1" not in repr(plan)


def test_playurl_plan_is_illegal_when_discovery_already_has_streams(
    tmp_path: Path,
) -> None:
    approval = _planned_and_approved(tmp_path)
    calls = _calls()
    run_bilibili_discovery(
        tmp_path,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=_factory(
            tmp_path,
            _fixture("tikhub_bilibili_play_info_dash.json"),
            calls,
        ),
    )

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-playurl-plan-unavailable$"):
        plan_bilibili_playurl(tmp_path, WORK_ID)


class _FakePlayurlClient:
    def __init__(self, root: Path, calls: dict[str, int]) -> None:
        assert (_work_root(root) / PLAYURL_EXECUTION_STARTED_FILE).is_file()
        calls["construct"] += 1
        self._calls = calls

    def fetch_play_info(self, url: str) -> TikHubBilibiliResponse:
        self._calls["play_info"] += 1
        raise AssertionError("discovery must not be repeated")

    def fetch_playurl(self, bv_id: str, cid: int) -> TikHubBilibiliResponse:
        self._calls["playurl"] += 1
        assert bv_id == "BV1ab411c7De"
        assert cid == 123456789
        payload = _fixture("tikhub_bilibili_play_info_progressive.json")
        return TikHubBilibiliResponse(payload, 200, "synthetic-playurl")

    def close(self) -> None:
        self._calls["close"] += 1


def test_playurl_calls_once_and_publishes_media_selection(tmp_path: Path) -> None:
    _run_identifier_discovery(tmp_path)
    plan_bilibili_playurl(tmp_path, WORK_ID)
    approval = _playurl_approval(tmp_path)
    calls = _calls()

    result = run_bilibili_playurl(
        tmp_path,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=lambda _settings: _FakePlayurlClient(tmp_path, calls),
    )

    assert calls == {"construct": 1, "play_info": 0, "playurl": 1, "close": 1}
    assert result.next_action == "media-download-ready"
    assert result.paid_api_requests == 1
    assert (_work_root(tmp_path) / PLAYURL_RESPONSE_FILE).is_file()
    assert (_work_root(tmp_path) / PLAYURL_RESULT_FILE).is_file()
    assert (_work_root(tmp_path) / "bilibili-media-selection.json").is_file()

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-playurl-not-approved$"):
        run_bilibili_playurl(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(tikhub_api_key="synthetic"),
            client_factory=lambda _settings: _FakePlayurlClient(tmp_path, calls),
        )
    assert calls["playurl"] == 1


def test_playurl_plan_or_response_drift_stops_before_client(tmp_path: Path) -> None:
    _run_identifier_discovery(tmp_path)
    plan_bilibili_playurl(tmp_path, WORK_ID)
    approval = _playurl_approval(tmp_path)
    response_path = _work_root(tmp_path) / DISCOVERY_RESPONSE_FILE
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["data"]["cid"] = 987654321
    response_path.write_text(json.dumps(response), encoding="utf-8")
    calls = _calls()

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-playurl-not-approved$"):
        run_bilibili_playurl(
            tmp_path,
            WORK_ID,
            approval,
            settings=SimpleNamespace(tikhub_api_key="synthetic"),
            client_factory=lambda _settings: _FakePlayurlClient(tmp_path, calls),
        )

    assert calls["construct"] == 0
    assert not (_work_root(tmp_path) / PLAYURL_RESPONSE_FILE).exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "tikhub"),
        ("action", "bilibili-play-info-discovery"),
        ("endpoint", "/wrong"),
        ("discovery_response_sha256", "0" * 63),
        ("bv_id", "not-a-bv-id"),
        ("cid", True),
        ("paid_api_request_count", 2),
        ("media_asset_count", 1),
        ("follow_redirects", True),
        ("no_retry", False),
        ("no_fallback", False),
    ],
)
def test_playurl_plan_rejects_policy_mutation(
    tmp_path: Path, field: str, value: object
) -> None:
    _run_identifier_discovery(tmp_path)
    plan_bilibili_playurl(tmp_path, WORK_ID)
    plan_path = _work_root(tmp_path) / PLAYURL_PLAN_FILE
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload[field] = value
    plan_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        BilibiliContractError, match="^bilibili-playurl-plan-invalid$"
    ):
        load_playurl_plan(plan_path)


def _run_dash_discovery(root: Path) -> None:
    approval = _planned_and_approved(root)
    calls = _calls()
    run_bilibili_discovery(
        root,
        WORK_ID,
        approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=_factory(
            root,
            _fixture("tikhub_bilibili_play_info_dash.json"),
            calls,
        ),
    )


def _media_approval(root: Path) -> Path:
    plan_path = _work_root(root) / MEDIA_PLAN_FILE
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    approval = _work_root(root) / "tikhub-bilibili-media-approval.json"
    publish_json_exclusive(
        _work_root(root),
        approval,
        {
            "action": plan["action"],
            "approved": True,
            "input_sha256": plan["input_sha256"],
            "no_fallback": True,
            "no_retry": True,
            "plan_sha256": sha256_file(plan_path),
            "provider": plan["provider"],
            "request_count": plan["media_asset_count"],
            "work_id": WORK_ID,
        },
    )
    return approval


def test_media_plan_hash_binds_two_assets_without_raw_urls(tmp_path: Path) -> None:
    _run_dash_discovery(tmp_path)

    plan = plan_bilibili_media(tmp_path, WORK_ID)

    value = json.loads(
        (_work_root(tmp_path) / MEDIA_PLAN_FILE).read_text(encoding="utf-8")
    )
    assert value["paid_api_request_count"] == 0
    assert value["media_asset_count"] == 2
    assert value["max_bytes_per_asset"] == 2 * 1024 * 1024 * 1024
    assert value["max_total_bytes"] == 4 * 1024 * 1024 * 1024
    assert value["connect_timeout_seconds"] == 15
    assert value["asset_timeout_seconds"] == 900
    assert value["max_redirect_hops_per_asset"] == 3
    assert value["follow_https_redirects"] is True
    assert value["no_retry"] is True
    assert value["no_backup_url"] is True
    assert value["no_fallback"] is True
    assert [item["label"] for item in value["assets"]] == ["video", "audio"]
    assert all(set(item) == {"label", "url_sha256", "initial_host_sha256", "expected_size"} for item in value["assets"])
    serialized = json.dumps(value)
    assert "example.invalid" not in serialized
    assert "private_url" not in serialized
    assert "BV1" not in repr(plan)


class _FakeAssetDownloader:
    def __init__(self, root: Path, calls: dict[str, int], *, fail_audio: bool = False) -> None:
        assert (_work_root(root) / MEDIA_EXECUTION_STARTED_FILE).is_file()
        calls["construct"] += 1
        self._calls = calls
        self._fail_audio = fail_audio

    def download(self, asset, target: Path, *, max_redirect_hops: int = 3):
        self._calls["assets"] += 1
        assert max_redirect_hops == 3
        if self._fail_audio and asset.kind == "audio":
            raise RuntimeError("synthetic private failure")
        target.parent.mkdir(parents=True, exist_ok=True)
        body = f"synthetic-{asset.kind}".encode("ascii")
        target.write_bytes(body)
        artifact = ArtifactRecord(
            relative_path=target.name,
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            path=target,
        )
        return DownloadedBilibiliAsset(
            kind=asset.kind,
            artifact=artifact,
            http_request_count=1,
            redirect_hops=0,
            final_host_sha256="a" * 64,
        )

    def close(self) -> None:
        self._calls["close"] += 1


def test_media_download_attempts_each_selected_asset_once_and_stops(
    tmp_path: Path,
) -> None:
    _run_dash_discovery(tmp_path)
    plan_bilibili_media(tmp_path, WORK_ID)
    approval = _media_approval(tmp_path)
    calls = {"construct": 0, "assets": 0, "close": 0}

    result = run_bilibili_media_download(
        tmp_path,
        WORK_ID,
        approval,
        downloader_factory=lambda: _FakeAssetDownloader(tmp_path, calls),
    )

    assert calls == {"construct": 1, "assets": 2, "close": 1}
    assert result.status == "succeeded"
    assert result.media_assets_attempted == 2
    assert result.media_assets_completed == 2
    assert result.paid_api_requests == 0
    assert (_work_root(tmp_path) / MEDIA_DOWNLOAD_RESULT_FILE).is_file()
    assert (_work_root(tmp_path) / "source-acquisition" / "video.m4s").is_file()
    assert (_work_root(tmp_path) / "source-acquisition" / "audio.m4a").is_file()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"


def test_partial_dash_failure_is_preserved_and_same_plan_cannot_rerun(
    tmp_path: Path,
) -> None:
    _run_dash_discovery(tmp_path)
    plan_bilibili_media(tmp_path, WORK_ID)
    approval = _media_approval(tmp_path)
    calls = {"construct": 0, "assets": 0, "close": 0}
    factory = lambda: _FakeAssetDownloader(tmp_path, calls, fail_audio=True)

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-media-download-failed$"):
        run_bilibili_media_download(
            tmp_path, WORK_ID, approval, downloader_factory=factory
        )

    assert calls == {"construct": 1, "assets": 2, "close": 1}
    assert (_work_root(tmp_path) / "source-acquisition" / "video.m4s").is_file()
    failure = json.loads(
        (_work_root(tmp_path) / MEDIA_DOWNLOAD_RESULT_FILE).read_text(encoding="utf-8")
    )
    assert failure["status"] == "failed"
    assert failure["media_assets_attempted"] == 2
    assert failure["media_assets_completed"] == 1

    with pytest.raises(BilibiliAcquisitionError, match="^bilibili-media-not-approved$"):
        run_bilibili_media_download(
            tmp_path, WORK_ID, approval, downloader_factory=factory
        )
    assert calls["assets"] == 2


def _load_cli_module():
    path = Path(__file__).parents[1] / "automation" / "scripts" / "acquire_bilibili_source.py"
    spec = importlib.util.spec_from_file_location("acquire_bilibili_source", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_plans_by_work_id_only_and_prints_redacted_status(
    tmp_path: Path, capsys
) -> None:
    source = tmp_path / "private-url.txt"
    source.write_text(SYNTHETIC_URL + "\n", encoding="utf-8")
    create_url_intake(
        tmp_path,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    module = _load_cli_module()

    assert module.main(["plan-discovery", WORK_ID], root=tmp_path) == 0
    captured = capsys.readouterr()
    assert "action=bilibili-discovery status=planned" in captured.out
    assert WORK_ID[:12] in captured.out
    assert SYNTHETIC_URL not in captured.out
    assert "BV1" not in captured.out
    assert str(_work_root(tmp_path)) not in captured.out
    assert captured.err == ""

    assert module.main(["status", WORK_ID], root=tmp_path) == 0
    status = capsys.readouterr()
    assert "stage=discovery_ready" in status.out


def test_cli_reports_saved_discovery_failure_before_plan_fallback(
    tmp_path: Path, capsys
) -> None:
    _offline_failed_saved_discovery(tmp_path)
    module = _load_cli_module()

    assert module.main(["status", WORK_ID], root=tmp_path) == 0

    status = capsys.readouterr()
    assert "stage=discovery_failed" in status.out
    assert "stage=discovery_ready" not in status.out


def test_offline_recovery_plan_binds_existing_failure_chain(tmp_path: Path) -> None:
    response_sha256, failed_result_sha256 = _offline_failed_saved_discovery(tmp_path)

    plan = plan_bilibili_discovery_recovery(
        tmp_path,
        WORK_ID,
        expected_response_sha256=response_sha256,
        expected_failed_result_sha256=failed_result_sha256,
    )

    private_root = _work_root(tmp_path)
    plan_path = private_root / DISCOVERY_RECOVERY_PLAN_FILE
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    assert value == {
        "schema_version": 1,
        "work_id": WORK_ID,
        "provider": "local-bilibili-recovery",
        "action": "bilibili-discovery-offline-recovery",
        "input_sha256": sha256_file(private_root / "source-input.txt"),
        "discovery_plan_sha256": sha256_file(private_root / DISCOVERY_PLAN_FILE),
        "response_sha256": response_sha256,
        "failed_result_sha256": failed_result_sha256,
        "network_required": False,
        "provider_credential_required": False,
        "paid_api_request_count": 0,
        "media_asset_count": 0,
        "no_key_read": True,
        "no_retry": True,
        "no_fallback": True,
        "preserve_originals": True,
    }
    assert load_discovery_recovery_plan(plan_path) == plan
    assert "example.invalid" not in json.dumps(value)


def test_offline_recovery_plan_rejects_unapproved_artifact_hash(
    tmp_path: Path,
) -> None:
    response_sha256, failed_result_sha256 = _offline_failed_saved_discovery(tmp_path)

    with pytest.raises(
        BilibiliAcquisitionError,
        match="^bilibili-discovery-recovery-plan-unavailable$",
    ):
        plan_bilibili_discovery_recovery(
            tmp_path,
            WORK_ID,
            expected_response_sha256="0" * 64,
            expected_failed_result_sha256=failed_result_sha256,
        )

    assert response_sha256 != "0" * 64
    assert not (_work_root(tmp_path) / DISCOVERY_RECOVERY_PLAN_FILE).exists()


def test_offline_recovery_preserves_originals_and_publishes_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response_sha256, failed_result_sha256 = _offline_failed_saved_discovery(tmp_path)
    private_root = _work_root(tmp_path)
    originals = {
        name: sha256_file(private_root / name)
        for name in (
            DISCOVERY_PLAN_FILE,
            DISCOVERY_EXECUTION_STARTED_FILE,
            DISCOVERY_RESPONSE_FILE,
            DISCOVERY_RESULT_FILE,
        )
    }
    plan_bilibili_discovery_recovery(
        tmp_path,
        WORK_ID,
        expected_response_sha256=response_sha256,
        expected_failed_result_sha256=failed_result_sha256,
    )
    monkeypatch.setattr(
        "boomearth.workbench.bilibili_acquisition.Settings.load",
        lambda *_args, **_kwargs: pytest.fail("recovery must not read settings"),
    )

    result = run_bilibili_discovery_recovery(tmp_path, WORK_ID)

    assert result.next_action == "media-download-ready"
    assert result.paid_api_requests == 0
    assert result.media_asset_count == 2
    assert result.response_sha256 == response_sha256
    assert result.original_result_sha256 == failed_result_sha256
    assert all(sha256_file(private_root / name) == digest for name, digest in originals.items())
    assert (private_root / DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE).is_file()
    assert (private_root / DISCOVERY_RECOVERY_RESULT_FILE).is_file()
    selection = json.loads(
        (private_root / "bilibili-media-selection.json").read_text(encoding="utf-8")
    )
    assert selection["bv_id"] is None
    assert selection["cid"] is None
    assert [asset["kind"] for asset in selection["assets"]] == ["video", "audio"]

    with pytest.raises(
        BilibiliAcquisitionError,
        match="^bilibili-discovery-recovery-unavailable$",
    ):
        run_bilibili_discovery_recovery(tmp_path, WORK_ID)
    assert all(sha256_file(private_root / name) == digest for name, digest in originals.items())


@pytest.mark.parametrize(
    ("artifact_name", "field", "replacement"),
    [
        ("tikhub-bilibili-discovery-approval.json", "request_count", 2),
        (DISCOVERY_EXECUTION_STARTED_FILE, "plan_sha256", "0" * 64),
    ],
)
def test_offline_recovery_revalidates_original_approval_and_execution_lock(
    tmp_path: Path, artifact_name: str, field: str, replacement: object
) -> None:
    response_sha256, failed_result_sha256 = _offline_failed_saved_discovery(tmp_path)
    private_root = _work_root(tmp_path)
    plan_bilibili_discovery_recovery(
        tmp_path,
        WORK_ID,
        expected_response_sha256=response_sha256,
        expected_failed_result_sha256=failed_result_sha256,
    )
    artifact = private_root / artifact_name
    value = json.loads(artifact.read_text(encoding="utf-8"))
    value[field] = replacement
    artifact.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(
        BilibiliAcquisitionError,
        match="^bilibili-discovery-recovery-unavailable$",
    ):
        run_bilibili_discovery_recovery(tmp_path, WORK_ID)

    assert not (private_root / "bilibili-media-selection.json").exists()
    assert not (private_root / DISCOVERY_RECOVERY_RESULT_FILE).exists()
    assert not (private_root / DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE).exists()


def test_media_plan_can_continue_from_offline_recovery(tmp_path: Path) -> None:
    response_sha256, failed_result_sha256 = _offline_failed_saved_discovery(tmp_path)
    plan_bilibili_discovery_recovery(
        tmp_path,
        WORK_ID,
        expected_response_sha256=response_sha256,
        expected_failed_result_sha256=failed_result_sha256,
    )
    run_bilibili_discovery_recovery(tmp_path, WORK_ID)

    plan = plan_bilibili_media(tmp_path, WORK_ID)

    assert plan.upstream_response_sha256 == response_sha256
    assert plan.paid_api_request_count == 0
    assert plan.media_asset_count == 2


def test_cli_status_tracks_offline_recovery_without_hiding_original_failure(
    tmp_path: Path, capsys
) -> None:
    response_sha256, failed_result_sha256 = _offline_failed_saved_discovery(tmp_path)
    module = _load_cli_module()
    plan_bilibili_discovery_recovery(
        tmp_path,
        WORK_ID,
        expected_response_sha256=response_sha256,
        expected_failed_result_sha256=failed_result_sha256,
    )

    assert module.main(["status", WORK_ID], root=tmp_path) == 0
    assert "stage=discovery_recovery_ready" in capsys.readouterr().out

    run_bilibili_discovery_recovery(tmp_path, WORK_ID)
    assert module.main(["status", WORK_ID], root=tmp_path) == 0
    assert "stage=media_plan_ready" in capsys.readouterr().out


def test_cli_reports_interrupted_offline_recovery_as_failed(
    tmp_path: Path, capsys
) -> None:
    response_sha256, failed_result_sha256 = _offline_failed_saved_discovery(tmp_path)
    private_root = _work_root(tmp_path)
    plan_bilibili_discovery_recovery(
        tmp_path,
        WORK_ID,
        expected_response_sha256=response_sha256,
        expected_failed_result_sha256=failed_result_sha256,
    )
    publish_json_exclusive(
        private_root,
        private_root / DISCOVERY_RECOVERY_EXECUTION_STARTED_FILE,
        {
            "schema_version": 1,
            "work_id": WORK_ID,
            "action": "bilibili-discovery-offline-recovery",
            "plan_sha256": sha256_file(private_root / DISCOVERY_RECOVERY_PLAN_FILE),
            "response_sha256": response_sha256,
            "original_result_sha256": failed_result_sha256,
            "started_at": FIXED_NOW.isoformat(),
        },
    )
    module = _load_cli_module()

    assert module.main(["status", WORK_ID], root=tmp_path) == 0

    assert "stage=discovery_recovery_failed" in capsys.readouterr().out


def test_cli_has_no_source_or_fallback_options() -> None:
    module = _load_cli_module()
    help_text = module.build_parser().format_help().lower()

    for forbidden in ("--url", "--cookie", "--browser", "--provider", "--retry", "--fallback"):
        assert forbidden not in help_text


def test_video_download_skill_documents_bilibili_stages_and_stop_boundary() -> None:
    skill = (
        Path(__file__).parents[1]
        / ".agents"
        / "skills"
        / "katerj-source-acquisition"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "acquire_bilibili_source.py plan-discovery <work_id>" in skill
    assert "acquire_bilibili_source.py plan-media <work_id>" in skill
    assert "acquire_bilibili_source.py finalize <work_id>" in skill
    assert "plan-playurl" in skill and "条件" in skill
    assert "media_ready" in skill
    assert "audio_ready" in skill
    assert "不授权转写" in skill
    assert "source_ready" not in skill


def test_video_download_skill_documents_hash_bound_offline_discovery_recovery() -> None:
    skill = (
        Path(__file__).parents[1]
        / ".agents"
        / "skills"
        / "katerj-source-acquisition"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "plan-recovery <work_id>" in skill
    assert "run-recovery <work_id>" in skill
    assert "不读取 API Key" in skill
    assert "不得覆盖原" in skill


def test_cli_never_prints_unknown_exception_details(monkeypatch, capsys) -> None:
    module = _load_cli_module()

    def fail(*_args, **_kwargs):
        raise ValueError(SYNTHETIC_URL)

    monkeypatch.setattr(module, "plan_bilibili_discovery", fail)
    assert module.main(["plan-discovery", WORK_ID], root=Path.cwd()) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=bilibili-command-failed\n"
    assert SYNTHETIC_URL not in captured.err


def test_cli_runs_directly_from_workspace_without_installed_package() -> None:
    workspace = Path(__file__).parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            str(workspace / "automation" / "scripts" / "acquire_bilibili_source.py"),
            "--help",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0
    assert "plan-discovery" in completed.stdout
    assert completed.stderr == ""

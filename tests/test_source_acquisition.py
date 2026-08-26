from __future__ import annotations

import json
import importlib.util
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from boomearth.providers.ytdlp import YtDlpResult
from boomearth.providers.tikhub import SourceMedia
from boomearth.workbench.source_artifacts import ArtifactRecord
from boomearth.workbench import source_acquisition
from boomearth.workbench.source_acquisition import (
    SourceAcquisitionError,
    plan_source_acquisition,
    run_source_acquisition,
)
from boomearth.workbench.source_artifacts import (
    canonical_json_bytes,
    publish_json_exclusive,
    sha256_file,
)
from boomearth.workbench.source_intake import create_url_intake
from boomearth.workbench.source_ledger import WashEventLedger


WORK_ID = "00000000-0000-4000-8000-000000000004"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 13, 6, 7, 8, tzinfo=timezone.utc)


def _work_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def _url_order(root: Path) -> None:
    url_file = root / "input.txt"
    url_file.write_text("https://example.invalid/private-item\n", encoding="utf-8")
    create_url_intake(
        root,
        url_file,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )


def _approval(root: Path) -> Path:
    plan_path = _work_root(root) / "source-acquisition-plan.json"
    plan = json.loads(plan_path.read_text("utf-8"))
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
    path = _work_root(root) / "source-acquisition-approval.json"
    publish_json_exclusive(_work_root(root), path, receipt)
    return path


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\0\0" * 16_000)


def test_plan_binds_private_url_hash_and_exact_ytdlp_network_policy(
    tmp_path: Path,
) -> None:
    _url_order(tmp_path)

    plan = plan_source_acquisition(tmp_path, WORK_ID, "yt-dlp")

    plan_path = _work_root(tmp_path) / "source-acquisition-plan.json"
    value = json.loads(plan_path.read_text("utf-8"))
    assert value == {
        "action": "source-acquisition",
        "browser_cookies": False,
        "credentials": False,
        "fee_possible": False,
        "follow_redirects": True,
        "input_sha256": sha256_file(_work_root(tmp_path) / "source-input.txt"),
        "internal_http_request_limit": None,
        "network_required": True,
        "no_fallback": True,
        "no_retry": True,
        "playlist_item_limit": 1,
        "provider": "yt-dlp",
        "redirect_policy": "yt-dlp-extractor-and-media-cdn-managed",
        "request_count": 1,
        "request_unit": "provider-invocation",
        "schema_version": 2,
        "timeout_seconds": 300,
        "work_id": WORK_ID,
    }
    assert plan.request_unit == "provider-invocation"
    assert plan.internal_http_request_limit is None
    assert plan.follow_redirects is True
    assert canonical_json_bytes(value) == plan_path.read_bytes()
    assert "private-item" not in repr(plan)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("schema_version", True),
        ("work_id", "not-a-uuid"),
        ("provider", "tikhub"),
        ("action", "other-action"),
        ("input_sha256", "0" * 63),
        ("request_count", 2),
        ("request_count", True),
        ("request_unit", "http-request"),
        ("internal_http_request_limit", 1),
        ("network_required", False),
        ("fee_possible", True),
        ("follow_redirects", False),
        ("redirect_policy", "unbounded"),
        ("timeout_seconds", 301),
        ("timeout_seconds", True),
        ("playlist_item_limit", 2),
        ("playlist_item_limit", True),
        ("browser_cookies", True),
        ("credentials", True),
        ("no_retry", False),
        ("no_fallback", False),
    ],
)
def test_ytdlp_source_plan_v2_rejects_policy_mutation(
    tmp_path: Path, field: str, value: object
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "yt-dlp")
    path = _work_root(tmp_path) / "source-acquisition-plan.json"
    payload = json.loads(path.read_text("utf-8"))
    payload[field] = value
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        SourceAcquisitionError, match="^source-acquisition-plan-invalid$"
    ):
        source_acquisition._load_source_acquisition_plan(path)


@pytest.mark.parametrize("change", ["missing", "unknown"])
def test_ytdlp_source_plan_v2_rejects_key_set_mutation(
    tmp_path: Path, change: str
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "yt-dlp")
    path = _work_root(tmp_path) / "source-acquisition-plan.json"
    payload = json.loads(path.read_text("utf-8"))
    if change == "missing":
        del payload["timeout_seconds"]
    else:
        payload["unexpected"] = False
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(
        SourceAcquisitionError, match="^source-acquisition-plan-invalid$"
    ):
        source_acquisition._load_source_acquisition_plan(path)


def test_run_revalidates_approval_then_calls_ytdlp_once_and_publishes_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "yt-dlp")
    approval = _approval(tmp_path)
    calls: list[tuple[Path, Path]] = []

    class FakeProvider:
        def __init__(self, executable: Path) -> None:
            assert executable == tmp_path / "yt-dlp.exe"

        def download(self, url_file: Path, output_dir: Path) -> YtDlpResult:
            calls.append((url_file, output_dir))
            output_dir.mkdir(parents=True)
            media = output_dir / "download.wav"
            _write_wav(media)
            return YtDlpResult(media, "youtube")

    monkeypatch.setattr(source_acquisition, "YtDlpProvider", FakeProvider)
    result = run_source_acquisition(
        tmp_path,
        WORK_ID,
        "yt-dlp",
        approval,
        settings=SimpleNamespace(yt_dlp_path=tmp_path / "yt-dlp.exe"),
    )

    assert len(calls) == 1
    assert calls[0][0] == _work_root(tmp_path) / "source-input.txt"
    assert result.relative_path == "source-media/original.wav"
    assert result.sha256 == sha256_file(result.path)
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_run_rejects_approval_hash_mismatch_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "yt-dlp")
    approval = _approval(tmp_path)
    value = json.loads(approval.read_text("utf-8"))
    value["plan_sha256"] = "0" * 64
    approval.write_bytes(canonical_json_bytes(value))
    called = False

    class ForbiddenProvider:
        def __init__(self, *_args, **_kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(source_acquisition, "YtDlpProvider", ForbiddenProvider)

    with pytest.raises(SourceAcquisitionError, match="^source-acquisition-not-approved$"):
        run_source_acquisition(
            tmp_path,
            WORK_ID,
            "yt-dlp",
            approval,
            settings=SimpleNamespace(yt_dlp_path=tmp_path / "yt-dlp.exe"),
        )

    assert called is False
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"


def test_run_rejects_legacy_v1_ytdlp_plan_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url_order(tmp_path)
    private_root = _work_root(tmp_path)
    plan_path = private_root / "source-acquisition-plan.json"
    publish_json_exclusive(
        private_root,
        plan_path,
        {
            "action": "source-acquisition",
            "fee_possible": False,
            "input_sha256": sha256_file(private_root / "source-input.txt"),
            "network_required": True,
            "no_fallback": True,
            "no_retry": True,
            "provider": "yt-dlp",
            "request_count": 1,
            "schema_version": 1,
            "work_id": WORK_ID,
        },
    )
    approval = _approval(tmp_path)
    provider_constructed = False

    class ForbiddenProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            nonlocal provider_constructed
            provider_constructed = True

    monkeypatch.setattr(source_acquisition, "YtDlpProvider", ForbiddenProvider)

    with pytest.raises(
        SourceAcquisitionError, match="^source-acquisition-not-approved$"
    ):
        run_source_acquisition(
            tmp_path,
            WORK_ID,
            "yt-dlp",
            approval,
            settings=SimpleNamespace(yt_dlp_path=tmp_path / "yt-dlp.exe"),
        )

    assert provider_constructed is False
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"


def test_run_rejects_url_mutation_after_plan_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "yt-dlp")
    approval = _approval(tmp_path)
    (_work_root(tmp_path) / "source-input.txt").write_text(
        "https://example.invalid/changed\n", encoding="utf-8"
    )
    called = False

    class ForbiddenProvider:
        def __init__(self, *_args, **_kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(source_acquisition, "YtDlpProvider", ForbiddenProvider)

    with pytest.raises(SourceAcquisitionError, match="^source-acquisition-input-changed$"):
        run_source_acquisition(
            tmp_path,
            WORK_ID,
            "yt-dlp",
            approval,
            settings=SimpleNamespace(yt_dlp_path=tmp_path / "yt-dlp.exe"),
        )

    assert called is False


def test_run_does_not_fallback_when_ytdlp_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "yt-dlp")
    approval = _approval(tmp_path)
    calls = 0

    class FailingProvider:
        def __init__(self, *_args, **_kwargs):
            pass

        def download(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("private url")

    monkeypatch.setattr(source_acquisition, "YtDlpProvider", FailingProvider)

    with pytest.raises(SourceAcquisitionError) as raised:
        run_source_acquisition(
            tmp_path,
            WORK_ID,
            "yt-dlp",
            approval,
            settings=SimpleNamespace(yt_dlp_path=tmp_path / "yt-dlp.exe"),
        )

    assert str(raised.value) == "source-acquisition-failed"
    assert "private url" not in repr(raised.value)
    assert calls == 1
    assert WashEventLedger(tmp_path).status(WORK_ID) == "source_registered"


def test_acquire_source_cli_plan_is_offline_and_source_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _url_order(tmp_path)
    script_path = (
        Path(__file__).parents[1] / "automation" / "scripts" / "acquire_source.py"
    )
    spec = importlib.util.spec_from_file_location("acquire_source_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["plan", WORK_ID, "--provider", "yt-dlp"], root=tmp_path) == 0

    captured = capsys.readouterr()
    assert "action=source-acquisition status=planned" in captured.out
    assert "private-item" not in captured.out
    assert captured.err == ""


def test_tikhub_plan_marks_possible_fee(tmp_path: Path) -> None:
    _url_order(tmp_path)

    plan = plan_source_acquisition(tmp_path, WORK_ID, "tikhub")

    assert plan.provider == "tikhub"
    assert plan.fee_possible is True
    assert plan.request_count == 1


def test_tikhub_run_resolves_once_downloads_once_and_never_calls_ytdlp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "tikhub")
    approval = _approval(tmp_path)
    calls = {"resolve": 0, "download": 0, "yt-dlp": 0, "closed": 0}

    class FakeResolver:
        def __init__(self, settings) -> None:
            assert settings is configured

        def resolve_authorized_url(self, url: str, *, authorized: bool) -> SourceMedia:
            calls["resolve"] += 1
            assert authorized is True
            assert url == "https://example.invalid/private-item"
            return SourceMedia(
                platform="douyin",
                public_work_id="safe-work",
                duration_s=1.0,
                private_media_url="https://media.example.invalid/private.wav",
            )

        def close(self) -> None:
            calls["closed"] += 1

    class FakeDownloader:
        def download(self, url: str, target: Path) -> ArtifactRecord:
            calls["download"] += 1
            assert url == "https://media.example.invalid/private.wav"
            _write_wav(target)
            return ArtifactRecord(
                relative_path=target.name,
                sha256=sha256_file(target),
                size_bytes=target.stat().st_size,
                path=target,
            )

    class ForbiddenYtDlp:
        def __init__(self, *_args, **_kwargs) -> None:
            calls["yt-dlp"] += 1

    configured = SimpleNamespace(
        tikhub_api_key="synthetic-credential",
        tikhub_api_base="https://api.tikhub.dev",
    )
    monkeypatch.setattr(source_acquisition, "TikHubClient", FakeResolver)
    monkeypatch.setattr(source_acquisition, "PrivateMediaDownloader", FakeDownloader)
    monkeypatch.setattr(source_acquisition, "YtDlpProvider", ForbiddenYtDlp)

    result = run_source_acquisition(
        tmp_path,
        WORK_ID,
        "tikhub",
        approval,
        settings=configured,
    )

    assert calls == {"resolve": 1, "download": 1, "yt-dlp": 0, "closed": 1}
    assert result.relative_path == "source-media/original.wav"
    assert WashEventLedger(tmp_path).status(WORK_ID) == "media_ready"


def test_tikhub_failure_never_dispatches_ytdlp_or_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url_order(tmp_path)
    plan_source_acquisition(tmp_path, WORK_ID, "tikhub")
    approval = _approval(tmp_path)
    calls = {"resolve": 0, "download": 0, "yt-dlp": 0}

    class FailingResolver:
        def __init__(self, _settings) -> None:
            pass

        def resolve_authorized_url(self, *_args, **_kwargs):
            calls["resolve"] += 1
            raise RuntimeError("private url")

        def close(self) -> None:
            pass

    class ForbiddenDownloader:
        def __init__(self, *_args, **_kwargs) -> None:
            calls["download"] += 1

    class ForbiddenYtDlp:
        def __init__(self, *_args, **_kwargs) -> None:
            calls["yt-dlp"] += 1

    monkeypatch.setattr(source_acquisition, "TikHubClient", FailingResolver)
    monkeypatch.setattr(source_acquisition, "PrivateMediaDownloader", ForbiddenDownloader)
    monkeypatch.setattr(source_acquisition, "YtDlpProvider", ForbiddenYtDlp)

    with pytest.raises(SourceAcquisitionError) as raised:
        run_source_acquisition(
            tmp_path,
            WORK_ID,
            "tikhub",
            approval,
            settings=SimpleNamespace(),
        )

    assert str(raised.value) == "source-acquisition-failed"
    assert "private url" not in repr(raised.value)
    assert calls == {"resolve": 1, "download": 0, "yt-dlp": 0}

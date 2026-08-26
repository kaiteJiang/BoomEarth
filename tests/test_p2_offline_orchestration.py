from __future__ import annotations

import json
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from boomearth.media.source_audio import normalize_source_audio
from boomearth.media.source_snapshot import snapshot_local_source
from boomearth.providers.paraformer import SourceTranscript
from boomearth.providers.tikhub import SourceMedia
from boomearth.providers.ytdlp import YtDlpResult
from boomearth.workbench import source_acquisition
from boomearth.workbench.handoff_compiler import compile_source_handoff
from boomearth.workbench.rewrite_package import REQUIRED_REVIEWS, prepare_rewrite_brief
from boomearth.workbench.source_acquisition import (
    plan_source_acquisition,
    run_source_acquisition,
)
from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    publish_json_exclusive,
    sha256_file,
)
from boomearth.workbench.source_intake import create_local_intake, create_url_intake
from boomearth.workbench.source_ledger import WashEventLedger
from boomearth.workbench.source_transcription import (
    plan_source_transcription,
    run_source_transcription,
)


def _work_root(root: Path, work_id: str) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / work_id
    )


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\0\0" * 16_000)


def _approval(root: Path, work_id: str, plan_name: str, provider: str, action: str) -> Path:
    private_root = _work_root(root, work_id)
    plan_path = private_root / plan_name
    plan = json.loads(plan_path.read_text("utf-8"))
    path = private_root / f"{action}-approval.json"
    publish_json_exclusive(
        private_root,
        path,
        {
            "action": action,
            "approved": True,
            "input_sha256": plan["input_sha256"],
            "no_fallback": True,
            "no_retry": True,
            "plan_sha256": sha256_file(plan_path),
            "provider": provider,
            "request_count": 1,
            "work_id": work_id,
        },
    )
    return path


def _source_media(
    root: Path,
    work_id: str,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, int]:
    provider_calls = {"yt-dlp": 0, "tikhub": 0}
    root.mkdir(parents=True, exist_ok=True)
    if source == "local":
        local = root / "authorized" / "source.wav"
        _write_wav(local)
        create_local_intake(
            root,
            local,
            authorized=True,
            now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
            uuid_factory=lambda: UUID(work_id),
        )
        snapshot_local_source(root, work_id)
        return provider_calls

    url_file = root / "url.txt"
    url_file.write_text("https://example.invalid/authorized-item\n", encoding="utf-8")
    create_url_intake(
        root,
        url_file,
        authorized=True,
        now=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        uuid_factory=lambda: UUID(work_id),
    )
    plan_source_acquisition(root, work_id, source)
    if source == "yt-dlp":
        plan = json.loads(
            (_work_root(root, work_id) / "source-acquisition-plan.json").read_text(
                "utf-8"
            )
        )
        assert plan["schema_version"] == 2
        assert plan["request_count"] == 1
        assert plan["request_unit"] == "provider-invocation"
        assert plan["internal_http_request_limit"] is None
        assert plan["follow_redirects"] is True
        assert plan["timeout_seconds"] == 300
        assert plan["browser_cookies"] is False
        assert plan["credentials"] is False
        assert plan["no_retry"] is True
        assert plan["no_fallback"] is True
    approval = _approval(
        root,
        work_id,
        "source-acquisition-plan.json",
        source,
        "source-acquisition",
    )
    if source == "yt-dlp":
        class FakeYtDlp:
            def __init__(self, _executable: Path) -> None:
                pass

            def download(self, _url_file: Path, output_dir: Path) -> YtDlpResult:
                provider_calls["yt-dlp"] += 1
                output_dir.mkdir(parents=True)
                media = output_dir / "download.wav"
                _write_wav(media)
                return YtDlpResult(media, "synthetic")

        monkeypatch.setattr(source_acquisition, "YtDlpProvider", FakeYtDlp)
        settings = SimpleNamespace(yt_dlp_path=root / "synthetic-yt-dlp.exe")
    else:
        class FakeResolver:
            def __init__(self, _settings) -> None:
                pass

            def resolve_authorized_url(self, _url: str, *, authorized: bool) -> SourceMedia:
                provider_calls["tikhub"] += 1
                assert authorized is True
                return SourceMedia(
                    "douyin",
                    "safe-id",
                    1.0,
                    "https://media.example.invalid/private.wav",
                )

            def close(self) -> None:
                pass

        class FakeDownloader:
            def download(self, _url: str, target: Path) -> ArtifactRecord:
                _write_wav(target)
                return ArtifactRecord(target.name, sha256_file(target), target.stat().st_size, target)

        monkeypatch.setattr(source_acquisition, "TikHubClient", FakeResolver)
        monkeypatch.setattr(source_acquisition, "PrivateMediaDownloader", FakeDownloader)
        settings = SimpleNamespace()
    run_source_acquisition(
        root,
        work_id,
        source,
        approval,
        settings=settings,
    )
    return provider_calls


def _finish_lane(
    root: Path,
    work_id: str,
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, tuple[str, ...], dict[str, int]]:
    provider_calls = _source_media(root, work_id, source, monkeypatch)
    normalize_source_audio(root, work_id)
    plan_source_transcription(root, work_id)
    approval = _approval(
        root,
        work_id,
        "source-transcription-plan.json",
        "paraformer",
        "source-transcription",
    )

    class FakeParaformer:
        last_request_id = "synthetic-request-id"

        def __init__(self, **_kwargs) -> None:
            pass

        def transcribe_file(self, _audio: Path, *, artifact_relative: str | None = None):
            transcript = _work_root(root, work_id) / "transcript.json"
            publish_json_exclusive(
                _work_root(root, work_id),
                transcript,
                {
                    "duration": 1.0,
                    "language": "zh-CN",
                    "sentences": [
                        {"text": "合成来源只用于证明三个入口能进入同一条私有处理通道"}
                    ],
                    "source_id": "safe-id",
                },
            )
            return SourceTranscript(1.0, 1, "zh-CN", artifact_relative or "")

    run_source_transcription(
        root,
        work_id,
        approval,
        settings=SimpleNamespace(),
        client_factory=FakeParaformer,
    )
    prepare_rewrite_brief(
        root,
        work_id,
        platform="douyin",
        duration_target_s=60,
        archive_slug=f"offline-{source}",
    )
    private_root = _work_root(root, work_id)
    candidate = private_root / "rewrite-candidate.json"
    publish_json_exclusive(
        private_root,
        candidate,
        {
            "archive_slug": f"offline-{source}",
            "duration_target_s": 60,
            "illustration_skill": "ian-xiaohei-illustrations",
            "platform": "douyin",
            "ratio": "16:9",
            "schema_version": 1,
            "segments": [
                {
                    "segment_id": "segment-001",
                    "text": "别急着追求自动化，先把每一步的输入和验收标准锁清楚。",
                    "visual_intent": "人物逐项核对流程卡片",
                }
            ],
            "title_candidates": ["真正能跑的工作流，先锁输入再谈自动化"],
            "visual": "xiaohei-white-first-v1",
            "work_id": work_id,
        },
    )
    review = private_root / "rewrite-review.json"
    publish_json_exclusive(
        private_root,
        review,
        {
            "candidate_sha256": sha256_file(candidate),
            "reviewed_at": "2026-08-13T10:11:12Z",
            "reviews": {name: "pass" for name in REQUIRED_REVIEWS},
            "schema_version": 1,
        },
    )
    publication = compile_source_handoff(root, work_id, candidate, review)
    handoff = (
        root
        / "01-内容生产"
        / "视频工作台"
        / publication.relative_path
    )
    text = handoff.read_text("utf-8")
    headings = tuple(line for line in text.splitlines() if line.startswith("## "))
    return WashEventLedger(root).status(work_id), headings, provider_calls


def test_local_and_both_url_providers_converge_on_one_private_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = []
    for index, source in enumerate(("local", "yt-dlp", "tikhub"), start=1):
        root = tmp_path / source
        work_id = f"00000000-0000-4000-8000-{index:012d}"
        results.append(_finish_lane(root, work_id, source, monkeypatch))

    assert {stage for stage, _, _ in results} == {"handoff_ready"}
    assert results[0][1] == results[1][1] == results[2][1]
    assert results[0][2] == {"yt-dlp": 0, "tikhub": 0}
    assert results[1][2] == {"yt-dlp": 1, "tikhub": 0}
    assert results[2][2] == {"yt-dlp": 0, "tikhub": 1}

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import UUID

import pytest

from boomearth.media.bilibili_source import (
    BilibiliSourceError,
    finalize_bilibili_source,
    probe_complete_av,
)
from boomearth.providers.bilibili_media_download import DownloadedBilibiliAsset
from boomearth.providers.tikhub_bilibili import TikHubBilibiliResponse
from boomearth.workbench.bilibili_acquisition import (
    plan_bilibili_media,
    run_bilibili_discovery,
    run_bilibili_media_download,
)
from boomearth.workbench.bilibili_contracts import (
    DISCOVERY_PLAN_FILE,
    MEDIA_PLAN_FILE,
    plan_bilibili_discovery,
)
from boomearth.workbench.source_artifacts import ArtifactRecord, publish_json_exclusive, sha256_file
from boomearth.workbench.source_intake import create_url_intake
from boomearth.workbench.source_ledger import WashEventLedger


WORK_ID = "00000000-0000-4000-8000-000000000016"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 16, 8, 9, 10, tzinfo=timezone.utc)
SYNTHETIC_URL = "https://www.bilibili.com/video/BV1ab411c7De?t=473.1"
FIXTURE = Path(__file__).parent / "fixtures" / "tikhub_bilibili_play_info_dash.json"


def _require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("local FFmpeg and FFprobe are required")


def _run(command: list[str]) -> None:
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )


def _synthetic_progressive(path: Path) -> None:
    _run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=1",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ]
    )


def _synthetic_dash(video: Path, audio: Path) -> None:
    _run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=green:s=640x360:r=30:d=1",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
        ]
    )
    _run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
            "-vn", "-c:a", "aac", str(audio),
        ]
    )


def _work_root(root: Path) -> Path:
    return root / "01-内容生产" / "视频工作台" / ".internal" / "洗稿" / WORK_ID


def _approval(root: Path, plan_name: str, approval_name: str, request_count: int) -> Path:
    plan_path = _work_root(root) / plan_name
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    target = _work_root(root) / approval_name
    publish_json_exclusive(
        _work_root(root),
        target,
        {
            "action": plan["action"], "approved": True,
            "input_sha256": plan["input_sha256"], "no_fallback": True,
            "no_retry": True, "plan_sha256": sha256_file(plan_path),
            "provider": plan["provider"], "request_count": request_count,
            "work_id": WORK_ID,
        },
    )
    return target


class _DashClient:
    def __init__(self, _settings) -> None:
        pass

    def fetch_play_info(self, _url: str) -> TikHubBilibiliResponse:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        return TikHubBilibiliResponse(payload, 200, "synthetic")

    def close(self) -> None:
        pass


class _LocalAssetDownloader:
    def __init__(self, video: Path, audio: Path) -> None:
        self.sources = {"video": video, "audio": audio}

    def download(self, asset, target: Path, *, max_redirect_hops: int = 3):
        assert max_redirect_hops == 3
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.sources[asset.kind], target)
        record = ArtifactRecord(
            relative_path=target.name,
            sha256=sha256_file(target),
            size_bytes=target.stat().st_size,
            path=target,
        )
        return DownloadedBilibiliAsset(asset.kind, record, 1, 0, "a" * 64)

    def close(self) -> None:
        pass


def _downloaded_dash(root: Path, video: Path, audio: Path) -> None:
    source = root / "private-url.txt"
    source.write_text(SYNTHETIC_URL + "\n", encoding="utf-8")
    create_url_intake(
        root, source, authorized=True, now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    plan_bilibili_discovery(root, WORK_ID)
    discovery_approval = _approval(root, DISCOVERY_PLAN_FILE, "discovery-approval.json", 1)
    run_bilibili_discovery(
        root, WORK_ID, discovery_approval,
        settings=SimpleNamespace(tikhub_api_key="synthetic"),
        client_factory=_DashClient,
    )
    plan_bilibili_media(root, WORK_ID)
    media_approval = _approval(root, MEDIA_PLAN_FILE, "media-approval.json", 2)
    run_bilibili_media_download(
        root, WORK_ID, media_approval,
        downloader_factory=lambda: _LocalAssetDownloader(video, audio),
    )


def test_probe_complete_av_requires_and_reports_one_video_and_audio(
    tmp_path: Path,
) -> None:
    _require_ffmpeg()
    source = tmp_path / "complete.mp4"
    _synthetic_progressive(source)

    probe = probe_complete_av(source)

    assert probe.container == "mp4"
    assert probe.duration_s > 0
    assert (probe.width, probe.height) == (640, 360)
    assert probe.video_codec == "h264"
    assert probe.audio_codec == "aac"
    assert probe.audio_sample_rate == 48000
    assert probe.audio_channels == 2


def test_probe_rejects_video_only_input(tmp_path: Path) -> None:
    _require_ffmpeg()
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    _synthetic_dash(video, audio)

    with pytest.raises(BilibiliSourceError, match="^bilibili-media-invalid$"):
        probe_complete_av(video)


def test_injected_fake_network_end_to_end_finalizes_dash_to_canonical_wav(
    tmp_path: Path,
) -> None:
    """Injected TikHub/media fakes prove offline wiring, not a live download."""
    _require_ffmpeg()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    video = inputs / "video.mp4"
    audio = inputs / "audio.m4a"
    _synthetic_dash(video, audio)
    _downloaded_dash(tmp_path, video, audio)

    result = finalize_bilibili_source(tmp_path, WORK_ID)

    work = _work_root(tmp_path)
    assert result.duration_s > 0
    assert (result.width, result.height) == (640, 360)
    assert result.media_sha256 == sha256_file(
        work / "source-media" / "original.mp4"
    )
    assert result.audio_sha256 == sha256_file(work / "source-audio-16k-mono.wav")
    assert (work / "source-acquisition" / "source-original.mp4").is_file()
    assert (work / "source-original.ffprobe.json").is_file()
    assert (work / "source-original-qc.json").is_file()
    assert WashEventLedger(tmp_path).status(WORK_ID) == "audio_ready"

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import wave

from PIL import Image
import pytest

import boomearth.video.avatar_composite as avatar_composite
from boomearth.video.avatar_composite import (
    AvatarCompositeError,
    Rect,
    plan_circle_avatar,
    render_circle_avatar,
)


def test_headroom_08_circle_is_lower_left_and_caption_safe() -> None:
    plan = plan_circle_avatar(source_size=(720, 1280), occupied=(), caption_safe_top=900)

    assert plan.profile == "headroom_08-circle-lower-left"
    assert (plan.canvas_width, plan.canvas_height) == (1920, 1080)
    assert plan.diameter == 210
    assert plan.x == 70
    assert plan.y == 774
    assert plan.y + plan.diameter == 984
    assert plan.source_crop.width == plan.source_crop.height == 720
    assert plan.source_crop.y == (1280 - 720) // 2


def test_circle_profile_fails_on_content_collision() -> None:
    with pytest.raises(AvatarCompositeError, match="avatar-layout-collision"):
        plan_circle_avatar(
            source_size=(720, 1280),
            occupied=(Rect(40, 480, 420, 420),),
            caption_safe_top=900,
        )


def test_renderer_rejects_a_mutated_plan_even_when_profile_name_matches(
    tmp_path: Path,
) -> None:
    plan = plan_circle_avatar(source_size=(720, 1280), occupied=())

    with pytest.raises(AvatarCompositeError, match="avatar-layout-invalid"):
        render_circle_avatar(
            tmp_path / "missing-background.mp4",
            tmp_path / "missing-master.mp4",
            tmp_path / "missing-narration.wav",
            tmp_path / "output.mp4",
            replace(plan, diameter=359),
        )


@pytest.mark.parametrize(
    "source_size", [(0, 1280), (720, 0), (200, 200), (720, 1280.5)]
)
def test_circle_profile_rejects_invalid_or_too_small_source(
    source_size: tuple[int, int],
) -> None:
    with pytest.raises(AvatarCompositeError, match="avatar-source-invalid"):
        plan_circle_avatar(source_size=source_size, occupied=())


def _require_media_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required")
    return ffmpeg, ffprobe


def _make_video(path: Path, *, size: str, color: str, seconds: float = 3.0) -> None:
    ffmpeg, _ = _require_media_tools()
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={size}:r=30:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=mono:d={seconds}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _make_wav(path: Path, *, seconds: float = 3.0) -> None:
    sample_rate = 48_000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * round(sample_rate * seconds))


def _probe(path: Path) -> dict[str, object]:
    _, ffprobe = _require_media_tools()
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    return json.loads(result.stdout)


def _frame(path: Path, output: Path) -> None:
    ffmpeg, _ = _require_media_tools()
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "1.0",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-y",
            str(output),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


def test_rendered_circle_uses_one_narration_track_and_preserves_transparent_corners(
    tmp_path: Path,
) -> None:
    background = tmp_path / "background.mp4"
    master = tmp_path / "master.mp4"
    narration = tmp_path / "narration.wav"
    output = tmp_path / "final.mp4"
    _make_video(background, size="1920x1080", color="blue")
    _make_video(master, size="720x1280", color="red")
    _make_wav(narration)
    plan = plan_circle_avatar(source_size=(720, 1280), occupied=())

    assert render_circle_avatar(background, master, narration, output, plan) == output

    media = _probe(output)
    videos = [stream for stream in media["streams"] if stream["codec_type"] == "video"]
    audios = [stream for stream in media["streams"] if stream["codec_type"] == "audio"]
    assert len(videos) == len(audios) == 1
    assert (videos[0]["codec_name"], audios[0]["codec_name"]) == ("h264", "aac")
    assert (int(videos[0]["width"]), int(videos[0]["height"])) == (1920, 1080)
    assert videos[0]["avg_frame_rate"] == "30/1"
    assert abs(float(media["format"]["duration"]) - 3.0) <= 1 / 30 + 0.02

    frame_path = tmp_path / "frame.png"
    _frame(output, frame_path)
    image = Image.open(frame_path).convert("RGB")
    corner = image.getpixel((plan.x + 5, plan.y + 5))
    center = image.getpixel(
        (plan.x + plan.diameter // 2, plan.y + plan.diameter // 2)
    )
    assert corner[2] > corner[0] + 40
    assert center[0] > center[2] + 40


def test_render_never_overwrites_an_existing_output(tmp_path: Path) -> None:
    background = tmp_path / "background.mp4"
    master = tmp_path / "master.mp4"
    narration = tmp_path / "narration.wav"
    output = tmp_path / "final.mp4"
    output.write_bytes(b"preserve")

    with pytest.raises(AvatarCompositeError, match="avatar-output-exists"):
        render_circle_avatar(
            background,
            master,
            narration,
            output,
            plan_circle_avatar(source_size=(720, 1280), occupied=()),
        )
    assert output.read_bytes() == b"preserve"


def test_failed_post_publish_validation_removes_new_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background = tmp_path / "background.mp4"
    master = tmp_path / "master.mp4"
    narration = tmp_path / "narration.wav"
    output = tmp_path / "final.mp4"
    _make_video(background, size="1920x1080", color="blue", seconds=0.5)
    _make_video(master, size="720x1280", color="red", seconds=0.5)
    _make_wav(narration, seconds=0.5)
    hashes = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(avatar_composite, "_sha256", lambda _path: next(hashes))

    with pytest.raises(AvatarCompositeError, match="avatar-output-invalid"):
        render_circle_avatar(
            background,
            master,
            narration,
            output,
            plan_circle_avatar(source_size=(720, 1280), occupied=()),
        )

    assert not output.exists()

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from PIL import Image, ImageDraw

from boomearth.video.avatar_defaults import DEFAULT_COMPOSITE_PROFILE


CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1080
AVATAR_DIAMETER = 210
AVATAR_X = 70
AVATAR_BOTTOM_MARGIN = 96
CAPTION_SAFE_TOP = 900
CAPTION_GAP = 24


class AvatarCompositeError(RuntimeError):
    """A fixed, redacted local avatar composition failure."""


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if (
            any(type(value) is not int for value in (self.x, self.y, self.width, self.height))
            or self.x < 0
            or self.y < 0
            or self.width <= 0
            or self.height <= 0
        ):
            raise AvatarCompositeError("avatar-layout-invalid")

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x + self.width <= other.x
            or other.x + other.width <= self.x
            or self.y + self.height <= other.y
            or other.y + other.height <= self.y
        )


@dataclass(frozen=True, slots=True)
class CircleAvatarPlan:
    canvas_width: int
    canvas_height: int
    diameter: int
    x: int
    y: int
    source_crop: Rect
    caption_safe_top: int
    profile: str


def plan_circle_avatar(
    *,
    source_size: tuple[int, int],
    occupied: tuple[Rect, ...],
    caption_safe_top: int = CAPTION_SAFE_TOP,
) -> CircleAvatarPlan:
    if (
        not isinstance(source_size, tuple)
        or len(source_size) != 2
        or any(type(value) is not int for value in source_size)
        or min(source_size) < AVATAR_DIAMETER
    ):
        raise AvatarCompositeError("avatar-source-invalid")
    if type(caption_safe_top) is not int or not AVATAR_DIAMETER + CAPTION_GAP < caption_safe_top <= CANVAS_HEIGHT:
        raise AvatarCompositeError("avatar-layout-invalid")
    width, height = source_size
    crop_size = min(width, height)
    if height >= width:
        crop_x = 0
        # Portrait digital twins often include substantial top headroom.  A
        # centered square keeps the face legible in the final circular crop.
        crop_y = (height - crop_size) // 2
    else:
        crop_x = (width - crop_size) // 2
        crop_y = 0
    crop = Rect(crop_x, crop_y, crop_size, crop_size)
    # The lower-left circular presenter must sit beside the centered caption
    # panel, not above it.  Its bottom edge is fixed to the approved video
    # safe margin so that the presenter remains small and visually secondary.
    y = CANVAS_HEIGHT - AVATAR_DIAMETER - AVATAR_BOTTOM_MARGIN
    avatar_bounds = Rect(AVATAR_X, y, AVATAR_DIAMETER, AVATAR_DIAMETER)
    if any(not isinstance(rect, Rect) for rect in occupied):
        raise AvatarCompositeError("avatar-layout-invalid")
    if any(avatar_bounds.intersects(rect) for rect in occupied):
        raise AvatarCompositeError("avatar-layout-collision")
    return CircleAvatarPlan(
        canvas_width=CANVAS_WIDTH,
        canvas_height=CANVAS_HEIGHT,
        diameter=AVATAR_DIAMETER,
        x=AVATAR_X,
        y=y,
        source_crop=crop,
        caption_safe_top=caption_safe_top,
        profile=DEFAULT_COMPOSITE_PROFILE,
    )


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attributes & 0x400)


def _safe_file(path: Path) -> bool:
    try:
        return not _is_reparse_point(path) and stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _probe(path: Path) -> dict[str, object]:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None or not _safe_file(path):
        raise AvatarCompositeError("avatar-media-invalid")
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
        timeout=30,
    )
    if result.returncode != 0:
        raise AvatarCompositeError("avatar-media-invalid")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AvatarCompositeError("avatar-media-invalid") from None
    if not isinstance(payload, dict):
        raise AvatarCompositeError("avatar-media-invalid")
    return payload


def probe_video_size(path: Path) -> tuple[int, int]:
    payload = _probe(path)
    try:
        video = next(
            stream for stream in payload["streams"] if stream["codec_type"] == "video"
        )
        size = (int(video["width"]), int(video["height"]))
    except (KeyError, StopIteration, TypeError, ValueError):
        raise AvatarCompositeError("avatar-media-invalid") from None
    if min(size) <= 0:
        raise AvatarCompositeError("avatar-media-invalid")
    return size


def _duration(path: Path) -> float:
    payload = _probe(path)
    try:
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise AvatarCompositeError("avatar-media-invalid") from None
    if not math.isfinite(duration) or duration <= 0:
        raise AvatarCompositeError("avatar-media-invalid")
    return duration


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_mask_and_border(directory: Path, diameter: int) -> tuple[Path, Path]:
    mask_path = directory / "mask.png"
    border_path = directory / "border.png"
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, diameter - 2, diameter - 2), fill=255)
    mask.save(mask_path)
    border = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    ImageDraw.Draw(border).ellipse(
        (3, 3, diameter - 4, diameter - 4),
        outline=(255, 255, 255, 230),
        width=5,
    )
    border.save(border_path)
    return mask_path, border_path


def _validate_output(path: Path, *, expected_duration: float) -> None:
    payload = _probe(path)
    try:
        videos = [stream for stream in payload["streams"] if stream["codec_type"] == "video"]
        audios = [stream for stream in payload["streams"] if stream["codec_type"] == "audio"]
        duration = float(payload["format"]["duration"])
        video = videos[0]
    except (IndexError, KeyError, TypeError, ValueError):
        raise AvatarCompositeError("avatar-composite-invalid") from None
    if (
        len(videos) != 1
        or len(audios) != 1
        or video.get("codec_name") != "h264"
        or audios[0].get("codec_name") != "aac"
        or (int(video.get("width", 0)), int(video.get("height", 0)))
        != (CANVAS_WIDTH, CANVAS_HEIGHT)
        or video.get("avg_frame_rate") != "30/1"
        or abs(duration - expected_duration) > 1 / 30 + 0.02
    ):
        raise AvatarCompositeError("avatar-composite-invalid")


def render_circle_avatar(
    background: Path,
    master: Path,
    narration: Path,
    output: Path,
    plan: CircleAvatarPlan,
) -> Path:
    background = Path(background).absolute()
    master = Path(master).absolute()
    narration = Path(narration).absolute()
    output = Path(output).absolute()
    if output.exists():
        raise AvatarCompositeError("avatar-output-exists")
    if (
        type(plan) is not CircleAvatarPlan
        or plan.profile != DEFAULT_COMPOSITE_PROFILE
        or plan.canvas_width != CANVAS_WIDTH
        or plan.canvas_height != CANVAS_HEIGHT
        or plan.diameter != AVATAR_DIAMETER
        or plan.x != AVATAR_X
        or type(plan.caption_safe_top) is not int
        or plan.y != CANVAS_HEIGHT - AVATAR_DIAMETER - AVATAR_BOTTOM_MARGIN
    ):
        raise AvatarCompositeError("avatar-layout-invalid")
    if not all(_safe_file(path) for path in (background, master, narration)):
        raise AvatarCompositeError("avatar-media-invalid")
    source_size = probe_video_size(master)
    expected_plan = plan_circle_avatar(
        source_size=source_size,
        occupied=(),
        caption_safe_top=plan.caption_safe_top,
    )
    if plan != expected_plan:
        raise AvatarCompositeError("avatar-layout-invalid")
    crop = plan.source_crop
    if crop.x + crop.width > source_size[0] or crop.y + crop.height > source_size[1]:
        raise AvatarCompositeError("avatar-source-invalid")
    narration_duration = _duration(narration)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AvatarCompositeError("avatar-media-invalid")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse_point(output.parent):
            raise AvatarCompositeError("avatar-output-invalid")
        with tempfile.TemporaryDirectory(prefix=".avatar-circle-", dir=output.parent) as temp_value:
            temp = Path(temp_value)
            mask, border = _write_mask_and_border(temp, plan.diameter)
            staged = temp / "composite.mp4"
            filter_complex = (
                f"[0:v]scale={plan.canvas_width}:{plan.canvas_height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={plan.canvas_width}:{plan.canvas_height},fps=30[background];"
                f"[1:v]crop={crop.width}:{crop.height}:{crop.x}:{crop.y},"
                f"scale={plan.diameter}:{plan.diameter},format=rgba[avatar_raw];"
                "[2:v]format=gray[mask];"
                "[avatar_raw][mask]alphamerge[avatar_circle];"
                f"[background][avatar_circle]overlay={plan.x}:{plan.y}:"
                "eof_action=repeat:shortest=0[with_avatar];"
                f"[with_avatar][3:v]overlay={plan.x}:{plan.y}:"
                "eof_action=repeat:shortest=0[video]"
            )
            result = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(background),
                    "-i",
                    str(master),
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-i",
                    str(mask),
                    "-loop",
                    "1",
                    "-framerate",
                    "30",
                    "-i",
                    str(border),
                    "-i",
                    str(narration),
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[video]",
                    "-map",
                    "4:a:0",
                    "-t",
                    f"{narration_duration:.6f}",
                    "-r",
                    "30",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    "-y",
                    str(staged),
                ],
                capture_output=True,
                check=False,
                timeout=max(120, round(narration_duration * 10)),
            )
            if result.returncode != 0 or not _safe_file(staged):
                raise AvatarCompositeError("avatar-composite-failed")
            _validate_output(staged, expected_duration=narration_duration)
            staged_hash = _sha256(staged)
            try:
                os.link(staged, output)
            except OSError:
                raise AvatarCompositeError("avatar-output-invalid") from None
            if not _safe_file(output) or _sha256(output) != staged_hash:
                try:
                    if output.exists() and os.path.samefile(staged, output):
                        output.unlink()
                except OSError:
                    pass
                raise AvatarCompositeError("avatar-output-invalid")
    except AvatarCompositeError:
        raise
    except (OSError, subprocess.SubprocessError):
        raise AvatarCompositeError("avatar-composite-failed") from None
    return output

"""Local-only assembly and acceptance of downloaded Bilibili source media."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from boomearth.media.source_audio import SourceAudioError, normalize_source_audio
from boomearth.media.source_snapshot import (
    SourceSnapshotError,
    snapshot_acquired_source,
)
from boomearth.workbench.bilibili_acquisition import MEDIA_SELECTION_FILE
from boomearth.workbench.bilibili_contracts import (
    MEDIA_DOWNLOAD_RESULT_FILE,
    MEDIA_PLAN_FILE,
    load_media_plan,
)
from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    publish_json_exclusive,
    sha256_file,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import SourceLedgerError, WashEventLedger


_REPARSE_POINT = 0x0400
_FFPROBE_FILE = "source-original.ffprobe.json"
_QC_FILE = "source-original-qc.json"


class BilibiliSourceError(RuntimeError):
    """A fixed local-finalization failure with no private paths."""

    def __repr__(self) -> str:
        return "BilibiliSourceError(<redacted>)"


@dataclass(frozen=True, slots=True)
class BilibiliMediaProbe:
    container: str
    duration_s: float
    width: int
    height: int
    display_aspect_ratio: str
    frame_rate: str
    video_codec: str
    audio_codec: str
    audio_sample_rate: int
    audio_channels: int


@dataclass(frozen=True, slots=True, repr=False)
class BilibiliFinalizeResult:
    source_media: ArtifactRecord
    source_audio: ArtifactRecord
    media_sha256: str
    audio_sha256: str
    duration_s: float
    width: int
    height: int

    def __repr__(self) -> str:
        return "BilibiliFinalizeResult(<redacted>)"


def _ordinary_file(path: Path) -> Path:
    try:
        formal = Path(os.path.abspath(os.fspath(path)))
        value = formal.lstat()
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or int(getattr(value, "st_file_attributes", 0)) & _REPARSE_POINT
            or value.st_size <= 0
        ):
            raise ValueError
        return formal
    except (OSError, TypeError, ValueError):
        raise BilibiliSourceError("bilibili-media-invalid") from None


def _positive_number(value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise BilibiliSourceError("bilibili-media-invalid") from None
    if not math.isfinite(result) or result <= 0:
        raise BilibiliSourceError("bilibili-media-invalid")
    return result


def probe_complete_av(
    path: Path,
    *,
    ffprobe: str = "ffprobe",
) -> BilibiliMediaProbe:
    """Require one valid local MP4 with video and audio technical metadata."""

    source = _ordinary_file(path)
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=index,codec_type,codec_name,width,height,display_aspect_ratio,r_frame_rate,sample_rate,channels,duration",
                "-of",
                "json",
                str(source),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise ValueError
        value = json.loads(completed.stdout)
        format_value = value["format"]
        streams = value["streams"]
        names = {
            part.strip().lower()
            for part in str(format_value["format_name"]).split(",")
        }
        if "mp4" not in names or not isinstance(streams, list):
            raise ValueError
        duration = _positive_number(format_value["duration"])
        videos = [
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ]
        audios = [
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ]
        if not videos or not audios:
            raise ValueError
        video = videos[0]
        audio = audios[0]
        width = int(video["width"])
        height = int(video["height"])
        sample_rate = int(audio["sample_rate"])
        channels = int(audio["channels"])
        if min(width, height, sample_rate, channels) <= 0:
            raise ValueError
        frame_rate = str(video["r_frame_rate"])
        rate_parts = frame_rate.split("/")
        if len(rate_parts) not in {1, 2}:
            raise ValueError
        rate = _positive_number(rate_parts[0])
        if len(rate_parts) == 2:
            rate /= _positive_number(rate_parts[1])
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError
        for stream in (video, audio):
            stream_duration = stream.get("duration")
            if stream_duration not in {None, "N/A"} and abs(
                _positive_number(stream_duration) - duration
            ) > 1.0:
                raise ValueError
        display_aspect_ratio = str(video.get("display_aspect_ratio") or f"{width}:{height}")
        video_codec = video["codec_name"]
        audio_codec = audio["codec_name"]
        if not isinstance(video_codec, str) or not isinstance(audio_codec, str):
            raise ValueError
        return BilibiliMediaProbe(
            container="mp4",
            duration_s=duration,
            width=width,
            height=height,
            display_aspect_ratio=display_aspect_ratio,
            frame_rate=frame_rate,
            video_codec=video_codec,
            audio_codec=audio_codec,
            audio_sample_rate=sample_rate,
            audio_channels=channels,
        )
    except BilibiliSourceError:
        raise
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        raise BilibiliSourceError("bilibili-media-invalid") from None


def _probe_value(probe: BilibiliMediaProbe) -> dict[str, object]:
    return {
        "schema_version": 1,
        "container": probe.container,
        "duration_s": probe.duration_s,
        "width": probe.width,
        "height": probe.height,
        "display_aspect_ratio": probe.display_aspect_ratio,
        "frame_rate": probe.frame_rate,
        "video_codec": probe.video_codec,
        "audio_codec": probe.audio_codec,
        "audio_sample_rate": probe.audio_sample_rate,
        "audio_channels": probe.audio_channels,
    }


def _downloaded_inputs(root: Path, work_id: str):
    try:
        order = load_work_order(root, work_id)
        if order.source_kind != "url":
            raise ValueError
        private_root = order.private_root
        source_input = verify_private_relative(private_root, "source-input.txt")
        plan_path = verify_private_relative(private_root, MEDIA_PLAN_FILE)
        plan = load_media_plan(plan_path)
        if (
            sha256_file(source_input) != order.source_input_sha256
            or plan.input_sha256 != order.source_input_sha256
        ):
            raise ValueError
        selection_path = verify_private_relative(private_root, MEDIA_SELECTION_FILE)
        if sha256_file(selection_path) != plan.selection_sha256:
            raise ValueError
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        if not isinstance(selection, dict) or selection.get("mode") not in {
            "progressive",
            "dash",
        }:
            raise ValueError
        result_path = verify_private_relative(
            private_root, MEDIA_DOWNLOAD_RESULT_FILE
        )
        result_sha256 = sha256_file(result_path)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not (
            isinstance(result, dict)
            and result.get("status") == "succeeded"
            and result.get("work_id") == work_id
            and result.get("plan_sha256") == sha256_file(plan_path)
            and result.get("selection_sha256") == plan.selection_sha256
            and result.get("upstream_response_sha256")
            == plan.upstream_response_sha256
            and result.get("media_assets_completed") == plan.media_asset_count
            and isinstance(result.get("assets"), list)
            and len(result["assets"]) == plan.media_asset_count
        ):
            raise ValueError
        paths: list[Path] = []
        hashes: list[str] = []
        expected_names = (
            ["progressive.mp4"]
            if selection["mode"] == "progressive"
            else ["video.m4s", "audio.m4a"]
        )
        for item, name in zip(result["assets"], expected_names, strict=True):
            if not isinstance(item, dict) or item.get("relative_path") != name:
                raise ValueError
            path = verify_private_relative(
                private_root, f"source-acquisition/{name}"
            )
            digest = sha256_file(path)
            if digest != item.get("sha256") or path.stat().st_size != item.get(
                "size_bytes"
            ):
                raise ValueError
            paths.append(path)
            hashes.append(digest)
        return private_root, plan, result_sha256, selection["mode"], paths, hashes
    except (
        SourceContractError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        raise BilibiliSourceError("bilibili-finalize-input-invalid") from None


def _assemble(
    inputs: list[Path],
    mode: str,
    temporary: Path,
    *,
    ffmpeg: str,
) -> None:
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n"]
    for source in inputs:
        command.extend(["-protocol_whitelist", "file", "-i", str(source)])
    if mode == "progressive":
        command.extend(["-map", "0:v:0", "-map", "0:a:0"])
    else:
        command.extend(["-map", "0:v:0", "-map", "1:a:0"])
    command.extend(["-c", "copy", "-movflags", "+faststart", str(temporary)])
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError):
        raise BilibiliSourceError("bilibili-mux-failed") from None
    if result.returncode != 0:
        raise BilibiliSourceError("bilibili-mux-failed")


def _full_decode(path: Path, *, ffmpeg: str) -> None:
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-protocol_whitelist",
                "file",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError):
        raise BilibiliSourceError("bilibili-full-decode-failed") from None
    if result.returncode != 0:
        raise BilibiliSourceError("bilibili-full-decode-failed")


def _result_from_existing_audio(
    root: Path,
    work_id: str,
    source_original: Path,
    *,
    ffmpeg: str,
    ffprobe: str,
) -> BilibiliFinalizeResult:
    probe = probe_complete_av(source_original, ffprobe=ffprobe)
    audio = normalize_source_audio(
        root, work_id, ffmpeg=ffmpeg, ffprobe=ffprobe
    )
    order = load_work_order(root, work_id)
    media = verify_private_relative(order.private_root, "source-media/original.mp4")
    return BilibiliFinalizeResult(
        source_media=ArtifactRecord(
            "source-media/original.mp4",
            sha256_file(media),
            media.stat().st_size,
            media,
        ),
        source_audio=audio.artifact,
        media_sha256=sha256_file(media),
        audio_sha256=audio.artifact.sha256,
        duration_s=probe.duration_s,
        width=probe.width,
        height=probe.height,
    )


def finalize_bilibili_source(
    root: Path,
    work_id: str,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> BilibiliFinalizeResult:
    """Finalize downloaded assets locally and stop at ``audio_ready``."""

    root = Path(root)
    try:
        order = load_work_order(root, work_id)
        stage = WashEventLedger(root).status(work_id)
        source_original = verify_private_relative(
            order.private_root, "source-acquisition/source-original.mp4"
        )
        if stage == "media_ready":
            return _result_from_existing_audio(
                root,
                work_id,
                source_original,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
        if stage != "source_registered":
            raise BilibiliSourceError("bilibili-finalize-stage-invalid")
        private_root, plan, download_result_sha, mode, inputs, before = (
            _downloaded_inputs(root, work_id)
        )
        probe_path = verify_private_relative(private_root, _FFPROBE_FILE)
        qc_path = verify_private_relative(private_root, _QC_FILE)
        if probe_path.exists() or qc_path.exists():
            raise BilibiliSourceError("bilibili-finalize-artifact-exists")
        output_dir = source_original.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".source-original.", suffix=".mp4", dir=output_dir
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.unlink()
        try:
            _assemble(inputs, mode, temporary, ffmpeg=ffmpeg)
            if [sha256_file(path) for path in inputs] != before:
                raise BilibiliSourceError("bilibili-finalize-input-changed")
            if source_original.exists() or source_original.is_symlink():
                if sha256_file(temporary) != sha256_file(source_original):
                    raise BilibiliSourceError("bilibili-finalize-output-changed")
            else:
                try:
                    os.link(temporary, source_original)
                except FileExistsError:
                    raise BilibiliSourceError(
                        "bilibili-finalize-artifact-exists"
                    ) from None
        finally:
            temporary.unlink(missing_ok=True)
        media_sha256 = sha256_file(source_original)
        probe = probe_complete_av(source_original, ffprobe=ffprobe)
        if sha256_file(source_original) != media_sha256:
            raise BilibiliSourceError("bilibili-finalize-output-changed")
        _full_decode(source_original, ffmpeg=ffmpeg)
        if sha256_file(source_original) != media_sha256:
            raise BilibiliSourceError("bilibili-finalize-output-changed")
        probe_sha = publish_json_exclusive(
            private_root, probe_path, _probe_value(probe)
        )
        publish_json_exclusive(
            private_root,
            qc_path,
            {
                "schema_version": 1,
                "work_id": work_id,
                "media_sha256": media_sha256,
                "media_size_bytes": source_original.stat().st_size,
                "probe_sha256": probe_sha,
                "full_decode_passed": True,
                "selection_sha256": plan.selection_sha256,
                "download_result_sha256": download_result_sha,
            },
        )
        source_media = snapshot_acquired_source(root, work_id, source_original)
        audio = normalize_source_audio(
            root, work_id, ffmpeg=ffmpeg, ffprobe=ffprobe
        )
        return BilibiliFinalizeResult(
            source_media=source_media,
            source_audio=audio.artifact,
            media_sha256=source_media.sha256,
            audio_sha256=audio.artifact.sha256,
            duration_s=probe.duration_s,
            width=probe.width,
            height=probe.height,
        )
    except BilibiliSourceError:
        raise
    except (
        SourceAudioError,
        SourceContractError,
        SourceLedgerError,
        SourceSnapshotError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise BilibiliSourceError("bilibili-finalize-failed") from None


__all__ = [
    "BilibiliFinalizeResult",
    "BilibiliMediaProbe",
    "BilibiliSourceError",
    "finalize_bilibili_source",
    "probe_complete_av",
]

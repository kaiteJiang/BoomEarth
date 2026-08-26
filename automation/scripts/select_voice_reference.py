from __future__ import annotations

from array import array
from dataclasses import dataclass
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import wave


def _canonical_workspace_root(source_root: Path) -> Path:
    marker = source_root / ".git"
    if not marker.is_file():
        return source_root
    try:
        prefix, separator, raw_git_dir = marker.read_text(
            encoding="utf-8"
        ).strip().partition(":")
        if prefix.casefold() != "gitdir" or separator != ":":
            return source_root
        git_dir = Path(raw_git_dir.strip())
        if not git_dir.is_absolute():
            git_dir = marker.parent / git_dir
        common_value = (git_dir / "commondir").read_text(encoding="utf-8").strip()
        common_dir = Path(common_value)
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir
        candidate = common_dir.resolve(strict=True).parent
        if (candidate / "automation" / "config" / "tts-routing.json").is_file():
            return candidate
    except (OSError, RuntimeError, UnicodeDecodeError):
        pass
    return source_root


FRAME_SECONDS = 0.02
TARGET_SAMPLE_RATE = 24_000
TARGET_CHANNELS = 1
PCM16_CODEC = "pcm_s16le"
MIN_WINDOW_SECONDS = 15.0
MAX_WINDOW_SECONDS = 20.0
ACTIVE_RMS_DBFS = -42.0
SILENCE_RMS_DBFS = -50.0
CLIPPING_SAMPLE = 32_700
MAX_CLIPPING_RATIO = 0.001
MIN_ACTIVE_RATIO = 0.70
MAX_OUTPUT_PEAK = int(32_767 * (10 ** (-3.0 / 20.0)))
VOICE_ID = "user-indextts2-black-gold-v3"
RECEIPT_SCHEMA = "boomearth.voice-reference-qc/v3"
AUTHORIZED_SOURCE_ENV = "BOOMEARTH_AUTHORIZED_VOICE_SOURCE"
WORKSPACE = _canonical_workspace_root(Path(__file__).resolve().parents[2])
PRIVATE_VOICE_DIRECTORY = (
    WORKSPACE / "01-内容生产" / "视频工作台" / ".internal" / "voice"
)
CANONICAL_REFERENCE_OUTPUT = PRIVATE_VOICE_DIRECTORY / f"{VOICE_ID}.wav"
CANONICAL_REFERENCE_RECEIPT = PRIVATE_VOICE_DIRECTORY / f"{VOICE_ID}.qc.json"
EDGE_EXCLUSION_SECONDS = 0.5


@dataclass(frozen=True)
class FrameMetric:
    start_seconds: float
    rms_dbfs: float
    active: bool
    silent: bool
    clipping_ratio: float


@dataclass(frozen=True)
class WindowScore:
    start_seconds: float
    duration_seconds: float
    score: float
    active_ratio: float
    silence_ratio: float
    clipping_ratio: float
    rms_stability: float
    edge_penalty: float


@dataclass(frozen=True)
class _CreatedArtifact:
    path: Path
    device: int
    inode: int


class _InputOrQualityFailure(Exception):
    pass


class _NoClobberFailure(Exception):
    pass


class _ToolFailure(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def analyze_pcm_frames(samples: array, sample_rate: int) -> tuple[FrameMetric, ...]:
    """Return deterministic 20 ms metrics for PCM16 mono samples."""
    if sample_rate <= 0:
        raise ValueError("sample_rate")

    frame_size = int(sample_rate * FRAME_SECONDS)
    if frame_size <= 0:
        raise ValueError("frame_size")

    metrics: list[FrameMetric] = []
    for frame_start in range(0, len(samples) - frame_size + 1, frame_size):
        frame = samples[frame_start : frame_start + frame_size]
        rms = math.sqrt(sum(sample * sample for sample in frame) / frame_size)
        rms_dbfs = -120.0 if rms == 0 else 20.0 * math.log10(rms / 32_767)
        clipping_ratio = sum(abs(sample) >= CLIPPING_SAMPLE for sample in frame) / frame_size
        metrics.append(
            FrameMetric(
                start_seconds=frame_start / sample_rate,
                rms_dbfs=rms_dbfs,
                active=rms_dbfs > ACTIVE_RMS_DBFS,
                silent=rms_dbfs < SILENCE_RMS_DBFS,
                clipping_ratio=clipping_ratio,
            )
        )
    return tuple(metrics)


def rank_windows(
    metrics: tuple[FrameMetric, ...], sample_rate: int, window_seconds: float
) -> tuple[WindowScore, ...]:
    """Rank eligible windows on half-second boundaries, best then earliest."""
    if sample_rate <= 0 or not math.isfinite(window_seconds):
        raise ValueError("window_seconds")

    frame_size = int(sample_rate * FRAME_SECONDS)
    window_frames = int(round(window_seconds * sample_rate / frame_size))
    boundary_frames = int(round(0.5 * sample_rate / frame_size))
    if window_frames <= 0 or boundary_frames <= 0:
        raise ValueError("window_seconds")

    total_duration = len(metrics) * FRAME_SECONDS
    scores: list[WindowScore] = []
    for first_frame in range(0, len(metrics) - window_frames + 1, boundary_frames):
        window = metrics[first_frame : first_frame + window_frames]
        active_ratio = sum(metric.active for metric in window) / window_frames
        silence_ratio = sum(metric.silent for metric in window) / window_frames
        clipping_ratio = sum(metric.clipping_ratio for metric in window) / window_frames
        if active_ratio < MIN_ACTIVE_RATIO or clipping_ratio > MAX_CLIPPING_RATIO:
            continue

        rms_values = [metric.rms_dbfs for metric in window if metric.active]
        rms_stability = _population_stdev(rms_values)
        start_seconds = window[0].start_seconds
        end_seconds = start_seconds + window_seconds
        if (
            start_seconds <= EDGE_EXCLUSION_SECONDS
            or end_seconds >= total_duration - EDGE_EXCLUSION_SECONDS
        ):
            continue
        edge_penalty = 0.0
        score = round(
            active_ratio
            - (0.75 * silence_ratio)
            - (2.0 * clipping_ratio)
            - (0.01 * rms_stability)
            - edge_penalty,
            6,
        )
        scores.append(
            WindowScore(
                start_seconds=start_seconds,
                duration_seconds=window_seconds,
                score=score,
                active_ratio=active_ratio,
                silence_ratio=silence_ratio,
                clipping_ratio=clipping_ratio,
                rms_stability=rms_stability,
                edge_penalty=edge_penalty,
            )
        )
    return tuple(sorted(scores, key=lambda item: (-item.score, item.start_seconds)))


def select_window(scores: tuple[WindowScore, ...]) -> WindowScore:
    if not scores:
        raise ValueError("no_eligible_window")
    return scores[0]


def build_receipt(
    *,
    source_metadata: dict[str, object],
    selected: WindowScore,
    conversion: dict[str, object],
    output_sha256: str,
) -> dict[str, object]:
    """Build the fixed private receipt without source content or a transcript."""
    return {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "source_metadata": source_metadata,
        "selected_interval": {
            "start_seconds": selected.start_seconds,
            "duration_seconds": selected.duration_seconds,
        },
        "metrics": {
            "score": selected.score,
            "active_ratio": selected.active_ratio,
            "silence_ratio": selected.silence_ratio,
            "clipping_ratio": selected.clipping_ratio,
            "rms_stability": selected.rms_stability,
            "edge_penalty": selected.edge_penalty,
        },
        "conversion": conversion,
        "output_sha256": output_sha256,
    }


def _population_stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(add_help=False)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--window-seconds", type=float, default=18.0)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--ffprobe", type=Path)
    return parser


def _resolve_tool(configured_path: Path | None, command: str) -> Path | None:
    if configured_path is not None:
        return configured_path if configured_path.is_file() else None
    resolved = shutil.which(command)
    return Path(resolved) if resolved else None


def _run_tool(arguments: list[str]) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(arguments, capture_output=True, check=False)
    except OSError:
        return None


def _decode_to_pcm(ffmpeg: Path, source: Path, decoded: Path) -> bool | None:
    result = _run_tool(
        [
            str(ffmpeg),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-vn",
            "-ac",
            str(TARGET_CHANNELS),
            "-ar",
            str(TARGET_SAMPLE_RATE),
            "-c:a",
            PCM16_CODEC,
            "-n",
            str(decoded),
        ]
    )
    return None if result is None else result.returncode == 0


def _read_pcm(decoded: Path) -> tuple[array, int]:
    with wave.open(str(decoded), "rb") as reader:
        if (
            reader.getnchannels() != TARGET_CHANNELS
            or reader.getsampwidth() != 2
            or reader.getframerate() != TARGET_SAMPLE_RATE
            or reader.getcomptype() != "NONE"
        ):
            raise ValueError("decoded_pcm_contract")
        samples = array("h")
        samples.frombytes(reader.readframes(reader.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples, TARGET_SAMPLE_RATE


def _attenuate_if_needed(samples: array) -> tuple[array, float]:
    peak = max((abs(sample) for sample in samples), default=0)
    if peak <= MAX_OUTPUT_PEAK:
        return samples, 0.0
    scale = MAX_OUTPUT_PEAK / peak
    adjusted = array("h", (int(round(sample * scale)) for sample in samples))
    return adjusted, 20.0 * math.log10(scale)


def _record_created_artifact(stream, path: Path, created_artifacts: list[_CreatedArtifact]) -> None:
    details = os.fstat(stream.fileno())
    created_artifacts.append(_CreatedArtifact(path=path, device=details.st_dev, inode=details.st_ino))


def _register_exclusively_created_artifact(stream, path: Path, created_artifacts: list[_CreatedArtifact]) -> None:
    try:
        _record_created_artifact(stream, path, created_artifacts)
    except Exception:
        stream.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        raise


def _write_pcm16_wav_exclusive(
    output: Path, samples: array, sample_rate: int, created_artifacts: list[_CreatedArtifact]
) -> None:
    with output.open("xb") as stream:
        _register_exclusively_created_artifact(stream, output, created_artifacts)
        with wave.open(stream, "wb") as writer:
            writer.setnchannels(TARGET_CHANNELS)
            writer.setsampwidth(2)
            writer.setframerate(sample_rate)
            writer.writeframes(samples.tobytes())


def _write_receipt_exclusive(
    receipt: Path, receipt_data: dict[str, object], created_artifacts: list[_CreatedArtifact]
) -> None:
    with receipt.open("x", encoding="utf-8", newline="\n") as stream:
        _register_exclusively_created_artifact(stream, receipt, created_artifacts)
        json.dump(receipt_data, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")


def _rollback_created_artifacts(created_artifacts: list[_CreatedArtifact]) -> None:
    for artifact in reversed(created_artifacts):
        try:
            details = artifact.path.stat()
            if details.st_dev == artifact.device and details.st_ino == artifact.inode:
                artifact.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_output(ffprobe: Path, output: Path, duration_seconds: float) -> bool | None:
    result = _run_tool(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration",
            "-of",
            "json",
            str(output),
        ]
    )
    if result is None:
        return None
    if result.returncode != 0:
        return False
    try:
        details = json.loads(result.stdout)
        stream = details["streams"][0]
        duration = float(details["format"]["duration"])
        return (
            stream["codec_name"] == PCM16_CODEC
            and int(stream["sample_rate"]) == TARGET_SAMPLE_RATE
            and int(stream["channels"]) == TARGET_CHANNELS
            and math.isclose(duration, duration_seconds, abs_tol=0.01)
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _safe_status(status: str) -> None:
    print(f"status={status}")


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attributes & 0x400)


def _has_reparse_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    for candidate in (absolute, *absolute.parents):
        if candidate.exists() or candidate.is_symlink():
            if _is_reparse_point(candidate):
                return True
    return False


def _is_ordinary_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _is_ordinary_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def _path_is_git_ignored(path: Path) -> bool:
    try:
        relative = path.resolve(strict=False).relative_to(WORKSPACE.resolve(strict=True))
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(relative)],
            cwd=WORKSPACE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, ValueError):
        return False
    return result.returncode == 0


def _path_is_tracked(path: Path) -> bool:
    try:
        relative = path.resolve(strict=False).relative_to(WORKSPACE.resolve(strict=True))
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(relative)],
            cwd=WORKSPACE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, ValueError):
        return True
    return result.returncode == 0


def _is_safe_canonical_request(source: Path, output: Path, receipt: Path) -> bool:
    authorized_value = os.environ.get(AUTHORIZED_SOURCE_ENV)
    if not authorized_value:
        return False
    authorized_source = Path(authorized_value)
    paths = (source, authorized_source, output, receipt, PRIVATE_VOICE_DIRECTORY)
    if any(not path.is_absolute() or ".." in path.parts or _has_reparse_component(path) for path in paths):
        return False
    if str(source) != authorized_value or str(output) != str(CANONICAL_REFERENCE_OUTPUT) or str(receipt) != str(CANONICAL_REFERENCE_RECEIPT):
        return False
    try:
        if source.resolve(strict=True) != authorized_source.resolve(strict=True):
            return False
        if output.parent.resolve(strict=True) != PRIVATE_VOICE_DIRECTORY.resolve(strict=True):
            return False
    except OSError:
        return False
    if not _is_ordinary_file(source) or not _is_ordinary_directory(PRIVATE_VOICE_DIRECTORY):
        return False
    return all(
        _path_is_git_ignored(path) and not _path_is_tracked(path)
        for path in (output, receipt)
    )


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _build_parser().parse_args(argv)
    except (SystemExit, ValueError):
        _safe_status("FAIL")
        return 2

    if not math.isfinite(arguments.window_seconds) or not (
        MIN_WINDOW_SECONDS <= arguments.window_seconds <= MAX_WINDOW_SECONDS
    ):
        _safe_status("FAIL")
        return 2

    source = arguments.source
    output = arguments.output
    receipt = arguments.receipt
    if not _is_safe_canonical_request(source, output, receipt):
        _safe_status("FAIL")
        return 2
    try:
        output_exists = output.exists()
        receipt_exists = receipt.exists()
        source_is_file = source.is_file()
        output_parent_is_dir = output.parent.is_dir()
        receipt_parent_is_dir = receipt.parent.is_dir()
    except OSError:
        _safe_status("FAIL")
        return 3

    if output_exists or receipt_exists or not source_is_file:
        _safe_status("FAIL")
        return 2
    if not output_parent_is_dir or not receipt_parent_is_dir:
        _safe_status("FAIL")
        return 2

    ffmpeg = _resolve_tool(arguments.ffmpeg, "ffmpeg")
    ffprobe = _resolve_tool(arguments.ffprobe, "ffprobe")
    if ffmpeg is None or ffprobe is None:
        _safe_status("FAIL")
        return 3

    created_artifacts: list[_CreatedArtifact] = []
    result_code = 3
    try:
        with tempfile.TemporaryDirectory(dir=output.parent, prefix="voice-reference-") as temporary_directory:
            decoded = Path(temporary_directory) / "decoded.wav"
            decoded_ok = _decode_to_pcm(ffmpeg, source, decoded)
            if decoded_ok is None:
                raise _ToolFailure
            if not decoded_ok:
                raise _InputOrQualityFailure

            try:
                samples, sample_rate = _read_pcm(decoded)
            except (OSError, ValueError, wave.Error) as error:
                raise _ToolFailure from error
            try:
                selected = select_window(
                    rank_windows(analyze_pcm_frames(samples, sample_rate), sample_rate, arguments.window_seconds)
                )
            except ValueError as error:
                raise _InputOrQualityFailure from error
            start = int(round(selected.start_seconds * sample_rate))
            count = int(round(selected.duration_seconds * sample_rate))
            selected_samples, attenuation_db = _attenuate_if_needed(samples[start : start + count])
            if len(selected_samples) != count:
                raise _InputOrQualityFailure

            try:
                _write_pcm16_wav_exclusive(output, selected_samples, sample_rate, created_artifacts)
            except FileExistsError as error:
                raise _NoClobberFailure from error
            verified = _verify_output(ffprobe, output, selected.duration_seconds)
            if verified is None:
                raise _ToolFailure
            if not verified:
                raise _ToolFailure

            receipt_data = build_receipt(
                source_metadata={"name": source.name, "size_bytes": source.stat().st_size},
                selected=selected,
                conversion={
                    "codec": PCM16_CODEC,
                    "sample_rate": sample_rate,
                    "channels": TARGET_CHANNELS,
                    "attenuation_db": attenuation_db,
                },
                output_sha256=_sha256(output),
            )
            try:
                _write_receipt_exclusive(receipt, receipt_data, created_artifacts)
            except FileExistsError as error:
                raise _NoClobberFailure from error
        result_code = 0
    except (_InputOrQualityFailure, _NoClobberFailure):
        result_code = 2
    except (_ToolFailure, OSError, ValueError, wave.Error):
        result_code = 3
    except Exception:
        result_code = 3
    finally:
        if result_code != 0:
            _rollback_created_artifacts(created_artifacts)

    if result_code != 0:
        _safe_status("FAIL")
        return result_code
    _safe_status("PASS")
    print(f"voice_id={VOICE_ID}")
    print(f"selected_duration_seconds={arguments.window_seconds:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Coordinate a checked V2 production render; this command never calls a provider."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from boomearth.audio.indextts2 import CURRENT_VOICE_ID
from check_delivery import DeliveryResult, check_delivery
from prepare_v2_production_sample import PreparedProject, prepare_project


CAPTION_FILES = (
    "asr-result.json",
    "captions_words.json",
    "captions.json",
    "captions.srt",
    "captions.vtt",
    "caption-qc.json",
)
REPARSE_POINT = 0x0400


@dataclass(frozen=True, slots=True)
class ProductionProject:
    """The exact active-project locations required by the production handoff."""

    workspace_root: Path
    archive_slug: str
    project_date: date
    active_identity: tuple[int, int]
    active_dir: Path
    archive_dir: Path
    media_dir: Path
    narration_path: Path
    manifest_path: Path
    captions_dir: Path
    render_project_dir: Path
    render_path: Path
    contact_sheet_path: Path
    contact_sheet_qc_path: Path
    caption_render_qc_path: Path
    handoff_path: Path
    delivery_report_path: Path


@dataclass(frozen=True, slots=True)
class ProductionSampleResult:
    exit_code: int
    diagnostics: tuple[str, ...]
    archive_path: str | None = None
    recovery_stage: str | None = None


def _is_reparse_point(path: Path) -> bool:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(entry, "st_file_attributes", 0) & REPARSE_POINT)


def _has_safe_ancestors(path: Path) -> bool:
    current = path.absolute()
    while True:
        if _is_reparse_point(current):
            return False
        if current == current.parent:
            return True
        current = current.parent


def _is_contained(root: Path, target: Path) -> bool:
    try:
        target.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    return _has_safe_ancestors(root.absolute()) and _has_safe_ancestors(target.absolute())


def _safe_existing_file(path: Path) -> bool:
    try:
        return _has_safe_ancestors(path) and stat.S_ISREG(path.lstat().st_mode) and not _is_reparse_point(path)
    except (OSError, RuntimeError):
        return False


def _safe_existing_directory(path: Path) -> bool:
    try:
        return _has_safe_ancestors(path) and stat.S_ISDIR(path.lstat().st_mode) and not _is_reparse_point(path)
    except OSError:
        return False


def _safe_mkdir(root: Path, target: Path) -> None:
    if not _is_contained(root, target) or _is_reparse_point(target):
        raise RuntimeError("unsafe-directory")
    target.mkdir(parents=True, exist_ok=True)
    if not _is_contained(root, target) or not _safe_existing_directory(target):
        raise RuntimeError("unsafe-directory")


def _safe_destination(root: Path, target: Path) -> None:
    if not _is_contained(root, target) or _is_reparse_point(target):
        raise RuntimeError("unsafe-output")
    _safe_mkdir(root, target.parent)
    if not _is_contained(root, target) or _is_reparse_point(target):
        raise RuntimeError("unsafe-output")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_slug(slug: str) -> bool:
    return re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug) is not None


def _period(today: date) -> str:
    return f"{today.month}月{'上旬' if today.day <= 15 else '下旬'}"


def _write_json(root: Path, path: Path, value: object) -> None:
    _safe_destination(root, path)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(root: Path, path: Path, value: str) -> None:
    _safe_destination(root, path)
    path.write_text(value, encoding="utf-8", newline="\n")


def _read_stable_file_bytes(path: Path) -> bytes:
    if not _safe_existing_file(path):
        raise RuntimeError("unsafe-source")
    before = path.lstat()
    try:
        with path.open("rb") as source:
            payload = source.read()
        after = path.lstat()
    except OSError:
        raise RuntimeError("unsafe-source") from None
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    if (
        identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(payload) != before.st_size
        or not _safe_existing_file(path)
    ):
        raise RuntimeError("unsafe-source")
    return payload


def _write_exact_file_atomically(root: Path, destination: Path, payload: bytes) -> None:
    _safe_destination(root, destination)
    temporary = destination.with_name(f".{destination.name}.pending")
    if temporary.exists() or _is_reparse_point(temporary):
        raise RuntimeError("unsafe-output")
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise RuntimeError("unsafe-output") from None
    if _read_stable_file_bytes(temporary) != payload:
        raise RuntimeError("unsafe-output")
    try:
        os.replace(temporary, destination)
    except OSError:
        raise RuntimeError("unsafe-output") from None
    if _read_stable_file_bytes(destination) != payload:
        raise RuntimeError("unsafe-output")


def _after_delivery_receipt_capture(report: Path) -> None:
    """Test seam after exact checker receipt bytes are captured and validated."""


def _handoff(slug: str, status: str, *, duration_seconds: float, word_count: int) -> str:
    receipt = ""
    if status == "已完成":
        receipt = (
            "\n## 制作回执\n\n"
            "- 交付检查：pass\n"
            "- 归档状态：已完成\n"
            "\n## QC结果\n\n"
            "- 联系表：6 帧（3x2），覆盖开场、中段和结尾\n"
            "- 字幕：最终 WAV 的词级时间戳\n"
        )
    return (
        "---\n"
        f"status: {status}\n"
        "platform: local-v1\n"
        "ratio: '16:9'\n"
        f"duration_target_s: {duration_seconds:.3f}\n"
        f"word_count: {word_count}\n"
        f"voice: {CURRENT_VOICE_ID}\n"
        "voice_provider: indextts2-local\n"
        "captions: asr-word-timestamps\n"
        "caption_style: anchor-dark\n"
        "visual: production-template\n"
        "illustration_skill: none\n"
        f"archive_slug: {slug}\n"
        "---\n\n"
        "## 新稿分段\n\n"
        "制作中的无来源交付稿。\n"
        f"{receipt}"
    )


def _run_command(argv: list[str], *, cwd: Path) -> None:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, check=False, timeout=240)
    if result.returncode != 0:
        raise RuntimeError("local-command-failed")


def _preflight(repo_root: Path) -> tuple[Path, Path]:
    node = shutil.which("node.exe") or shutil.which("node")
    entrypoint = repo_root / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    required = (
        repo_root / "automation" / "scripts" / "prepare_v2_production_sample.py",
        entrypoint,
        repo_root / "video-production-sample" / "index.template.html",
    )
    if (
        not _has_safe_ancestors(repo_root)
        or node is None
        or not _safe_existing_file(Path(node))
        or not all(_safe_existing_file(path) for path in required)
        or shutil.which("ffmpeg") is None
        or shutil.which("ffprobe") is None
    ):
        raise RuntimeError("local-prerequisite")
    return Path(node), entrypoint


def _open_no_reparse_directory(path: Path, access: int) -> int:
    kernel32 = ctypes.windll.kernel32
    create_file = kernel32.CreateFileW
    create_file.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p)
    create_file.restype = ctypes.c_void_p
    handle = create_file(str(path), access, 0x00000007, None, 3, 0x02000000 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise RuntimeError("unsafe-directory")
    return int(handle)


def _handle_directory_identity(handle: int) -> tuple[int, int]:
    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", ctypes.c_uint32), ("creation_low", ctypes.c_uint32), ("creation_high", ctypes.c_uint32),
            ("access_low", ctypes.c_uint32), ("access_high", ctypes.c_uint32),
            ("write_low", ctypes.c_uint32), ("write_high", ctypes.c_uint32),
            ("volume", ctypes.c_uint32), ("size_high", ctypes.c_uint32), ("size_low", ctypes.c_uint32),
            ("links", ctypes.c_uint32), ("index_high", ctypes.c_uint32), ("index_low", ctypes.c_uint32),
        ]
    value = ByHandleFileInformation()
    if not ctypes.windll.kernel32.GetFileInformationByHandle(ctypes.c_void_p(handle), ctypes.byref(value)):
        raise RuntimeError("unsafe-directory")
    return value.volume, (value.index_high << 32) | value.index_low


def _close_handle(handle: int) -> None:
    ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def _path_from_directory_handle(handle: int) -> Path:
    size = 32_768
    buffer = ctypes.create_unicode_buffer(size)
    length = ctypes.windll.kernel32.GetFinalPathNameByHandleW(
        ctypes.c_void_p(handle), buffer, size, 0
    )
    if length == 0 or length >= size:
        raise RuntimeError("unsafe-directory")
    value = buffer.value
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _move_directory_no_replace(
    stage: Path,
    destination: Path,
    *,
    expected_source_identity: tuple[int, int],
    expected_parent_identity: tuple[int, int],
    expected_source_manifest: dict[str, str] | None = None,
) -> None:
    """Rename through opened directory handles on Windows; fallback is only for non-Windows tests."""
    if os.name == "nt":
        if _directory_identity(stage) != expected_source_identity or _directory_identity(destination.parent) != expected_parent_identity:
            raise RuntimeError("unsafe-directory")
        ntdll = ctypes.WinDLL("ntdll")
        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_void_p)]
        set_information = ntdll.NtSetInformationFile
        set_information.argtypes = (ctypes.c_void_p, ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int)
        set_information.restype = ctypes.c_long
        source_handle = _open_no_reparse_directory(stage, 0x00010000)
        try:
            parent_handle = _open_no_reparse_directory(destination.parent, 0x00000007)
        except RuntimeError:
            _close_handle(source_handle)
            raise
        try:
            if _handle_directory_identity(source_handle) != expected_source_identity or _handle_directory_identity(parent_handle) != expected_parent_identity:
                raise RuntimeError("unsafe-directory")
            _after_rename_handles_open(stage, destination)
            if expected_source_manifest is not None and not _publication_manifest_matches(
                _path_from_directory_handle(source_handle), expected_source_manifest
            ):
                raise RuntimeError("publication-input")
            encoded_name = destination.name.encode("utf-16-le")
            class RenameHeader(ctypes.Structure):
                _fields_ = [("replace", ctypes.c_ubyte), ("root", ctypes.c_void_p), ("length", ctypes.c_uint32)]
            name_offset = RenameHeader.length.offset + ctypes.sizeof(ctypes.c_uint32)
            information_size = name_offset + len(encoded_name)
            buffer = (ctypes.c_byte * max(ctypes.sizeof(RenameHeader), information_size))()
            header = RenameHeader.from_buffer(buffer)
            header.replace, header.root, header.length = False, parent_handle, len(encoded_name)
            ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded_name, len(encoded_name))
            status = IoStatusBlock()
            result = set_information(source_handle, ctypes.byref(status), ctypes.byref(buffer), len(buffer), 10)
            if result != 0:
                raise FileExistsError("archive destination is unavailable")
        finally:
            _close_handle(source_handle)
            _close_handle(parent_handle)
        return
    if _directory_identity(stage) != expected_source_identity or _directory_identity(destination.parent) != expected_parent_identity or destination.exists():
        raise FileExistsError("archive destination is unavailable")
    if expected_source_manifest is not None and not _publication_manifest_matches(stage, expected_source_manifest):
        raise RuntimeError("publication-input")
    os.rename(stage, destination)


def _after_rename_handles_open(stage: Path, destination: Path) -> None:
    """Test seam after source and destination-parent handles are fixed."""


def _before_archive_move(stage: Path, destination: Path) -> None:
    """Test seam immediately before an archive source path is opened."""


def _after_active_parent_handle_open(parent: Path, leaf: str) -> None:
    """Test seam after the active parent handle is fixed."""


def _create_active_leaf_no_replace(parent: Path, leaf: str, *, expected_parent_identity: tuple[int, int]) -> None:
    """Create an active leaf relative to a verified parent handle on Windows."""
    if os.name != "nt":
        if _directory_identity(parent) != expected_parent_identity:
            raise RuntimeError("unsafe-directory")
        (parent / leaf).mkdir(exist_ok=False)
        return
    if _directory_identity(parent) != expected_parent_identity:
        raise RuntimeError("unsafe-directory")
    parent_handle = _open_no_reparse_directory(parent, 0x00000007)
    try:
        if _handle_directory_identity(parent_handle) != expected_parent_identity:
            raise RuntimeError("unsafe-directory")
        _after_active_parent_handle_open(parent, leaf)
        ntdll = ctypes.WinDLL("ntdll")
        class UnicodeString(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ushort), ("maximum_length", ctypes.c_ushort), ("buffer", ctypes.c_wchar_p)]
        class ObjectAttributes(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("root", ctypes.c_void_p), ("name", ctypes.POINTER(UnicodeString)), ("attributes", ctypes.c_ulong), ("security", ctypes.c_void_p), ("quality", ctypes.c_void_p)]
        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_void_p)]
        native_name = ctypes.create_unicode_buffer(leaf)
        unicode_name = UnicodeString(
            len(leaf) * 2,
            (len(leaf) + 1) * 2,
            ctypes.cast(native_name, ctypes.c_wchar_p),
        )
        attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent_handle, ctypes.pointer(unicode_name), 0x40, None, None)
        status = IoStatusBlock()
        created = ctypes.c_void_p()
        create = ntdll.NtCreateFile
        create.argtypes = (ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.POINTER(ObjectAttributes), ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32)
        create.restype = ctypes.c_long
        result = create(ctypes.byref(created), 0x00100001, ctypes.byref(attributes), ctypes.byref(status), None, 0, 0x00000007, 2, 0x00000001 | 0x00000020 | 0x00200000, None, 0)
        if result != 0:
            raise RuntimeError("unsafe-directory")
        _close_handle(int(created.value))
    finally:
        _close_handle(parent_handle)


def _read_production_caption_metadata(project: ProductionProject) -> tuple[int, int]:
    required = tuple(project.captions_dir / name for name in CAPTION_FILES)
    if (
        not _safe_existing_file(project.narration_path)
        or not _safe_existing_file(project.manifest_path)
        or any(not _safe_existing_file(path) for path in required)
    ):
        raise ValueError("caption-gate")
    try:
        qc = json.loads((project.captions_dir / "caption-qc.json").read_text(encoding="utf-8"))
        captions = json.loads((project.captions_dir / "captions.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("caption-gate") from exc
    if (
        not isinstance(qc, dict)
        or qc.get("status") != "pass"
        or qc.get("timing_source") != "volcengine-word-timestamps"
        or qc.get("source_media") != project.narration_path.name
        or qc.get("narration_sha256") != _sha256(project.narration_path)
        or not isinstance(qc.get("alignment_coverage"), (int, float))
        or isinstance(qc.get("alignment_coverage"), bool)
        or not math.isfinite(float(qc["alignment_coverage"]))
        or float(qc["alignment_coverage"]) < 0.90
        or not isinstance(qc.get("asr_resource_id"), str)
        or not qc["asr_resource_id"].strip()
        or not isinstance(captions, list)
        or not captions
    ):
        raise ValueError("caption-gate")
    word_count = 0
    for caption in captions:
        if not isinstance(caption, dict) or not isinstance(caption.get("text"), str):
            raise ValueError("caption-gate")
        word_count += sum(character.isalnum() for character in caption["text"])
    if word_count <= 0:
        raise ValueError("caption-gate")
    return len(captions), word_count


def _clear_stale_reports(project: ProductionProject) -> None:
    for report in (project.active_dir / "delivery-report.json", project.delivery_report_path):
        if not report.exists():
            continue
        if not _is_contained(project.active_dir, report) or not _safe_existing_file(report):
            raise RuntimeError("unsafe-delivery-report")
        report.unlink()


def _frame_times(duration_seconds: float) -> list[float]:
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("invalid-duration")
    return [round(duration_seconds * ratio, 3) for ratio in (0.05, 0.25, 0.45, 0.55, 0.75, 0.95)]


def _default_contact_sheet_qc(times: list[float]) -> dict[str, object]:
    return {
        "frame_count": 6,
        "times_s": times,
        "layout": "3x2",
        "coverage": ["open", "early", "mid", "transition", "late", "near-end"],
    }


def _finalize_media(render: Path, narration: Path, output: Path, duration_seconds: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg-unavailable")
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(render),
            "-i",
            str(narration),
            "-t",
            f"{duration_seconds:.6f}",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(output),
        ],
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0 or not _safe_existing_file(output):
        raise RuntimeError("final-media-failed")


def _create_contact_sheet(final_media: Path, output: Path, duration_seconds: float) -> list[float]:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg-unavailable")
    times = _frame_times(duration_seconds)
    argv = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    for timestamp in times:
        argv.extend(["-ss", f"{timestamp:.3f}", "-i", str(final_media)])
    filters = ";".join(f"[{index}:v]scale=480:270,setsar=1[f{index}]" for index in range(6))
    layout = "|".join(("0_0", "480_0", "960_0", "0_270", "480_270", "960_270"))
    filters += ";" + "".join(f"[f{index}]" for index in range(6))
    filters += f"xstack=inputs=6:layout={layout}[sheet]"
    argv.extend(["-filter_complex", filters, "-map", "[sheet]", "-frames:v", "1", "-y", str(output)])
    result = subprocess.run(argv, capture_output=True, check=False, timeout=60)
    if result.returncode != 0 or not _safe_existing_file(output):
        raise RuntimeError("contact-sheet-failed")
    return times


def _render_caption_qc(final_media: Path, captions_path: Path, output: Path) -> None:
    """Prove visible bottom-safe-zone caption pixels from extracted final-MP4 frames."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg-unavailable")
    try:
        captions = json.loads(captions_path.read_text(encoding="utf-8"))
        times = [round((float(item["start"]) + float(item["end"])) / 2, 3) for item in captions]
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeError("caption-render-qc") from exc
    if not times:
        raise RuntimeError("caption-render-qc")
    checks: list[dict[str, object]] = []
    for timestamp in times[:6]:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", str(final_media), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            capture_output=True, check=False, timeout=60,
        )
        pixels = result.stdout
        if result.returncode != 0 or len(pixels) != 1920 * 1080 * 3:
            raise RuntimeError("caption-render-qc")
        safe_zone = pixels[(1080 - 150) * 1920 * 3:]
        bright = sum(1 for index in range(0, len(safe_zone), 3) if min(safe_zone[index:index + 3]) >= 185)
        dark = sum(1 for index in range(0, len(safe_zone), 3) if max(safe_zone[index:index + 3]) <= 90)
        if bright < 12 or dark < 500:
            raise RuntimeError("caption-render-qc")
        checks.append(
            {
                "time_s": timestamp,
                "frame_sha256": hashlib.sha256(pixels).hexdigest(),
                "bright_pixels": bright,
                "dark_pixels": dark,
            }
        )
    _write_json(
        output.parents[1], output,
        {
            "status": "pass", "timing_source": "volcengine-word-timestamps", "caption_style": "anchor-dark",
            "final_video_sha256": _sha256(final_media), "captions_sha256": _sha256(captions_path),
            "frame_checks": checks,
        },
    )


def _write_caption_overrides(render_project: Path) -> None:
    """Supply the optional local HyperFrames overrides file without weakening layout audits."""
    overrides = render_project / "caption-overrides.json"
    if not overrides.exists():
        _write_text(render_project, overrides, "[]\n")
    elif not _safe_existing_file(overrides):
        raise RuntimeError("unsafe-render-project")


def _directory_identity(path: Path) -> tuple[int, int]:
    entry = path.lstat()
    if not stat.S_ISDIR(entry.st_mode) or _is_reparse_point(path):
        raise RuntimeError("unsafe-directory")
    if os.name == "nt":
        handle = _open_no_reparse_directory(path, 0x00000080)
        try:
            return _handle_directory_identity(handle)
        finally:
            _close_handle(handle)
    return (entry.st_dev, entry.st_ino)


def _project_paths(workspace: Path, slug: str, project_date: date) -> dict[str, Path]:
    name = f"{project_date.isoformat()}-{slug}"
    active = workspace / "01-内容生产" / "视频工作台" / "制作中" / name
    media = active / "工程" / "media"
    archive = workspace / "01-内容生产" / "视频工作台" / "已制作" / _period(project_date) / name
    return {
        "active_dir": active, "archive_dir": archive, "media_dir": media,
        "narration_path": media / "narration.wav", "manifest_path": media / "voice_manifest.json",
        "captions_dir": media / "captions", "render_project_dir": active / "工程" / "render-project",
        "render_path": active / "成片" / "boomearth-v2-production.mp4",
        "contact_sheet_path": active / "质检" / "contact-sheet.jpg",
        "contact_sheet_qc_path": active / "质检" / "contact-sheet-qc.json",
        "caption_render_qc_path": active / "质检" / "caption-render-qc.json",
        "handoff_path": active / "交接稿.md", "delivery_report_path": active / "工程" / "delivery-report.json",
    }


def _project_is_canonical(project: ProductionProject) -> bool:
    workspace = Path(project.workspace_root).absolute()
    if not _safe_slug(project.archive_slug) or not isinstance(project.project_date, date):
        return False
    expected = _project_paths(workspace, project.archive_slug, project.project_date)
    if workspace != project.workspace_root.absolute() or any(getattr(project, name) != value for name, value in expected.items()):
        return False
    try:
        return _directory_identity(project.active_dir) == project.active_identity
    except (OSError, RuntimeError):
        return False


def _capture_inputs(
    project: ProductionProject,
    extra_paths: tuple[Path, ...] = (),
) -> dict[Path, tuple[int, int, int, int, str]]:
    paths = (
        project.narration_path,
        project.manifest_path,
        *(project.captions_dir / name for name in CAPTION_FILES),
        *extra_paths,
    )
    if len(set(paths)) != len(paths):
        raise RuntimeError("production-input")
    snapshots: dict[Path, tuple[int, int, int, int, str]] = {}
    for path in paths:
        if not _safe_existing_file(path) or not _is_contained(project.active_dir, path):
            raise RuntimeError("production-input")
        before = path.lstat()
        digest = _sha256(path)
        after = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise RuntimeError("production-input")
        snapshots[path] = (*identity, digest)
    return snapshots


def _default_handoff_text(
    project: ProductionProject,
    status: str,
    duration_seconds: float,
    word_count: int,
) -> str:
    return _handoff(
        project.archive_slug,
        status,
        duration_seconds=duration_seconds,
        word_count=word_count,
    )


def _inputs_match(snapshots: dict[Path, tuple[int, int, int, int, str]]) -> bool:
    try:
        return all(
            _safe_existing_file(path)
            and (*((current := path.lstat()).st_dev, current.st_ino, current.st_size, current.st_mtime_ns), _sha256(path)) == snapshot
            for path, snapshot in snapshots.items()
        )
    except OSError:
        return False


def _capture_publication_manifest(
    project_root: Path,
    project: ProductionProject,
    handoff: Path,
    *,
    include_delivery_report: bool = False,
    extra_paths: tuple[Path, ...] = (),
) -> dict[str, str]:
    paths = (
        project.narration_path, project.manifest_path, *(project.captions_dir / name for name in CAPTION_FILES),
        project.render_path, project.contact_sheet_path, project.contact_sheet_qc_path,
        project.caption_render_qc_path, handoff, *extra_paths,
    )
    if include_delivery_report:
        paths += (project.delivery_report_path,)
    manifest: dict[str, str] = {}
    for path in paths:
        if not _safe_existing_file(path) or not _is_contained(project_root, path):
            raise RuntimeError("publication-input")
        manifest[path.relative_to(project_root).as_posix()] = _sha256(path)
    return manifest


def _publication_manifest_matches(project_root: Path, manifest: dict[str, str]) -> bool:
    try:
        return all(
            _safe_existing_file(project_root / relative)
            and _sha256(project_root / relative) == digest
            for relative, digest in manifest.items()
        )
    except (OSError, ValueError):
        return False


def _write_publication_manifest(project_root: Path, manifest: dict[str, str]) -> None:
    _write_json(project_root, project_root / "工程" / "publication-manifest.json", {"artifacts": manifest})


def _valid_delivery_receipt(value: object, publication: dict[str, str]) -> bool:
    """Accept only the in-process production checker receipt for this exact artifact snapshot."""
    return (
        isinstance(value, dict)
        and value.get("status") == "pass"
        and value.get("mode") == "production"
        and value.get("rules") == [{"id": "delivery-hard-gates", "status": "pass"}]
        and value.get("artifacts") == {
            "handoff": "交接稿.md",
            "narration": "工程/media/narration.wav",
            "final": "成片/",
            "contact_sheet": "质检/contact-sheet.jpg",
            "contact_sheet_qc": "质检/contact-sheet-qc.json",
        }
        and value.get("artifact_sha256") == publication
        and isinstance(value.get("media"), dict)
        and value.get("skipped_stages") == []
    )


def _sanitize_incomplete_stage(
    stage: Path,
    project: ProductionProject,
    duration_seconds: float,
    word_count: int,
    handoff_text_fn: Callable[[ProductionProject, str, float, int], str] = _default_handoff_text,
) -> None:
    handoff = stage / project.handoff_path.relative_to(project.active_dir)
    _write_text(
        stage,
        handoff,
        handoff_text_fn(project, "制作中", duration_seconds, word_count),
    )
    for relative in (Path("delivery-report.json"), project.delivery_report_path.relative_to(project.active_dir)):
        candidate = stage / relative
        if candidate.exists() and _safe_existing_file(candidate):
            candidate.unlink()


def initialize_production_project(*, workspace_root: Path, archive_slug: str) -> ProductionProject:
    """Create the active project only; narration and captions remain external gated inputs."""

    try:
        workspace = Path(workspace_root)
    except (TypeError, ValueError):
        raise ValueError("production project inputs are invalid") from None
    if not _safe_slug(archive_slug) or not _has_safe_ancestors(workspace):
        raise ValueError("production project inputs are invalid")
    today = date.today()
    paths = _project_paths(workspace, archive_slug, today)
    active, archive = paths["active_dir"], paths["archive_dir"]
    targets = (active, archive, active.parent, archive.parent)
    if not all(_is_contained(workspace, target) for target in targets):
        raise ValueError("production project initialization failed")
    if active.exists() or _is_reparse_point(active) or archive.exists() or _is_reparse_point(archive):
        raise ValueError("production project initialization failed")
    try:
        _safe_mkdir(workspace, active.parent)
        active_parent_identity = _directory_identity(active.parent)
        _create_active_leaf_no_replace(active.parent, active.name, expected_parent_identity=active_parent_identity)
        if _directory_identity(active.parent) != active_parent_identity:
            raise RuntimeError("unsafe-directory")
        active_identity = _directory_identity(active)
        project = ProductionProject(workspace, archive_slug, today, active_identity, **paths)
        _safe_mkdir(active, project.media_dir)
        _safe_mkdir(active, project.captions_dir)
        _safe_mkdir(active, project.render_path.parent)
        _safe_mkdir(active, project.contact_sheet_path.parent)
        _write_text(active, project.handoff_path, _handoff(archive_slug, "制作中", duration_seconds=1.0, word_count=1))
    except (OSError, RuntimeError) as exc:
        raise ValueError("production project initialization failed") from None
    return project


def load_production_project(
    *, workspace_root: Path, active_project: str
) -> ProductionProject:
    """Reconstruct one canonical existing active project without caller-owned paths."""

    try:
        workspace = Path(workspace_root)
        match = re.fullmatch(
            r"(?P<project_date>\d{4}-\d{2}-\d{2})-(?P<slug>[a-z0-9][a-z0-9-]{0,79})",
            active_project,
        )
        if match is None or Path(active_project).name != active_project:
            raise ValueError
        project_date = date.fromisoformat(match.group("project_date"))
        archive_slug = match.group("slug")
    except (TypeError, ValueError):
        raise ValueError("production project load failed") from None
    if not _safe_slug(archive_slug) or not _has_safe_ancestors(workspace):
        raise ValueError("production project load failed")
    paths = _project_paths(workspace, archive_slug, project_date)
    active = paths["active_dir"]
    archive = paths["archive_dir"]
    if (
        active.name != active_project
        or not _is_contained(workspace, active)
        or not _is_contained(workspace, archive)
        or not _safe_existing_directory(active)
        or archive.exists()
        or _is_reparse_point(archive)
    ):
        raise ValueError("production project load failed")
    try:
        identity = _directory_identity(active)
        project = ProductionProject(
            workspace,
            archive_slug,
            project_date,
            identity,
            **paths,
        )
    except (OSError, RuntimeError):
        raise ValueError("production project load failed") from None
    if not _project_is_canonical(project):
        raise ValueError("production project load failed")
    return project


def finalize_production_sample(
    *,
    project: ProductionProject,
    audio_approved: bool,
    approved_narration_sha256: str | None = None,
    run_command: Callable[..., None] = _run_command,
    check_delivery_fn: Callable[..., DeliveryResult] = check_delivery,
    prepare_project_fn: Callable[..., PreparedProject] = prepare_project,
    preflight_fn: Callable[[Path], tuple[Path, Path]] = _preflight,
    mux_fn: Callable[[Path, Path, Path, float], None] = _finalize_media,
    contact_sheet_fn: Callable[[Path, Path, float], list[float]] = _create_contact_sheet,
    caption_render_qc_fn: Callable[[Path, Path, Path], None] = _render_caption_qc,
    extra_input_paths: tuple[Path, ...] = (),
    extra_publication_paths_fn: Callable[[ProductionProject], tuple[Path, ...]] | None = None,
    post_render_qc_fn: Callable[[ProductionProject, float], None] | None = None,
    pre_archive_private_inputs_fn: Callable[[ProductionProject], None] | None = None,
    expected_contact_sheet_times_fn: Callable[[float], list[float]] = _frame_times,
    contact_sheet_qc_payload_fn: Callable[[list[float]], dict[str, object]] = _default_contact_sheet_qc,
    handoff_text_fn: Callable[[ProductionProject, str, float, int], str] = _default_handoff_text,
    repo_root: Path = ROOT,
) -> ProductionSampleResult:
    """Return only a redacted result when a local filesystem race is observed."""

    try:
        return _finalize_production_sample(
            project=project,
            audio_approved=audio_approved,
            approved_narration_sha256=approved_narration_sha256,
            run_command=run_command,
            check_delivery_fn=check_delivery_fn,
            prepare_project_fn=prepare_project_fn,
            preflight_fn=preflight_fn,
            mux_fn=mux_fn,
            contact_sheet_fn=contact_sheet_fn,
            caption_render_qc_fn=caption_render_qc_fn,
            extra_input_paths=extra_input_paths,
            extra_publication_paths_fn=extra_publication_paths_fn,
            post_render_qc_fn=post_render_qc_fn,
            pre_archive_private_inputs_fn=pre_archive_private_inputs_fn,
            expected_contact_sheet_times_fn=expected_contact_sheet_times_fn,
            contact_sheet_qc_payload_fn=contact_sheet_qc_payload_fn,
            handoff_text_fn=handoff_text_fn,
            repo_root=repo_root,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        return ProductionSampleResult(2, ("rule=production-local-stage",))


def _finalize_production_sample(
    *,
    project: ProductionProject,
    audio_approved: bool,
    approved_narration_sha256: str | None = None,
    run_command: Callable[..., None] = _run_command,
    check_delivery_fn: Callable[..., DeliveryResult] = check_delivery,
    prepare_project_fn: Callable[..., PreparedProject] = prepare_project,
    preflight_fn: Callable[[Path], tuple[Path, Path]] = _preflight,
    mux_fn: Callable[[Path, Path, Path, float], None] = _finalize_media,
    contact_sheet_fn: Callable[[Path, Path, float], list[float]] = _create_contact_sheet,
    caption_render_qc_fn: Callable[[Path, Path, Path], None] = _render_caption_qc,
    extra_input_paths: tuple[Path, ...] = (),
    extra_publication_paths_fn: Callable[[ProductionProject], tuple[Path, ...]] | None = None,
    post_render_qc_fn: Callable[[ProductionProject, float], None] | None = None,
    pre_archive_private_inputs_fn: Callable[[ProductionProject], None] | None = None,
    expected_contact_sheet_times_fn: Callable[[float], list[float]] = _frame_times,
    contact_sheet_qc_payload_fn: Callable[[list[float]], dict[str, object]] = _default_contact_sheet_qc,
    handoff_text_fn: Callable[[ProductionProject, str, float, int], str] = _default_handoff_text,
    repo_root: Path = ROOT,
) -> ProductionSampleResult:
    """Render and archive only a QC-passed production project with approved narration."""

    if not audio_approved or not isinstance(approved_narration_sha256, str):
        return ProductionSampleResult(2, ("rule=audio-approval-required",))
    workspace = Path(project.workspace_root)
    repo_root = Path(repo_root)
    if (
        not _project_is_canonical(project)
        or project.archive_dir.exists()
        or _is_reparse_point(project.archive_dir)
        or not all(_is_contained(workspace, target) for target in (project.active_dir, project.archive_dir))
    ):
        return ProductionSampleResult(2, ("rule=production-project-state",))
    try:
        if not isinstance(extra_input_paths, tuple):
            raise RuntimeError("production-input")
        inputs = _capture_inputs(project, extra_input_paths)
        if inputs[project.narration_path][-1] != approved_narration_sha256.casefold():
            return ProductionSampleResult(2, ("rule=audio-approval-required",))
        _clear_stale_reports(project)
    except RuntimeError:
        return ProductionSampleResult(2, ("rule=production-local-stage",))
    try:
        caption_count, word_count = _read_production_caption_metadata(project)
    except (OSError, ValueError):
        return ProductionSampleResult(2, ("rule=production-caption-gate",))
    duration_seconds = 0.0
    archive_stage: Path | None = None
    final_recovery: Path | None = None
    try:
        node, hyperframes = preflight_fn(repo_root)
        prepared = prepare_project_fn(
            narration=project.narration_path,
            manifest=project.manifest_path,
            captions_dir=project.captions_dir,
            output_dir=project.render_project_dir,
            workspace_root=workspace,
        )
        duration_seconds = float(prepared.duration_seconds)
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise ValueError("invalid-duration")
        render_project = Path(prepared.output_dir)
        if render_project != project.render_project_dir or not _is_contained(project.active_dir, render_project) or not _safe_existing_directory(render_project):
            raise RuntimeError("unsafe-render-project")
        _write_caption_overrides(render_project)
        snapshot_narration = render_project / "media" / "narration.wav"
        if not _safe_existing_file(snapshot_narration) or _sha256(snapshot_narration) != inputs[project.narration_path][-1]:
            raise RuntimeError("production-input")
        for command in ("lint", "validate"):
            run_command([str(node), str(hyperframes), command, str(render_project)], cwd=repo_root)
        run_command([str(node), str(hyperframes), "inspect", str(render_project), "--samples", "15"], cwd=repo_root)
        render = render_project / "renders" / "boomearth-v2-production.mp4"
        run_command(
            [
                str(node), str(hyperframes),
                "render",
                str(render_project),
                "-o",
                str(render),
                "--fps",
                "30",
                "--quality",
                "standard",
                "--workers",
                "1",
            ],
            cwd=repo_root,
        )
        if not _safe_existing_file(render):
            raise RuntimeError("render-artifact")
        _safe_destination(workspace, project.render_path)
        mux_fn(render, snapshot_narration, project.render_path, duration_seconds)
        if not _safe_existing_file(project.render_path):
            raise RuntimeError("final-media")
        _safe_destination(workspace, project.contact_sheet_path)
        times = contact_sheet_fn(project.render_path, project.contact_sheet_path, duration_seconds)
        expected_times = expected_contact_sheet_times_fn(duration_seconds)
        if not _safe_existing_file(project.contact_sheet_path) or times != expected_times:
            raise RuntimeError("contact-sheet")
        _write_json(
            project.active_dir,
            project.contact_sheet_qc_path,
            contact_sheet_qc_payload_fn(times),
        )
        caption_render_qc_fn(project.render_path, project.captions_dir / "captions.json", project.caption_render_qc_path)
        if not _safe_existing_file(project.caption_render_qc_path):
            raise RuntimeError("caption-render-qc")
        if post_render_qc_fn is not None:
            post_render_qc_fn(project, duration_seconds)
        if not _inputs_match(inputs) or not _project_is_canonical(project):
            raise RuntimeError("production-input")
        _write_text(
            project.active_dir,
            project.handoff_path,
            handoff_text_fn(project, "制作中", duration_seconds, word_count),
        )
        extra_publication_paths = (
            extra_publication_paths_fn(project)
            if extra_publication_paths_fn is not None
            else ()
        )
        if not isinstance(extra_publication_paths, tuple):
            raise RuntimeError("publication-input")
        publication = _capture_publication_manifest(
            project.active_dir,
            project,
            project.handoff_path,
            extra_paths=extra_publication_paths,
        )
        _write_publication_manifest(project.active_dir, publication)
        checked = check_delivery_fn(project.handoff_path, project.active_dir, project.render_path, sample_mode=False)
        if checked.exit_code != 0:
            _clear_stale_reports(project)
            return ProductionSampleResult(checked.exit_code, tuple(checked.diagnostics))
        if not _publication_manifest_matches(project.active_dir, publication):
            raise RuntimeError("publication-input")
        report = project.active_dir / "delivery-report.json"
        try:
            receipt_bytes = _read_stable_file_bytes(report)
            receipt = json.loads(receipt_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("delivery-report") from None
        if not _valid_delivery_receipt(receipt, publication):
            raise RuntimeError("delivery-report")
        _after_delivery_receipt_capture(report)
        _write_exact_file_atomically(project.active_dir, project.delivery_report_path, receipt_bytes)
        try:
            copied_receipt = json.loads(_read_stable_file_bytes(project.delivery_report_path).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("delivery-report") from None
        if not _valid_delivery_receipt(copied_receipt, publication):
            raise RuntimeError("delivery-report")
        report.unlink()
        if pre_archive_private_inputs_fn is not None:
            pre_archive_private_inputs_fn(project)
        if (
            not _publication_manifest_matches(project.active_dir, publication)
            or not _project_is_canonical(project)
        ):
            raise RuntimeError("publication-input")
        completion = _capture_publication_manifest(
            project.active_dir,
            project,
            project.handoff_path,
            include_delivery_report=True,
            extra_paths=extra_publication_paths,
        )
        if not _is_contained(workspace, project.archive_dir.parent) or _is_reparse_point(project.archive_dir.parent):
            raise RuntimeError("unsafe-archive")
        _safe_mkdir(workspace, project.archive_dir.parent)
        if project.archive_dir.exists() or _is_reparse_point(project.archive_dir):
            raise RuntimeError("archive-collision")
        parent_identity = _directory_identity(project.archive_dir.parent)
        archive_stage = project.archive_dir.parent / f".{project.active_dir.name}.incomplete"
        if archive_stage.exists() or _is_reparse_point(archive_stage):
            raise RuntimeError("archive-collision")
        _before_archive_move(project.active_dir, archive_stage)
        _move_directory_no_replace(
            project.active_dir,
            archive_stage,
            expected_source_identity=project.active_identity,
            expected_parent_identity=parent_identity,
            expected_source_manifest=completion,
        )
        stage_identity = _directory_identity(archive_stage)
        stage_manifest = completion
        _write_publication_manifest(archive_stage, stage_manifest)
        if _directory_identity(project.archive_dir.parent) != parent_identity or project.archive_dir.exists():
            _sanitize_incomplete_stage(archive_stage, project, duration_seconds, word_count, handoff_text_fn)
            raise RuntimeError("archive-collision")
        if not _publication_manifest_matches(archive_stage, stage_manifest):
            _sanitize_incomplete_stage(archive_stage, project, duration_seconds, word_count, handoff_text_fn)
            raise RuntimeError("publication-input")
        _before_archive_move(archive_stage, project.archive_dir)
        _move_directory_no_replace(
            archive_stage,
            project.archive_dir,
            expected_source_identity=stage_identity,
            expected_parent_identity=parent_identity,
            expected_source_manifest=stage_manifest,
        )
        final_recovery = project.archive_dir
        if _directory_identity(project.archive_dir) != stage_identity or not _publication_manifest_matches(project.archive_dir, stage_manifest):
            raise RuntimeError("publication-input")
        final_handoff = project.archive_dir / project.handoff_path.relative_to(project.active_dir)
        _write_text(
            project.archive_dir,
            final_handoff,
            handoff_text_fn(project, "已完成", duration_seconds, word_count),
        )
        completed_manifest = dict(completion)
        completed_manifest[project.handoff_path.relative_to(project.active_dir).as_posix()] = _sha256(final_handoff)
        _write_publication_manifest(project.archive_dir, completed_manifest)
        if not _publication_manifest_matches(project.archive_dir, completed_manifest):
            raise RuntimeError("publication-input")
        final_recovery = None
        return ProductionSampleResult(0, tuple(checked.diagnostics), str(project.archive_dir))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        recovery_stage = None
        if archive_stage is not None and _safe_existing_directory(archive_stage):
            try:
                _sanitize_incomplete_stage(archive_stage, project, duration_seconds, word_count, handoff_text_fn)
                recovery_stage = archive_stage.name
            except (OSError, RuntimeError):
                pass
        if final_recovery is not None and _safe_existing_directory(final_recovery):
            try:
                _sanitize_incomplete_stage(final_recovery, project, duration_seconds, word_count, handoff_text_fn)
                recovery_stage = final_recovery.name
            except (OSError, RuntimeError):
                pass
        try:
            _clear_stale_reports(project)
        except RuntimeError:
            pass
        return ProductionSampleResult(2, ("rule=production-local-stage",), recovery_stage=recovery_stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Initialize or finalize a V2 production project; no provider is called."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init", help="create a dated active project")
    initialize.add_argument("--workspace-root", type=Path, default=ROOT)
    initialize.add_argument("--archive-slug", required=True)
    finalize = commands.add_parser(
        "finalize",
        help="render, check, and archive an existing active project without provider calls",
    )
    finalize.add_argument("--workspace-root", type=Path, default=ROOT)
    finalize.add_argument("--active-project", required=True)
    finalize.add_argument("--audio-approved", action="store_true")
    finalize.add_argument("--approved-narration-sha256", required=True)
    args = parser.parse_args(argv)
    if args.command == "init":
        try:
            initialize_production_project(
                workspace_root=args.workspace_root,
                archive_slug=args.archive_slug,
            )
        except ValueError:
            print("rule=production-project-initialize")
            return 2
        print("status=awaiting-narration-and-caption-gates")
        return 0
    try:
        project = load_production_project(
            workspace_root=args.workspace_root,
            active_project=args.active_project,
        )
    except ValueError:
        print("rule=production-project-load")
        return 2
    result = finalize_production_sample(
        project=project,
        audio_approved=args.audio_approved,
        approved_narration_sha256=args.approved_narration_sha256,
    )
    for diagnostic in result.diagnostics:
        print(diagnostic)
    if result.exit_code == 0 and result.archive_path is not None:
        try:
            archive = Path(result.archive_path).absolute().relative_to(
                Path(args.workspace_root).absolute()
            )
        except ValueError:
            print("rule=production-project-state")
            return 2
        print(f"archive={archive.as_posix()}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

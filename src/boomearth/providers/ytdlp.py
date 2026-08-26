"""Safe, one-item yt-dlp subprocess boundary for authorized private URLs."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_EXTRACTOR_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class YtDlpError(RuntimeError):
    """A fixed-message yt-dlp failure."""

    def __repr__(self) -> str:
        return "YtDlpError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class YtDlpResult:
    downloaded_path: Path = field(repr=False)
    extractor: str

    def __repr__(self) -> str:
        return "YtDlpResult(<redacted>)"


def _is_reparse(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        int(getattr(value, "st_file_attributes", 0))
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _ordinary_file(path: Path, *, unavailable: str) -> Path:
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        _ensure_no_reparse_components(absolute)
        value = absolute.lstat()
        if not stat.S_ISREG(value.st_mode) or _is_reparse(value) or value.st_size <= 0:
            raise OSError
        return absolute
    except (OSError, TypeError, ValueError):
        raise YtDlpError(unavailable) from None


def _ensure_no_reparse_components(path: Path) -> None:
    current = path
    pending: list[Path] = []
    while True:
        pending.append(current)
        if current == current.parent:
            break
        current = current.parent
    for component in reversed(pending):
        try:
            value = component.lstat()
        except FileNotFoundError:
            continue
        if _is_reparse(value):
            raise OSError


def _safe_directory(path: Path) -> Path:
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        _ensure_no_reparse_components(absolute)
        absolute.mkdir(parents=True, exist_ok=True)
        _ensure_no_reparse_components(absolute)
        if not absolute.is_dir():
            raise OSError
        return absolute
    except (OSError, TypeError, ValueError):
        raise YtDlpError("yt-dlp-output-invalid") from None


def _one_line(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8")
        lines = value.splitlines()
        if len(lines) != 1 or not lines[0] or lines[0] != lines[0].strip():
            raise ValueError
        return lines[0]
    except (OSError, UnicodeError, ValueError):
        raise YtDlpError("yt-dlp-failed") from None


class YtDlpProvider:
    """Invoke yt-dlp exactly once while discarding process diagnostics."""

    def __init__(
        self,
        executable: Path,
        *,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self._executable = Path(executable)
        self._runner = runner

    def download(self, url_file: Path, output_dir: Path) -> YtDlpResult:
        executable = _ordinary_file(self._executable, unavailable="yt-dlp-unavailable")
        source_input = _ordinary_file(url_file, unavailable="yt-dlp-input-invalid")
        output = _safe_directory(output_dir)
        result_file = output / ".download-result.txt"
        extractor_file = output / ".download-extractor.txt"
        if result_file.exists() or extractor_file.exists():
            raise YtDlpError("yt-dlp-output-invalid")
        argv = [
            str(executable),
            "--no-playlist",
            "--playlist-items",
            "1",
            "--no-update",
            "--no-progress",
            "--no-warnings",
            "--no-netrc",
            "--retries",
            "0",
            "--fragment-retries",
            "0",
            "--extractor-retries",
            "0",
            "--file-access-retries",
            "0",
            "--restrict-filenames",
            "--paths",
            str(output),
            "--output",
            "download.%(ext)s",
            "--print-to-file",
            "after_move:filepath",
            str(result_file),
            "--print-to-file",
            "after_move:extractor",
            str(extractor_file),
            "--batch-file",
            str(source_input),
        ]
        try:
            completed = self._runner(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            raise YtDlpError("yt-dlp-failed") from None
        if not isinstance(completed, subprocess.CompletedProcess) or completed.returncode != 0:
            raise YtDlpError("yt-dlp-failed")
        media_value = _one_line(result_file)
        extractor = _one_line(extractor_file)
        if _EXTRACTOR_RE.fullmatch(extractor) is None:
            raise YtDlpError("yt-dlp-failed")
        try:
            media = _ordinary_file(Path(media_value), unavailable="yt-dlp-failed")
            if os.path.commonpath((str(output), str(media))) != str(output):
                raise ValueError
            candidates = [
                item
                for item in output.iterdir()
                if item not in {result_file, extractor_file}
                and item.is_file()
                and not item.is_symlink()
            ]
            if len(candidates) != 1 or candidates[0] != media:
                raise ValueError
        except (OSError, ValueError):
            raise YtDlpError("yt-dlp-failed") from None
        return YtDlpResult(media, extractor)


__all__ = ["YtDlpError", "YtDlpProvider", "YtDlpResult"]

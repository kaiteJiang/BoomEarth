from __future__ import annotations

import subprocess
import os
from pathlib import Path

import pytest

from boomearth.providers.ytdlp import YtDlpError, YtDlpProvider


def _executable(tmp_path: Path) -> Path:
    executable = tmp_path / "yt-dlp.exe"
    executable.write_bytes(b"synthetic executable")
    return executable


def _url_file(tmp_path: Path) -> Path:
    path = tmp_path / "source-input.txt"
    path.write_text("https://example.invalid/private-item\n", encoding="utf-8")
    return path


def test_ytdlp_command_is_single_item_machine_readable_and_no_update(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    output = tmp_path / "output"

    def runner(argv, **kwargs):
        calls.append((list(argv), kwargs))
        output.mkdir(exist_ok=True)
        media = output / "download.mp4"
        media.write_bytes(b"synthetic media")
        (output / ".download-result.txt").write_text(str(media), encoding="utf-8")
        (output / ".download-extractor.txt").write_text("youtube", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    provider = YtDlpProvider(_executable(tmp_path), runner=runner)
    result = provider.download(_url_file(tmp_path), output)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert "--no-playlist" in argv
    assert argv[argv.index("--playlist-items") + 1] == "1"
    assert "--no-update" in argv
    assert "--no-progress" in argv
    assert "--no-netrc" in argv
    for option in (
        "--retries",
        "--fragment-retries",
        "--extractor-retries",
        "--file-access-retries",
    ):
        assert argv[argv.index(option) + 1] == "0"
    for forbidden in (
        "--cookies",
        "--cookies-from-browser",
        "--username",
        "--password",
        "--video-password",
        "--client-certificate",
        "--netrc",
    ):
        assert forbidden not in argv
    assert "--dump-json" not in argv
    assert "--batch-file" in argv
    assert "https://example.invalid/private-item" not in argv
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["timeout"] == 300
    assert result.downloaded_path == output / "download.mp4"
    assert result.extractor == "youtube"
    assert "private-item" not in repr(result)


@pytest.mark.parametrize("mode", ["nonzero", "missing", "multiple", "redirect"])
def test_ytdlp_rejects_invalid_process_result(tmp_path: Path, mode: str) -> None:
    output = tmp_path / "output"

    def runner(argv, **_kwargs):
        output.mkdir(exist_ok=True)
        result_file = output / ".download-result.txt"
        extractor_file = output / ".download-extractor.txt"
        if mode == "nonzero":
            return subprocess.CompletedProcess(argv, 1, stderr=b"private url")
        media = output / "download.mp4"
        media.write_bytes(b"synthetic media")
        extractor_file.write_text("youtube", encoding="utf-8")
        if mode == "missing":
            result_file.write_text(str(output / "absent.mp4"), encoding="utf-8")
        elif mode == "multiple":
            other = output / "other.mp4"
            other.write_bytes(b"other")
            result_file.write_text(f"{media}\n{other}\n", encoding="utf-8")
        else:
            outside = tmp_path / "outside.mp4"
            outside.write_bytes(b"outside")
            result_file.write_text(str(outside), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    provider = YtDlpProvider(_executable(tmp_path), runner=runner)

    with pytest.raises(YtDlpError) as raised:
        provider.download(_url_file(tmp_path), output)

    assert str(raised.value) == "yt-dlp-failed"
    assert "private url" not in repr(raised.value)


def test_ytdlp_rejects_missing_executable_without_runner_call(tmp_path: Path) -> None:
    called = False

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True

    provider = YtDlpProvider(tmp_path / "missing.exe", runner=runner)

    with pytest.raises(YtDlpError, match="^yt-dlp-unavailable$"):
        provider.download(_url_file(tmp_path), tmp_path / "output")

    assert called is False


def test_ytdlp_maps_timeout_without_retry(tmp_path: Path) -> None:
    calls = 0

    def runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired("yt-dlp", 300, stderr=b"private")

    provider = YtDlpProvider(_executable(tmp_path), runner=runner)

    with pytest.raises(YtDlpError, match="^yt-dlp-failed$"):
        provider.download(_url_file(tmp_path), tmp_path / "output")

    assert calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_ytdlp_rejects_reparse_output_before_runner(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    output = tmp_path / "output"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(output), str(outside)],
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    calls = 0

    def runner(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    try:
        provider = YtDlpProvider(_executable(tmp_path), runner=runner)
        with pytest.raises(YtDlpError, match="^yt-dlp-output-invalid$"):
            provider.download(_url_file(tmp_path), output)
        assert calls == 0
        assert not list(outside.iterdir())
    finally:
        if output.exists():
            os.rmdir(output)

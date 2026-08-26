"""Retrieve one approved private video URL from stdin without retaining it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from boomearth.video.heygen_recovery import (  # noqa: E402
    PrivateVideoDownloadError,
    download_private_video,
)


class _RedactedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        print("status=failed category=arguments-invalid")
        raise SystemExit(2)


def _open(url: str):
    from urllib.request import urlopen

    return urlopen(url, timeout=900)


def _probe(path: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise ValueError
        payload = json.loads(completed.stdout)
        format_data = payload.get("format")
        streams = payload.get("streams")
        if not isinstance(format_data, dict) or not isinstance(streams, list):
            raise ValueError
        container = format_data["format_name"]
        if not isinstance(container, str) or not container.strip():
            raise ValueError
        facts: dict[str, object] = {"container": container}
        if "duration" in format_data:
            facts["duration_seconds"] = float(format_data["duration"])
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            width = stream.get("width")
            height = stream.get("height")
            codec = stream.get("codec_name")
            if (
                stream.get("codec_type") == "video"
                and type(width) is int
                and width > 0
                and type(height) is int
                and height > 0
                and isinstance(codec, str)
                and codec.strip()
            ):
                facts.update(
                    {
                        "codec_type": "video",
                        "codec": codec,
                        "width": width,
                        "height": height,
                    }
                )
                break
        if facts.get("codec_type") != "video":
            raise ValueError
        return facts
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        raise PrivateVideoDownloadError("download-probe-failed") from None


def main() -> int:
    parser = _RedactedArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        download_private_video(
            sys.stdin,
            arguments.output,
            arguments.receipt,
            opener=_open,
            probe=_probe,
        )
    except PrivateVideoDownloadError as exc:
        print(f"status=failed category={exc}")
        return 2
    print("status=downloaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

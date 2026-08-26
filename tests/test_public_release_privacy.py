from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_private_tts_route_is_local_only_and_example_is_public() -> None:
    tracked = set(
        subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8"
        ).splitlines()
    )

    assert "automation/config/tts-routing.json" not in tracked
    assert "automation/config/tts-routing.example.json" in tracked


def test_public_runtime_sources_do_not_pin_author_machine_paths() -> None:
    public_files = (
        ROOT / ".env.example",
        ROOT / "src" / "boomearth" / "config.py",
        ROOT / "src" / "boomearth" / "audio" / "indextts2.py",
        ROOT / "automation" / "config" / "tts-routing.example.json",
    )
    forbidden = ("C:\\Users\\1", "D:\\Program Files", "E:\\自动化脚本")

    for path in public_files:
        text = path.read_text(encoding="utf-8")
        assert all(value not in text for value in forbidden), path

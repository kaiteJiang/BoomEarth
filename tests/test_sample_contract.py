"""Offline contract tests for the deterministic BoomEarth HyperFrames sample."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import wave


REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "video-sample"
INDEX_PATH = SAMPLE_DIR / "index.html"
DESIGN_PATH = SAMPLE_DIR / "DESIGN.md"
PACKAGE_PATH = REPO_ROOT / "package.json"
PREPARE_SCRIPT = REPO_ROOT / "automation" / "scripts" / "prepare_video_sample.py"

EXPECTED_CAPTIONS = [
    {
        "start": 0.35,
        "end": 2.05,
        "text": "先把想法说清楚。",
        "source": "synthetic-local-sample",
    },
    {
        "start": 2.25,
        "end": 4.1,
        "text": "画面、声音和节奏，再慢慢对齐。",
        "source": "synthetic-local-sample",
    },
    {
        "start": 5.2,
        "end": 7.1,
        "text": "十秒钟，也可以讲明白一件事。",
        "source": "synthetic-local-sample",
    },
    {
        "start": 7.35,
        "end": 9.45,
        "text": "每一步，都留在本地。",
        "source": "synthetic-local-sample",
    },
]

EXPECTED_CAPTION_QC = {
    "status": "pass",
    "timing_source": "synthetic-local-sample",
    "synthetic": True,
    "alignment_note": "Synthetic source-free timings for the deterministic 10-second local sample; not ASR output.",
    "groups": 4,
    "duration_seconds": 10.0,
    "one_group_visible_at_a_time": True,
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_sample_project_declares_the_pinned_local_render_contract() -> None:
    """Would fail if the sample stopped being a pinned, reproducible local render."""

    package = json.loads(_read(PACKAGE_PATH))
    dependencies = package["dependencies"]

    assert re.fullmatch(r"\d+\.\d+\.\d+", dependencies["hyperframes"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", dependencies["gsap"])
    assert set(
        (
            "hf:doctor",
            "hf:lint",
            "hf:validate",
            "hf:inspect",
            "hf:render:sample",
            "hf:prepare:sample",
        )
    ).issubset(package["scripts"])
    assert "hyperframes render video-sample" in package["scripts"]["hf:render:sample"]
    assert "boomearth-v1-sample.mp4" in package["scripts"]["hf:render:sample"]
    assert package["scripts"]["hf:prepare:sample"] == "python automation/scripts/prepare_video_sample.py"
    assert package["boomearthSample"]["prepareUsage"] == (
        "npm run hf:prepare:sample -- --source-wav <authorized-local-wav>"
    )


def test_sample_composition_is_local_deterministic_and_caption_safe() -> None:
    """Would fail if the delivery gained remote media, nondeterminism, or unsafe captions."""

    html = _read(INDEX_PATH)

    assert 'data-composition-id="boomearth-v1-sample"' in html
    assert 'data-width="1920"' in html
    assert 'data-height="1080"' in html
    assert 'data-start="0"' in html
    assert 'data-duration="10"' in html
    assert "<template" not in html.lower()
    assert "http://" not in html.lower()
    assert "https://" not in html.lower()
    assert 'src="node_modules/gsap/dist/gsap.min.js"' in html
    assert "../node_modules" not in html
    assert "@font-face" in html
    assert 'local("Microsoft YaHei")' in html
    assert 'local("SimSun")' in html
    assert "Math.random" not in html
    assert "Date.now" not in html
    assert "repeat: -1" not in html
    assert "setTimeout" not in html
    assert not re.search(r"\b(?:async|await|Promise)\b", html)
    assert not re.search(r"\.(?:play|pause|seek)\s*\(", html)
    assert 'src="media/narration.wav"' in html
    assert "<audio" in html
    assert "<video" not in html
    assert 'data-caption-source="media/captions.json"' in html
    assert 'data-caption-safe-zone="anchor-dark"' in html
    assert "caption-qc.json" in html
    assert ".sample-caption-group" in html
    assert 'className = "sample-caption-group"' in html
    assert ".caption-group" not in html
    assert re.search(r"\.sample-caption-group\s*\{[^}]*opacity:\s*1;", html, re.DOTALL)
    assert 'window.__timelines["boomearth-v1-sample"] = tl' in html
    assert "gsap.timeline({ paused: true })" in html
    assert "tl.set(group.element" in html


def test_design_makes_the_approved_warm_visual_contract_explicit() -> None:
    """Would fail if the approved warm visual system lost its documented boundary."""

    design = _read(DESIGN_PATH)

    for marker in (
        "#F7F1E7",
        "#17130F",
        "#C63F2F",
        "#E47A35",
        "#2C63A3",
        "anchor-dark",
        "Microsoft YaHei",
        "SimSun",
    ):
        assert marker in design


def _write_synthetic_wav(path: Path, *, duration_seconds: int = 11) -> None:
    sample_rate = 8_000
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\x00\x00" * sample_rate * duration_seconds)


def _run_prepare(source_wav: Path, workspace_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PREPARE_SCRIPT),
            "--source-wav",
            str(source_wav),
            "--workspace-root",
            str(workspace_root),
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def test_prepare_cli_creates_exact_local_sample_artifacts_from_an_explicit_wav(
    tmp_path: Path,
) -> None:
    """Would fail if a clean checkout could not recreate local sample inputs without APIs."""

    workspace_root = tmp_path / "clean-checkout"
    media_dir = workspace_root / "video-sample" / "media"
    media_dir.mkdir(parents=True)
    source_wav = tmp_path / "authorized-input" / "fixture.wav"
    _write_synthetic_wav(source_wav)

    result = _run_prepare(source_wav, workspace_root)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "status=prepared\n"
    assert result.stderr == ""
    assert {path.name for path in media_dir.iterdir()} == {
        "narration.wav",
        "captions.json",
        "caption-qc.json",
    }
    with wave.open(str(media_dir / "narration.wav"), "rb") as narration:
        assert narration.getnchannels() == 1
        assert narration.getsampwidth() == 2
        assert narration.getframerate() == 8_000
        assert narration.getnframes() == 80_000
    assert json.loads(_read(media_dir / "captions.json")) == EXPECTED_CAPTIONS
    assert json.loads(_read(media_dir / "caption-qc.json")) == EXPECTED_CAPTION_QC
    published = "\n".join(path.read_text(encoding="utf-8") for path in media_dir.glob("*.json"))
    assert str(source_wav) not in published


def test_prepare_cli_refuses_invalid_or_preexisting_local_inputs_without_echoing_paths(
    tmp_path: Path,
) -> None:
    """Would fail if invalid inputs leaked paths or a rerun clobbered published sample media."""

    workspace_root = tmp_path / "clean-checkout"
    media_dir = workspace_root / "video-sample" / "media"
    media_dir.mkdir(parents=True)
    invalid_source = tmp_path / "authorized-input" / "not-a-wav.txt"
    invalid_source.parent.mkdir()
    invalid_source.write_text("not audio", encoding="utf-8")

    invalid = _run_prepare(invalid_source, workspace_root)

    assert invalid.returncode == 2
    assert invalid.stdout == ""
    assert invalid.stderr == "error=source-wav-invalid\n"
    assert str(invalid_source) not in invalid.stderr
    assert list(media_dir.iterdir()) == []

    source_wav = tmp_path / "authorized-input" / "fixture.wav"
    _write_synthetic_wav(source_wav)
    assert _run_prepare(source_wav, workspace_root).returncode == 0
    before = {path.name: path.read_bytes() for path in media_dir.iterdir()}

    repeat = _run_prepare(source_wav, workspace_root)

    assert repeat.returncode == 2
    assert repeat.stdout == ""
    assert repeat.stderr == "error=sample-artifacts-exist\n"
    assert str(source_wav) not in repeat.stderr
    assert {path.name: path.read_bytes() for path in media_dir.iterdir()} == before


def test_generated_sample_artifacts_are_ignored_but_the_media_skeleton_can_be_tracked() -> None:
    """Would fail if generated private media could enter Git or the required skeleton disappeared."""

    assert (SAMPLE_DIR / "media" / ".gitkeep").is_file()
    ignored_paths = (
        "video-sample/media/narration.wav",
        "video-sample/media/captions.json",
        "video-sample/media/caption-qc.json",
        "video-sample/renders/boomearth-v1-sample.mp4",
        "video-sample/reports/inspect.json",
    )
    for relative_path in ignored_paths:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative_path],
            cwd=REPO_ROOT,
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    skeleton_ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", "video-sample/media/.gitkeep"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert skeleton_ignored.returncode == 1, skeleton_ignored.stderr

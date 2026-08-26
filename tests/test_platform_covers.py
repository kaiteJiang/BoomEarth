from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

import boomearth.video.platform_covers as platform_covers
from boomearth.video.platform_covers import (
    PlatformCoverError,
    render_platform_covers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "automation" / "scripts" / "render_platform_covers.py"
FONT = Path("C:/Windows/Fonts/msyh.ttc")
IMAGE_DIMENSIONS = {
    "封面/抖音-作品封面-1080x1920.png": [1080, 1920],
    "封面/抖音-主页预览-1080x1440.png": [1080, 1440],
    "封面/视频号-作品封面-1080x1260.png": [1080, 1260],
    "质检/cover-contact-sheet.jpg": [960, 640],
}
SAFE_BOXES = {
    "封面/抖音-作品封面-1080x1920.png": [72, 300, 1008, 1620],
    "封面/抖音-主页预览-1080x1440.png": [72, 60, 1008, 1380],
    "封面/视频号-作品封面-1080x1260.png": [72, 60, 1008, 1200],
}


def _source(project: Path) -> Path:
    path = project / "工程" / "assets" / "semantic" / "scene-01.png"
    path.parent.mkdir(parents=True)
    image = Image.new("RGB", (1280, 720), (238, 198, 56))
    for x in range(160, 1120):
        for y in range(100, 620):
            if (x // 80 + y // 80) % 2:
                image.putpixel((x, y), (26, 79, 113))
    image.save(path)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_renderer_creates_exact_platform_crops_and_hash_bound_qc(tmp_path: Path) -> None:
    """Would fail if a cover had the wrong platform geometry or self-declared evidence."""

    assert FONT.is_file(), "the Windows Chinese font fixture is required"
    project = tmp_path / "project"
    source = _source(project)

    result = render_platform_covers(
        project, source, "普通人学 AI，先把这四件事练明白", font_path=FONT
    )

    assert result.status == "pass"
    assert [path.relative_to(project).as_posix() for path in result.artifacts] == [
        *IMAGE_DIMENSIONS,
        "质检/cover-qc.json",
    ]
    for relative, dimensions in IMAGE_DIMENSIONS.items():
        with Image.open(project / relative) as image:
            image.load()
            assert list(image.size) == dimensions
    with (
        Image.open(project / "封面" / "抖音-作品封面-1080x1920.png") as upload,
        Image.open(project / "封面" / "抖音-主页预览-1080x1440.png") as preview,
    ):
        expected = upload.crop((0, 240, 1080, 1680))
        assert preview.mode == expected.mode
        assert preview.tobytes() == expected.tobytes()

    qc = json.loads((project / "质检" / "cover-qc.json").read_text("utf-8"))
    assert qc["schema_version"] == 1
    assert qc["status"] == "pass"
    assert qc["contract"] == "platform-defaults-v1"
    assert qc["source_sha256"] == _sha256(source)
    assert qc["headline_sha256"] == hashlib.sha256(
        "普通人学 AI，先把这四件事练明白".encode("utf-8")
    ).hexdigest()
    assert qc["dimensions"] == IMAGE_DIMENSIONS
    assert qc["safe_boxes"] == SAFE_BOXES
    assert qc["font_size_px"] >= 64
    assert qc["headline_line_count"] in {2, 3}
    assert qc["artifacts_sha256"] == {
        relative: _sha256(project / relative) for relative in IMAGE_DIMENSIONS
    }


def test_renderer_reuses_only_fully_verified_matching_outputs(tmp_path: Path) -> None:
    """Would fail if a retry rewrote accepted evidence or trusted a changed image."""

    project = tmp_path / "project"
    source = _source(project)
    headline = "先学会判断，再让 AI 替你动手"
    first = render_platform_covers(project, source, headline, font_path=FONT)
    before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in first.artifacts}

    reused = render_platform_covers(project, source, headline, font_path=FONT)

    assert reused.reused is True
    assert {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in reused.artifacts} == before

    upload = project / "封面" / "抖音-作品封面-1080x1920.png"
    upload.write_bytes(b"mismatched-existing-cover")
    with pytest.raises(PlatformCoverError, match="^cover-output-exists$"):
        render_platform_covers(project, source, headline, font_path=FONT)
    assert upload.read_bytes() == b"mismatched-existing-cover"


def test_renderer_reuse_rejects_palette_images_with_different_decoded_colors(
    tmp_path: Path,
) -> None:
    """Would fail if equal palette indices hid a visually different profile crop."""

    project = tmp_path / "project"
    source = _source(project)
    headline = "先判断，再让 AI 动手"
    render_platform_covers(project, source, headline, font_path=FONT)
    upload = project / "封面" / "抖音-作品封面-1080x1920.png"
    preview = project / "封面" / "抖音-主页预览-1080x1440.png"
    upload_image = Image.new("P", (1080, 1920), 0)
    upload_image.putpalette([255, 0, 0] + [0] * 765)
    upload_image.save(upload)
    preview_image = Image.new("P", (1080, 1440), 0)
    preview_image.putpalette([0, 0, 255] + [0] * 765)
    preview_image.save(preview)
    qc_path = project / "质检" / "cover-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["artifacts_sha256"]["封面/抖音-作品封面-1080x1920.png"] = _sha256(
        upload
    )
    qc["artifacts_sha256"]["封面/抖音-主页预览-1080x1440.png"] = _sha256(
        preview
    )
    qc_path.write_text(json.dumps(qc, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(PlatformCoverError, match="^cover-output-exists$"):
        render_platform_covers(project, source, headline, font_path=FONT)


@pytest.mark.parametrize(
    ("headline", "error"),
    [
        ("", "cover-headline-invalid"),
        ("第一行\n第二行", "cover-headline-invalid"),
        ("太" * 80, "cover-headline-invalid"),
        ("https://example.invalid/private", "cover-headline-invalid"),
        (".internal/private", "cover-headline-invalid"),
    ],
)
def test_renderer_rejects_malformed_or_nonpublic_headlines(
    tmp_path: Path, headline: str, error: str
) -> None:
    """Would fail if malformed/private text reached a public cover or a diagnostic."""

    project = tmp_path / "project"
    source = _source(project)
    with pytest.raises(PlatformCoverError, match=f"^{error}$"):
        render_platform_covers(project, source, headline, font_path=FONT)


def test_renderer_rejects_outside_traversal_and_unsafe_output_targets(tmp_path: Path) -> None:
    """Would fail if source or output reparses escaped the project without redacted errors."""

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.png"
    Image.new("RGB", (640, 640), "red").save(outside)
    traversal = project / "nested" / ".." / ".." / outside.name
    with pytest.raises(PlatformCoverError, match="^cover-source-invalid$"):
        render_platform_covers(project, traversal, "合法公开标题", font_path=FONT)
    with pytest.raises(PlatformCoverError, match="^cover-source-invalid$"):
        render_platform_covers(project, outside, "合法公开标题", font_path=FONT)

    source = _source(project)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    try:
        os.symlink(redirected, project / "封面", target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(PlatformCoverError, match="^cover-output-invalid$"):
        render_platform_covers(project, source, "合法公开标题", font_path=FONT)
    assert list(redirected.iterdir()) == []


def test_renderer_cleans_the_current_hard_link_when_post_link_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a failed validation left a partial cover that blocked retry."""

    project = tmp_path / "project"
    source = _source(project)
    target = project / "封面" / "抖音-作品封面-1080x1920.png"
    native_sha256 = platform_covers._sha256
    mismatch_once = True

    def mismatched_target(path: Path) -> str:
        nonlocal mismatch_once
        digest = native_sha256(path)
        if path == target and mismatch_once:
            mismatch_once = False
            return "0" * 64
        return digest

    monkeypatch.setattr(platform_covers, "_sha256", mismatched_target)
    with pytest.raises(PlatformCoverError, match="^cover-output-invalid$"):
        render_platform_covers(project, source, "失败后必须可以重试", font_path=FONT)
    assert all(
        not (project / relative).exists()
        for relative in [*IMAGE_DIMENSIONS, "质检/cover-qc.json"]
    )

    monkeypatch.setattr(platform_covers, "_sha256", native_sha256)
    result = render_platform_covers(
        project, source, "失败后必须可以重试", font_path=FONT
    )
    assert result.status == "pass"


def test_renderer_cli_prints_only_relative_artifacts_and_status(tmp_path: Path) -> None:
    """Would fail if the offline CLI exposed private absolute input paths."""

    project = tmp_path / "private-project"
    source = _source(project)
    run = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(project),
            str(source),
            "把判断力练成你的 AI 基本功",
            "--font",
            str(FONT),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert run.returncode == 0
    assert run.stderr == ""
    assert run.stdout.splitlines() == [
        "status=pass reused=false",
        *(f"artifact={relative}" for relative in [*IMAGE_DIMENSIONS, "质检/cover-qc.json"]),
    ]
    assert str(project) not in run.stdout
    assert str(source) not in run.stdout

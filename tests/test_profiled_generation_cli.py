from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PREPARE = ROOT / "automation" / "scripts" / "prepare_profiled_generation.py"
COMPLETE = ROOT / "automation" / "scripts" / "complete_profiled_generation.py"


def _v4_prompt(*, scene_id: str = "scene-01") -> bytes:
    return f"""---
scene_id: {scene_id}
visual_type: comparison
visual_style: vivid-comic-explainer
visual_mode: human-action
visual_system: profiled-illustration-v4
visual_theme: vivid-comic-explainer
ratio: 16:9
target_size: 3840x2160
text_policy: none
caption_safe_zone: bottom-150px
---
1. 当前场景唯一判断：判断
2. 语义主体：人物
3. 核心动作或关系：对比
4. 必须可见的证据：两个状态
5. 构图、左侧程序文字区和底部字幕安全区：保留安全区
6. 当前主题的线条、材质和色板：鲜彩漫画
7. 当前主题专项结构：左右对比
8. 为什么画面能解释判断：对比可见
9. overlay labels，仅供 renderer：无
10. 禁止意象、文字、Logo、水印、UI、PPT 页面和无关装饰：全部禁止
""".encode("utf-8")


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _png(path: Path) -> None:
    Image.new("RGB", (3840, 2160), (242, 235, 220)).save(path, format="PNG")


def test_prepare_rejects_noncanonical_v4_sections_before_publication(
    tmp_path: Path,
) -> None:
    prepare = _module(PREPARE, "prepare_profiled_generation_invalid_prompt")
    prompt_source = tmp_path / "prompt-source.md"
    prompt_source.write_bytes(
        _v4_prompt().replace(
            "1. 当前场景唯一判断：".encode("utf-8"),
            "1. 画面判断：".encode("utf-8"),
        )
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = prepare.main(
            [
                "--project-root",
                str(tmp_path),
                "--theme",
                "vivid-comic-explainer",
                "--scene",
                "scene-01",
                "--candidate-index",
                "1",
                "--prompt-source",
                str(prompt_source),
                "--call-id",
                "11111111-1111-4111-8111-111111111111",
            ]
        )

    assert result == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue().strip() == "illustration-prompt-invalid"
    published_root = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / "vivid-comic-explainer"
    )
    assert not (published_root / "prompts" / "scene-01.md").exists()
    assert not (
        published_root / "generation" / "scene-01-candidate-01-intent.json"
    ).exists()


def test_prepare_rejects_v4_frontmatter_that_does_not_match_target(
    tmp_path: Path,
) -> None:
    prepare = _module(PREPARE, "prepare_profiled_generation_mismatched_target")
    prompt_source = tmp_path / "prompt-source.md"
    prompt_source.write_bytes(_v4_prompt(scene_id="scene-02"))
    stderr = io.StringIO()

    with redirect_stderr(stderr):
        result = prepare.main(
            [
                "--project-root",
                str(tmp_path),
                "--theme",
                "vivid-comic-explainer",
                "--scene",
                "scene-01",
                "--candidate-index",
                "1",
                "--prompt-source",
                str(prompt_source),
            ]
        )

    assert result == 2
    assert stderr.getvalue().strip() == "illustration-prompt-invalid"
    assert not (tmp_path / "工程").exists()


def test_production_entrypoints_enforce_prepare_then_external_tool_then_complete(
    tmp_path: Path,
) -> None:
    prepare = _module(PREPARE, "prepare_profiled_generation_under_test")
    complete = _module(COMPLETE, "complete_profiled_generation_under_test")
    prompt_source = tmp_path / "prompt-source.md"
    candidate_source = tmp_path / "imagegen-result.png"
    prompt_source.write_bytes(_v4_prompt())
    _png(candidate_source)

    output = io.StringIO()
    with redirect_stdout(output):
        assert prepare.main(
            [
                "--project-root",
                str(tmp_path),
                "--theme",
                "vivid-comic-explainer",
                "--scene",
                "scene-01",
                "--candidate-index",
                "1",
                "--prompt-source",
                str(prompt_source),
                "--call-id",
                "11111111-1111-4111-8111-111111111111",
            ]
        ) == 0
    ticket = json.loads(output.getvalue())
    assert ticket["sequence"] == 1
    assert not (tmp_path / Path(ticket["candidate_path"])).exists()

    output = io.StringIO()
    with redirect_stdout(output):
        assert complete.main(
            [
                "--project-root",
                str(tmp_path),
                "--theme",
                "vivid-comic-explainer",
                "--scene",
                "scene-01",
                "--candidate-index",
                "1",
                "--candidate-source",
                str(candidate_source),
                "--call-id",
                ticket["call_id"],
                "--intent-sha256",
                ticket["intent_sha256"],
            ]
        ) == 0
    artifact = json.loads(output.getvalue())
    assert (tmp_path / Path(artifact["path"])).is_file()
    assert (tmp_path / Path(artifact["generation_receipt_path"])).is_file()


def test_complete_entrypoint_cannot_bypass_missing_prepare_evidence(
    tmp_path: Path,
) -> None:
    complete = _module(COMPLETE, "complete_profiled_generation_without_prepare")
    candidate_source = tmp_path / "imagegen-result.png"
    _png(candidate_source)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = complete.main(
            [
                "--project-root",
                str(tmp_path),
                "--theme",
                "vivid-comic-explainer",
                "--scene",
                "scene-01",
                "--candidate-index",
                "1",
                "--candidate-source",
                str(candidate_source),
                "--call-id",
                "11111111-1111-4111-8111-111111111111",
                "--intent-sha256",
                "0" * 64,
            ]
        )

    assert result == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue().strip() == "profiled-generation-evidence-invalid"
    assert not (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / "vivid-comic-explainer"
        / "candidates"
        / "scene-01-candidate-01.png"
    ).exists()


def test_complete_entrypoint_adapts_runtime_native_png_when_explicitly_enabled(
    tmp_path: Path,
) -> None:
    prepare = _module(PREPARE, "prepare_profiled_generation_for_native_adaptation")
    complete = _module(COMPLETE, "complete_profiled_generation_native_adaptation")
    prompt_source = tmp_path / "prompt-source.md"
    candidate_source = tmp_path / "imagegen-native-result.png"
    prompt_source.write_bytes(_v4_prompt())
    Image.new("RGB", (1672, 941), (21, 42, 84)).save(candidate_source, format="PNG")

    output = io.StringIO()
    with redirect_stdout(output):
        assert prepare.main(
            [
                "--project-root",
                str(tmp_path),
                "--theme",
                "vivid-comic-explainer",
                "--scene",
                "scene-01",
                "--candidate-index",
                "1",
                "--prompt-source",
                str(prompt_source),
                "--call-id",
                "11111111-1111-4111-8111-111111111111",
            ]
        ) == 0
    ticket = json.loads(output.getvalue())

    output = io.StringIO()
    with redirect_stdout(output):
        assert complete.main(
            [
                "--project-root",
                str(tmp_path),
                "--theme",
                "vivid-comic-explainer",
                "--scene",
                "scene-01",
                "--candidate-index",
                "1",
                "--candidate-source",
                str(candidate_source),
                "--call-id",
                ticket["call_id"],
                "--intent-sha256",
                ticket["intent_sha256"],
                "--adapt-runtime-native",
            ]
        ) == 0
    artifact = json.loads(output.getvalue())
    with Image.open(tmp_path / Path(artifact["path"])) as image:
        assert image.size == (3840, 2160)

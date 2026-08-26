"""Fully offline contract integration for Scheme A's three production systems."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import wave
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from PIL import Image

from boomearth.providers.x_article import XArticleImage
from boomearth.video.content_plan import compile_content_plan
from boomearth.video.illustration_manifest import load_illustration_manifest_snapshot
from boomearth.video.motion_plan import compile_motion_plan
from boomearth.video.render_project import prepare_content_render_project
from boomearth.video.scene_qc import publish_motion_preview_qc, render_scene_qc
from boomearth.video.scene_timeline import build_scene_timeline
from boomearth.workbench.handoff_compiler import compile_source_handoff
from boomearth.workbench.rewrite_package import REQUIRED_REVIEWS, prepare_rewrite_brief
from boomearth.workbench.source_artifacts import publish_json_exclusive, sha256_file
from boomearth.workbench.source_intake import create_x_article_intake
from boomearth.workbench.x_article_acquisition import (
    plan_x_article_acquisition,
    run_x_article_acquisition,
)
from test_scene_qc import _make_video


WORK_ID = "00000000-0000-4000-8000-000000000014"
CANONICAL_URL = "https://x.com/fixture_author/status/1234567890123456789"
ARTICLE_FIXTURE = Path(__file__).parent / "fixtures" / "x_article" / "article-code-images.html"
CHECKER = Path(__file__).parents[1] / "automation" / "scripts" / "check_delivery.py"
CONTENT_RUNNER = (
    Path(__file__).parents[1] / "automation" / "scripts" / "run_content_production.py"
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _png(width: int = 1920, height: int = 1080) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (width, height), (238, 232, 220)).save(stream, format="PNG")
    return stream.getvalue()


class _FixtureArticleTransport:
    def fetch_page(self, url: str) -> bytes:
        assert url == CANONICAL_URL
        return ARTICLE_FIXTURE.read_bytes()

    def fetch_image(self, _url: str) -> XArticleImage:
        payload = _png(16, 9)
        return XArticleImage(payload=payload, format="png", width=16, height=9)


def _checker():
    spec = importlib.util.spec_from_file_location("scheme_a_delivery_checker", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _content_runner():
    spec = importlib.util.spec_from_file_location(
        "scheme_a_content_runner", CONTENT_RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_x_article_rewrite_editorial_motion_render_and_delivery_are_connected(
    tmp_path: Path,
) -> None:
    private_url = tmp_path / "private-x-url.txt"
    private_url.write_text(CANONICAL_URL + "\n", encoding="utf-8")
    order = create_x_article_intake(
        tmp_path,
        private_url,
        authorized=True,
        now=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
        uuid_factory=lambda: UUID(WORK_ID),
    )
    plan_x_article_acquisition(tmp_path, WORK_ID)
    plan_path = order.private_root / "x-article-acquisition-plan.json"
    action = json.loads(plan_path.read_text(encoding="utf-8"))
    approval = order.private_root / "x-article-acquisition-approval.json"
    publish_json_exclusive(
        order.private_root,
        approval,
        {
            "action": action["action"],
            "approved": True,
            "input_sha256": action["input_sha256"],
            "no_fallback": True,
            "no_retry": True,
            "plan_sha256": sha256_file(plan_path),
            "provider": action["provider"],
            "request_count": 21,
            "work_id": WORK_ID,
        },
    )
    run_x_article_acquisition(
        tmp_path,
        WORK_ID,
        approval,
        transport=_FixtureArticleTransport(),
    )

    prepare_rewrite_brief(
        tmp_path,
        WORK_ID,
        platform="douyin",
        duration_target_s=45,
        archive_slug="2026-08-14-article-scheme-a",
    )
    narration_text = "换个起点，先确认观众真正遇到的困难，再重新组织一套可以落地的判断。"
    candidate = order.private_root / "rewrite-candidate.json"
    publish_json_exclusive(
        order.private_root,
        candidate,
        {
            "archive_slug": "2026-08-14-article-scheme-a",
            "duration_target_s": 45,
            "illustration_skill": "ra-video-illustrations",
            "platform": "douyin",
            "ratio": "16:9",
            "schema_version": 1,
            "segments": [
                {
                    "segment_id": "segment-001",
                    "text": narration_text,
                    "visual_intent": "从混乱线索中筛出一条清晰行动路径",
                }
            ],
            "title_candidates": ["换个起点，判断会更清楚"],
            "visual": "editorial-motion-v2",
            "work_id": WORK_ID,
        },
    )
    review = order.private_root / "rewrite-review.json"
    publish_json_exclusive(
        order.private_root,
        review,
        {
            "candidate_sha256": sha256_file(candidate),
            "reviewed_at": "2026-08-14T08:09:10Z",
            "reviews": {name: "pass" for name in REQUIRED_REVIEWS},
            "schema_version": 1,
        },
    )
    compile_source_handoff(tmp_path, WORK_ID, candidate, review)
    project = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "2026-08-14-article-scheme-a"
    )

    media = project / "工程" / "media"
    captions = media / "captions"
    prompts = project / "工程" / "assets" / "editorial-illustrations" / "prompts"
    captions.mkdir(parents=True)
    prompts.mkdir(parents=True)
    narration = media / "narration.wav"
    with wave.open(str(narration), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\0\0" * 16_000 * 5)
    segments = media / "segments.jsonl"
    segments.write_bytes(_canonical({"text": narration_text}))
    narration_sha = sha256_file(narration)
    (media / "voice_manifest.json").write_bytes(
        _canonical(
            {
                "output_sha256": narration_sha,
                "segment_contract_path": str(segments.absolute()),
                "segment_contract_sha256": sha256_file(segments),
                "segment_count": 1,
            }
        )
    )
    spoken = [character for character in narration_text if character.isalnum()]
    words = [
        {
            "text": character,
            "start": round(0.1 + index * 0.09, 3),
            "end": round(0.17 + index * 0.09, 3),
            "isGap": False,
        }
        for index, character in enumerate(spoken)
    ]
    (captions / "captions_words.json").write_bytes(_canonical(words))
    cue_end = words[-1]["end"]
    (captions / "captions.json").write_bytes(
        _canonical(
            [
                {
                    "start": 0.1,
                    "end": cue_end,
                    "text": narration_text,
                    "source": "volcengine-word-timestamps",
                }
            ]
        )
    )
    (captions / "caption-qc.json").write_bytes(
        _canonical(
            {
                "status": "pass",
                "timing_source": "volcengine-word-timestamps",
                "alignment_coverage": 1.0,
                "narration_sha256": narration_sha,
            }
        )
    )
    (captions / "asr-result.json").write_bytes(_canonical({"fixture": True}))
    (captions / "captions.srt").write_text(
        f"1\n00:00:00,100 --> 00:00:04,000\n{narration_text}\n",
        encoding="utf-8",
    )
    (captions / "captions.vtt").write_text(
        f"WEBVTT\n\n00:00.100 --> 00:04.000\n{narration_text}\n",
        encoding="utf-8",
    )

    prompt = prompts / "scene-01.md"
    prompt.write_text(
        """---
scene_id: scene-01
visual_type: concept-scene
visual_style: editorial-scene
ratio: 16:9
target_size: 3840x2160
text_policy: none
caption_safe_zone: bottom-150px
---

1. 唯一判断：换一个事实起点，行动路径会更清楚。
2. 主体与核心动作：人物从混乱线索中筛出一条清晰路径。
3. 构图和留白：主体在右中，左侧和底部留白。
4. 风格、材质和色板：成熟编辑插画与克制蓝橙配色。
5. 允许的短标签：无。
6. 明确禁止项：文字、字幕、Logo、水印、平台 UI。
""",
        encoding="utf-8",
    )
    image = project / "工程" / "assets" / "editorial-illustrations" / "scene-01.png"
    image.write_bytes(_png())
    content_candidate = project / "工程" / "content-plan.candidate.json"
    content_candidate.write_bytes(
        _canonical(
            {
                "schema_version": 2,
                "project_id": project.name,
                "ratio": "16:9",
                "visual_system": "editorial-motion-v2",
                "illustration_skill": "ra-video-illustrations",
                "typography_scale": "mobile-readable",
                "caption_style": "anchor-dark",
                "scenes": [
                    {
                        "id": "scene-01",
                        "narration_segment_ids": ["segment-001"],
                        "chapter": "重新判断",
                        "label": "起点",
                        "progress": "01 / 01",
                        "title_lines": ["换个起点", "路径更清楚"],
                        "subtitle_lines": ["先确认真实困难，再组织行动"],
                        "kicker": "从事实出发",
                        "notes": [
                            {"label": "先", "text": "筛出真实困难"},
                            {"label": "再", "text": "组织可落地判断"},
                        ],
                        "visual_intent": "从混乱线索中筛出一条清晰行动路径",
                        "visual_asset": "工程/assets/editorial-illustrations/scene-01.png",
                        "layout_variant": "wide-visual",
                        "visual_type": "concept-scene",
                        "visual_style": "editorial-scene",
                    }
                ],
            }
        )
    )
    compile_content_plan(project_root=project, candidate_path=content_candidate)
    build_scene_timeline(project_root=project)
    manifest_path = (
        project
        / "工程"
        / "assets"
        / "editorial-illustrations"
        / "illustration-manifest.json"
    )
    manifest_path.write_bytes(
        _canonical(
            {
                "schema_version": 1,
                "illustration_skill": "ra-video-illustrations",
                "visual_system": "editorial-motion-v2",
                "assets": [
                    {
                        "scene_id": "scene-01",
                        "visual_type": "concept-scene",
                        "visual_style": "editorial-scene",
                        "prompt_path": "工程/assets/editorial-illustrations/prompts/scene-01.md",
                        "prompt_sha256": sha256_file(prompt),
                        "asset_path": "工程/assets/editorial-illustrations/scene-01.png",
                        "asset_sha256": sha256_file(image),
                        "width": 1920,
                        "height": 1080,
                        "qc_status": "pass",
                    }
                ],
            }
        )
    )
    load_illustration_manifest_snapshot(project)
    compile_motion_plan(project_root=project)
    prepared = prepare_content_render_project(
        project_root=project,
        output_dir=project / "工程" / "render-project",
        repo_root=Path(__file__).parents[1],
    )
    html = (prepared.output_dir / "index.html").read_text(encoding="utf-8")
    assert "editorial-illustrations/scene-01.png" in html
    assert (
        'src="assets/perspective-grid-v5/perspective-grid-v5-loop.mp4"'
        in html
    )
    assert "muted playsinline loop" in html
    grid = (
        prepared.output_dir
        / "assets"
        / "perspective-grid-v5"
        / "perspective-grid-v5-loop.mp4"
    )
    assert sha256_file(grid) == (
        "4aa1d98d5a00d4ce0039e8ec71cdae6fe3ecccfedcfa777ddf583df0489ad4cf"
    )
    assert "repeat:-1" not in html

    final = project / "成片" / "final.mp4"
    _make_video(final)
    render_scene_qc(
        final_mp4=final,
        content_plan=project / "工程" / "content-plan.json",
        scene_timeline=project / "工程" / "scene-timeline.json",
        qc_root=project / "质检",
    )
    publish_motion_preview_qc(final, project)
    errors: list[str] = []
    artifacts = _checker()._content_delivery_artifacts(
        project,
        final,
        {
            "frame_count": 1,
            "times_s": [2.5],
            "layout": "1x1",
            "coverage": "all-scene-midpoints",
        },
        errors,
    )
    relative = {path.relative_to(project).as_posix() for path in artifacts}
    assert errors == []
    assert "工程/assets/editorial-illustrations/illustration-manifest.json" in relative
    assert "工程/motion-plan.json" in relative
    assert "质检/motion-preview-qc.json" in relative

    _timeline, protected, _handoff, _motion, _cover_sources = _content_runner()._content_contract(
        SimpleNamespace(active_dir=project)
    )
    protected_relative = {path.relative_to(project).as_posix() for path in protected}
    assert "工程/assets/editorial-illustrations/illustration-manifest.json" in protected_relative
    assert {
        f"工程/assets/editorial-illustrations/prompts/scene-{index:02d}.md"
        for index in range(1, 2)
    } <= protected_relative

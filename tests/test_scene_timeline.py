"""Final-audio-only scene timeline contract tests."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import math
import wave
from pathlib import Path

import pytest

from boomearth.audio.indextts2 import CURRENT_VOICE_ID
from boomearth.video.content_plan import compile_content_plan
from boomearth.video.scene_timeline import (
    SceneTimelineError,
    build_scene_timeline,
    validate_scene_timeline,
)


SCRIPT = Path(__file__).parents[1] / "automation" / "scripts" / "build_scene_timeline.py"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
SEGMENTS = (
    "第一段介绍 Claude Max。",
    "第二段解释底层事实。",
    "第三段重新搭建流程。",
    "第四段完成最终验证。",
)


def _write_wav(path: Path, *, seconds: float = 16.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * round(seconds * 16_000))


def _candidate(project: Path) -> dict[str, object]:
    variants = ("standard", "long-title", "wide-visual", "close")
    return {
        "schema_version": 1,
        "project_id": project.name,
        "ratio": "16:9",
        "visual_system": "xiaohei-white-first-v1",
        "typography_scale": "mobile-readable",
        "caption_style": "anchor-dark",
        "scenes": [
            {
                "id": f"scene-{index:02d}",
                "narration_segment_ids": [f"segment-{index:03d}"],
                "chapter": "核心问题",
                "label": "为什么",
                "progress": f"0{index}",
                "title_lines": [f"第{index}个结论"],
                "subtitle_lines": ["从底层事实出发"],
                "kicker": "关键点",
                "notes": [{"label": "结论", "text": "先验证再行动"}],
                "visual_intent": "用小黑人物和本地短标签表现具体动作",
                "visual_asset": f"工程/assets/xiaohei-illustrations/scene-{index:02d}.png",
                "overlay_labels": ["动作主体", "关键结果"],
                "layout_variant": variants[index - 1],
            }
            for index in range(1, 5)
        ],
    }


def _words() -> list[dict[str, object]]:
    return [
        {"text": "第一段介绍", "start": 0.2, "end": 1.5, "isGap": False},
        {"text": "Claude Max", "start": 1.6, "end": 3.0, "isGap": False},
        {"text": "", "start": 3.0, "end": 4.2, "isGap": True},
        {"text": "第二段解释", "start": 4.2, "end": 5.4, "isGap": False},
        {"text": "底层事实", "start": 5.5, "end": 7.0, "isGap": False},
        {"text": "第三段重新", "start": 7.4, "end": 8.7, "isGap": False},
        {"text": "搭建流程", "start": 8.8, "end": 10.5, "isGap": False},
        {"text": "第四段完成", "start": 11.0, "end": 12.3, "isGap": False},
        {"text": "最终验证", "start": 12.4, "end": 14.0, "isGap": False},
    ]


def _populate_project(
    project: Path,
    *,
    words: list[dict[str, object]] | None = None,
    segments: tuple[str, ...] = SEGMENTS,
) -> None:
    media = project / "工程" / "media"
    assets = project / "工程" / "assets" / "xiaohei-illustrations"
    captions = media / "captions"
    captions.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    handoff = "\n\n".join(
        f"### segment-{index:03d}\n\n{text}" for index, text in enumerate(segments, 1)
    )
    (project / "交接稿.md").write_text(
        "---\n"
        "status: 制作中\n"
        "platform: local-v1\n"
        "ratio: '16:9'\n"
        "duration_target_s: 16.000\n"
        "word_count: 40\n"
        f"voice: {CURRENT_VOICE_ID}\n"
        "voice_provider: indextts2-local\n"
        "captions: asr-word-timestamps\n"
        "caption_style: anchor-dark\n"
        "visual: xiaohei-white-first-v1\n"
        "illustration_skill: ian-xiaohei-illustrations\n"
        f"archive_slug: {project.name.removeprefix('2026-08-13-')}\n"
        "---\n\n"
        f"## 新稿分段\n\n{handoff}\n\n## 分段视觉意图\n\n- 已审核。\n",
        encoding="utf-8",
    )
    batch = media / "segments.jsonl"
    batch_bytes = b"".join(
        (json.dumps({"text": text}, ensure_ascii=False) + "\n").encode("utf-8")
        for text in segments
    )
    batch.write_bytes(batch_bytes)
    narration = media / "narration.wav"
    _write_wav(narration)
    narration_hash = hashlib.sha256(narration.read_bytes()).hexdigest()
    (media / "voice_manifest.json").write_text(
        json.dumps(
            {
                "segment_contract_path": str(batch.absolute()),
                "segment_contract_sha256": hashlib.sha256(batch_bytes).hexdigest(),
                "segment_count": 4,
                "output_sha256": narration_hash,
            }
        ),
        encoding="utf-8",
    )
    for index in range(1, 5):
        (assets / f"scene-{index:02d}.png").write_bytes(PNG)
    candidate = project / "工程" / "content-plan.candidate.json"
    candidate.write_text(json.dumps(_candidate(project), ensure_ascii=False), encoding="utf-8")
    compile_content_plan(project_root=project, candidate_path=candidate)
    word_values = words if words is not None else _words()
    (captions / "captions_words.json").write_text(
        json.dumps(word_values, ensure_ascii=False), encoding="utf-8"
    )
    (captions / "caption-qc.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "timing_source": "volcengine-word-timestamps",
                "alignment_coverage": 1.0,
                "narration_sha256": narration_hash,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "2026-08-13-scene-timeline"
    _populate_project(root)
    return root


def test_inter_scene_silence_belongs_to_the_previous_scene(project: Path) -> None:
    timeline = build_scene_timeline(project_root=project).timeline

    assert timeline.scenes[0].end == 4.2
    assert timeline.scenes[1].start == 4.2
    assert tuple((scene.start, scene.end) for scene in timeline.scenes) == (
        (0.0, 4.2),
        (4.2, 7.4),
        (7.4, 11.0),
        (11.0, 16.0),
    )


def test_timeline_binds_final_audio_words_and_content_plan_hashes(project: Path) -> None:
    result = build_scene_timeline(project_root=project)

    media = project / "工程" / "media"
    assert result.timeline.narration_sha256 == hashlib.sha256(
        (media / "narration.wav").read_bytes()
    ).hexdigest()
    assert result.timeline.captions_words_sha256 == hashlib.sha256(
        (media / "captions" / "captions_words.json").read_bytes()
    ).hexdigest()
    assert result.timeline.content_plan_sha256 == hashlib.sha256(
        (project / "工程" / "content-plan.json").read_bytes()
    ).hexdigest()
    assert result.timeline.alignment_coverage == 1.0
    assert all(scene.alignment_coverage == 1.0 for scene in result.timeline.scenes)
    assert result.path.read_bytes().endswith(b"\n")


def test_multiple_consecutive_segments_can_share_one_scene(tmp_path: Path) -> None:
    project = tmp_path / "2026-08-13-merged-scene"
    _populate_project(project)
    formal = project / "工程" / "content-plan.json"
    formal.unlink()
    candidate_path = project / "工程" / "content-plan.candidate.json"
    candidate = _candidate(project)
    candidate["scenes"][0]["narration_segment_ids"] = ["segment-001", "segment-002"]  # type: ignore[index]
    candidate["scenes"].pop(1)  # type: ignore[union-attr]
    for index, scene in enumerate(candidate["scenes"], 1):  # type: ignore[union-attr]
        scene["id"] = f"scene-{index:02d}"
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    compile_content_plan(project_root=project, candidate_path=candidate_path)

    timeline = build_scene_timeline(project_root=project).timeline

    assert timeline.scenes[0].segment_ids == ("segment-001", "segment-002")
    assert timeline.scenes[0].end == 7.4


@pytest.mark.parametrize("coverage", [0.89, float("nan")])
def test_caption_qc_must_be_passed_and_wav_bound(project: Path, coverage: float) -> None:
    qc_path = project / "工程" / "media" / "captions" / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["alignment_coverage"] = coverage
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="caption QC is invalid"):
        build_scene_timeline(project_root=project)

    assert not (project / "工程" / "scene-timeline.json").exists()


def test_caption_qc_hash_must_match_the_final_wav(project: Path) -> None:
    qc_path = project / "工程" / "media" / "captions" / "caption-qc.json"
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    qc["narration_sha256"] = "0" * 64
    qc_path.write_text(json.dumps(qc), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="caption QC is invalid"):
        build_scene_timeline(project_root=project)


def test_overall_real_word_alignment_below_point_nine_is_rejected(project: Path) -> None:
    words_path = project / "工程" / "media" / "captions" / "captions_words.json"
    words = _words()
    words[0]["text"] = "完全无关"
    words[1]["text"] = "仍然错误"
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="word alignment is invalid"):
        build_scene_timeline(project_root=project)


def test_scene_alignment_below_point_eight_is_rejected(project: Path) -> None:
    words_path = project / "工程" / "media" / "captions" / "captions_words.json"
    words = _words()
    words[4]["text"] = "底层"
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="scene alignment is invalid"):
        build_scene_timeline(project_root=project)


def test_extra_non_gap_asr_word_is_rejected(project: Path) -> None:
    words_path = project / "工程" / "media" / "captions" / "captions_words.json"
    words = _words()
    words.insert(2, {"text": "额外幻觉", "start": 3.0, "end": 3.1, "isGap": False})
    words[3]["start"] = 3.1
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="word alignment is invalid"):
        build_scene_timeline(project_root=project)


def test_six_single_character_unmapped_asr_noises_do_not_block_timeline(
    project: Path,
) -> None:
    words_path = project / "工程" / "media" / "captions" / "captions_words.json"
    words = _words()
    words.insert(2, {"text": "늬", "start": 3.00, "end": 3.04, "isGap": False})
    words.insert(3, {"text": "샴", "start": 3.05, "end": 3.21, "isGap": False})
    words.insert(4, {"text": "绘", "start": 3.22, "end": 3.42, "isGap": False})
    words.insert(5, {"text": "画", "start": 3.43, "end": 3.59, "isGap": False})
    words.insert(6, {"text": "稿", "start": 3.60, "end": 3.68, "isGap": False})
    words.insert(7, {"text": "件", "start": 3.69, "end": 3.77, "isGap": False})
    words[8]["start"] = 3.78
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    timeline = build_scene_timeline(project_root=project).timeline

    assert timeline.alignment_coverage >= 0.90


def test_short_ascii_prefix_noise_before_mapped_english_word_does_not_block_timeline(
    tmp_path: Path,
) -> None:
    """Would fail if one brief provider prefix before a correct English token blocked timing."""

    project = tmp_path / "2026-08-13-short-ascii-prefix"
    segments = ("第一段介绍 Skill。", *SEGMENTS[1:])
    words = _words()
    words[1]["text"] = "Skill"
    words.insert(1, {"text": "Geek", "start": 1.51, "end": 1.59, "isGap": False})
    _populate_project(project, words=words, segments=segments)

    timeline = build_scene_timeline(project_root=project).timeline

    assert timeline.alignment_coverage >= 0.90


def test_equivalent_ascii_number_normalization_does_not_block_timeline(
    tmp_path: Path,
) -> None:
    project = tmp_path / "2026-08-13-number-normalization"
    segments = ("第一段介绍十一 Claude Max。", *SEGMENTS[1:])
    words = _words()
    words.insert(1, {"text": "11", "start": 1.51, "end": 1.59, "isGap": False})
    _populate_project(project, words=words, segments=segments)

    timeline = build_scene_timeline(project_root=project).timeline

    assert timeline.alignment_coverage >= 0.90


def test_equivalent_ascii_number_range_normalization_does_not_block_timeline(
    tmp_path: Path,
) -> None:
    project = tmp_path / "2026-08-13-number-range-normalization"
    segments = ("第一段介绍四到八 Claude Max。", *SEGMENTS[1:])
    words = _words()
    words.insert(1, {"text": "4~8", "start": 1.51, "end": 1.59, "isGap": False})
    _populate_project(project, words=words, segments=segments)

    timeline = build_scene_timeline(project_root=project).timeline

    assert timeline.alignment_coverage >= 0.90


def test_non_equivalent_ascii_number_range_normalization_is_rejected(
    tmp_path: Path,
) -> None:
    project = tmp_path / "2026-08-13-wrong-number-range-normalization"
    segments = ("第一段介绍四到八 Claude Max。", *SEGMENTS[1:])
    words = _words()
    words.insert(1, {"text": "4~9", "start": 1.51, "end": 1.59, "isGap": False})
    _populate_project(project, words=words, segments=segments)

    with pytest.raises(SceneTimelineError, match="word alignment is invalid"):
        build_scene_timeline(project_root=project)


@pytest.mark.parametrize(("script_token", "asr_token"), [("它", "他"), ("得", "的")])
def test_equivalent_single_character_homophone_does_not_block_timeline(
    tmp_path: Path, script_token: str, asr_token: str
) -> None:
    project = tmp_path / f"2026-08-13-homophone-{ord(script_token)}"
    segments = (f"第一段介绍{script_token} Claude Max。", *SEGMENTS[1:])
    words = _words()
    words.insert(1, {"text": asr_token, "start": 1.51, "end": 1.59, "isGap": False})
    for index, text in enumerate(("늬", "샴", "绘", "画", "稿", "件")):
        words.insert(
            3 + index,
            {
                "text": text,
                "start": 3.00 + index * 0.08,
                "end": 3.04 + index * 0.08,
                "isGap": False,
            },
        )
    words[9]["start"] = 3.48
    _populate_project(project, words=words, segments=segments)

    timeline = build_scene_timeline(project_root=project).timeline

    assert timeline.alignment_coverage >= 0.90


def test_non_equivalent_ascii_number_normalization_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "2026-08-13-wrong-number-normalization"
    segments = ("第一段介绍十一 Claude Max。", *SEGMENTS[1:])
    words = _words()
    words.insert(1, {"text": "12", "start": 1.51, "end": 1.59, "isGap": False})
    _populate_project(project, words=words, segments=segments)

    with pytest.raises(SceneTimelineError, match="word alignment is invalid"):
        build_scene_timeline(project_root=project)


def test_seven_single_character_unmapped_asr_noises_block_timeline(
    project: Path,
) -> None:
    words_path = project / "工程" / "media" / "captions" / "captions_words.json"
    words = _words()
    for index, text in enumerate(("늬", "샴", "绘", "画", "稿", "件", "错")):
        words.insert(2 + index, {
            "text": text,
            "start": 3.00 + index * 0.08,
            "end": 3.04 + index * 0.08,
            "isGap": False,
        })
    words[9]["start"] = 3.60
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="word alignment is invalid"):
        build_scene_timeline(project_root=project)


@pytest.mark.parametrize(
    "mutation",
    [
        {"start": True},
        {"end": math.inf},
        {"unexpected": 1},
        {"start": 2.0, "end": 1.0},
    ],
)
def test_word_records_are_strict_and_finite(project: Path, mutation: dict[str, object]) -> None:
    words_path = project / "工程" / "media" / "captions" / "captions_words.json"
    words = _words()
    words[0].update(mutation)
    words_path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="caption words are invalid"):
        build_scene_timeline(project_root=project)


def test_word_records_reject_duplicate_json_keys(project: Path) -> None:
    path = project / "工程" / "media" / "captions" / "captions_words.json"
    path.write_text(
        '[{"text":"第一段介绍","start":9.0,"start":0.2,"end":1.5,"isGap":false}]',
        encoding="utf-8",
    )

    with pytest.raises(SceneTimelineError, match="caption words are invalid"):
        build_scene_timeline(project_root=project)


def test_scene_shorter_than_one_second_is_rejected(project: Path) -> None:
    words = _words()
    words[2]["end"] = 3.5
    words[3]["start"] = 3.5
    words[3]["end"] = 3.7
    words[4]["start"] = 3.7
    words[4]["end"] = 3.9
    words[5]["start"] = 4.0
    words[5]["end"] = 5.0
    words[6]["start"] = 5.1
    words[6]["end"] = 6.0
    words[7]["start"] = 6.5
    words[7]["end"] = 7.5
    words[8]["start"] = 7.6
    words[8]["end"] = 8.5
    path = project / "工程" / "media" / "captions" / "captions_words.json"
    path.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SceneTimelineError, match="scene timing is invalid"):
        build_scene_timeline(project_root=project)


def test_alignment_failure_has_no_estimated_timing_fallback(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boomearth.video.scene_timeline as timeline_module

    def reject_alignment(*_args, **_kwargs):
        raise RuntimeError("synthetic alignment failure")

    monkeypatch.setattr(timeline_module, "align_display_script", reject_alignment)

    with pytest.raises(SceneTimelineError, match="word alignment is invalid"):
        build_scene_timeline(project_root=project)

    assert not (project / "工程" / "scene-timeline.json").exists()


def test_changed_upstream_artifact_prevents_publication(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boomearth.video.scene_timeline as timeline_module

    real_matches = timeline_module.snapshot_matches
    words_path = project / "工程" / "media" / "captions" / "captions_words.json"
    changed = False

    def change_words(snapshot) -> bool:
        nonlocal changed
        if snapshot.path == words_path.absolute() and not changed:
            changed = True
            words_path.write_text("[]\n", encoding="utf-8")
        return real_matches(snapshot)

    monkeypatch.setattr(timeline_module, "snapshot_matches", change_words)

    with pytest.raises(SceneTimelineError, match="timeline input changed"):
        build_scene_timeline(project_root=project)

    assert not (project / "工程" / "scene-timeline.json").exists()


def test_validate_scene_timeline_rejects_discontinuous_value(project: Path) -> None:
    result = build_scene_timeline(project_root=project)
    value = json.loads(result.path.read_text(encoding="utf-8"))
    value["scenes"][1]["start"] = 4.3

    with pytest.raises(SceneTimelineError, match="scene timing is invalid"):
        validate_scene_timeline(value, project_root=project)


def test_validate_timeline_rederives_word_boundaries_not_just_continuity(
    project: Path,
) -> None:
    result = build_scene_timeline(project_root=project)
    value = json.loads(result.path.read_text(encoding="utf-8"))
    value["scenes"][0]["end"] = 4.3
    value["scenes"][1]["start"] = 4.3

    with pytest.raises(SceneTimelineError, match="scene timing is invalid"):
        validate_scene_timeline(value, project_root=project)


def test_timeline_is_no_clobber(project: Path) -> None:
    build_scene_timeline(project_root=project)

    with pytest.raises(SceneTimelineError, match="timeline publication failed"):
        build_scene_timeline(project_root=project)


def test_cli_failure_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    spec = importlib.util.spec_from_file_location("scene_timeline_cli_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    exit_code = module.main(
        [
            "--workspace-root",
            "missing-workspace",
            "--active-project",
            "2026-08-13-missing",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().out == "rule=scene-timeline-build\n"


def test_cli_builds_only_for_a_canonical_active_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = importlib.util.spec_from_file_location("scene_timeline_cli_success", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import run_v2_production_sample as orchestrator

    workspace = tmp_path / "workspace"
    created = orchestrator.initialize_production_project(
        workspace_root=workspace, archive_slug="scene-timeline-cli"
    )
    _populate_project(created.active_dir)

    exit_code = module.main(
        [
            "--workspace-root",
            str(workspace),
            "--active-project",
            created.active_dir.name,
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "status=scene-timeline-published\n"
        "artifact=工程/scene-timeline.json\n"
        "scenes=4\n"
    )

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from boomearth.video.motion_plan import (
    compile_motion_plan,
    MotionPlanError,
    load_motion_plan_snapshot,
    validate_motion_plan,
)


def _write_inputs(root: Path) -> dict[str, object]:
    engineering = root / "工程"
    captions = engineering / "media" / "captions"
    captions.mkdir(parents=True)
    content = {
        "schema_version": 2,
        "visual_system": "editorial-motion-v2",
        "scenes": [
            {
                "id": "scene-01",
                "chapter": "01",
                "progress": "1/1",
                "title_lines": ["Title", "Second"],
                "subtitle_lines": ["Sub"],
                "visual_asset": "工程/assets/editorial-illustrations/scene-01.png",
                "kicker": "Kicker",
                "notes": [{"label": "A", "text": "B"}],
            }
        ],
    }
    timeline = {
        "schema_version": 1,
        "duration_seconds": 4.0,
        "scenes": [{"id": "scene-01", "start": 0.0, "end": 4.0}],
    }
    words = [{"text": "Title", "start": 0.1, "end": 0.4, "isGap": False}]
    for path, value in (
        (engineering / "content-plan.json", content),
        (engineering / "scene-timeline.json", timeline),
        (captions / "captions_words.json", words),
    ):
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "schema_version": 1,
        "motion_profile": "editorial-cards-v2",
        "content_plan_sha256": digest(engineering / "content-plan.json"),
        "scene_timeline_sha256": digest(engineering / "scene-timeline.json"),
        "captions_words_sha256": digest(captions / "captions_words.json"),
        "duration_seconds": 4.0,
        "scenes": [
            {
                "scene_id": "scene-01",
                "start": 0.0,
                "end": 4.0,
                "entries": [
                    {"target": "chapter", "at": 0.0, "duration": 0.24, "motion": "fade-down"},
                    {"target": "title-line-1", "at": 0.1, "duration": 0.34, "motion": "line-reveal"},
                    {"target": "visual", "at": 0.32, "duration": 0.46, "motion": "scale-settle"},
                    {"target": "note-row-1", "at": 0.8, "duration": 0.28, "motion": "fade-up"},
                ],
                "semantic_cues": [
                    {"text": "Title", "target": "title-line-1", "at": 1.2, "duration": 0.4, "motion": "accent-pulse"}
                ],
                "ambient": {"target": "visual", "motion": "slow-parallax", "start": 0.8, "end": 3.6, "strength": 0.35},
            }
        ],
    }


def test_validate_motion_plan_accepts_exact_hash_bound_component_motion(tmp_path: Path) -> None:
    value = _write_inputs(tmp_path)
    plan = validate_motion_plan(value, project_root=tmp_path)
    assert plan.motion_profile == "editorial-cards-v2"
    assert plan.scenes[0].entries[1].target == "title-line-1"
    assert plan.scenes[0].ambient is not None


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda v: v.update(extra=True), "motion-plan-invalid"),
        (lambda v: v.update(motion_profile="legacy"), "motion-plan-profile-invalid"),
        (lambda v: v["scenes"][0]["entries"][0].update(target="missing"), "motion-plan-target-invalid"),
        (lambda v: v["scenes"][0]["entries"][0].update(motion="bounce"), "motion-plan-effect-invalid"),
        (lambda v: v["scenes"][0]["entries"][0].update(at=3.9, duration=0.2), "motion-plan-timing-invalid"),
        (lambda v: v["scenes"][0]["entries"].append(dict(v["scenes"][0]["entries"][0])), "motion-plan-target-invalid"),
        (
            lambda v: v["scenes"][0]["entries"].append(
                {
                    **v["scenes"][0]["entries"][0],
                    "at": 1.0,
                    "motion": "fade-up",
                }
            ),
            "motion-plan-target-invalid",
        ),
        (lambda v: v["scenes"][0]["ambient"].update(end=4.1), "motion-plan-timing-invalid"),
    ],
)
def test_validate_motion_plan_rejects_unknown_duplicate_or_out_of_bounds(
    tmp_path: Path, mutation, error: str
) -> None:
    value = _write_inputs(tmp_path)
    mutation(value)
    with pytest.raises(MotionPlanError, match=f"^{error}$"):
        validate_motion_plan(value, project_root=tmp_path)


def test_validate_motion_plan_rejects_upstream_hash_or_profile_drift(tmp_path: Path) -> None:
    value = _write_inputs(tmp_path)
    (tmp_path / "工程" / "content-plan.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(MotionPlanError, match="^motion-plan-input-changed$"):
        validate_motion_plan(value, project_root=tmp_path)


def test_load_motion_plan_snapshot_captures_formal_plan(tmp_path: Path) -> None:
    value = _write_inputs(tmp_path)
    path = tmp_path / "工程" / "motion-plan.json"
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    snapshot = load_motion_plan_snapshot(tmp_path)
    assert snapshot.path == path
    assert snapshot.snapshot.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_compile_motion_plan_is_deterministic_reveals_components_before_midpoint(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)

    snapshot = compile_motion_plan(project_root=tmp_path)

    scene = snapshot.plan.scenes[0]
    assert scene.entries[0].target == "chapter"
    assert min(entry.at for entry in scene.entries) >= 0.1
    assert {entry.target for entry in scene.entries} >= {
        "progress", "title-line-1", "title-line-2", "subtitle-line-1", "visual",
        "note-card", "kicker", "note-row-1"
    }
    assert max(entry.at + entry.duration for entry in scene.entries) <= 2.0
    title_entry = next(item for item in scene.entries if item.target == "title-line-1")
    assert title_entry.at + title_entry.duration <= 0.6
    assert scene.ambient is not None
    assert scene.semantic_cues[0].at == 0.1
    before = snapshot.snapshot.payload
    (tmp_path / "工程" / "motion-plan.json").unlink()
    assert compile_motion_plan(project_root=tmp_path).snapshot.payload == before


def test_compile_motion_plan_is_no_clobber(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    compile_motion_plan(project_root=tmp_path)
    with pytest.raises(MotionPlanError, match="^motion-plan-exists$"):
        compile_motion_plan(project_root=tmp_path)


def test_compile_motion_plan_reveals_kicker_when_scene_has_no_notes(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    path = tmp_path / "工程" / "content-plan.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["scenes"][0]["notes"] = []
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    scene = compile_motion_plan(project_root=tmp_path).plan.scenes[0]
    targets = {entry.target for entry in scene.entries}
    assert {"chapter", "progress", "note-card", "kicker"} <= targets
    assert not {target for target in targets if target.startswith("note-row-")}


def test_compile_motion_plan_cues_longest_spoken_phrase_in_each_title_line(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    content_path = tmp_path / "工程" / "content-plan.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["scenes"][0]["title_lines"] = [
        "模型只占一半",
        "执行环境组成另一半",
    ]
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    words_path = tmp_path / "工程" / "media" / "captions" / "captions_words.json"
    words_path.write_text(
        json.dumps(
            [
                {"text": "模型", "start": 0.2, "end": 0.5, "isGap": False},
                {
                    "text": "执行环境",
                    "start": 0.8,
                    "end": 1.3,
                    "isGap": False,
                },
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    cues = compile_motion_plan(project_root=tmp_path).plan.scenes[0].semantic_cues

    assert [(cue.text, cue.target, cue.at) for cue in cues] == [
        ("模型", "title-line-1", 0.2),
        ("执行环境", "title-line-2", 0.8),
    ]


def test_compile_motion_plan_rejects_mismatched_scene_counts_with_domain_error(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    path = tmp_path / "工程" / "scene-timeline.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["scenes"] = []
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(MotionPlanError, match="^motion-plan-input-changed$"):
        compile_motion_plan(project_root=tmp_path)


def test_compile_motion_plan_supports_v3_type_led_and_overlay_label_targets(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path)
    content_path = tmp_path / "工程" / "content-plan.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["schema_version"] = 3
    content["visual_system"] = "semantic-handdrawn-v3"
    scene = content["scenes"][0]
    scene["visual_asset"] = None
    scene["visual_mode"] = "type-led"
    scene["overlay_labels"] = ["可回查", "太复杂"]
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    plan = compile_motion_plan(project_root=tmp_path).plan

    assert plan.motion_profile == "semantic-handdrawn-v3"
    targets = {entry.target for entry in plan.scenes[0].entries}
    assert {"visual", "overlay-label-1", "overlay-label-2"} <= targets


def test_compile_motion_plan_supports_profiled_v4(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    content_path = tmp_path / "工程" / "content-plan.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["schema_version"] = 4
    content["visual_system"] = "profiled-illustration-v4"
    content["visual_theme"] = "vivid-comic-explainer"
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    plan = compile_motion_plan(project_root=tmp_path).plan

    assert plan.motion_profile == "profiled-illustration-v4"


@pytest.mark.parametrize(
    ("theme_id", "expected_effect"),
    [
        ("vivid-comic-explainer", "scale-settle"),
        ("engineering-sketch-explainer", "line-reveal"),
        ("four-panel-comic-explainer", "wipe-right"),
        ("blue-black-whiteboard-explainer", "line-reveal"),
    ],
)
def test_profiled_motion_uses_theme_effect_and_reveals_all_eight_labels(
    tmp_path: Path, theme_id: str, expected_effect: str
) -> None:
    _write_inputs(tmp_path)
    content_path = tmp_path / "工程" / "content-plan.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    content["schema_version"] = 4
    content["visual_system"] = "profiled-illustration-v4"
    content["visual_theme"] = theme_id
    content["scenes"][0]["overlay_labels"] = [f"标签{index}" for index in range(1, 9)]
    content_path.write_text(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    scene = compile_motion_plan(project_root=tmp_path).plan.scenes[0]

    visual = next(entry for entry in scene.entries if entry.target == "visual")
    assert visual.motion == expected_effect
    assert {
        entry.target for entry in scene.entries if entry.target.startswith("overlay-label-")
    } == {f"overlay-label-{index}" for index in range(1, 9)}

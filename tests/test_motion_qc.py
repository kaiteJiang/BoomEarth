from __future__ import annotations

from boomearth.video.motion_plan import AmbientMotion, MotionEntry, MotionPlan, SceneMotion, SemanticCue
from boomearth.video.scene_qc import motion_preview_times


def _plan() -> MotionPlan:
    return MotionPlan(
        1, "editorial-cards-v2", "a" * 64, "b" * 64, "c" * 64, 4.0,
        (SceneMotion(
            "scene-01", 0.0, 4.0,
            (
                MotionEntry("title-line-1", 0.1, 0.3, "line-reveal"),
                MotionEntry("visual", 0.5, 0.4, "scale-settle"),
                MotionEntry("note-row-1", 1.0, 0.24, "fade-up"),
            ),
            (SemanticCue("key", "title-line-1", 2.0, 0.4, "accent-pulse"),),
            AmbientMotion("visual", "slow-parallax", 1.0, 3.6, 0.35),
        ),),
    )


def test_motion_preview_times_cover_six_required_states_and_semantic_sweep() -> None:
    values = motion_preview_times(_plan())
    scene = values["scene-01"]
    assert set(scene) == {
        "start", "title-stable", "visual-stable", "notes-progress",
        "midpoint", "late", "cue-01-before", "cue-01-during", "cue-01-after",
    }
    assert scene["start"] == 0.05
    assert scene["title-stable"] > 0.4
    assert scene["visual-stable"] > 0.9
    assert scene["cue-01-before"] < scene["cue-01-during"] < scene["cue-01-after"]
    assert len(set(scene.values())) == len(scene)
    assert all(0.0 < value < 4.0 for value in scene.values())


def test_motion_preview_times_avoids_early_semantic_sample_collision() -> None:
    plan = MotionPlan(
        1, "editorial-cards-v2", "a" * 64, "b" * 64, "c" * 64, 5.0,
        (SceneMotion(
            "scene-01", 0.0, 5.0,
            (
                MotionEntry("title-line-1", 0.08, 0.3, "line-reveal"),
                MotionEntry("visual", 0.6, 0.38, "scale-settle"),
                MotionEntry("note-row-1", 1.0, 0.24, "fade-up"),
            ),
            (SemanticCue("early", "title-line-1", 0.1, 0.34, "accent-pulse"),),
            AmbientMotion("visual", "slow-parallax", 1.0, 4.6, 0.35),
        ),),
    )

    scene = motion_preview_times(plan)["scene-01"]

    assert len(scene) == len(set(scene.values()))
    assert scene["start"] < scene["cue-01-before"] < 0.1
    assert scene["cue-01-before"] < scene["cue-01-during"] < scene["cue-01-after"]


def test_motion_preview_times_omits_impossible_before_state_at_scene_start() -> None:
    source = _plan()
    scene = source.scenes[0]
    plan = MotionPlan(
        source.schema_version,
        source.motion_profile,
        source.content_plan_sha256,
        source.scene_timeline_sha256,
        source.captions_words_sha256,
        source.duration_seconds,
        (
            SceneMotion(
                scene.scene_id,
                scene.start,
                scene.end,
                scene.entries,
                (
                    SemanticCue(
                        "opening",
                        "title-line-1",
                        scene.start,
                        0.4,
                        "accent-pulse",
                    ),
                ),
                scene.ambient,
            ),
        ),
    )

    values = motion_preview_times(plan)["scene-01"]

    assert "cue-01-before" not in values
    assert scene.start < values["cue-01-during"] < values["cue-01-after"]

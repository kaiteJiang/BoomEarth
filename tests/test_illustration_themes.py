from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import boomearth.video.illustration_themes as illustration_themes
from boomearth.video.illustration_themes import (
    CHINESE_STYLE_CATALOG,
    PROFILED_VISUAL_SYSTEM,
    THEMES,
    IllustrationThemeError,
    get_theme,
)


EXPECTED_THEMES = {
    "vivid-comic-explainer",
    "engineering-sketch-explainer",
    "four-panel-comic-explainer",
    "blue-black-whiteboard-explainer",
    "xiaohuang-warm-first-v1",
}

EXPECTED_QC = {
    "vivid-comic-explainer": {
        "character_consistent",
        "expression_supports_claim",
        "action_explains_claim",
        "accent_palette_controlled",
        "not_decorative_cartoon",
    },
    "engineering-sketch-explainer": {
        "engineering_subject_real",
        "callouts_support_claim",
        "mechanical_exception_valid",
        "linework_clean",
        "diagram_not_overloaded",
    },
    "four-panel-comic-explainer": {
        "exactly_four_panels",
        "reading_order_clear",
        "beats_continuous",
        "character_consistent",
        "one_event_per_panel",
        "lower_panels_caption_safe",
    },
    "blue-black-whiteboard-explainer": {
        "marker_material_clear",
        "blue_black_palette_only",
        "structure_type_clear",
        "reading_path_clear",
        "not_ppt_page",
        "not_character_led",
    },
    "xiaohuang-warm-first-v1": {
        "xiaohuang_identity_consistent",
        "character_performs_action",
        "native_labels_correct",
        "warm_white_canvas",
        "not_system_label_overlay",
    },
}


def test_profiled_theme_registry_is_exact_and_immutable() -> None:
    assert PROFILED_VISUAL_SYSTEM == "profiled-illustration-v4"
    assert set(THEMES) == EXPECTED_THEMES
    assert all(theme.directory == theme.id for theme in THEMES.values())
    assert {
        theme.chinese_name for theme in THEMES.values()
    } == {
        "鲜彩漫画讲解",
        "工程手稿图解",
        "四格连环漫画",
        "蓝黑白板讲解",
        "小黄温度插画",
    }
    assert {
        theme_id: set(theme.required_qc)
        for theme_id, theme in THEMES.items()
    } == EXPECTED_QC
    with pytest.raises(TypeError):
        THEMES["another"] = THEMES["vivid-comic-explainer"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        THEMES["vivid-comic-explainer"].directory = "changed"  # type: ignore[misc]


def test_chinese_style_catalog_has_nine_unique_stable_entries() -> None:
    assert [entry.chinese_name for entry in CHINESE_STYLE_CATALOG] == [
        "小黑怪诞插画",
        "编辑动效插画",
        "语义手绘插画",
        "动态文字卡片",
        "鲜彩漫画讲解",
        "工程手稿图解",
        "四格连环漫画",
        "蓝黑白板讲解",
        "小黄温度插画",
    ]
    assert [entry.target for entry in CHINESE_STYLE_CATALOG[:4]] == [
        "xiaohei-white-first-v1",
        "editorial-motion-v2",
        "semantic-handdrawn-v3",
        "semantic-handdrawn-v3/type-led",
    ]
    assert [entry.target for entry in CHINESE_STYLE_CATALOG[4:]] == [
        "vivid-comic-explainer",
        "engineering-sketch-explainer",
        "four-panel-comic-explainer",
        "blue-black-whiteboard-explainer",
        "xiaohuang-warm-first-v1",
    ]
    assert len({entry.invocation for entry in CHINESE_STYLE_CATALOG}) == 9
    assert all(entry.invocation.endswith("。") for entry in CHINESE_STYLE_CATALOG)


def test_get_theme_fails_closed_for_unknown_or_non_string_ids() -> None:
    assert get_theme("vivid-comic-explainer") is THEMES["vivid-comic-explainer"]
    for value in ("unknown", "", None, 4):
        with pytest.raises(
            IllustrationThemeError, match="^illustration-theme-invalid$"
        ):
            get_theme(value)  # type: ignore[arg-type]


def test_default_request_resolves_to_xiaohei_contract() -> None:
    assert illustration_themes.resolve_visual_style(
        "default"
    ) == illustration_themes.ResolvedVisualStyle(
        target="xiaohei-white-first-v1",
        schema_version=1,
        visual_system="xiaohei-white-first-v1",
        visual_theme=None,
        illustration_skill="katerj-xiaohei-illustrations",
    )
    assert illustration_themes.DEFAULT_VISUAL_TARGET == "xiaohei-white-first-v1"


def test_xiaohuang_request_resolves_to_registered_profiled_theme() -> None:
    assert illustration_themes.resolve_visual_style(
        "xiaohuang-warm-first-v1"
    ) == illustration_themes.ResolvedVisualStyle(
        target="xiaohuang-warm-first-v1",
        schema_version=4,
        visual_system="profiled-illustration-v4",
        visual_theme="xiaohuang-warm-first-v1",
        illustration_skill="ra-video-illustrations",
    )


def test_default_request_never_resolves_to_type_led() -> None:
    assert (
        illustration_themes.resolve_visual_style("semantic-handdrawn-v3/type-led").target
        == illustration_themes.TYPE_LED_TARGET
    )
    assert (
        illustration_themes.resolve_visual_style("default").target
        != illustration_themes.TYPE_LED_TARGET
    )


@pytest.mark.parametrize("requested", [None, "", "cinematic-food", "类似漫画"])
def test_unregistered_or_missing_visual_request_fails_closed(requested: object) -> None:
    with pytest.raises(
        IllustrationThemeError, match="^illustration-style-invalid$"
    ):
        illustration_themes.resolve_visual_style(requested)

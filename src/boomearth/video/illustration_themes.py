"""Frozen profiled illustration themes and user-facing Chinese catalog."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


PROFILED_VISUAL_SYSTEM: Final[str] = "profiled-illustration-v4"
DEFAULT_VISUAL_TARGET: Final[str] = "sponge-host-handdrawn-v1"
TYPE_LED_TARGET: Final[str] = "semantic-handdrawn-v3/type-led"


class IllustrationThemeError(ValueError):
    """A fixed-message illustration theme lookup failure."""


@dataclass(frozen=True, slots=True)
class ChineseStyleEntry:
    chinese_name: str
    invocation: str
    target: str


@dataclass(frozen=True, slots=True)
class IllustrationTheme:
    id: str
    chinese_name: str
    invocation: str
    directory: str
    prompt_style: str
    required_qc: frozenset[str]


@dataclass(frozen=True, slots=True)
class ResolvedVisualStyle:
    target: str
    schema_version: int
    visual_system: str
    visual_theme: str | None
    illustration_skill: str


_THEME_VALUES = (
    IllustrationTheme(
        id="sponge-host-handdrawn-v1",
        chinese_name="方块海绵插画",
        invocation="这条视频使用方块海绵插画风格。",
        directory="sponge-host-handdrawn-v1",
        prompt_style="white-hand-drawn-sponge-host-with-native-chinese-labels",
        required_qc=frozenset({"sponge_identity_consistent", "character_performs_action", "native_labels_correct", "white_canvas", "not_system_label_overlay"}),
    ),
    IllustrationTheme(
        id="vivid-comic-explainer",
        chinese_name="鲜彩漫画讲解",
        invocation=(
            "这条视频使用鲜彩漫画讲解风格，用人物、动作、表情和鲜明强调色解释文案。"
        ),
        directory="vivid-comic-explainer",
        prompt_style="clean-black-comic-linework-with-controlled-vivid-accents",
        required_qc=frozenset(
            {
                "character_consistent",
                "expression_supports_claim",
                "action_explains_claim",
                "accent_palette_controlled",
                "not_decorative_cartoon",
            }
        ),
    ),
    IllustrationTheme(
        id="engineering-sketch-explainer",
        chinese_name="工程手稿图解",
        invocation=(
            "这条视频使用工程手稿图解风格，只画文案真实涉及的产品、结构和流程。"
        ),
        directory="engineering-sketch-explainer",
        prompt_style="precise-dark-gray-engineering-linework-with-pale-wash-accents",
        required_qc=frozenset(
            {
                "engineering_subject_real",
                "callouts_support_claim",
                "mechanical_exception_valid",
                "linework_clean",
                "diagram_not_overloaded",
            }
        ),
    ),
    IllustrationTheme(
        id="four-panel-comic-explainer",
        chinese_name="四格连环漫画",
        invocation=(
            "这条视频使用四格连环漫画风格，每个场景用四个连续画格讲清变化。"
        ),
        directory="four-panel-comic-explainer",
        prompt_style="fixed-two-by-two-causal-comic-with-consistent-characters",
        required_qc=frozenset(
            {
                "exactly_four_panels",
                "reading_order_clear",
                "beats_continuous",
                "character_consistent",
                "one_event_per_panel",
                "lower_panels_caption_safe",
            }
        ),
    ),
    IllustrationTheme(
        id="blue-black-whiteboard-explainer",
        chinese_name="蓝黑白板讲解",
        invocation=(
            "这条视频使用蓝黑白板讲解风格，用马克笔线条、框线和箭头解释关系。"
        ),
        directory="blue-black-whiteboard-explainer",
        prompt_style="whiteboard-marker-structure-with-black-lines-and-blue-emphasis",
        required_qc=frozenset(
            {
                "marker_material_clear",
                "blue_black_palette_only",
                "structure_type_clear",
                "reading_path_clear",
                "not_ppt_page",
                "not_character_led",
            }
        ),
    ),
    IllustrationTheme(
        id="xiaohuang-warm-first-v1",
        chinese_name="小黄温度插画",
        invocation=(
            "这条视频使用小黄温度插画风格，让固定暖黄色角色用动作和原生手写中文解释文案。"
        ),
        directory="xiaohuang-warm-first-v1",
        prompt_style=(
            "warm-white-hand-drawn-xiaohuang-character-with-native-chinese-labels"
        ),
        required_qc=frozenset(
            {
                "xiaohuang_identity_consistent",
                "character_performs_action",
                "native_labels_correct",
                "warm_white_canvas",
                "not_system_label_overlay",
            }
        ),
    ),
)

THEMES: Mapping[str, IllustrationTheme] = MappingProxyType(
    {theme.id: theme for theme in _THEME_VALUES}
)

CHINESE_STYLE_CATALOG: Final[tuple[ChineseStyleEntry, ...]] = (
    ChineseStyleEntry(
        "小黑怪诞插画",
        "这条视频使用小黑怪诞插画风格。",
        "xiaohei-white-first-v1",
    ),
    ChineseStyleEntry(
        "编辑动效插画",
        "这条视频使用编辑动效插画风格。",
        "editorial-motion-v2",
    ),
    ChineseStyleEntry(
        "语义手绘插画",
        "这条视频使用语义手绘插画风格。",
        "semantic-handdrawn-v3",
    ),
    ChineseStyleEntry(
        "动态文字卡片",
        "这条视频使用动态文字卡片风格，画面以文字关系、路径和轻量图标为主。",
        "semantic-handdrawn-v3/type-led",
    ),
    *(ChineseStyleEntry(theme.chinese_name, theme.invocation, theme.id) for theme in _THEME_VALUES),
)


def get_theme(theme_id: str) -> IllustrationTheme:
    if not isinstance(theme_id, str):
        raise IllustrationThemeError("illustration-theme-invalid")
    try:
        return THEMES[theme_id]
    except KeyError:
        raise IllustrationThemeError("illustration-theme-invalid") from None


def resolve_visual_style(requested: object) -> ResolvedVisualStyle:
    """Resolve one exact registered visual target for new production."""

    target = DEFAULT_VISUAL_TARGET if requested == "default" else requested
    if not isinstance(target, str):
        raise IllustrationThemeError("illustration-style-invalid")
    if target in THEMES:
        return ResolvedVisualStyle(
            target=target,
            schema_version=4,
            visual_system=PROFILED_VISUAL_SYSTEM,
            visual_theme=target,
            illustration_skill="ra-video-illustrations",
        )
    legacy = {
        "xiaohei-white-first-v1": (
            1,
            "xiaohei-white-first-v1",
            None,
            "katerj-xiaohei-illustrations",
        ),
        "editorial-motion-v2": (
            2,
            "editorial-motion-v2",
            None,
            "ra-video-illustrations",
        ),
        "semantic-handdrawn-v3": (
            3,
            "semantic-handdrawn-v3",
            None,
            "ra-video-illustrations",
        ),
        TYPE_LED_TARGET: (
            3,
            "semantic-handdrawn-v3",
            None,
            "ra-video-illustrations",
        ),
    }
    try:
        schema_version, visual_system, visual_theme, illustration_skill = legacy[
            target
        ]
    except KeyError:
        raise IllustrationThemeError("illustration-style-invalid") from None
    return ResolvedVisualStyle(
        target=target,
        schema_version=schema_version,
        visual_system=visual_system,
        visual_theme=visual_theme,
        illustration_skill=illustration_skill,
    )


__all__ = [
    "CHINESE_STYLE_CATALOG",
    "DEFAULT_VISUAL_TARGET",
    "PROFILED_VISUAL_SYSTEM",
    "THEMES",
    "TYPE_LED_TARGET",
    "ChineseStyleEntry",
    "IllustrationTheme",
    "IllustrationThemeError",
    "ResolvedVisualStyle",
    "get_theme",
    "resolve_visual_style",
]

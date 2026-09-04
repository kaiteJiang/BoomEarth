from pathlib import Path

from boomearth.video.illustration_themes import CHINESE_STYLE_CATALOG, THEMES

ROOT = Path(__file__).parents[1]
SKILL = ROOT / ".agents/skills/ra-video-illustrations"
DIRECTOR = ROOT / ".agents/skills/katerj-video-director"
XIAOHEI = ROOT / ".agents/skills/katerj-xiaohei-illustrations"
XIAOHUANG = ROOT / ".agents/skills/katerj-xiaohuang-illustrations"


def test_video_illustration_skill_declares_native_generation_and_v2_contracts() -> None:
    text = (SKILL / "SKILL.md").read_text("utf-8")
    assert text.startswith("---\nname: ra-video-illustrations\n")
    for phrase in (
        "concept-scene", "comparison", "framework", "technical", "clean-collage",
        "editorial-scene", "minimal-vector", "technical-diagram", "screen-print-metaphor",
        "runtime-native imagegen", "prompts/scene-", "illustration-manifest.json",
        "1920×1080", "16:9", "bottom-150px", "不调用外部 CLI", "不切换 provider",
    ):
        assert phrase in text
    assert (SKILL / "references/style-profiles.md").is_file()
    assert (SKILL / "references/prompt-contract.md").is_file()
    assert (SKILL / "references/video-frame-qc.md").is_file()
    attribution = (SKILL / "references/upstream-attribution.md").read_text("utf-8")
    assert "awesome-gpt-image-2" in attribution
    assert "baoyu" in attribution.lower()


def test_video_illustration_skill_routes_v2_and_v3_by_semantic_evidence() -> None:
    skill = (SKILL / "SKILL.md").read_text("utf-8")
    routing_path = SKILL / "references/semantic-routing-v3.md"
    assert routing_path.is_file()
    routing = routing_path.read_text("utf-8")
    prompt = (SKILL / "references/prompt-contract.md").read_text("utf-8")
    qc = (SKILL / "references/video-frame-qc.md").read_text("utf-8")
    attribution = (SKILL / "references/upstream-attribution.md").read_text("utf-8")

    for phrase in (
        "semantic-handdrawn-v3",
        "source-collage",
        "human-action",
        "handdrawn-flow",
        "type-led",
        "semantic-qc.json",
        "一次定向修复",
        "不切换 Provider",
    ):
        assert phrase in skill
    for phrase in (
        "真实来源素材",
        "人物动作",
        "手绘关系",
        "文字主导",
        "实体机器人",
        "AI、Agent、模型、插件、工具、架构",
    ):
        assert phrase in routing
    for forbidden in (
        "机器人",
        "齿轮",
        "工厂",
        "机械臂",
        "金属卡匣",
        "电路板",
        "工业流水线",
        "发动机",
        "机械底座",
    ):
        assert forbidden in routing
    for field in (
        "visual_mode",
        "语义主体",
        "核心动作或关系",
        "必须在画面中看到的证据",
        "为什么该画面能够解释当前判断",
    ):
        assert field in prompt
    for check in (
        "subject_match",
        "action_match",
        "evidence_complete",
        "claim_readable",
        "non_generic",
        "forbidden_absent",
        "mobile_readable",
        "caption_safe",
    ):
        assert check in qc
    assert "chujianyun/awesome-gpt-image2-ppt-skills" in attribution
    assert "pure-white-handdrawn-ppt-infographic" in attribution
    assert "comic-explainer-illustration" in attribution


def test_production_director_defaults_new_pages_to_xiaohei_and_keeps_explicit_v2_v3_v4() -> None:
    text = (DIRECTOR / "SKILL.md").read_text("utf-8")
    assert "ra-video-illustrations" in text
    assert "默认使用 `xiaohei-white-first-v1`（schema 1）" in text
    assert "由 `katerj-xiaohei-illustrations`" in text
    assert "显式要求 `semantic-handdrawn-v3`（schema 3）" in text
    assert "显式要求 `editorial-motion-v2`（schema 2）" in text
    assert "不得迁移旧项目" in text


def test_skill_publishes_all_chinese_style_entries_and_theme_ids() -> None:
    library_path = SKILL / "references/theme-library.md"
    assert library_path.is_file()
    library = library_path.read_text("utf-8")
    for entry in CHINESE_STYLE_CATALOG:
        assert entry.chinese_name in library
        assert entry.invocation in library
        assert entry.target in library
    for theme_id, theme in THEMES.items():
        assert theme_id in library
        assert theme.prompt_style in library
        for check in theme.required_qc:
            assert check in library

    attribution = (SKILL / "references/upstream-attribution.md").read_text("utf-8")
    assert "当前未声明许可证" in attribution
    assert "MIT License 的 [chujianyun" not in attribution


def test_skill_documents_schema4_prompt_qc_and_routing_contract() -> None:
    skill = (SKILL / "SKILL.md").read_text("utf-8")
    prompt = (SKILL / "references/prompt-contract.md").read_text("utf-8")
    qc = (SKILL / "references/video-frame-qc.md").read_text("utf-8")
    director = (
        ROOT / ".agents/skills/katerj-video-director/SKILL.md"
    ).read_text("utf-8")

    for phrase in (
        "profiled-illustration-v4",
        "manifest schema 3",
        "visual_theme",
        "theme-library.md",
    ):
        assert phrase in skill
    for phrase in (
        "正文严格写十项",
        "visual_system: profiled-illustration-v4",
        "visual_theme:",
        "prompt-before-generation",
    ):
        assert phrase in prompt
    assert "主题专属 QC" in qc
    assert "显式 schema 4 主题继续走" in director
    assert "默认使用 `xiaohei-white-first-v1`（schema 1）" in director


def test_new_video_default_is_xiaohei_and_profiled_themes_are_explicit_only() -> None:
    director = (DIRECTOR / "SKILL.md").read_text(encoding="utf-8")
    illustrations = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    library = (SKILL / "references/theme-library.md").read_text(encoding="utf-8")
    delivery = (DIRECTOR / "references/delivery-gates.md").read_text(encoding="utf-8")

    assert "默认使用 `xiaohei-white-first-v1`（schema 1）" in director
    assert "`type-led` 只能由用户或交接稿显式选择" in illustrations
    assert "未明确指定时，新建视频默认使用 `xiaohei-white-first-v1`" in library
    assert "数字人、字幕、标签和轻量图标都不能代替主题场景素材" in delivery
    assert "缺少真实主题素材时必须失败" in delivery
    assert "默认使用 `semantic-handdrawn-v3`（schema 3）" not in director
    assert "五个 `profiled-illustration-v4` 主题" in library


def test_xiaohei_skill_preserves_native_handwritten_text_and_explicit_fallback() -> None:
    skill = (XIAOHEI / "SKILL.md").read_text(encoding="utf-8")

    for phrase in (
        "illustration_text_mode: embedded",
        "text_policy: embedded",
        "handwritten_labels:",
        "local-fallback",
        "原生手写文字",
        "禁止胶囊标签",
        "纯白画布",
        "边缘羽化",
    ):
        assert phrase in skill
    assert "Keep cloud-generated source art text-free" not in skill


def test_xiaohuang_skill_preserves_identity_and_native_text() -> None:
    skill = (XIAOHUANG / "SKILL.md").read_text(encoding="utf-8")

    assert skill.startswith("---\nname: katerj-xiaohuang-illustrations\n")
    for phrase in (
        "暖黄不规则种子形身体",
        "空心爱心天线",
        "黑色竖椭圆眼",
        "原生手写中文",
        "xiaohuang-warm-first-v1",
        "text_policy: embedded",
        "禁止系统胶囊标签",
        "不覆盖旧资产",
    ):
        assert phrase in skill
    for relative in (
        "references/character-dna.md",
        "references/prompt-contract.md",
        "references/qa-checklist.md",
        "agents/openai.yaml",
    ):
        assert (XIAOHUANG / relative).is_file()


def test_xiaohei_hyperframes_template_keeps_white_feathered_art_contract() -> None:
    director_root = ROOT / ".agents/skills/ra-video-production-director"
    layout = (director_root / "references/xiaohei-16x9-layout.md").read_text(
        encoding="utf-8"
    )
    template = (
        director_root / "assets/xiaohei-16x9-template/build_index.py"
    ).read_text(encoding="utf-8")

    assert "native handwritten labels" in layout
    assert "pure white canvas" in layout
    assert "edge feather" in layout
    assert "mask-image:" in template
    assert "filter: blur" not in template

from __future__ import annotations

from boomearth.captions.align import align_display_script, group_caption_phrases
from boomearth.captions.display import format_caption_text
from boomearth.captions.qc import _subtitle_text
from boomearth.providers.volcengine_asr import ASRWord


def test_caption_grouping_keeps_terminal_sentences_separate_and_removes_punctuation() -> None:
    script = "第一句已经完整。第二句也完整。"
    words = (
        ASRWord("第一句已经完整", 0.0, 1.2),
        ASRWord("第二句也完整", 1.3, 2.2),
    )

    captions = group_caption_phrases(script, words, align_display_script(script, words))

    assert [(caption.start, caption.end, caption.text) for caption in captions] == [
        (0.0, 1.2, "第一句已经完整"),
        (1.3, 2.2, "第二句也完整"),
    ]


def test_caption_grouping_keeps_comma_delimited_clauses_on_separate_lines() -> None:
    script = "甲乙丙丁戊己庚辛壬癸，子丑寅卯辰巳午未申酉。"
    words = (
        ASRWord("甲乙丙丁戊己庚辛壬癸", 0.0, 1.0),
        ASRWord("子丑寅卯辰巳午未申酉", 1.1, 2.0),
    )

    captions = group_caption_phrases(
        script,
        words,
        align_display_script(script, words),
        max_units=12,
    )

    assert [caption.text for caption in captions] == [
        "甲乙丙丁戊己庚辛壬癸",
        "子丑寅卯辰巳午未申酉",
    ]


def test_caption_grouping_combines_short_enumeration_items_until_the_line_limit() -> None:
    script = "定时检查信息、自动整理网盘、根据可靠资料做法律检索、读取会议并生成总结。"
    words = (
        ASRWord("定时检查信息", 0.0, 0.8),
        ASRWord("自动整理网盘", 0.9, 1.7),
        ASRWord("根据可靠资料做法律检索", 1.8, 3.1),
        ASRWord("读取会议并生成总结", 3.2, 4.3),
    )

    captions = group_caption_phrases(
        script,
        words,
        align_display_script(script, words),
        max_units=15,
    )

    assert [caption.text for caption in captions] == [
        "定时检查信息 自动整理网盘",
        "根据可靠资料做法律检索",
        "读取会议并生成总结",
    ]


def test_caption_grouping_combines_short_semantic_clauses_without_crossing_a_sentence() -> None:
    script = "这个更新值不值得用，得把它能做什么、怎么协作、权限怎么给放在一起看。"
    words = (
        ASRWord("这个更新值不值得用", 0.0, 1.1),
        ASRWord("得把它能做什么", 1.2, 2.1),
        ASRWord("怎么协作", 2.2, 2.8),
        ASRWord("权限怎么给放在一起看", 2.9, 4.1),
    )

    captions = group_caption_phrases(
        script,
        words,
        align_display_script(script, words),
        max_units=16,
    )

    assert [caption.text for caption in captions] == [
        "这个更新值不值得用",
        "得把它能做什么 怎么协作",
        "权限怎么给放在一起看",
    ]


def test_caption_grouping_never_strands_a_connector_after_a_semantic_cut() -> None:
    script = "普通聊天工具主要给答案，WorkBuddy 这类 Agent 还能调用电脑工具，把回答接到文档表格和操作上。"
    words = (
        ASRWord("普通聊天工具主要给答案", 0.0, 1.0),
        ASRWord("WorkBuddy 这类 Agent 还能调用电脑工具", 1.1, 2.6),
        ASRWord("把回答接到文档表格和操作上", 2.7, 4.0),
    )

    captions = group_caption_phrases(
        script,
        words,
        align_display_script(script, words),
        max_units=20,
    )

    assert [caption.text for caption in captions] == [
        "普通聊天工具主要给答案",
        "WorkBuddy 这类 Agent 还能调用电脑工具",
        "把回答接到文档表格和操作上",
    ]


def test_caption_grouping_never_merges_across_a_terminal_sentence_boundary() -> None:
    script = "先说它是什么。普通聊天工具主要给答案。"
    words = (
        ASRWord("先说它是什么", 0.0, 1.0),
        ASRWord("普通聊天工具主要给答案", 1.1, 2.5),
    )

    captions = group_caption_phrases(script, words, align_display_script(script, words))

    assert [caption.text for caption in captions] == [
        "先说它是什么",
        "普通聊天工具主要给答案",
    ]


def test_display_punctuation_uses_comma_space_and_keeps_final_question() -> None:
    assert format_caption_text("GPT-6 Sol，Luna，怎么选？") == "GPT-6 Sol Luna 怎么选？"
    assert format_caption_text("版本 5.5，继续看。") == "版本 5.5 继续看"
    assert format_caption_text("这句结束！") == "这句结束"
    assert format_caption_text("这句结束，") == "这句结束"
    assert format_caption_text("他说：你会选哪个？！”") == "他说：你会选哪个？"
    assert format_caption_text("C++、Node.js，都要讲。") == "C++ Node.js 都要讲"


def test_word_timed_caption_keeps_comma_space_without_changing_timing() -> None:
    script = "你想省时间，就先找返工点？"
    words = (ASRWord("你想省时间", 0.0, 1.0), ASRWord("就先找返工点", 1.2, 2.4))
    captions = group_caption_phrases(script, words, align_display_script(script, words))
    assert [(cue.start, cue.end, cue.text) for cue in captions] == [
        (0.0, 2.4, "你想省时间 就先找返工点？")
    ]
    assert "你想省时间 就先找返工点？" in _subtitle_text(captions, is_vtt=False)
    assert "你想省时间 就先找返工点？" in _subtitle_text(captions, is_vtt=True)

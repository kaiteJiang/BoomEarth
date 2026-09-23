---
name: katerj-script-rewrite
description: Use when private source material must be organized into an Astra-authored script and a source-free BoomEarth production handoff with factual review evidence.
---

# Astra 文稿与来源交接

读取根 AGENTS.md。本入口保留来源整理、技术合同与公开编译边界，不是去 AI 味改稿器。

## 写作

Astra 直接理解输入并创作：事实、机制、案例与读者需要讲清楚，正文自由组织。不得调用 katerj-human-writing、ra-人话、katerj-ai-writing-review、dbs-ai-check 或相关别名；不强制套钩子、CTA、标题公式。开头与结尾协作要求待用户讨论。

用户已批准稿件时绑定全文和哈希，不改词、不加引导语。只要求审稿时先交稿，不启动媒体。现成稿没有外部来源时直接交导演，不凭空建立采集。

## 新来源稿的可编译证据

1. 阅读私有正文/逐字稿与 brief，提取可核验事实，Astra 自己组织解释。
2. 新 candidate 使用已有 schema 1：有 segments、title_candidates、visual、illustration_skill 等正式字段，不加 opening_contract；默认 visual 为 sponge-host-handdrawn-v1，illustration_skill 为 ra-video-illustrations。
3. 新 review 使用 schema 3，保留 candidate_sha256、reviewed_at、reviews 字段，reviews 精确四项：
   - facts：事实与输入一致，不虚构实测或效果；
   - logic：正文推理和前后关系完整；
   - source_free：公开稿没有私有来源标识、路径和逐字稿泄露；
   - script_integrity：批准全文和候选绑定正确，未擅改词句。
4. 每项实际审查后写 pass 或 fail；不得用旧Skill名字伪造执行记录。审核失败先修正未批准稿；涉及定稿修订时保存新版本。
5. 保存 private candidate/review，经 `automation/scripts/compile_source_handoff.py compile` 发布并检查 receipt/ledger handoff_ready。不得手写来源任务的待制作交接稿绕过编译器。

既有 candidate/review schema 1/2 继续验证旧项目；旧 opening_contract 不删、不重套。新自由文稿不能伪装旧固定开头合同来过检。

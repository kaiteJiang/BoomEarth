# BoomEarth workspace instructions

## Current default video identity (2026-09-07)

- 新视频默认 `sponge-host-handdrawn-v1`（方块海绵插画）；外观唯一真源与 SHA-256 见 `video-daheihuang/sponge-ip/DESIGN.md`。本节覆盖旧 Skill 的小黑默认值，不迁移已绑定历史项目。
- 简化为口播 + 单行字幕 + 语义对应的海绵插画及动作；默认不使用 HeyGen。原生手写字、背景融合、配音及真实时间字幕规则沿用本体。
- 用户最终纠正：除了小黑角色替换为已定稿海绵，其余构图、文字、背景融合、节奏、动作及比例沿用小黑原版；撤销上一轮额外放大布局。完整逐场景高清生图，不裁小尺寸设定图放大铺屏，不用尺寸插值冒充高清原生细节。最终视频仍为 1080p。
- 使用注册海绵主题入口，继承 `katerj-xiaohei-illustrations` 的本体规则，仅替换角色 DNA；每场海绵实际参与语义动作，不使用独立小人加通用图标代替完整场景。

- Keep implementation changes within the file ownership declared by the active task brief.
- Never read, print, modify, or copy external credential files such as `API.txt`; preserve them unchanged and keep their values out of tracked files, tests, logs, reports, and diffs.
- Real V1 credentials may exist only in the ignored root `.env` or the process environment. Never add HeyGen credentials to the V1 contract.
- Use redacted diagnostics only: credential status may be `SET`/`UNSET`; never print credentials, prefixes, or lengths.
- Do not call live APIs from default tests or environment checks.
- Use `uv run pytest tests/test_config.py -q` for the Task 1 config tests and `uv run python automation/scripts/check_env.py` for the redacted environment check.
- Do not stage `.env` or the root `yt-dlp.exe`; the latter is a local binary and must remain ignored.

## Writing and approved narration

- 用户于 2026-09-06 明确要求：后续文稿由当前 Agent 用自己的语言直接创作，不调用“人话”“去 AI 味”类 Skill，包括相关历史别名；事实核对、内容逻辑检查和技术质检照常进行。
- 2026-09-07 再次确认：文稿统一由 Astra 理解输入后直接编写，正文自由组织，不调用任何去 AI 味/人话类 Skill。开头与结尾的协作要求待用户另行讨论，不新增固定模板、钩子或 CTA 合同；已批准文稿不擅改。后续视频采用海绵原版全幅插画样片路线。
- 用户确认定稿后，保存并绑定该版本全文；配音和字幕准备不得擅自改词、删句或添加 CTA。断句工具只可调整边界。
- 本项目字幕允许由 Agent 挑选少量精彩词句做局部底色强调；保持单行、固定底部锚点，强调不得改变真实音频时间戳。

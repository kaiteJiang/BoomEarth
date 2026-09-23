---
name: katerj-video-director
description: Use when a BoomEarth approved script or handoff must become a rendered video, or an existing production must resume from local evidence in a new conversation.
---

# KaterJ Video Director

读取根 AGENTS.md、[当前接手手册](../../../docs/VIDEO-PRODUCTION-RUNBOOK.md) 与 [连续制作编排](../../../docs/VIDEO-CONTINUOUS-ORCHESTRATION.md)。按 [routing](references/routing.md) 选择专业阶段，最终渲染、恢复、归档前完整读取 [delivery-gates](references/delivery-gates.md)。

## 绑定与接手

队列交接稿 frontmatter 是 ratio、duration、voice、captions、visual、cover、avatar 的绑定合同，不迁移历史主题、不重切批准 narration segments。直接输入稿由导演建立同等生产计划，不能虚构来源证据。

先检查 production note 的接手状态、实际文件与哈希。正式归档有效则复验报告；最终 WAV/manifest/字幕六件套QC匹配则复用，不重复 TTS/ASR。每阶段更新 note 的最后有效gate、产物、下一步及失败。

## 当前新项目默认

- sponge-host-handdrawn-v1：schema 4 / profiled-illustration-v4 / ra-video-illustrations；角色真源见 video-daheihuang/sponge-ip/DESIGN.md。仅换角色，继承 katerj-xiaohei-illustrations 原生字、白底融合与语义动作。
- 全幅完整场景；新海绵 native-source 合同保留原生PNG，按实际像素显示，不用4K插值冒充高清。标题卡片与系统标签不得遮盖或替代原生文字。
- Astra直接写稿，默认不串人话/去AI味Skill，用户当次明确指定优先；新稿在定稿前按根AGENTS.md调用jl-video-intro-outro固定开头结尾。批准稿/最终音频的字词不可变，不向已定稿历史项目追补话术。
- 本地 IndexTTS2 黑金v3，1.12×；最终WAV为时间轴。anchor-dark字幕单行、固定底部锚点，少量局部底色强调不改变时序。
- 默认无HeyGen；仅显式启用或旧绑定才读取官方Skill和头像恢复gate。
- 最终1920×1080、30fps、H.264/AAC；视频作品封面维持3:4 Punk合同，文章5:2配图不改变该默认。

## 制作

1. 写 production note、内容/场景/素材/运动计划与字幕安全区，记录时长和批准稿哈希。
2. `jl-oral-linebreaks` 和项目内 `katerj-oral-linebreaks` 对批准稿只调整边界。旁白合同 dry-run 通过后启动一次 `katerj-local-tts`；同时让 `ra-video-illustrations` 准备海绵 prompt→生成→complete证据→真实语义QC→manifest。图片与配音并行，不得用原图文件存在替代完整证据，也不把样片回执迁入正式项目。TTS 每 30 分钟只检查一次，健康运行时继续其他工作，不循环轮询。
3. 最终无损 WAV、manifest、脚本和 provenance 验证通过后，`katerj-audio-subtitles` 调用精确 WAV 获取词时间；按 `jl-oral-linebreaks` 用真实词边界分句，句中逗号转空格、行尾只保留问号，六件套/QC全过后交 `katerj-caption-rendering`。
4. katerj-motion-director 根据最终音频定场景与组件cue；组件显现、擦除、淡入和轻微停稳，稳定底板，避免机械重复动作。
5. 可选人物仅用同一最终旁白；默认跳过。通过 canonical run_content_production.py finalize 合成，不用历史样片builder当正式编译器。
6. 逐场景帧、关键词cue帧、联系表、音频/字幕/素材provenance、安全区和完整解码检查；check_delivery.py通过后归档到 已制作/<X月上旬或X月下旬>/<日期-主题>/。
7. 队列回填交接稿制作回执/status；已声明封面则走ra-video-cover。非队列标题按用户选择，不擅自落选定标题。

本地修复、校验与渲染继续执行；真实外部阶段遵守已绑定输入、计划、预算和授权，不重复询问已覆盖范围、不默换Provider。完成必须有真实正式交付回执；样片通过、进程结束、计划存在都不等于推荐最终成片。

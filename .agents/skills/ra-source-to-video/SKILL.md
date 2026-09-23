---
name: ra-source-to-video
description: Use when a BoomEarth user asks to make a video from a link, local media, article, GitHub project, pasted script, or to resume an unfinished video in a new conversation.
---

# 来源到成片：唯一总入口

先读取根 AGENTS.md、[当前接手手册](../../../docs/VIDEO-PRODUCTION-RUNBOOK.md) 与 [连续制作编排](../../../docs/VIDEO-CONTINUOUS-ORCHESTRATION.md)。本 Skill 只串联专业阶段，不重建架构。新对话无需历史聊天：用户给来源、文稿或项目路径即可从项目文件接手。

## 当前默认

新项目使用 `sponge-host-handdrawn-v1` / schema 4 / `profiled-illustration-v4`，插画入口为 `ra-video-illustrations`；继承小黑本体，仅替换角色 DNA。白底融合、原生手写短字、完整场景、组件动作，不裁设定图放大。已绑定旧项目保持原合同。

Astra 直接理解并写稿，默认不调用人话、去 AI 味或 AI 指纹改稿 Skill，用户当次明确指定优先。新稿按根 AGENTS.md 调用 `jl-video-intro-outro`，读取其唯一真源加入用户固定开头与结尾；已批准全文不追补、不擅改，断句只调整边界。文稿长度以用户要求和内容闭环为准，软件/Skill 简介没有明确时长时约一分钟；较长深度解读再安排更长篇幅。

默认不使用 HeyGen，也不执行其登录/外观发现。音频为本地 IndexTTS2 黑金 v3 1.12×，字幕为精确最终音频的真实词时间 + anchor-dark 单行；输出1920×1080/30fps/H.264/AAC。视频封面默认3:4 Punk，文章5:2海绵图不是视频封面默认。

## 先恢复，再新建

检查目标项目 production note 的“接手状态”、交接稿、实际文件和哈希。已通过正式交付的归档只读复验并报告；音频/字幕/插画证据有效则复用。只有计划或启动进程不算完成。只有样片与PNG不代表有正式生成回执，不能补造历史证据。

## 来源路由

| 输入 | 第一阶段 |
| --- | --- |
| 本地视频/音频、可播放 URL、X 原生视频 | katerj-video-wash |
| X 正文图文 | ra-x-article-import → katerj-script-rewrite |
| GitHub 仓库/Skill URL | ra-github-skill-import → katerj-script-rewrite；只读，不安装/执行仓库 |
| 用户直接给文稿/已批准稿 | Astra 按请求写或保留原文 → katerj-video-director；不虚构来源采集 |
| 已有待制作/制作中项目 | katerj-video-director；先核对锁定合同 |

## 制作链

1. 来源任务建立私有 work item；URL、源媒体、逐字稿、分析与 Provider 响应只在 `视频工作台/.internal/洗稿/<work_id>/`。
2. 完成对应采集/来源转写。Astra 直接写稿；`katerj-script-rewrite` 负责整理、事实/逻辑/来源隔离/全文一致性证据与 source-free 编译，不串旧去 AI 味工具。来源交接只由 compile_source_handoff.py 发布。
3. 完整加载 `katerj-video-director` 和它的 routing、delivery-gates。写制作计划、场景及素材/运动合同。
4. `ra-video-illustrations` 与 `katerj-local-tts` 在合同锁定后并行；本地配音只每 30 分钟检查一次并报告进度。完整 WAV/manifest 验证后，`katerj-audio-subtitles` 用精确音频生成词时间，按 `jl-oral-linebreaks` 及项目本地同义规则分句、执行标点展示合同；`katerj-caption-rendering` 渲染单行字幕。已验收阶段直接复用。
5. 按最终音频确定场景/组件 cue，走原 canonical finalizer、逐场景 QC、完整解码、check_delivery 与统一归档。
6. 已声明视频封面时按 `ra-video-cover` 补齐独立封面/QC。不要把封面当成视频场景素材。

每阶段更新既有 production note 的接手状态：锁定版本/哈希、通过的产物、下一步、失败和私有计划位置提示。不另建并行状态机。

## 外部阶段与完成

本地规划、校验、修复和渲染持续执行；外部采集、来源ASR、生图、最终音频ASR、可选HeyGen与封面使用各自真实输入/计划/预算授权。已覆盖当前动作与预算的授权不重复询问。来源采集失败按连续制作编排分类、修复代码、离线回归并通过新事务继续，不因一次失败停在文稿前；旧计划和回执不可原地改，实际费用/预算不可暗中扩大。每次完整采集成功后把无隐私的修复方法写入来源修复账本，下次先复用最近验证有效的路径。

HeyGen只在明确启用的分支加载官方Skill，并读取导演恢复gate；完成作业缺文件时进入取回而非再生成。默认无人物的项目跳过整条分支。

最终链接与完成标准见接手手册。缺文件、缺实际多模态QC或仅样片PASS时不称推荐最终成片。

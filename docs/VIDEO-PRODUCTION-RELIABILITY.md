# 分段、文字入场与归档恢复

本页维护正式制作中可复用的检查方法。制作入口和恢复顺序仍见 [runbook](VIDEO-PRODUCTION-RUNBOOK.md)；本页不定义第二套渲染器，也不改变已批准文稿、最终配音或主题。

## 音频分段与文稿段落

TTS 为推理而拆出的音频片段，可能多于交接稿中的语义段落。保留两套边界；不要为了使数量相等而删句、重写文稿或重新生成已验收音频。

当两者不一一对应时，在项目内提供 `工程/audio-segment-map.json`。它是按音频顺序排列的数组，例如三段音频对应两段文稿：

```json
[
  {"audio_segment": 1, "handoff_segment": "segment-001"},
  {"audio_segment": 2, "handoff_segment": "segment-001"},
  {"audio_segment": 3, "handoff_segment": "segment-002"}
]
```

映射必须完整、连续、按序覆盖每个真实音频片段与全部批准段落；同一语义段的音频片段必须相邻。合并文本应匹配原段落，不能依靠映射掩盖缺词或错序。缺失、重复、跳段、回访、非法字段及文件篡改均失败；原来的一对一合同不需要额外映射。

编译器将映射路径、映射 SHA、旁白合同 SHA、批准段落 SHA 写入 `content-plan.json`。时间轴、渲染输入快照、交付 receipt 和 publication manifest 都绑定同一映射。归档只更新状态，不改变段落内容绑定。

## 文字卡使用真实词起点

一条字幕可能包含多个文字卡触发短语。卡片应在各自短语的第一个真实 ASR token 开始时入场，不能共同使用整条字幕起点。

`sponge_components.resolve_cue_start` 要求完整、连续、顺序一致的语句匹配；场内 cue 严格递增并位于场景边界内。缺词、动作调序、部分别名或没有依据的时间猜测都不能通过。

显示文本与 ASR 原词存在已审字面差异时，只允许已有的整句审核链：`caption-qc.json` 指向语义审阅文件，其证据绑定 `caption-final-mapping-audit-v01.json`，并核对最终 WAV、字幕、原词和真实边界。渲染器读取并保护这些依赖，不自动批准差异，也不创建临时近义词表。缺少审核链时先完成实际审阅。

## 两层视觉检查

1. **绘制过程。** 在正式完整时间轴上运行 `qc_comic_semantic_process.mjs`，实际查看每个语义部件。检查完成区域稳定、后续区域不提前出现、浅色区域不漏绘、末帧不补洞以及回退确定。具体分区规则见 [鲜彩漫画逐区绘制](comic-semantic-drawing.md)。
2. **实际 MP4。** 每场查看入场、绘制中途及完成态，确认左右分栏、完整人物、原生文字和单行字幕。文字卡以正式 HTML 的 `data-cue-at` 清单为准，每个 cue 查看前 0.1 秒、触发时刻、后 0.3 秒和后 0.7 秒，越过片头/片尾的采样点限制在有效视频区间内。确认每项有独立强调阶段，前后卡片没有碰撞或同时误入场。

`motion-plan.semantic_cues` 与文字卡清单是两套输入；motion-preview PASS 不等于所有文字卡已查。审核记录绑定最终 MP4、正式 HTML、最终词时间、过程报告和实际查看的图；未查看不能标视觉 PASS。机器音轨匹配与完整解码也不等于连续主观试听或用户验收。场景数、cue 数和抽帧数均从当前项目读取，不沿用旧片固定数量。

## 已渲染项目只续归档

正式 MP4、必要 QC、实际画面复核和 `工程/publication-manifest.json` 已齐备，仅 receipt 或归档失败时，先修失败原因并核对既有哈希，再使用正式恢复入口：

```powershell
uv run python automation/scripts/resume_content_archive.py --workspace-root . --active-project "YYYY-MM-DD-topic"
```

恢复入口重新运行交付检查，要求 receipt 与 publication 的项目和 SHA 完全一致，再进行不覆盖目标的原子归档。它不会重新生成 TTS、ASR、图片或视频。已有完整归档直接复验交付；缺少有效 publication 或媒体检查时，先恢复缺失证据，不能伪造 PASS 迁就归档。

不要对已渲染项目直接再次执行 `run_content_production.py finalize`，该入口会重新准备和渲染。不要复制某一项目的一次性包装器，硬编码路径、数量、PID 或哈希。

完成交接稿在移动目录前由已验证快照生成。失败清理复用同一个已初始化 renderer，使迁移后的目录可以恢复为“制作中”；不能因旧路径消失而留下假完成。旧 delivery report 使用唯一名称留存，失败记录和有效回执分别保留。Windows 长任务的外层日志放在待移动目录外，进程结束后再归入工程，避免开放句柄阻止移动。

## 封面版本与边距

封面遵循 [ra-video-cover](../.agents/skills/ra-video-cover/SKILL.md)：每次授权生成一张，最终 1080×1440，关键内容至少留出 72px。巨型透视字要连同顶部笔画、实体侧面和完整副标题一起检查；prompt 可预留更大空间，最终仍以实际图片为准。

未通过时保留图、prompt、计划、回执和失败 QC。用户明确要求“另生成一张”即授权新的单张版本，建立新版本计划后执行，不重复确认该授权。版本递增，不自动重试，不把其他作品的贴边例外沿用到当前作品。

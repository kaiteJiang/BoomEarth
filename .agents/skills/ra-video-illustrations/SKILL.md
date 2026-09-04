---
name: ra-video-illustrations
description: Use when a BoomEarth 16:9 scene explicitly selects a registered V2, V3, or profiled V4 illustration target, or needs semantic evidence checks, especially when AI, Agent, tools, architecture, workflows, comparisons, authorized source images, or type-led explanations must match the narration rather than become generic technology decoration.
---

# 视频插画 V2 / V3 / V4

## 核心合同

为 BoomEarth 生成逐场景、本地、可审计的 16:9 视觉。图片必须解释当前文案的主体、动作或关系，不能只营造“科技感”。先发布合同文件，再调用 runtime-native imagegen；不调用外部 CLI，不读取或创建 API Key，不切换 Provider。原生工具不可用时停止。

版本必须明确：

- schema 1 / `xiaohei-white-first-v1`：新视频默认值，由 `katerj-xiaohei-illustrations` 负责；历史调用名 `ian-xiaohei-illustrations` 仍由兼容桥承接，本 Skill 不代替它生成小黑素材。
- schema 2 / `editorial-motion-v2`：保留 V2 manifest schema 1，只修复新 prompt 的语义路由。
- schema 3 / `semantic-handdrawn-v3`：只在显式选择时使用 `source-collage`、`human-action` 或 `handdrawn-flow`，并要求 manifest schema 2 与 `semantic-qc.json`。`type-led` 只能由用户或交接稿显式选择，精确目标为 `semantic-handdrawn-v3/type-led`，且整条计划的所有场景都必须使用 `type-led`；普通 V3 不得混入该模式。
- schema 4 / `profiled-illustration-v4`：仅在用户或交接稿显式选择主题库中的精确主题时使用。`visual_theme` 必须是主题库中的精确 ID，并要求 manifest schema 3、同主题目录与主题专属 QC。
- schema 4 / `xiaohuang-warm-first-v1`：由 `katerj-xiaohuang-illustrations` 提供角色 DNA、原生手写中文和主题 QC；仍由本 Skill 发布 V4 prompt、生成证据和 manifest。

九个中文调用入口和五个 V4 主题见 [theme-library.md](references/theme-library.md)。类型、风格与模式见 [style-profiles.md](references/style-profiles.md)；V3 判断顺序必须使用 [semantic-routing-v3.md](references/semantic-routing-v3.md)。

V2 继续允许 `concept-scene`、`comparison`、`framework`、`technical`、`clean-collage`，以及 `editorial-scene`、`minimal-vector`、`technical-diagram`、`screen-print-metaphor`、`clean-collage`。历史合同中的“不切换 provider”与 V3 的“不切换 Provider”含义相同。

## 工作流

1. 从已批准的 source-free scene intent 提炼唯一判断、语义主体、动作/关系和必须可见的证据；不要复制整段口播、来源标题、URL 或逐字稿。
2. 先服从交接稿已经规范化的精确视觉目标。只有精确目标为 `semantic-handdrawn-v3/type-led` 时才选择文字主导；普通 V3 只在真实素材、人物动作和手绘关系之间路由。`AI`、`Agent`、`模型`、`插件`、`工具`、`架构` 本身不是机器人、齿轮或工厂的理由。
3. V2 raster 先写 `工程/assets/editorial-illustrations/prompts/scene-XX.md`；V3 raster 先写 `工程/assets/semantic-handdrawn/prompts/scene-XX.md`；V3 `type-led` 只写 `type-led/scene-XX.json`；V4 必须运行 `automation/scripts/prepare_profiled_generation.py`，由生产入口先严格校验 [prompt-contract.md](references/prompt-contract.md) 的 frontmatter 与十段标题，再独占发布 `工程/assets/profiled-illustrations/{visual_theme}/prompts/scene-XX.md` 和序号 1 的调用意图。不合规时入口返回失败且不发布任何 prompt/intent；先修正本地合同并重新 `prepare`，不得先调用 ImageGen。
4. 只有 `prepare` 成功并返回 call ID 与 intent SHA-256，才使用 runtime-native imagegen 逐张生成；工具返回的本地 PNG 必须交给 `automation/scripts/complete_profiled_generation.py`，由生产入口校验序号 1 后独占发布候选图和序号 2 的完成回执。回执绑定同一 prompt SHA-256、调用 ID、调用意图 SHA-256 与候选图 SHA-256；缺少该证据链的 manifest 一律失败关闭。V2 最低 1920×1080；V3/V4 交付资产统一 3840×2160。比例固定 16:9，底部保留 `bottom-150px`，raster `text_policy` 固定 `none`。
5. 除 `xiaohuang-warm-first-v1` 外，精确中文标签由 renderer 根据 `overlay_labels` 绘制。小黄主题必须按自身合同把 2–4 个短标签生成为原生手写中文，renderer 禁止再叠系统标签；任何主题都不能生成标题、字幕、长段文字或程序 UI。
6. 每个 V3/V4 raster 先生成一个候选；真实语义 QC 失败时只允许一次定向修复。V4 修复必须保留基础 prompt，并独占发布独立的 `scene-XX-repair-02.md` 与第二组生成证据。第二个候选仍失败就停止，不切换 Provider，不降低 QC，不改成空白占位图。
7. 按 [video-frame-qc.md](references/video-frame-qc.md) 查看实际图片。V3 八项语义检查全部为 true；V4 还必须通过所选主题的精确专属检查，之后才能写入 `semantic-qc.json`。测试 fixture 的 pass 不能冒充真实视觉审核。
8. 只把通过版本和所有候选的路径/哈希写入 `illustration-manifest.json`，再运行离线 validator。

```powershell
uv run python automation/scripts/validate_illustrations.py "<PROJECT_ROOT>"
```

历史项目不迁移、不重写。任何显式视觉合同一旦发布，生成失败不能静默降级到 V3、V2、小黑、动态文字卡片、V4 其他主题或其他 Provider。

上游借鉴与许可边界见 [upstream-attribution.md](references/upstream-attribution.md)。

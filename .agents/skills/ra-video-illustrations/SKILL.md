---
name: ra-video-illustrations
description: Use when a BoomEarth scene needs the default sponge illustration style, an explicit registered V2/V3/V4 theme, or semantic image evidence matched to narration.
---

# 视频插画 V2 / V3 / V4

## 核心合同

为 BoomEarth 生成逐场景、本地、可审计的 16:9 视觉。图片必须解释当前文案的主体、动作或关系，不能只营造“科技感”。先发布合同文件，再调用 runtime-native imagegen；不调用外部 CLI，不读取或创建 API Key，不切换 Provider。原生工具不可用时停止。

版本必须明确：

- schema 4 / `sponge-host-handdrawn-v1`：新视频默认值，本 Skill 发布 V4 prompt/生成证据/manifest；先读取根 AGENTS.md、`video-daheihuang/sponge-ip/DESIGN.md` 与 `katerj-xiaohei-illustrations` 本体，只换角色。新 prompt 使用 `target_size: native-source`、`text_policy: embedded`，原生完整PNG不插值放大。
- schema 1 / `xiaohei-white-first-v1`：显式小黑或历史绑定由 `katerj-xiaohei-illustrations` 负责；历史调用名 `ian-xiaohei-illustrations` 继续兼容，不是新项目默认。
- schema 2 / `editorial-motion-v2`：保留 V2 manifest schema 1，只修复新 prompt 的语义路由。
- schema 3 / `semantic-handdrawn-v3`：只在显式选择时使用 `source-collage`、`human-action` 或 `handdrawn-flow`，并要求 manifest schema 2 与 `semantic-qc.json`。`type-led` 只能由用户或交接稿显式选择，精确目标为 `semantic-handdrawn-v3/type-led`，且整条计划的所有场景都必须使用 `type-led`；普通 V3 不得混入该模式。
- schema 4 / `profiled-illustration-v4`：默认海绵或显式主题使用。`visual_theme` 必须是主题库中的精确 ID，并要求 manifest schema 3、同主题目录与主题专属 QC。
- schema 4 / `xiaohuang-warm-first-v1`：由 `katerj-xiaohuang-illustrations` 提供角色 DNA、原生手写中文和主题 QC；仍由本 Skill 发布 V4 prompt、生成证据和 manifest。

当前中文调用入口和精确主题见 [theme-library.md](references/theme-library.md)；代码注册表 `illustration_themes.py` 为校验真源。其他类型、风格与模式见 [style-profiles.md](references/style-profiles.md)；仅选择 V3 时读取 [semantic-routing-v3.md](references/semantic-routing-v3.md)。

V2 继续允许 `concept-scene`、`comparison`、`framework`、`technical`、`clean-collage`，以及 `editorial-scene`、`minimal-vector`、`technical-diagram`、`screen-print-metaphor`、`clean-collage`。历史合同中的“不切换 provider”与 V3 的“不切换 Provider”含义相同。

## 工作流

1. 从已批准的 source-free scene intent 提炼唯一判断、语义主体、动作/关系和必须可见的证据；不要复制整段口播、来源标题、URL 或逐字稿。
2. 先服从交接稿已经规范化的精确视觉目标。只有精确目标为 `semantic-handdrawn-v3/type-led` 时才选择文字主导；普通 V3 只在真实素材、人物动作和手绘关系之间路由。`AI`、`Agent`、`模型`、`插件`、`工具`、`架构` 本身不是机器人、齿轮或工厂的理由。
3. V2 raster 先写 `工程/assets/editorial-illustrations/prompts/scene-XX.md`；V3 raster 先写 `工程/assets/semantic-handdrawn/prompts/scene-XX.md`；V3 `type-led` 只写 `type-led/scene-XX.json`；V4 必须运行 `automation/scripts/prepare_profiled_generation.py`，由生产入口先严格校验 [prompt-contract.md](references/prompt-contract.md) 的 frontmatter 与十段标题，再独占发布 `工程/assets/profiled-illustrations/{visual_theme}/prompts/scene-XX.md` 和序号 1 的调用意图。不合规时入口返回失败且不发布任何 prompt/intent；先修正本地合同并重新 `prepare`，不得先调用 ImageGen。
4. 只有 `prepare` 成功并返回 call ID 与 intent SHA-256，才使用 runtime-native imagegen 逐张生成；原始 PNG 交给 `complete_profiled_generation.py` 独占发布候选和完成回执，绑定同一 prompt/call/intent/候选哈希。新海绵 `native-source` 至少1440×810、16:9容差0.01，记录实际尺寸，禁止4K插值；保持原图字节并检查显示倍率不超过1。旧3840×2160合同、其他主题及V2/V3尺寸保持原约束。底部安全区 `bottom-150px`。
5. 海绵与 `xiaohuang-warm-first-v1` 使用 `text_policy: embedded`：按各自角色合同生成2–4个准确原生手写短标签，禁止叠系统标签。其余V4主题按原合同由renderer绘制短标签；都不能用页面标题、字幕、长段文字或程序UI代替语义图片。海绵全幅分支不显示旧模板标题卡片。
6. 每个 V3/V4 raster 先生成一个候选；真实语义 QC 失败时只允许一次定向修复。V4 修复必须保留基础 prompt，并独占发布独立的 `scene-XX-repair-02.md` 与第二组生成证据。第二个候选仍失败就停止，不切换 Provider，不降低 QC，不改成空白占位图。
7. 按 [video-frame-qc.md](references/video-frame-qc.md) 查看实际图片。V3 八项语义检查全部为 true；V4 还必须通过所选主题的精确专属检查，之后才能写入 `semantic-qc.json`。测试 fixture 的 pass 不能冒充真实视觉审核。
8. 只把通过版本和所有候选的路径/哈希写入 `illustration-manifest.json`，再运行离线 validator。

```powershell
uv run python automation/scripts/validate_illustrations.py "<PROJECT_ROOT>"
```

历史项目不迁移、不重写。任何显式视觉合同一旦发布，生成失败不能静默降级到 V3、V2、小黑、动态文字卡片、V4 其他主题或其他 Provider。

上游借鉴与许可边界见 [upstream-attribution.md](references/upstream-attribution.md)。

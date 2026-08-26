# Prompt 合同

## V2 raster

frontmatter 固定为：

```yaml
---
scene_id: scene-01
visual_type: concept-scene
visual_style: editorial-scene
ratio: 16:9
target_size: 3840x2160
text_policy: none
caption_safe_zone: bottom-150px
---
```

正文依次写六项：

1. 唯一判断。
2. 主体和核心动作。
3. 构图与留白，主体默认右中。
4. 风格、材质和受控色板。
5. 允许的 0–3 个短标签；默认无标签。
6. 禁止标题、字幕、页码、Logo、水印、平台 UI、海报/PPT/文字墙。

prompt 文件必须早于生成调用存在；不得使用无记录的 inline prompt。

V2 新 prompt 的第 2、6 项必须说明真实主体、动作/关系、该画面为何能证明当前判断，以及默认机械禁用集合。不能因为出现 AI、Agent 或架构就选择机械意象。

## V3 raster

位置：`工程/assets/semantic-handdrawn/prompts/scene-XX.md`。

frontmatter 固定为：

```yaml
---
scene_id: scene-01
visual_type: concept-scene
visual_style: semantic-handdrawn
visual_mode: human-action
ratio: 16:9
target_size: 3840x2160
text_policy: none
caption_safe_zone: bottom-150px
---
```

正文依次写八项：

1. 当前场景的唯一判断。
2. 语义主体。
3. 核心动作或关系。
4. 必须在画面中看到的证据。
5. 构图、左侧标题区和底部字幕安全区。
6. 纯白/浅色、黑灰手绘线和受控淡彩色板。
7. 为什么该画面能够解释当前判断。
8. 禁止的意象、文字、Logo、水印和无关装饰。

所有精确标签走 `overlay_labels`；prompt 不要求图片模型写中文。

## V3 type-led

`type-led` 不创建 imagegen prompt。先发布并哈希绑定 `工程/assets/semantic-handdrawn/type-led/scene-XX.json`，内容只允许 schema、scene id、`visual_mode: type-led`、16:9/3840×2160、`bottom-150px`、已审核 labels、layout 和 connectors。renderer 依据该合同生成文字、下划线、路径、分区与轻量图标。

## V4 profiled raster

位置：`工程/assets/profiled-illustrations/{visual_theme}/prompts/scene-XX.md`。必须执行 prompt-before-generation：生产入口 `automation/scripts/prepare_profiled_generation.py` 在外部工具调用前先校验完整 frontmatter 与下列十段标题；校验失败时不发布 prompt 或 intent，也不得调用 ImageGen。校验通过后，入口才独占发布 prompt，并在 `generation/scene-XX-candidate-NN-intent.json` 写入序号 1 的调用意图、调用 ID 与 prompt SHA-256；不允许临时 inline prompt，也不允许覆盖已有同名文件。

runtime-native imagegen 返回本地 PNG 后，必须运行 `automation/scripts/complete_profiled_generation.py` 并传回 `prepare` 输出的 call ID 与 intent SHA-256。`complete` 会先恢复并验证序号 1，之后才独占发布候选图和 `generation/scene-XX-candidate-NN-receipt.json`；没有 prepare 证据时不得封存图片。序号 2 的完成回执必须绑定同一调用 ID、调用意图 SHA-256、prompt 路径与 SHA-256、候选图路径与 SHA-256。manifest 的每个 `candidate_artifacts` 项必须同时记录 intent/receipt 的路径和 SHA-256；离线 validator 验证完整事件链。prompt 与候选图的修改时间比较只保留为附加防御，不再作为调用顺序的主要证据。

首个候选使用不可变基础合同 `prompts/scene-XX.md`。只有首个候选真实 QC 失败时，第二次且最后一次定向修复才使用独立的 `prompts/scene-XX-repair-02.md`；不得覆盖基础合同。每个候选项还必须记录自己的 `generation_prompt_path` 与 `generation_prompt_sha256`。repair prompt 仍须满足完整十段合同、同一 scene/theme/visual mode，并且内容哈希必须与基础合同不同。

frontmatter 固定为：

```yaml
---
scene_id: scene-01
visual_type: comparison
visual_style: vivid-comic-explainer
visual_mode: human-action
visual_system: profiled-illustration-v4
visual_theme: vivid-comic-explainer
ratio: 16:9
target_size: 3840x2160
text_policy: none
caption_safe_zone: bottom-150px
---
```

正文严格写十项，编号必须正好为 1–10：

1. 当前场景唯一判断。
2. 语义主体。
3. 核心动作或关系。
4. 必须可见的证据。
5. 构图、左侧程序文字区和底部字幕安全区。
6. 当前主题的线条、材质和色板。
7. 当前主题专项结构。
8. 为什么画面能解释判断。
9. overlay labels，仅供 renderer，不要求 imagegen 写字。
10. 禁止意象、文字、Logo、水印、UI、PPT 页面和无关装饰。

`visual_style`、`visual_theme`、content plan、主题目录、manifest 与 semantic QC 必须使用同一个精确主题 ID。四格主题的第 7 项必须写四个连续节拍；工程手稿的第 7 项必须声明真实实体设备和机械例外，没有则明确写无；蓝黑白板的第 7 项必须写明流程、系统、分组、对比或循环中的一种。

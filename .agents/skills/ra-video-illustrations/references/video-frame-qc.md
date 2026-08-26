# 视频画面 QC

- 原图可解码，PNG/JPEG/WebP，16:9，至少 1920×1080。
- 缩小到 960×540 后主旨仍一眼可懂。
- 主体在右中视觉框内不被裁切；底部 150px 保持可用。
- 没有页面标题、字幕、水印、大段文字、完整 PPT、知识板或海报排版。
- 背景不抢主体，程序标题和深色字幕栏仍有对比。
- 相邻场景色板、线条和材质连续；图片不重复解释程序卡片文字。
- 技术图只保留主路径；人物、手、产品 UI 和短标签没有明显错误。

## V3 语义 QC

主审必须打开实际图片，并为每个场景逐项记录：

- `subject_match`：主体与 `semantic_subjects` 一致。
- `action_match`：动作或关系与 `semantic_action` 一致。
- `evidence_complete`：所有 `required_visual_evidence` 都可见。
- `claim_readable`：不看标题也能大致理解唯一判断。
- `non_generic`：不是可替换到任意 AI 文案的泛科技装饰。
- `forbidden_absent`：没有计划禁止的意象。
- `mobile_readable`：缩到 960×540 仍能看懂主关系。
- `caption_safe`：底部 150px 没有关键证据。

八项必须全部为 true，scene 才能是 `pass`。`semantic-qc.json` 还要绑定 content plan、prompt/type-led 合同和正式资产 SHA-256，并写一句 source-free 相关性理由与 `reviewer_type: multimodal-review`。

图片里出现机器人、齿轮、工厂、机械臂、金属卡匣、电路板、工业流水线、发动机或机械底座时，除非 content plan 明确把对应实体设备列为语义主体，否则 `forbidden_absent` 必须为 false。泛科技装饰必须让 `non_generic` 为 false。

## V4 主题专属 QC

V4 先执行上述八项通用检查，再只执行所选主题的精确专属集合；少一项、多一项、某项为 false 或混入另一主题的检查都不能通过。

- `vivid-comic-explainer`：`character_consistent`、`expression_supports_claim`、`action_explains_claim`、`accent_palette_controlled`、`not_decorative_cartoon`。
- `engineering-sketch-explainer`：`engineering_subject_real`、`callouts_support_claim`、`mechanical_exception_valid`、`linework_clean`、`diagram_not_overloaded`。
- `four-panel-comic-explainer`：`exactly_four_panels`、`reading_order_clear`、`beats_continuous`、`character_consistent`、`one_event_per_panel`、`lower_panels_caption_safe`。
- `blue-black-whiteboard-explainer`：`marker_material_clear`、`blue_black_palette_only`、`structure_type_clear`、`reading_path_clear`、`not_ppt_page`、`not_character_led`。

正式图字节必须与候选 01 或候选 02 之一完全一致。主审打开 3840×2160 实图后记录相关性理由；fixture、纯色占位图和仅有文件哈希的自动检查不能替代真实多模态判断。

四主题真实验收还必须由 `multimodal-review` 在 2×2 contact sheet 上记录六组两两区别：每一对主题都要写明线条、材质、结构或叙事方式的可见差异。四个不同文件哈希不能代替视觉区分结论；每个 prompt、主题审核记录和共享测试判断必须绑定同一个 claim SHA-256。

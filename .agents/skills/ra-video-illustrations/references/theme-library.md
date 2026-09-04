# 视频插画主题库

本表是 BoomEarth 的稳定中文调用合同。用户明确说出某条中文指令时，按对应目标执行；未明确指定时，新建视频默认使用 `xiaohei-white-first-v1`，并路由到 `katerj-xiaohei-illustrations`。五个 `profiled-illustration-v4` 主题和 `semantic-handdrawn-v3/type-led` 仅允许显式选择，绝不是回退方案。历史项目不迁移。

## 既有四种入口

### 小黑怪诞插画

- 中文指令：`这条视频使用小黑怪诞插画风格。`
- 目标：`xiaohei-white-first-v1`

### 编辑动效插画

- 中文指令：`这条视频使用编辑动效插画风格。`
- 目标：`editorial-motion-v2`

### 语义手绘插画

- 中文指令：`这条视频使用语义手绘插画风格。`
- 目标：`semantic-handdrawn-v3`

### 动态文字卡片

- 中文指令：`这条视频使用动态文字卡片风格，画面以文字关系、路径和轻量图标为主。`
- 目标：`semantic-handdrawn-v3/type-led`

## V4 五个独立主题

五者统一使用 `profiled-illustration-v4`、content plan schema 4、manifest schema 3、3840×2160、16:9 和 `bottom-150px`。前四个主题使用 `text_policy: none`，标签由 renderer 根据 `overlay_labels` 绘制；小黄主题使用 `text_policy: embedded`，由图片模型生成经过审核的短手写中文，renderer 禁止重复叠标签。每个主题必须先写 prompt，再生成候选，再做八项通用 QC 与本主题专属 QC。

### 鲜彩漫画讲解

- 中文指令：`这条视频使用鲜彩漫画讲解风格，用人物、动作、表情和鲜明强调色解释文案。`
- 主题 ID：`vivid-comic-explainer`
- prompt 风格：`clean-black-comic-linework-with-controlled-vivid-accents`
- 适合：人物选择、认知变化、冲突、失败与转折。画面必须由人物动作解释判断，不可只放可爱角色装饰。
- 固定视觉：干净黑色漫画线稿，白色或浅色底，少量鲜艳红、黄、蓝强调；人物、道具和表情在相邻场景保持一致。
- 专属 QC：`character_consistent`、`expression_supports_claim`、`action_explains_claim`、`accent_palette_controlled`、`not_decorative_cartoon`。

### 工程手稿图解

- 中文指令：`这条视频使用工程手稿图解风格，只画文案真实涉及的产品、结构和流程。`
- 主题 ID：`engineering-sketch-explainer`
- prompt 风格：`precise-dark-gray-engineering-linework-with-pale-wash-accents`
- 适合：真实产品结构、软件模块关系、数据路径、接口或实体设备。不能因文案出现 AI、Agent、模型、工具、架构就补机器人、齿轮或工厂。
- 固定视觉：深灰精确线稿、少量浅色水洗强调、清晰标注线和单一主路径；避免满页零件、伪机械剖面和无关工业装饰。
- 机械例外：只有 content plan 的 `semantic_subjects` 明确包含真实实体设备，且 `theme_exceptions` 精确声明所需机械对象时才可出现；泛科技隐喻仍禁止。
- 专属 QC：`engineering_subject_real`、`callouts_support_claim`、`mechanical_exception_valid`、`linework_clean`、`diagram_not_overloaded`。

### 四格连环漫画

- 中文指令：`这条视频使用四格连环漫画风格，每个场景用四个连续画格讲清变化。`
- 主题 ID：`four-panel-comic-explainer`
- prompt 风格：`fixed-two-by-two-causal-comic-with-consistent-characters`
- 适合：问题到结果、前后变化、因果链和操作后果。每个场景必须是固定 2×2 四格，并在 `theme_structure` 写出四个互不重复的连续节拍。
- 固定视觉：左上到右下阅读顺序；一格一个事件；人物、服饰、道具和场所连续；下方两格保留字幕安全区。
- 专属 QC：`exactly_four_panels`、`reading_order_clear`、`beats_continuous`、`character_consistent`、`one_event_per_panel`、`lower_panels_caption_safe`。

### 蓝黑白板讲解

- 中文指令：`这条视频使用蓝黑白板讲解风格，用马克笔线条、框线和箭头解释关系。`
- 主题 ID：`blue-black-whiteboard-explainer`
- prompt 风格：`whiteboard-marker-structure-with-black-lines-and-blue-emphasis`
- 适合：流程、系统、分组、对比或循环。`theme_structure` 必须明确选定一种关系结构，画面按单一路径解释。
- 固定视觉：白板质感、黑色马克笔主线、蓝色唯一强调色、框线和箭头；不是 PPT 页面，也不以人物表演为主。
- 专属 QC：`marker_material_clear`、`blue_black_palette_only`、`structure_type_clear`、`reading_path_clear`、`not_ppt_page`、`not_character_led`。

### 小黄温度插画

- 中文指令：`这条视频使用小黄温度插画风格，让固定暖黄色角色用动作和原生手写中文解释文案。`
- 主题 ID：`xiaohuang-warm-first-v1`
- prompt 风格：`warm-white-hand-drawn-xiaohuang-character-with-native-chinese-labels`
- 适合：知识解释、步骤、对比、痛点和认知转折。小黄必须承担关键动作，不得只站在文字旁边。
- 固定视觉：暖白底、轻黑手绘线、蜡笔或彩铅质感、暖黄不规则种子形角色、空心爱心天线、竖椭圆眼、淡腮红和细黑四肢；每场 2–4 个原生手写短标签。
- 专属 QC：`xiaohuang_identity_consistent`、`character_performs_action`、`native_labels_correct`、`warm_white_canvas`、`not_system_label_overlay`。

## 通用拒绝条件

八项通用检查 `subject_match`、`action_match`、`evidence_complete`、`claim_readable`、`non_generic`、`forbidden_absent`、`mobile_readable`、`caption_safe` 必须全部为 true。主题 ID、主题目录、prompt frontmatter、候选图、正式图、manifest 和 semantic QC 任一处不一致即停止；不得跨主题借图、静默降级或切换 Provider。

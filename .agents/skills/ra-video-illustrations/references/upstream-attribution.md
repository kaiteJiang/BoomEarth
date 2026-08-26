# 上游归属

工作流借鉴 [JimLiu/baoyu-skills](https://github.com/JimLiu/baoyu-skills) 中 `baoyu-article-illustrator` 的 Type × Style 分离、prompt-before-generation 和逐图生成思想。

风格选型参考 [freestylefly/awesome-gpt-image-2](https://github.com/freestylefly/awesome-gpt-image-2) 的 `gpt-image-2-style-library` 及其 Prompt-as-Code 方法。只借鉴结构化风格语言，不复制社区示例图，不把海报、UI mockup 或文字墙模板直接用于视频正文插画。

BoomEarth 的运行边界更窄：只用 runtime-native imagegen、结构化主题库、16:9 视频构图、prompt/asset manifest 双哈希和底部字幕安全区。各上游材料按其实际许可证状态使用并保留本说明。

V3/V4 还参考 [chujianyun/awesome-gpt-image2-ppt-skills](https://github.com/chujianyun/awesome-gpt-image2-ppt-skills)。本次固定提交采集显示该仓库当前未声明许可证，因此 BoomEarth 只记录思想来源，不复制或再分发其 SKILL 正文、预览图、代码与素材，也不执行仓库内容：

- `pure-white-handdrawn-ppt-infographic` 的纯白留白、黑灰手绘线、淡彩标签、流程、分叉、闭环和左右对比视觉语言；
- `comic-explainer-illustration` 的内容压缩，以及把抽象观点翻译成人物、动作、道具、表情和一条关系路径的方法。
- `engineering-sketch-illustration` 的精确线稿、标注线与真实结构优先思想；
- `four-panel-comic-explainer-illustration` 的固定四格因果叙事思想；
- `whiteboard-handdrawn-explainer-illustration` 的马克笔、框线、箭头与单一阅读路径思想。

BoomEarth 的主题名称、中文指令、十段 prompt 合同、目录契约、机械例外规则、哈希绑定和 QC 字段均为本项目重新设计。BoomEarth 不复制上游预览图，不引入完整 PPT 生成流程，不让图片模型负责准确中文。标题、短标签、字幕和进度仍由本地 renderer 确定性绘制。

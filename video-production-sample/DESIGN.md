# BoomEarth V2 production render design

## Visual intent

一个克制、清晰的中文生产渲染画面：它不复述源内容，只呈现已核验本地旁白与词级字幕共同驱动的离线成片结构。画布固定为 1920×1080；两幕以柔和的 focus dissolve 衔接，底部始终保留独立的 `anchor-dark` 字幕区域。

## Palette and typography

- `#F7F1E7` 用于暖白主场景和浅色文字对比。
- `#17130F` 用于正文和 `anchor-dark` 字幕面板。
- `#2C63A3` 用于结构标签。
- `#DED4C4` 用于第二幕背景。
- 标题使用 `SimSun`, `STSong`, `Songti SC`, serif；正文和字幕使用 `Microsoft YaHei`, `Microsoft JhengHei`, `PingFang SC`, sans-serif。

## Layout and motion

- 画面主体使用 112px 上下、140px 左右内边距；底部 150px 完全保留给 `anchor-dark`，主体内容不得进入该区域。
- 标题不小于 96px，正文为 34px，字幕为 42px。字幕同一时刻只显示一个提示组。
- 第一幕和第二幕均有确定性的入场动画；两幕之间使用有限时长的 blur/focus dissolve。只有最终场景在结尾淡出。
- 旁白时长来自已提交的 WAV 探针。模板中的场景分割和所有音频 `data-duration` 由该有限时长替换；`captions.json` 的提示时间戳直接加载，绝不插值、缩放或重算。

## What not to do

- 不使用远程 URL、CDN、网络请求、云端媒体、TTS 生成或音频上传。
- 不使用随机数、当前时间、异步时间线、媒体播放调用或无限循环。
- 不覆盖活动工程中的 WAV、清单或字幕，也不在渲染快照中修改它们的字节内容。
- 不让主内容与 `anchor-dark` 字幕面板重叠，也不把生产字幕描述为合成样片字幕。

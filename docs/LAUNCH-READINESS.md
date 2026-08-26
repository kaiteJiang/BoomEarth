# BoomEarth 上线就绪状态

更新日期：2026-08-16
适用范围：个人本地优先视频生产工作台，不包含 SaaS、多用户、自动发布和计费。

## 当前唯一默认合同

- 输出：`1920x1080 / 16:9 / 30fps`。
- 声音：本地 IndexTTS2 `user-indextts2-black-gold-v3`，禁止静默降级。
- 视觉：未指定时使用 `profiled-illustration-v4 / vivid-comic-explainer`。
- 字幕：锁定最终音频后使用火山/豆包词级时间戳；默认 `anchor-dark`。
- 数字人：读取被 Git 忽略的私有默认配置；首选外观不可用时只允许同组降级。
- 数字人布局：`headroom_08-circle-lower-left`；矩形、9:16 小窗和全屏只接受用户显式覆盖。
- 来源隐私：URL、源标题、源逐字稿和仓库原文只留在私有工作区。

## 能力矩阵

| 能力 | 状态 | 上线前动作 |
| --- | --- | --- |
| 授权本地 MP4 到 16:9 成片 | 真实跑通 | 无 |
| X 文章及正文图片采集 | 真实跑通 | 无 |
| X 文章 V2/V3 视频 | 真实跑通 | 无 |
| GitHub Skill URL 解析与 source-free 交接 | 已实现且历史真实采集 | 恢复或重建可复验验收包 |
| 通用视频 URL 下载 | 代码、计划和授权门完成 | 运行一次真实下载并保留结果 |
| Paraformer 源音频转写 | 真实跑通 | 无 |
| 火山/豆包最终音频词级字幕 | 真实跑通 | 在 Release Candidate 上再验收 |
| 黑金 v3 IndexTTS2 | 真实跑通 | 在 HeyGen 新默认样片上再验收 |
| V1/V2/V3 视觉 | 真实跑通 | 无 |
| V4 四主题 | 四张真实静帧验收通过 | 在 Release Candidate 使用默认主题 |
| HeyGen 上传、生成和下载 | 历史真实跑通 | 按新默认外观与黑金 v3 重跑 10 秒 |
| 左下圆形数字人合成 | 本地媒体验收通过 | 使用真实 HeyGen master 做布局样片 |
| 完整新默认全链路 | 未完成 | 完成一条 GitHub Skill URL Release Candidate |

## 完成上线的硬门槛

1. 真实 HeyGen master 通过新圆形布局与字幕安全区 QC。
2. 一条授权视频 URL 完成真实下载、哈希绑定和可转写检查。
3. GitHub Skill URL 的 hash-bound 私有验收包可重新验证。
4. 新默认外观、黑金 v3 和圆形布局完成一次 10 秒 HeyGen 云端验收。
5. 一条 GitHub Skill URL 从采集到 16:9 正式成片完整归档。
6. preflight、失败恢复手册和上线验收报告完成。

## 已明确不属于当前上线范围

- 手语动作、手语翻译和“大白”熊猫。
- GUI、多人协作、云端 SaaS、账号计费。
- 抖音、小红书、B 站等平台自动发布。
- Qushuiyin；它不是当前依赖，也不是上线阻塞项。

## 验证基线

- 主分支全量离线测试：`1361 passed, 15 skipped, 0 failed`。
- V4 四主题显式真实验收：`1 passed`。
- 私有头像 ID 不进入 Git；默认配置只存在于被忽略的私有目录。
- 历史 40.297 秒 HeyGen master 和 10 秒横版样片保留为真实服务证据，但不能替代新默认合同验收。

后续任务与授权节点以
[`docs/superpowers/plans/2026-08-16-boomearth-launch-readiness.md`](superpowers/plans/2026-08-16-boomearth-launch-readiness.md)
为准。

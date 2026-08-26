---
name: ra-video-cover
description: Use when a recommended final or canonical pre-publication QC needs a public Chinese social-video work cover.
---

# 视频作品封面

仅在推荐成片通过 QC 并归档后运行。封面不是分镜、插图或成片制作入口。

## 当前默认合同

新项目使用 `covers: punk-cover-giant-title-3x4-v1`：

- **REQUIRED SUB-SKILL:** Use `punk-cover`。
- 风格固定为 `giant-perspective-chinese-title`（巨型透视中文标题），无需再次询问默认风格。
- 只生成一张竖版 `3:4` 作品封面，本地交付规格固定 `1080×1440`。
- 只交付 `封面/作品封面-1080x1440.png` 与 `质检/punk-cover-qc.json`。
- 不生成主页预览、视频号裁切、平台套图、联系表或多版本候选。

标题采用三层结构：A 层是手机缩略图可读的短主题大字；B 层补全内容价值；C 层只放确有必要的署名、致敬或栏目标签。巨型中文标题必须成为场景中的实体空间结构，不能退化为普通标题覆盖背景图。

## 生成

1. 只从 source-free 交接稿、公开标题候选和最终画面计划提炼主题、受众、情绪、视觉隐喻与禁用元素；不得写入来源 URL、逐字稿、私有路径或私有 ID。
2. 按 `punk-cover` 读取 cover blueprint 以及 `giant-perspective-chinese-title` 的 `META.md`、`STYLE.md`，先保存完整 prompt 到项目归档内 `punk-assets/punk-cover/<slug>/prompts/cover.md`。
3. ImageGen 是新的独立外部阶段。每个项目在调用前请求一次精确 ImageGen 批准，绑定 prompt 路径与 SHA-256、不可变计划 SHA-256、work_id、OpenAI runtime-native imagegen、一次调用、可能费用、300 秒超时、零重试、零 fallback、零重定向，并排除 4K、其他 Provider、主页预览和多候选。
4. 批准后只调用一次。失败即保留证据并停止；不得自动重试、换 Provider 或生成第二版。
5. 将本次工具明确返回的原图保存为 `punk-assets/punk-cover/<slug>/cover.png`，不得扫描宽泛生成目录猜测产物。按比例无损适配为 `1080×1440` 后再发布正式封面。

## 质检与版本

逐字检查所有中文，确认主标题正确可读、透视未破坏字形、主题与视频一致、关键内容在 72px 安全边距内、无 Logo/水印/来源信息/私有信息，并验证 PNG 可完整解码、尺寸为 `1080×1440`。`质检/punk-cover-qc.json` 绑定 prompt、原始生成图和正式封面的 SHA-256，声明 `homepage_preview_generated: false`。

不覆盖旧封面：正式路径已存在时使用下一个 `封面/作品封面-1080x1440-vNN.png`，对应 QC 同步使用版本号。已批准旧图与失败证据全部保留。

## 历史兼容

`platform-defaults-v1` 是只读历史合同。已有项目的三张平台封面、主页预览、联系表和 `质检/cover-qc.json` 继续按原哈希验收，不删除、不重写、不迁移。新项目不得再声明该历史合同。

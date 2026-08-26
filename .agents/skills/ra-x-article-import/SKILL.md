---
name: ra-x-article-import
description: Use when an authorized public X Article or X 图文帖子 must be captured into BoomEarth as private Markdown, structured JSON, and local images; not for X native video/audio downloads.
---

# X Article 私有导入

## 核心边界

只采集用户明确授权的公开 X Article/图文帖子。URL、作者、原文、远程图片 URL 与 HTML 全程留在私有工作区，不进入 argv、公开交接稿、Git 或默认日志。

X Article/图文帖子使用本 Skill；X 原生媒体交给 `katerj-source-acquisition`。历史调用名 `ra-video-download` 仍由兼容桥承接。不读取 Cookie，不登录，不重试，不自动降级，不切换 TikHub、Qushuiyin、浏览器或其他 provider。

## 工作流

1. 将规范 `https://x.com/<user>/status/<id>` 单独保存到未跟踪的 UTF-8 私有文件 `PRIVATE_URL_FILE`。不要把 URL 直接写进命令行。
2. 建立私有任务：

```powershell
uv run python automation/scripts/source_intake.py x-article --input-file "<PRIVATE_URL_FILE>" --authorized
```

3. 离线生成计划：

```powershell
uv run python automation/scripts/acquire_x_article.py plan <work_id>
```

4. 核对私有计划固定为一次页面请求、最多二十次图片请求、`follow_redirects=false`、`no_retry=true`、`no_fallback=true`。联网前必须存在与完整计划哈希和 21 次总预算绑定的批准凭据。
5. 只执行一次已批准动作：

```powershell
uv run python automation/scripts/acquire_x_article.py run <work_id> --approval "<PRIVATE_APPROVAL_JSON>"
```

## 高媒体恢复档位

默认始终使用上面的 20 张图片档位。只有同时满足以下条件，才使用一次显式 `high-media` 新事务：

1. 标准事务以 `x-article-request-budget-exceeded` 安全失败；
2. ledger 仍为 `source_registered`，不存在正式 article/manifest、staging 或 partial；
3. 用户明确批准一次新页面请求、最多 60 次正文图片请求、总预算 61、不重试、不重定向且不调用其他 Provider；
4. 为同一个私有 URL 创建新的 work_id、计划和批准凭据。不得修改或复用旧 work_id 的计划与批准文件。

只在新 work_id 上离线生成高媒体计划：

```powershell
uv run python automation/scripts/acquire_x_article.py plan <new_work_id> --profile high-media
```

计划必须精确为 action `x-article-acquisition-high-media`、页面预算 1、图片预算 60、总预算 61。批准凭据必须绑定该 action、完整计划 SHA-256、输入 SHA-256、`request_count=61`、`no_retry=true` 和 `no_fallback=true`。

`run` 命令保持不变且没有 profile 参数；档位只能从已发布并批准的计划恢复。真实运行失败后停止，不发起第三次 X 请求，不切换 TikHub、Qushuiyin、浏览器自动化或其他 Provider。

## 验收

- ledger 必须从 `source_registered` 进入 `article_ready`。
- 私有目录必须存在 `article-source/article.json`、`article-source/article.md`、本地图片和 `x-article-manifest.json`。
- Markdown 不含远程图片依赖；manifest 与正文、JSON、图片哈希一致。
- 任一图片、解析、预算或发布步骤失败时，状态不得前进，也不得留下可误认作完整文章的正式目录。
- 完成采集后交给 `katerj-script-rewrite`；历史调用名 `ra-洗稿` 仍由兼容桥承接。不要自动开始改写、TTS、ASR、生图或成片。

解析借鉴与许可证范围见 [upstream-attribution.md](references/upstream-attribution.md)。

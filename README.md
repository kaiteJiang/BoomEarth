<div align="center">

# BoomEarth

### 把想讲清楚的内容，画成会动的鲜彩漫画。

**鲜彩漫画 · 逐件手绘 · 本地配音 · 真实时间字幕 · 1080p 视频**

[观看 18 秒 Demo](docs/assets/vivid-comic/2026-10/drawing-demo-18s.mp4) · [本地体验](#本地体验) · [制作指南](docs/VIDEO-PRODUCTION-RUNBOOK.md) · [绘制原理](docs/comic-semantic-drawing.md)

[![BoomEarth 鲜彩漫画演示：人物、物件和浅色区域依次绘制](docs/assets/vivid-comic/2026-10/drawing-preview.gif)](docs/assets/vivid-comic/2026-10/drawing-demo-18s.mp4)

**点击动图观看高清 MP4。18 秒 · 1920 × 1080 · 30 fps · 无配音**

</div>

BoomEarth 是面向自媒体、知识创作者和一人公司的本地视频制作工作流。把文章、选题或已定稿的文案交给 Agent，让采集、写稿、配音、插画、字幕、动效和质检沿着同一份工程往下走。

这一版用鲜彩漫画展示内容：干净的白底、鲜明的颜色、参与情境的人物，配上左侧信息卡和右侧逐件绘制的画面。观众既能看清当前讲到的重点，也能看到一个场景逐渐成形。

## 先看这 18 秒

演示保留现有的左文右图、信息卡入场和手持画笔动效，集中展示两个场景：

| 时间 | 画面 | 看什么 |
| --- | --- | --- |
| 0–9 秒 | 人物与工作台 | 台历先于被它遮挡的电脑出现，相机、笔记本和桌面逐件完成。 |
| 9–18 秒 | 阅读与生活 | 笔筒、书堆、台灯和绿植分别绘制；闹钟、水果之后，浅色枕头随画笔填满。 |

这是一段**绘制效果演示**，没有音轨。完整制作流程中的配音与字幕由最终音频驱动，见 [部署与使用指南](docs/DEPLOYMENT-AND-USAGE.md)。

### 实际成片截图

| 工作台：每件物品各自完成 | 阅读场景：浅色也能完整显现 |
| --- | --- |
| ![鲜彩漫画工作台场景实际成片截图](docs/assets/vivid-comic/2026-10/workspace-scene.webp) | ![鲜彩漫画阅读场景实际成片截图](docs/assets/vivid-comic/2026-10/reading-scene.webp) |

以上截图直接取自同一段演示视频。原生插画为 1672 × 941，视频输出为 1920 × 1080；截图只做 WebP 编码。

## 画完一件，再画下一件

手绘感来自画笔与物件的对应关系。人物完成之后，画笔依次走向台历、电脑、相机；枕头被扫过的地方，线条和浅色一起显现。

这版绘制逻辑处理了四个容易破坏观感的问题：

- **物件有独立分区。** 书本、台灯、笔筒、绿植即使轮廓相接，也各自绘制；文字只占自己的真实笔迹。
- **遮挡有先后顺序。** 先画前景物件，再画后方可见表面，避免完成的电脑上还空着一个台历形状。
- **浅色随笔填满。** 笔刷第一次触及像素时就呈现原色，白鞋、屏幕、枕头和淡色阴影都参与覆盖检查。
- **已经完成的画面保持稳定。** 后续笔画不改掉前面的颜色，最后一帧也不靠叠加整图补洞。

本次两场演示检查了 **24 个分区、216 个过程检查点**，并抽查实际 MP4 的 **69 帧**。检查覆盖分区提前显现、浅色遗漏、已完成像素变化、末帧补色和回退重放。公开规格与文件哈希见 [演示记录](docs/assets/vivid-comic/2026-10/manifest.json)，具体实现见 [逐区绘制说明](docs/comic-semantic-drawing.md)。

## 从一篇内容到一条视频

创作者最容易被消耗的，是反复搬运同一份内容：文章改成口播，口播拆成场景，配音重做后又重新对字幕，最后还得确认素材和成片是不是同一版。BoomEarth 把这些环节连接起来，并保留能继续制作的工程记录。

| 环节 | BoomEarth 负责组织的工作 |
| --- | --- |
| 来源与文稿 | 接入授权的文章、视频、GitHub 项目或现成文稿；保存来源证据，定稿后绑定全文版本。 |
| 声音 | 对接本地 IndexTTS2，按段合成并记录进度；恢复制作时复用已完成素材。 |
| 插画 | 根据口播语义规划人物动作、物件和原生文字，逐场景检查画面。 |
| 字幕 | 从最终音频获取词级时间，再组织单行字幕与少量重点强调。 |
| 动效 | 让标题、信息卡、画笔和场景切换围绕内容与时间轴展开。 |
| 交付 | 检查画面、字幕与媒体文件，记录哈希，归档成片和工程。 |

```mermaid
flowchart LR
    A[文章 / 选题 / 定稿] --> B[来源核验与文稿确认]
    B --> C[本地配音]
    B --> D[语义插画与物件分区]
    C --> E[最终音频与词级字幕]
    D --> F[逐件绘制与信息卡]
    E --> G[渲染与质检]
    F --> G
    G --> H[成片与可恢复工程]
```

观点、表达和最终成片由创作者决定。Agent 负责把已经确定的内容往下推进，让制作过程能检查、能复用，也能在中断之后继续。

## 本地体验

当前主要在 Windows 上验证。准备 Git、Python **3.11**、uv、Node.js **22.12+**、FFmpeg / ffprobe，以及 Chrome 或 Chromium。

```powershell
git clone https://github.com/kaiteJiang/BoomEarth.git
cd BoomEarth
uv python install 3.11
uv sync --dev
npm ci

# 准备已包含在仓库中的两场漫画示例
node automation/scripts/prepare_vivid_comic_demo.mjs
npx hyperframes preview examples/vivid-comic
```

这个演示使用仓库中的两张插画与已审分区，**不需要 TTS、ASR 或生图密钥**。准备脚本会从本地已安装的依赖生成运行资源。预览和渲染说明见 [示例工程](examples/vivid-comic/README.md)。

要从自己的文稿开始制作有声视频，还需接入本地配音环境、授权的声音参考、图像生成能力和最终音频 ASR。详细配置见 [部署与使用指南](docs/DEPLOYMENT-AND-USAGE.md)；基础环境可用 `uv run pytest tests/test_config.py -q` 与 `npm run hf:doctor` 检查。

### 选择这一版画风

向制作 Agent 指定：

> 这条视频使用鲜彩漫画逐项绘制风格，保留左文右图、信息卡和手持画笔动效。人物、物件与文字分别分区，浅色随笔完整显现。

对应主题 ID 为 **`vivid-comic-component-brush-v1`**。本页展示这一主题；现有海绵主题与历史工程继续保留。新插画仍需要逐物件分区、遮挡顺序和过程画面复核。

## 继续了解

| 想了解的内容 | 入口 |
| --- | --- |
| 跑起来并调整这段演示 | [鲜彩漫画示例](examples/vivid-comic/README.md) |
| 物件分区、浅色填充和检查方法 | [逐区绘制说明](docs/comic-semantic-drawing.md) |
| 从新任务开始，或恢复中断制作 | [制作 Runbook](docs/VIDEO-PRODUCTION-RUNBOOK.md) · [总控 Skill](.agents/skills/ra-source-to-video/SKILL.md) |
| 安装配音、字幕与外部能力 | [部署与使用指南](docs/DEPLOYMENT-AND-USAGE.md) |
| 理解任务接续和阶段交付 | [连续编排](docs/VIDEO-CONTINUOUS-ORCHESTRATION.md) |
| 查看早期样片与演进 | [海绵样片](docs/sponge-fullscene-sample.md) · [项目历史](docs/PROJECT-HISTORY.md) |

核心代码在 `src/boomearth/`，执行脚本在 `automation/scripts/`，回归检查在 `tests/`。公开仓库包含代码、文档和选定的演示素材；私有来源、声音参考、密钥及完整生产工程留在本地。

原创代码和项目自有工作流入口见 [MIT LICENSE](LICENSE)。保留的第三方组件、字体和素材遵循各自许可，使用与分发前请查看 [LICENSE-NOTES.md](LICENSE-NOTES.md)。

<div align="center">

**让每个重点，都有一个看得见的画面。**

</div>

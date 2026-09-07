# 开源视频工作流：从部署到成片

![开源视频工作流](assets/sponge/2026-09/tutorial-cover.png)

如果你已经有了值得讲的内容，却总卡在配音、配图、字幕和剪辑上，BoomEarth 想替你解决的就是这段距离。

你负责表达什么，Agent 理解内容并组织制作，确定性工具把素材变成能播放、能检查的视频。第一次把环境接好，以后重复使用同一条生产线。文章、软件介绍、教程解读都可以成为输入，不用每次重新研究一套软件。

本文按当前白底海绵插画、无数字人的路线讲解。先看[32 秒实际样片](assets/sponge/2026-09/fullscene-sample.mp4)，再开始安装。代码开源，外部生图、识别服务和 Agent 订阅各自计费；不要把“克隆仓库成功”当成“所有模型都已经装好”。

## 一、先弄清楚要安装哪几块

可以把它理解成一个小型视频工作室。

| 部件 | 通俗理解 | 需要准备什么 |
| --- | --- | --- |
| Agent 与项目 Skills | 理解文稿并安排步骤的导演 | 能读写项目、运行命令的 Agent；当前由 Astra 写稿 |
| Python 与 Node 依赖 | 工作台上的工具 | Python 3.11、uv、Node.js 22+、npm |
| 本地 IndexTTS2 | 配音员 | 独立模型环境、模型权重和自己的授权参考录音 |
| 图像生成能力 | 插画师 | 当前 Agent 环境可调用的 OpenAI ImageGen 与额度 |
| 最终音频 ASR | 精确打时间点的字幕员 | 火山/豆包 flash 服务配置及用量 |
| FFmpeg 与浏览器渲染 | 剪辑与输出设备 | FFmpeg、ffprobe、渲染器可用的 Chromium/Chrome |

本地配音依赖实际硬件与模型运行环境。Windows 是当前主要实测环境，其他系统需要重新检查路径、字体和模型兼容性，不宣称照搬就能全部跑通。

## 二、下载并安装项目

先安装 Git、Node.js 22+、FFmpeg 和 uv。uv 的安装方式见[官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)。FFmpeg 安装后应同时能找到 ffmpeg 和 ffprobe。

在 PowerShell 中检查：

```powershell
git --version
uv --version
node --version
npm --version
ffmpeg -version
ffprobe -version
```

这些命令能输出版本后，再安装项目：

```powershell
git clone https://github.com/kaiteJiang/BoomEarth.git
cd BoomEarth
uv python install 3.11
uv sync --dev
npm ci
uv run pytest tests/test_config.py -q
npm run hf:doctor
```

uv sync 安装 Python 依赖，npm ci 按锁文件安装动画与渲染依赖。配置测试通过，说明基础配置代码可用；hf:doctor 用来定位浏览器或渲染环境问题。它们不会证明模型、云端服务和完整视频生产已经全部打通。

如果 doctor 报浏览器缺失，按提示准备兼容浏览器后再检查，不要先跑长视频。项目没有一个替代全部阶段准备的“任意文稿一键出片”命令。

## 三、接上自己的声音

BoomEarth 使用本地 IndexTTS2。它不随本仓库一起下载，也不会把作者的私人音色录音公开给部署者。

先按 [IndexTTS 官方仓库](https://github.com/index-tts/index-tts)准备环境。注意：上游默认已经发展到 2.5，本项目的已验证路由仍绑定 **IndexTTS2**，不能直接换成 2.5 并沿用旧验收结论。选择与 IndexTTS2 及本地 CLI 兼容的版本、对应权重，并记录实际版本。

在 IndexTTS 自己的环境里，先合成一句短话。确认音频能播放、中文清楚，再接到 BoomEarth。这个顺序能区分“模型本身没装好”和“项目路径没填对”。

仅在目标文件尚不存在时，从示例创建本地配置；已有配置不要覆盖：

```powershell
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
if (-not (Test-Path automation/config/tts-routing.json)) {
    Copy-Item automation/config/tts-routing.example.json automation/config/tts-routing.json
}
```

用本地编辑器填写真实路径，不把密钥粘贴进公开对话。TTS 路由中重点是：

- interpreter_path：IndexTTS 环境的 Python，不是随便一个系统 Python。
- cli_script_path、model_dir：实际可运行的 CLI 与 IndexTTS2 权重目录；示例路径不是已安装资源。
- reference_audio_path：自己的授权、无损参考 WAV，不是一次生成结果或网上下载的人声。
- reference_audio_sha256：上述精确文件的 SHA-256；示例中的全零值必须替换。
- provenance_ledger_path：本地音色证据账本；按配音 Skill 建立并核对，不把空文件冒充有效记录。

路径需要互相匹配：在根 .env 设置 INDEXTTS2_ROOT 与 INDEXTTS2_PYTHON 后，JSON 的解释器必须指向同一个 Python，CLI 必须是该模型目录内的 indextts/cli_v2.py，权重目录是 checkpoints。账本固定在项目内的 01-内容生产/视频工作台/.internal/voice/indextts2-provenance-ledger.json，不能照抄示例中任意盘符。

首次音色接入让 Agent 读取 select_voice_reference.py 与 activate_voice_reference.py：前者为授权原录音选择合格片段并产出 QC 回执，后者将通过的参考文件写入本地路由和证据账本。它们有精确路径、哈希和授权输入约束，并不是给音频改个文件名即可激活。先检查输入，再执行本地准备；不要手工填写假的通过状态。

user-indextts2-black-gold-v3 是现有路由标识，不代表仓库附送作者声音。新部署者需要绑定自己的授权声音与证据，不能伪造原作者 provenance。参考音频、路由和账本都留在本地。具体接入要求见[本地配音 Skill](../.agents/skills/katerj-local-tts/SKILL.md)。

## 四、只接通本次需要的外部能力

现成文稿做海绵视频，通常需要图像生成与最终音频 ASR，不需要 GitHub 采集、视频下载、来源转写或 HeyGen。

生图使用当前 Agent 环境提供的运行时 ImageGen 工具。它不是装完 Python 包就自动获得的能力；先确认工具可用和额度可用。没有这个工具时，不应暗中切换另一家模型。

最终字幕使用火山/豆包 flash Base64 路线：把精确锁定 WAV 提交，拿回真实词时间戳。按 .env.example 配置 VOLCENGINE_API_KEY 和匹配的资源项，并确认账号已开通相应服务。不要把来源视频的转写当成最终旁白的字幕，也不要为了上传音频另走 TOS 中转。

输入如果是视频，才额外准备下载及 Paraformer 来源转写；输入是 X 或 GitHub，走其专用采集步骤。正文写作与原始来源证据分开保存。

```powershell
uv run python automation/scripts/check_env.py
```

这是只读环境诊断，不会真实调用 Provider，凭据仅报告 SET/UNSET。它覆盖较完整的 V1 配置：未使用来源阶段显示缺失，不等于当前文稿路线必然不能做。让 Agent 根据本次路由区分必需项与非必需项，不为消除所有缺失提示而开通不需要的服务。

## 五、先做一条短片，把链路跑通

在 Agent 中打开本项目目录，让它先完整读取根 AGENTS.md，再从 ra-source-to-video 和制作导演进入流程。当前项目规则优先于旧 Skill 的小黑、数字人和文稿默认值。

可以这样开始：

> 请按 BoomEarth 当前流程，把下面的文稿制作成约 30 秒横版验证视频。文稿由 Astra 直接编写，不调用人话或去 AI 味 Skill；先给我看稿。视觉使用 sponge-host-handdrawn-v1，只替换原小黑角色，保留原生手写中文与完整场景，不要数字人。按项目合同准备配音、真实词时间字幕、渲染与 QC，保留每阶段结果。输入内容：……

这段话是在发起生产任务，不是替代媒体服务授权。按项目外部调用计划核对实际输入与预算；如果调用失败，先看已有回执，避免反复扣费。已经完成的音频、字幕或图片不要无理由重做。

首次选两三个核心意思就够了：例如“先写清楚文稿”“再做配音”“最后对齐画面”。短片能完整通过，再做两分钟教程；一开始就做十分钟，定位问题会更费时间。

## 六、日常制作经过哪些步骤

### 1. 理解内容，锁定文稿

Astra 直接阅读、理解并写作。正文围绕读者需要知道的问题组织，不通过去 AI 味工具二次改造。开头和结尾的协作规则另行确定；已确认稿件保存全文，配音和字幕不能擅自加词删句。

文章不等于口播稿。把“有哪些功能”转成“谁遇到什么问题、这东西怎样帮忙、怎么开始用”，让每一段都推进理解。来源事实仍需核对，不把宣传话术当成实测结果。

### 2. 把核心意思变成画面

按内容与时长决定场景数，不机械规定每几秒必须换一图。每场写清核心意思、角色动作、道具关系和少量原生手写标签，再生成完整图片。

海绵需要锁稿、对齐、搬运或连接物件，动作本身说明关系；不是站在图标旁边。采用[批准的角色参考](assets/sponge/2026-09/character-approved.png)，但不能从设定图裁小块放大铺屏。

生成后检查字有没有写错、原生文字有没有被吞、手脚与道具关系是否成立、画面是否覆盖核心意思。记录实际尺寸与显示倍率：本次样片原图 1672×941，显示 1440×810，没有上采样。

### 3. 锁定声音，再确定时间

本地 TTS 先出候选音频，听读音、停顿和完整性。接受后锁定最终 WAV 及哈希，再进行最终音频 ASR。后面发现稿件必须改时，生成新版本并重新绑定时间轴，不能拿旧字幕凑合。

断句只调整边界，不改文稿；每帧只有一条单行字幕。少量词句可以局部底色强调，但不能改动真实音频时间。

### 4. 合成不是把图片排成 PPT

背景保持稳定，围绕讲解使用组件显现、擦除、淡入与轻微停稳。原生字随相关画面出现，不用后加标签盖住它。图像近白边缘和纯白画布融合；字幕占独立底部安全区，人物与关键标签不遮挡。

当前公开样片验证的是完整插画的组件级显现，并非全身逐帧动作生成。复杂交互需另做分层素材与运动验收，不能把整页缩放宣传为角色会走会跳。

### 5. 检查，然后归档

最终视频要完整解码，检查规格、声音、单行字幕、各场景语义、原生文字和边缘融合。联系表是总览，关键帧用于检查细节。正式生产还需 check_delivery.py 通过；这次公开的 32 秒片明确标为风格审阅样片，不把专项检查冒充完整正式交付验收。

归档保留成片、工程、质检以及必要旁白、字幕和素材。后续修订生成新版本，不覆盖已批准版本。

## 七、进阶命令：知道入口，不跳过准备

日常让总编排调用专业脚本即可。想看命令参数，可以安全执行帮助：

```powershell
uv run python automation/scripts/compile_content_plan.py --help
uv run python automation/scripts/run_content_production.py finalize --help
uv run python automation/scripts/check_delivery.py --help
```

run_content_production.py finalize 是已有完整项目的本地收尾入口，不负责从零生成文稿、图片或音频。它需要活动项目、批准旁白哈希及齐全的内容、时间轴和素材合同，不能拿空目录直接调用。不要为“跑通命令”伪造 QC 或审批标记。

## 八、遇到问题，先查这一层

| 现象 | 先检查 | 不要做什么 |
| --- | --- | --- |
| 配音启动失败 | 模型单独短句测试、解释器、CLI、权重 | 静默换系统朗读 |
| 生图无法调用 | Agent 是否真的有 ImageGen 与额度 | 把安装依赖当作工具授权 |
| 画面发虚 | 原图像素、显示倍率、是否裁剪设定图 | 放大后声称原生高清 |
| 插画没文字或字形违和 | 原生字、embedded 合同、系统覆盖层 | 再贴标签遮掩问题 |
| 背景出现方框 | 近白色、白底校正和边缘融合 | 只检查 PNG、不看成片 |
| 字幕不准 | ASR 输入是否就是最终 WAV | 按字数重新估时间 |
| 制作中断 | 已有阶段回执与真实产物 | 从采集起全部再跑 |
| 缺私人声音 | 配置自己的授权参考音频与证据 | 寻找作者未公开的录音 |

## 九、怎样真正节省时间和费用

先定稿，后生图配音；先做短样片，后扩成长片；失败从具体阶段恢复，已锁定产物不随意重做。素材只有在语义、风格和证据都匹配时才复用，不能为了省一次生成塞入无关图片。

总成本分成：首次环境投入、本地计算、Agent/图像额度、ASR 用量和人工审阅。不开数字人可省去该环节，现成文稿可省去来源采集与转写。历史“约 4 毛、20 分钟”仅适用于特定基础快线估算，不是新插画路线的固定报价。

低门槛，不是隐藏前置条件；而是把前置条件说清，把日常动作固定下来。部署一次之后，让每次创作都集中在内容，而不是反复装工具、找文件和手工对轴。

先跑一条 30 秒短片，确认声音、插画、字幕与合成全部连通，再扩展内容长度与场景复杂度。回到 [README](../README.md) 查看入口，或查看[本次公开素材](assets/sponge/2026-09/)。

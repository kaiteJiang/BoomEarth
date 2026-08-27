# BoomEarth

BoomEarth 是一套面向中文创作者的本地优先视频生产工作台：把一个获授权的
视频、音频、X 图文、GitHub 项目或现成文稿，推进为可播放、可质检、可恢复、
可归档的横版口播视频。

它解决的不是“能不能生成一个 MP4”，而是更难的生产问题：来源如何隔离、
文稿如何去来源化、旁白怎样锁定、字幕怎样真正跟音频同步、每个场景是否有
语义相关素材、失败后能否从证据继续，以及最终交付物能否被机器重新验证。

## 为什么值得用

- **从来源直达成片**：本地媒体、可播放视频 URL、X 图文和 GitHub 项目都有明确入口。
- **本地优先、隐私分层**：来源 URL、逐字稿和 Provider 响应只留在被 Git 忽略的私有工作单。
- **KaterJ Skill 编排**：18 个 `katerj-*` 真源 Skill 负责选题、采集、转写、改写、配音、字幕、画面、运动和 QC；旧调用名继续由兼容桥承接。
- **最终音频决定字幕**：字幕基于锁定旁白的真实词级时间戳，不按字数或场景时长猜测。
- **画面不是字幕背景板**：每个场景必须有与旁白语义匹配的真实素材，并保留 manifest、哈希和 QC 证据。
- **可恢复而不是盲目重跑**：外部调用按不可变计划授权，失败保留证据，恢复从第一个未完成 gate 开始。
- **交付闭环**：成片、旁白、字幕、插画、封面、联系表和 delivery receipt 统一归档。

## 4 毛、20 分钟，指的是什么

在已有本地硬件和模型、使用本地 TTS、关闭 HeyGen、复用已审核基础素材，且
外部服务价格与既有配置不变时，**基础口播快线的新增云端用量可以低至约
0.4 元，约 20 分钟完成一条视频**。这是特定配置下的生产目标和经验值，不是
对所有机器、所有文稿、复杂插画、首次模型安装、网络波动或 Provider 计费的
统一承诺；硬件折旧、电费、订阅费、失败重试和人工审阅时间不包含在 0.4 元内。

## 当前默认成片合同

```text
授权来源
→ 私有采集与哈希锁定
→ 来源转写
→ source-free 中文口播稿
→ 逐场景小黑插画与组件级运动
→ 本地 IndexTTS2 黑金 v3 旁白
→ 最终音频词级 ASR 字幕
→ 可选圆形数字人
→ 1920×1080 / 30fps / H.264 / AAC
→ 联系表、QC、delivery checker、统一归档
```

新项目默认使用 `xiaohei-white-first-v1` 小黑插画风格和 3:4 Punk 作品封面。
数字人是可选层，关闭后不会自动降级到其他头像或 Provider。

## 本地开始

```powershell
uv sync --dev
uv run pytest tests/test_config.py -q
uv run python automation/scripts/check_env.py
```

以上命令不会调用在线 Provider。真实采集、来源转写、ImageGen、最终音频 ASR
和 HeyGen 都必须使用绑定 work id、输入哈希、计划哈希、预算、零重试和零
fallback 的独立批准；一个阶段的批准不会自动授权下一阶段。

## 项目资料

- [开发历史与架构演进](docs/PROJECT-HISTORY.md)
- [开源介绍口播稿](docs/OPEN-SOURCE-STORY.md)
- [KaterJ Skill 迁移说明](.agents/skills/KATERJ-MIGRATION.md)
- [上线就绪状态](docs/LAUNCH-READINESS.md)
- [第三方来源与许可证边界](LICENSE-NOTES.md)

## 许可证

BoomEarth 原创代码与 `katerj-*` Skill 按根目录 [LICENSE](LICENSE) 的 MIT
条款提供。保留在兼容目录中的第三方脚本、字体、示例和素材不因此被重新许可；
它们继续遵循各自许可证及 [LICENSE-NOTES.md](LICENSE-NOTES.md)。

---

下面保留从 V1 到当前生产基线的详细技术记录。历史章节中的旧 Skill 名称仍然
可以调用，但新开发应以 `katerj-*` 真源为准。

## 技术参考：V1 目标与流程

V1 面向自有或获授权内容，完成一条本地优先的视频二创流程。

```text
授权本地媒体 / 用户批准 URL
→ 私有转写
→ 无来源交接稿
→ 本地 IndexTTS2
→ 对最终 WAV 做 ASR，生成词级字幕
→ HyperFrames
→ QC 归档
```

每个项目按以下顺序推进

1. 确认本地媒体或 URL 的处理授权。
2. 在私有区域完成转写和内容清洗。
3. 生成不带来源信息的公开交接稿。
4. 使用本地 IndexTTS2 生成配音，并合成为最终 WAV。
5. 以最终 WAV 重新进行 ASR，使用真实词级时间戳生成字幕。
6. 交给 HyperFrames 合成视频，完成 QC 后归档成片、音频和字幕。

编写配音分段时，每段尾部静音最多 60 秒；无效值会失败，不会被自动截断。

## 版权与来源权利

仅处理自有、获授权或合法可用的素材。无法确认权利的素材不得进入 V1 流程。

## 隐私边界

源 URL、源标题、源逐字稿只允许进入
`01-内容生产/视频工作台/.internal/洗稿`。

上述三类信息不得进入公开交接稿，也不得复制到 README、日志或测试中。公开交接稿只保留后续配音、字幕、成片和 QC 所需的无来源内容。

## 状态流

```text
待制作 → 制作中 → 已制作
```

- `待制作` 代表授权已确认，私有转写和交接稿准备条件满足。
- `制作中` 代表 IndexTTS2、最终 WAV、ASR 词级字幕、HyperFrames 或 QC 尚未全部完成。
- `已制作` 代表最终 WAV、词级字幕、成片已完成 QC，并已归档。

## V1 边界

- V1 不含 HeyGen。未来如接入 HeyGen，另走 CLI OAuth 流程，凭据不进入 V1 契约。
- 采集优先级为授权本地文件、TikHub、yt-dlp。yt-dlp 只处理用户批准且有权访问的 URL。TikHub 媒体下载只接受 HTTPS，不跟随重定向，不向媒体主机发送 TikHub 凭据，单个响应体上限固定为 2 GiB（`2 * 1024 * 1024 * 1024` 字节）。
- Qushuiyin 当前不属于 V1 依赖，仅作为未来可插拔的故障备用。不添加 Qushuiyin 的 key 或 config。

## P2 授权来源到生产交接

### 来源比例与生产比例

来源媒体只提供内容，不决定新视频画布。本地文件或经批准取得的平台
媒体可以是横屏、竖屏、方形或其他受支持画幅；来源几何信息只能留在
`.internal` 私有审计层。所有新 rewrite brief、无来源交接稿、内容计划
和最终视频统一使用 `16:9 / 1920x1080 / 30fps`。生产端不会自动继承
来源比例，也不会把来源视频直接拉伸为横版画面。

如果一个已发布的 legacy 交接稿需要在进入 P1 前修正为横版，使用以下
两个纯本地命令。它们不调用 Provider：

```powershell
uv run python automation/scripts/apply_production_policy.py apply <work-id> --active-project <project-slug> --workspace-root C:\BoomEarth
uv run python automation/scripts/apply_production_policy.py verify <work-id> --active-project <project-slug> --workspace-root C:\BoomEarth
```

修正保留原 publication receipt，通过独立的
`production-policy-receipt.json` 绑定修改前后交接稿哈希，并把 ledger
推进到 `production_started`。命令不重做来源转写、插图或旁白。

P2 已提供本地文件、yt-dlp URL、TikHub URL 三种入口，它们汇入同一个私有来源契约。默认推荐先用 yt-dlp 验收真实 URL；TikHub 是需要单独批准的可选 provider，Qushuiyin 不是依赖。URL 必须放在私有输入文件中，不能作为命令行参数。

```powershell
# 本地文件入口（输出 work_id）
uv run python automation/scripts/source_intake.py local "<ABSOLUTE_MEDIA_PATH>" --authorized

# URL 入口（输出 work_id）
uv run python automation/scripts/source_intake.py url --input-file "<PRIVATE_URL_FILE>" --authorized

# URL 入口才需要：先计划，再用与计划完全匹配的批准凭据运行一次
uv run python automation/scripts/acquire_source.py plan <work_id> --provider yt-dlp
uv run python automation/scripts/acquire_source.py run <work_id> --provider yt-dlp --approval "<PRIVATE_ACQUISITION_APPROVAL_JSON>"

# 三种入口从这里开始共用同一流程
uv run python automation/scripts/normalize_source_audio.py <work_id>
uv run python automation/scripts/transcribe_source.py plan <work_id>
uv run python automation/scripts/transcribe_source.py run <work_id> --approval "<PRIVATE_TRANSCRIPTION_APPROVAL_JSON>"
uv run python automation/scripts/prepare_rewrite.py <work_id> --platform douyin --ratio 9:16 --duration-target-s 60 --archive-slug "<SAFE_SLUG>"
uv run python automation/scripts/compile_source_handoff.py compile <work_id> --candidate "<PRIVATE_REWRITE_CANDIDATE_JSON>" --review "<PRIVATE_REWRITE_REVIEW_JSON>"
```

两类 `run` 都必须由操作者先检查对应私有 plan，再创建绑定 work_id、provider、输入哈希和 plan 哈希的批准 JSON。失败不重试、不自动切换 provider。来源转写会上传计划锁定的 WAV 到阿里云 DashScope Paraformer，可能产生额度或费用；默认测试不联网。

改写候选和审阅结果必须分别保存为私有 `rewrite-candidate.json` 与 `rewrite-review.json`，并通过 `ra-洗稿`、`ra-人话`、`dbs-ai-check`、`dbs-hook`、`dbs-resonate`、`ra-video-title` 六项审阅。不得直接写入 `待制作`；只有编译器能发布无来源交接稿。`publication-receipt.json` 与 ledger 的 `handoff_ready` 同时成立，才代表 P2 已安全交给 P1。

当前完成度是“离线三入口汇流与合同测试通过后可进入真实 URL 验收”，不等于真实 yt-dlp/TikHub 下载、真实 Paraformer 上传或整条生产成片已经在 P2 本轮运行。

### GitHub Skill URL 解读入口

项目级 `ra-github-skill-import` 支持把用户授权的公开 GitHub Skill 仓库、目录或 `SKILL.md` URL 采集为私有、哈希锁定的 Markdown 阅读快照，再复用现有 `ra-洗稿` 和无来源交接流程。它不会安装 Skill、执行仓库代码或把仓库来源信息写进公开交接稿。

该入口采用独立的 `github-skill` 状态线：

```text
source_registered → github_skill_planned → github_skill_ready → rewrite_ready → handoff_ready
```

默认测试不访问 GitHub。真实采集前必须单独审核输入哈希、计划哈希、48 次请求上限、允许主机以及不重试／不重定向／不降级约束；采集授权不等于 TTS、生图、ASR、HeyGen 或视频生成授权。

## 密钥与 API 安全

- 密钥只允许存在于被 Git 忽略的根 `.env` 或进程环境。
- 禁止把密钥写入日志、测试或 Markdown，也不得在诊断输出中显示密钥内容。
- API smoke 只允许显式 opt-in，默认测试和环境检查不调用 live API。

## 安全命令

下面的 `example.invalid`、标题和逐字稿均为虚构占位符，不代表真实来源。

```powershell
uv sync --dev
uv run pytest tests/test_config.py -q
uv run python automation/scripts/check_env.py
uv run python automation/scripts/wash_ledger.py check https://example.invalid/item
uv run python automation/scripts/wash_ledger.py add https://example.invalid/item
```

`check` 和 `add` 的输出格式为 `id=<12位前缀> status=present|missing|added|duplicate`，不得显示完整哈希、源 URL、源标题、源逐字稿或密钥。

## 离线交付检查与测试

以下命令不调用 live API；测试只使用合成 fixture，不读取或生成真实配音。

```powershell
uv run pytest -q
uv run python automation/scripts/check_env.py
uv run python automation/scripts/check_delivery.py <交接稿.md> <项目目录> <成片.mp4> --sample-mode
```

`check_delivery.py` 的退出码固定为：`0` 通过、`2` 交接契约或 QC 不通过、`3` 缺少或不安全的产物、`4` 公开交接稿触发隐私边界。它只输出规则名和安全相对产物标签；不会输出来源内容、URL、绝对私有路径或凭据。`delivery-report.json` 仅在所有硬门槛通过后最后写入。

## 本地 10 秒样片（不调用 API）

样片只接受一份由操作者明确授权的、已经存在的本地 IndexTTS2 WAV。它不采集、不转写、不合成 TTS、不做 live ASR，也没有 HeyGen 路径；样片字幕标记为 `synthetic-local-sample`，绝不称为 ASR。

```powershell
uv run python automation/scripts/run_v1_sample.py `
  --source-wav <authorized-local-wav> `
  --audio-approved `
  --workspace-root C:\BoomEarth `
  --archive-slug <safe-lowercase-slug>
```

`--audio-approved` 是渲染和归档前的强制人工门槛；没有该参数时命令在启动任何子进程或创建归档前退出。通过后，样片会使用 Task 8 的本地 HyperFrames 准备／检查／渲染命令，生成联系表，先运行交付检查，再以无覆盖方式归档到 `01-内容生产/视频工作台/已制作/<月上旬|月下旬>/<date-slug>/`。归档根目录不写 `视频标题.md`。

每次运行会在 `<workspace>/runtime/v1-sample-<uuid>/` 创建并在结束时删除隔离副本；不会读取、覆盖或复用共享 `video-sample/media` 或 `video-sample/renders`。归档前，活动工程使用 `工程/media` 保存六件字幕工件（含显式 synthetic ASR 标记）、最终联系表与语音 provenance；通过后交接稿状态才变为 `已完成`，并将完整的日期-slug 工程目录原子移动到归档叶目录。

## V2 生产验收工程（本地初始化与显式 ASR 门）

V2 不是 V1 合成样片的替代品。它只接收已经由本地 IndexTTS2 锁定的最终 `narration.wav`、相邻的 `voice_manifest.json`，以及从这份**同一 WAV**生成的六件 production 字幕工件。`caption-qc.json` 必须在同一字幕事务中记录该 WAV 的 `narration_sha256`、`volcengine-word-timestamps` 和不少于 `0.90` 的对齐覆盖率。

下面命令只创建 dated active-project 契约；不调用 TTS、ASR、上传、网络或 API，也不会生成 synthetic-local-sample 工件：

```powershell
uv run python automation/scripts/run_v2_production_sample.py `
  init `
  --workspace-root C:\BoomEarth `
  --archive-slug <safe-lowercase-slug>

uv run pytest tests/test_v2_production_orchestration.py tests/test_caption_qc.py -q
```

只有在操作者明确审核了最终 WAV、允许上传该**精确路径**并确认可能的火山／豆包 ASR 配额消耗后，才可运行下列联网命令。两个授权开关缺一不可；它不是默认测试、初始化或渲染的一部分：

```powershell
# 会上传音频并可能消耗 Volcengine/Doubao ASR 配额；显式人工授权后才运行
uv run python automation/scripts/run_live_smokes.py run `
  --services volcengine `
  --volcengine-audio <task-6-locked-final-wav> `
  --authorized-network `
  --authorized-audio-upload
```

ASR 成功后，使用 canonical 字幕流程把六件工件直接写入活动工程的 `工程/media/captions/`；不要事后重写 `caption-qc.json`。最终编排必须同时传入操作者审核时记录的 `approved_narration_sha256`；当前 WAV 任一字节变化都会使审核失效。渲染准备目录位于活动工程的 `工程/render-project`，其中 Task 1 快照的 `media/narration.wav` 是唯一可供渲染/mux 的音频来源；它随工程归档，绝不作为可递归删除的 runtime。HyperFrames 通过受信任的 `node.exe` 与其原生 `.mjs` 入口以参数数组运行，不执行 `.cmd` 包装器。

六件 production 字幕工件的规范目录是活动工程的 `工程/media/captions/`。已有 ASR 原始结果可以通过 `ra-audio-to-subtitles` 的 `--asr-result` 路径离线重建；该路径不调用服务商，但仍会把 `caption-qc.json` 绑定到父目录中 `narration.wav` 的精确 SHA-256。不要在 `工程/media/` 根目录保留第二套 production 字幕工件。

旁白与六件字幕工件审核通过后，使用下面的纯本地命令渲染、质检并归档既有活动工程。`--active-project` 只接受 `YYYY-MM-DD-safe-slug` 工程名，不接受绝对路径；命令不会调用 TTS、ASR 或其他 provider：

```powershell
uv run python automation/scripts/run_v2_production_sample.py `
  finalize `
  --workspace-root C:\BoomEarth `
  --active-project <YYYY-MM-DD-safe-slug> `
  --audio-approved `
  --approved-narration-sha256 <approved-final-wav-sha256>
```

`caption-render-qc.json` 由最终 MP4 在规范字幕 cue 中点提取的真实 RGB 帧生成，检查底部 `anchor-dark` safe zone 的可见像素，并绑定最终视频、`captions.json`、每帧摘要和重新计算的 bright/dark 像素计数；production delivery checker 会自行重提帧并拒绝哈希正确但无可见字幕的画面。`finalize_production_sample(..., audio_approved=True, approved_narration_sha256=<approved-digest>)` 只在 production delivery checker 以 `sample_mode=False` 通过后发布：每个受保护工件（旁白、voice manifest、六字幕工件、成片、联系表、两份 QC、交接稿及 checker receipt）都进入 publication manifest，receipt 还必须绑定该精确快照。Windows 上 active 创建和两次 archive rename 都相对于已打开、经 volume/file identity 比对且不跟随 reparse point 的目录句柄执行；final source manifest 通过已打开的 stage 句柄验证。最终 rename 前 staging 始终为 `制作中`，只有最终树和 manifest 已验证后才写入完成回执；失败会保留确定的未完成恢复树并清除 pass receipt。

## P1 内容驱动生产通道（纯本地执行）

P1 接收已经人工审核的无来源交接稿、最终旁白与生产字幕，以及四个或更多本地插图。TTS、ASR 和插图生成是三个独立的上游审核阶段；下面三条命令都不会调用它们，也不会读取 provider 凭据。渲染器不会生成插图，只会校验、快照并使用 `工程/assets/xiaohei-illustrations/` 中已审核的本地图片。

第一步：把 `工程/content-plan.candidate.json` 作为人工审核后的候选输入，编译为不可覆盖的正式内容计划。纯本地/不调用 provider。

```powershell
uv run python automation/scripts/compile_content_plan.py --workspace-root C:\BoomEarth --active-project <dated-project> --candidate 工程/content-plan.candidate.json
```

第二步：从最终旁白的词级时间戳生成逐场景时间线。纯本地/不调用 provider。

```powershell
uv run python automation/scripts/build_scene_timeline.py --workspace-root C:\BoomEarth --active-project <dated-project>
```

第三步：渲染、逐场景取帧质检、运行交付检查并原子归档。纯本地/不调用 provider。必须显式提供 `--audio-approved`，并让 `--approved-narration-sha256` 等于人工确认的最终旁白 SHA-256；缺少批准或 WAV 任一字节变化都会在渲染前失败关闭。

```powershell
uv run python automation/scripts/run_content_production.py finalize --workspace-root C:\BoomEarth --active-project <dated-project> --audio-approved --approved-narration-sha256 <approved-hash>
```

三个发布点都采用 no-clobber（不覆盖）语义：正式内容计划、场景时间线、活动渲染目录或目标归档一旦已存在，命令不会替换旧文件或旧目录。失败只输出固定规则名，活动工程保留为 `制作中` 供检查或恢复；成功时只输出工作区相对归档路径。最终归档包含内容计划、场景时间线、所有插图、每场景三张预览、全场景中点联系表、字幕画面 QC、交付回执和 publication manifest，并可再次用 production delivery checker 做只读历史复验。

## 显式 opt-in 的联网 smoke 命令

下面命令不是默认测试的一部分。只在已确认授权、额度和费用后由操作者手动运行；它们可能访问外部服务或消耗配额。

```powershell
# 联网账户状态检查，不输出账户详情
uv run python automation/scripts/api_smoke.py tikhub-account

# 可能上传音频并产生 DashScope 费用；必须同时给出授权确认
uv run python automation/scripts/api_smoke.py paraformer --audio <authorized-local-audio> --authorized

# 可能消耗火山 / 豆包 ASR 配额；只接受 Task 6 已锁定的最终 WAV
uv run python automation/scripts/api_smoke.py volcengine --audio <task-6-locked-final-wav>
```

这些命令的 `status` 仅使用稳定值：`OK`、`AUTHORIZATION_REQUIRED`、`CONFIG_ERROR`、`INPUT_ERROR`、`LOCAL_CONTRACT_ERROR`、`NETWORK_ERROR`、`SERVICE_ERROR`、`TIMEOUT` 或 `INTERNAL_ERROR`。TikHub 同时保留安全的 `http_status`、`account_active` 和 `quota_present` 字段；Paraformer 和 Volcengine 保留各自已有的安全汇总字段及白名单 request ID。诊断绝不输出异常文本、服务响应体或头、逐字稿、账户详情、凭据、私有绝对路径、音频哈希或来源信息。

## 联合 live-smoke 预检与执行门槛

`run_live_smokes.py` 用于把多个已选 smoke 串成一个显式的人工门槛。`plan` 只读取已选服务对应凭据的 `SET`／`UNSET` 红色化状态，并检查本地输入／Task 6 WAV provenance；它不会构造 provider client 或 smoke runner，不会发起 DNS、HTTP、上传音频、真实 TTS 或任何真实请求。若凭据状态缺失或不是这两个固定值，预检会失败关闭。

```powershell
# 只做本地预检；service 必须显式给出，且会按 TikHub、Paraformer、Volcengine 的固定顺序处理
uv run python automation/scripts/run_live_smokes.py plan `
  --services tikhub paraformer `
  --paraformer-audio <approved-absolute-audio-path>

# 仅在已逐项确认联网权限、音频上传权限、服务范围、可能额度／费用和精确音频路径后执行
uv run python automation/scripts/run_live_smokes.py run `
  --services paraformer volcengine `
  --paraformer-audio <approved-absolute-audio-path> `
  --volcengine-audio <task-6-locked-final-wav> `
  --authorized-network `
  --authorized-audio-upload
```

`--services` 是必填参数；未知、重复或缺失的服务都会作为命令行错误失败。TikHub 只需要 `--authorized-network`。只要选择 Paraformer 或 Volcengine，还必须有 `--authorized-audio-upload`；Paraformer 必须提供明确批准的绝对音频路径，Volcengine 必须提供带相邻 `voice_manifest.json` 的 Task 6 已锁定最终 WAV。`--workspace-root` 必须是既存的绝对安全目录；其祖先及 `runtime/live-smokes` 不能是符号链接、junction 或 reparse point。相对路径、路径穿越、符号链接／junction／reparse ancestor、非普通文件和缺失文件均会在 runner 创建前失败。工作区外的绝对音频路径只在它是普通文件且每个现存祖先都安全时才允许。

选中的服务始终以 TikHub → Paraformer → Volcengine 的顺序执行。每次音频服务调用前都会再次检查普通文件／安全祖先，Volcengine 还会再次运行相邻 manifest 的 canonical probe；这是针对预检后文件变更的 fail-closed 缓解措施，不提供文件句柄级身份保证。第一项失败后，后续已选项标记为 `SKIPPED`，报告中的 `fail_closed_early` 为 `true`，不会继续调用 runner，也没有重试、continue-on-error 或隐式服务选择。

每次 `plan` 或 `run` 都会以无覆盖方式在被 Git 忽略的 `runtime/live-smokes/` 写入固定 schema 的 JSON 报告；文件名采用 UTC 时间和 UUID。报告只保存稳定的服务名、结果、状态、安全 request ID、实际网络尝试布尔值、fail-closed 状态，以及已选凭据名称的 `SET`／`UNSET` 状态。它不保存原始 stdout/stderr、异常文本、响应体／头、逐字稿、凭据值、来源 URL／标题／文本、音频哈希或私有音频绝对路径。默认适配器在有界内存中截获既有 smoke 的 stdout/stderr，严格匹配服务专属键集合；stderr、超限或任何未知／重复／缺失／畸形字段都会变为 `INTERNAL_ERROR`，原始缓冲永不转发或存储。`OK`、`NETWORK_ERROR`、`SERVICE_ERROR`、`TIMEOUT` 被保守视为已尝试联网；本地设置／输入／授权类失败不会，畸形或内部错误同样按未尝试处理。若报告文件无法安全独占创建，`run` 不会调用 runner；最终本地写入失败也不会重试任何云调用，并会输出保留已知服务结果的 `REPORT_ERROR` 回退报告。

退出码固定为：`0` 表示 `plan` 预检 READY，或 `run` 中全部已选服务 PASS；`1` 表示至少一个已选服务执行失败；`2` 表示命令行、授权、预检或本地报告失败。

TASK15 的实现和默认测试在第一个真实请求之前停止。不得把这两个命令当作自动化或默认测试的一部分；在人工确认节点 A 明确批准前，不运行 `run`。

联合 smoke 的本地配置读取只检查已选服务对应的凭据是否为 `SET`／`UNSET`，不会因为未选择的服务缺少凭据而阻断 `plan`。每个受选音频及 Volcengine 相邻 manifest 会在预检和实际调用前以非哈希 `lstat` 身份（设备/文件索引可用值、大小、mtime、模式）比对；替换会失败关闭。报告目录也在独占创建前后比较目录身份。这些是 V1 的竞态检测措施，不是 Win32 handle-relative 创建或上传句柄级身份保证。

适配器还要求既有 smoke 的退出码与稳定 status 精确对应：`0` 仅 `OK`，`1` 仅本地契约／联网／服务／超时／内部失败，`2` 仅授权／配置／输入失败；boolean、负数及其他组合均失败关闭。已选服务的非敏感默认配置也在本地检查，且不写入报告。

测试注入的 settings loader、probe 与 factory 是受信任的本地 test seam；生产默认 CLI 不会转发这些 callback 的输出。默认 API adapter 仍有界截获并丢弃既有 smoke stdout/stderr。

未来如需 HeyGen，必须另行建立显式 OAuth/计费和人工批准流程；HeyGen 凭据、调用和数字人路径都不属于 V1 交接契约或默认命令。

##特此感谢
 项目能快速成型 借鉴了卡神一些X上的文章思路，以及 雪踏乌云大佬 苍何大佬 等一众大佬开源SKILL上的灵感。再次感叹 AI时代站在巨人的肩膀上有了真正身临其境的体验。

# 当前视频制作入口与接手手册

本页是新对话的最小启动顺序与阶段索引，不是新的渲染架构。当前版本：`sponge-host-handdrawn-v1`，正式流程沿用现有总编排、专业 Skill 和交付脚本。根 AGENTS.md 与已批准项目合同优先。

来源失败自修复、配音期间并行工作与 30 分钟检查、字幕标点的统一规则见 [连续制作编排](VIDEO-CONTINUOUS-ORCHESTRATION.md)。新对话必须与本页一同读取；修复经验从 [来源修复账本](SOURCE-RECOVERY-LEDGER.md) 选最近完整验收的路径。

## 新对话从这里开始

在 BoomEarth 项目目录打开新对话。先读根 AGENTS.md，然后完整读取 `.agents/skills/ra-source-to-video/SKILL.md` 和本页链接的连续制作编排。收到已批准稿或已有项目时继续加载 `katerj-video-director` 及它的 routing、delivery-gates 引用；只读本次来源/阶段需要的其他 Skill，不全量重读开发历史。

可直接发送：

> 按 BoomEarth 当前海绵版完整制作流程处理以下内容：……。先核查是否已有项目和可复用产物，再从第一个未完成阶段继续。

继续已有项目时：

> 继续这个项目：〈制作中或待制作项目路径〉。核对 production note、已批准文稿、最终音频、字幕及素材回执，从第一个未完成阶段继续，不重新生成已验收资产。

这些话不替代实际素材、阶段回执或外部服务授权；新对话不能把另一个项目的批准搬过来。用户只要审稿时停在稿件，不自行启动媒体制作。

## 当前默认，一次读清

- 文稿由 Astra 直接理解输入并写作，默认不调用人话或去 AI 味 Skill；用户当次明确指定的写作 Skill 优先。正文自由组织，事实核对和逻辑检查保留。新视频提交审阅前调用 `C:/Users/1/.codex/skills/jl-video-intro-outro/SKILL.md`，按其中唯一模板加入固定开头和结尾，仅替换当期主题；已定稿历史项目不追补。
- 已批准全文保存、绑定哈希。配音、字幕断句不能擅自改词；发现内容错误要形成修订版本，不覆盖旧定稿。
- 海绵只替换小黑角色 DNA，保留原版手绘语言、原生短中文、白底融合、布局节奏与组件动作；角色真实参与场景。参考 [角色合同](../video-daheihuang/sponge-ip/DESIGN.md) 和 [当前样片](sponge-fullscene-sample.md)。
- 每场完整生图，记录实际尺寸，不裁设定图放大、不以插值冒充高清。根据正文核心意思和真实音频时长安排场景，不固定图数。
- 新海绵正式prompt使用 target_size: native-source，至少1440×810、16:9容差0.01，prepare/complete和manifest保留原图字节。当前原生分支禁止visual-frame缩放/位移，保留透明度动作；更细组件分层需要单独设计与验收，不能把本轮接通合同说成全身动作库已完成。跨项目native-source复用入口尚未开放；同项目证据有效可直接续做。
- 本地 IndexTTS2 黑金 v3、1.12 倍音速、canonical 私有无损参考；无静默降级。最终 WAV 是唯一时间轴。
- 字幕先真实词时间，后按 `jl-oral-linebreaks` 闭合断句和 anchor-dark 渲染；严格单行。句中逗号用空格，行尾只保留问号，具体见连续制作编排。可在六件套完成后用哈希绑定的 `工程/caption-emphasis.json` 只选重要词或核心判断做局部底色，多数句子不标注；不改 `captions.json` 的字词和时间，渲染器按每句实际字宽收紧外框。
- 默认无 HeyGen，不加载默认头像、不登录、不刷新外观组；用户明确启用或旧合同绑定时才走该分支。
- 视频仍 1920×1080 / 30fps / H.264/AAC。视频作品封面默认沿用已注册 3:4 Punk 合同；文章的 5:2 海绵配图不是视频封面默认值。
- 历史大画面、JKL、裁角色图集、程序拼四肢方案不是当前生产入口。样片专项 PASS 不代替正式交付检查。

## 阶段与对应 Skill

| 阶段 | 入口与职责 | 完成证据 / 下一步 |
| --- | --- | --- |
| 接收/恢复 | ra-source-to-video | 确定新建或续做、精确来源类型、当前项目合同 |
| 视频/音频来源 | katerj-video-wash → katerj-source-acquisition → katerj-source-transcription | 私有采集/媒体校验/Paraformer 来源逐字稿 |
| X 图文来源 | ra-x-article-import | 私有正文快照；X 原生视频走视频路线 |
| GitHub 项目/Skill | ra-github-skill-import | 只读私有快照，不安装 Skill、不执行仓库代码 |
| 文稿与交接 | Astra 直接写；katerj-script-rewrite 只管来源整理、审阅证据和编译 | 定稿与哈希；来源任务经 compile_source_handoff.py 发布无来源交接稿 |
| 制作导演 | katerj-video-director | production note、content-plan、逐场景意图、素材与运动计划 |
| 海绵插画 | ra-video-illustrations；继承 katerj-xiaohei-illustrations 本体与 DESIGN 角色 | prepare → ImageGen → complete 证据、原生文字/身份/语义 QC、manifest |
| 运动与节拍 | katerj-motion-director | 最终音频上的场景与组件 cue；稳定底板，不整页漂移 |
| 口播边界 | jl-oral-linebreaks；项目内 katerj-oral-linebreaks 保持同一合同 | 批准稿只调整断句；不改词、不估算时间 |
| 配音 | katerj-local-tts | 最终 WAV、segments.jsonl、voice_manifest.json 与一致哈希 |
| 字幕时间 | katerj-audio-subtitles → katerj-oral-linebreaks | 精确 WAV 的 flash Base64 ASR、字幕六件套、caption-qc PASS |
| 字幕样式 | katerj-caption-rendering | anchor-dark、单行与安全区、caption-render QC |
| 可选人物 | 官方 heygen-video，仅显式启用 | 同一最终 WAV、批准 master、圆形合成；不计场景素材 |
| 合成/验收/归档 | katerj-video-director；需要参照复刻时 katerj-replica-qc | run_content_production.py finalize、逐场景 QC、check_delivery.py、统一归档 |
| 封面/标题 | ra-video-cover；katerj-video-titles 按项目类型 | 独立封面与 QC；队列标题候选留在交接稿，不创建视频标题.md |

以上名称均为 `.agents/skills/<name>/SKILL.md`（官方 heygen-video 除外）。历史 ra-/rn-/tts-skill/skill-captions 别名只作为兼容桥，进入后加载 canonical Skill，不复制一套旧流程。

## 新稿的来源编译合同

新来源稿保持现有 candidate schema 1（无强制 opening_contract），正文由 Astra 撰写。新审阅使用 schema 3 的中性检查项：facts、logic、source_free、script_integrity。检查是真实的事实、逻辑、公开边界和全文一致性判断，不是把旧 Skill 名称填 pass。

旧 schema 1/2 的 candidate/review 继续供旧项目验证；已有 opening_contract 不重新套模板或删字段。源内容、来源分析、Provider 响应始终留在 `视频工作台/.internal/洗稿/<work_id>/`。编译器负责公开边界，不能绕过失败手写来源交接稿。用户直接给稿、没有外部来源时由导演接管，不凭空创建采集或转写步骤。

## 每个项目都留下可接手状态

复用项目现有 production note，增加“接手状态”小节，不新建第二套状态机。字段至少包括：

- 当前阶段、最后一个通过的 gate、唯一下一步；
- 已批准文稿版本及 SHA-256，最终 WAV 与脚本合同 SHA-256；
- 已通过的字幕目录、素材 manifest、运动计划、最后有效 QC 的项目相对位置；
- 哪些产物已验证可复用，哪些只是候选；
- 外部计划/回执在私有工作单的位置提示、剩余预算和授权范围（不得在公开 note 写源 URL、签名或私有 ID）；
- 当前失败原因和保留的旧版本，不把“进程已启动”当成阶段完成。

新对话先验证文件和哈希，再决定下一步。note 是索引，不胜过文件事实与正式回执。

## 续做决策

| 现有证据 | 下一步 |
| --- | --- |
| 正式归档与 delivery PASS | 只读复验并交付链接，不重渲染 |
| 最终 WAV + manifest + 字幕六件套/QC 均匹配 | 复用音频字幕，从素材/渲染未完成处继续 |
| 图片有合规生成证据、哈希与语义 QC | 复用；不因对话切换重新生图 |
| 只有 PNG 或样片 QC，缺正式生成证据 | 先核查原证据是否可恢复；不补造历史 prepare/complete 回执 |
| Provider 已完成但缺本地文件 | 按对应恢复 gate 读取/取回，不再生成 |
| 只有计划、无成功产物 | 仍是未完成；按实际授权执行，不标 PASS |

失败诊断、哈希、规划、渲染等本地工作持续推进；真实外部阶段按已绑定授权和预算执行。当前任务未授权的新费用/新 Provider 不能用“默认流程”代替授权。

## 交付边界

活动项目在 `01-内容生产/视频工作台/制作中/<日期-主题>/`，最终归档在 `已制作/<X月上旬或X月下旬>/<日期-主题>/`。视频进成片，关键帧/联系表进质检，必要工程进工程。

`run_content_production.py finalize` 是准备齐全项目的收尾入口，不是从零媒体生成器。先检查其 --help 和必需合同。交付必须完整解码、规格通过、旁白 provenance 和字幕时序正确、每场语义图与文字可见、运动和安全区通过、check_delivery 通过。

最终回报：MP4、最终旁白、字幕/SRT、插画目录、联系表、关键 QC、delivery 回执与归档根目录；如启用人物按私有边界报告，不公开 Provider master。封面单独报告状态。实际未通过的环节明确指出，不称“推荐最终成片”。

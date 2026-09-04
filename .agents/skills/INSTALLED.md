# BoomEarth V1 project skills

## Provenance

- Canonical repository: `https://github.com/Pluviobyte/rnskill.git`
- Pinned commit: `766e4eba4162630c783b531f531a3fafe6f45c81`
- Observed remote `HEAD` during installation: `766e4eba4162630c783b531f531a3fafe6f45c81`
- Observed HEAD difference: none
- Install date: `2026-08-09`
- Target: `.agents/skills/`
- Method: shallow source checkout inside the ignored plan SDD workspace, detached checkout of the full pinned SHA, then manual copy of only the 18 names below. No global install, no `npx skills add -g`, no `--all`, and no `.git` directory was copied.

The temporary source checkout was kept under `.superpowers/sdd/2026-08-09-boomearth-v1-implementation/`, which is ignored by the existing root `.gitignore`. It is not part of this commit.

## Exact upstream-installed names

Exactly these 18 project-level skill directories are installed under `.agents/skills/`:

1. `ra-选题`
2. `ra-hook`
3. `ra-video-wash-pipeline`
4. `ra-video-download`
5. `ra-逐字稿提取skill`
6. `ra-洗稿`
7. `ra-人话`
8. `ra-video-title`
9. `dbs-ai-check`
10. `dbs-hook`
11. `dbs-resonate`
12. `ra-video-production-director`
13. `tts-skill`
14. `ra-audio-to-subtitles`
15. `skill-captions`
16. `rn-motion-director`
17. `rn-replica-qc`
18. `ian-xiaohei-illustrations`

## Project-authored workflow Skills

- `ra-x-article-import`: BoomEarth 私有 X Article 采集路由。
- `ra-github-skill-import`: BoomEarth 私有 GitHub Skill URL 解读与洗稿路由；离线合同已实现，目标仓库一次获批真实采集状态为 `REAL_ACCEPTANCE_PASS`。
- `katerj-oral-linebreaks`: BoomEarth 自有中文口播与单行字幕闭合断句规则，提供 14 字硬复核和本地检查器。
- `katerj-xiaohuang-illustrations`: BoomEarth 自有小黄温度插画合同，注册 `xiaohuang-warm-first-v1`，锁定角色身份、原生手写中文与主题 QC。

这些目录由 BoomEarth 独立维护，不计入上面的 18 个固定上游安装项，也不改变其历史字节一致性口径。

## Original installation audit snapshot (2026-08-09)

The following counts and parity results describe the pinned-source installation
audit performed on 2026-08-09. They are a historical point-in-time snapshot,
not a claim that the currently customized tree remains byte-identical to that
snapshot.

- Pre-install audit: `RED`; expected 18, observed root skill directories 0, missing expected directories 18, extra directories 0.
- Post-install root audit: expected directories 18, observed directories 18, extra directories 0.
- `SKILL.md` audit: 18 of 18 present; each frontmatter `name` matches its directory name.
- Relative resource audit: `references` 24 files, `scripts` 13 files, `assets` 97 files; missing relative resources 0.
- Pinned source correspondence: source files `169`, corresponding target files `169`.
- Pinned source byte parity: `168` byte-identical files, `1` declared mismatch, and `0` undeclared mismatches using deterministic relative file lists and SHA-256 plus byte length comparison.
- Declared local mismatch at the time of that audit: `.agents/skills/ra-逐字稿提取skill/scripts/transcript.py` differed from the pinned source skill file because BoomEarth hardened the `--doctor` credential status helper to return only `SET` or `UNSET`; it no longer exposed credential fragments. The canonical upstream commit remained pinned, and this was the one declared local security patch in the 2026-08-09 snapshot.
- `INSTALLED.md` is documentation and is not counted as a skill directory.

At the close of the 2026-08-09 installation audit, no other upstream
`SKILL.md`, reference, script, configuration, or asset was edited. That is a
historical installation result, not a statement about the current tree.

## Current intentional local customizations

The current tree has not been rerun through the original full deterministic
pinned-source file-list, SHA-256, and byte-length comparison. Therefore this
document makes no current byte-parity or exhaustive-difference claim.

Known intentional local divergences from pinned source are:

- `.agents/skills/ra-逐字稿提取skill/scripts/transcript.py`: the existing
  redacted `--doctor` credential-status patch remains applicable and returns
  only `SET` or `UNSET` rather than credential fragments.
- `.agents/skills/tts-skill/SKILL.md`: Task 4 local IndexTTS2 v2 routing,
  canonical private-reference, Windows invocation, and no-fallback rules.
- `.agents/skills/ra-洗稿/SKILL.md`: Task 4 new-production handoff voice v2.
- `.agents/skills/ra-video-wash-pipeline/SKILL.md`: Task 4 new-production
  handoff voice v2.
- `.agents/skills/ra-video-production-director/SKILL.md`: Task 4 local voice v2,
  Windows runtime, historical-v1 preservation, and no-fallback rules.
- `.agents/skills/ra-video-production-director/references/delivery-gates.md`:
  Task 4 voice-v2 provenance and strict local-only delivery gates.

This list records known intentional customizations established by repository
history. It does not substitute for, or imply the result of, a fresh full pin
comparison. No substitute integration file was invented. When an integration
is mentioned below but is absent or not verified, it is recorded as deferred.

## Integration matrix

Terminology and V1 boundary lock: the only ASR provider label used for final word-timestamp subtitles is **火山引擎豆包语音 ASR**. **火山 ASR/豆包语音 ASR 是同一项集成，不拆成两个 Provider**. Source transcription uses **阿里云 DashScope Paraformer** as an independent service. Source acquisition uses an authorized local file, TikHub, or yt-dlp. Qushuiyin is only an upstream optional branch inside `ra-逐字稿提取skill`; BoomEarth V1 deliberately does not select it, does not require it, and does not use it as a readiness gate. `QUSHUIYIN_API_KEY=UNSET` is therefore an intentional, non-blocking configuration state.

`LOCAL_READY` means the selected skill and its bundled local rules/resources are loadable. It does not claim that an end-to-end production workflow has run. `PARTIAL_DEFERRED` records a working local adapter whose real provider/input acceptance or downstream runtime verification remains open. `BLOCKED_INTERFACE` means an integration still cannot be invoked against the current workspace contract.

| Skill | Required integration | Status | Evidence and current boundary |
|---|---|---|---|
| `ra-选题` | `01-内容生产/00-选题池`, `01-内容生产/数据统计`, `01-内容生产/00-选题池/_选题卡模板.md`, `个人定位.md`, `automation/scripts/hot_monitor_local_job.sh`, and the upstream `wash_ledger.py` interface | `BLOCKED_INTERFACE` | `01-内容生产/00-选题池` and `01-内容生产/数据统计` are present. `01-内容生产/00-选题池/_选题卡模板.md`, `个人定位.md` and `automation/scripts/hot_monitor_local_job.sh` are missing. The `wash_ledger.py` file exists, but the upstream skill requires `filter`, `--title`, `--status`, `--from-source-json`, `--card`, `--handoff`, `--work-id` and exit codes 3/4, while the Task 2 CLI exposes only `check`/`add URL` and exit codes 0/1/2. The ledger contract is therefore `BLOCKED_INTERFACE`; a later adapter is required. No substitute was created. |
| `ra-hook` | No external runtime for the hook taxonomy; optional `dbs-hook` quality pass | `LOCAL_READY` | `SKILL.md` and `agents/openai.yaml` are present; no relative `references`, `scripts` or `assets` are required. `dbs-hook` is one of the selected installed skills. |
| `ra-video-wash-pipeline` | P2 source intake/acquisition/normalization/transcription/rewrite/handoff adapters; private wash ledger; P1 handoff parser | `PARTIAL_DEFERRED` | The shipped Windows commands now join authorized local, yt-dlp and TikHub inputs to one private contract, enforce explicit plan-bound approvals, six reviews and compiler-only source-free publication. Offline three-lane orchestration is covered by tests. A real URL/provider run and downstream production acceptance remain Task 11/12 gates. |
| `ra-video-download` | `source_intake.py`; `acquire_source.py`; yt-dlp; optional TikHub credential; FFmpeg/ffprobe | `PARTIAL_DEFERRED` | The Windows adapter accepts local files or private URL input files, creates immutable private artifacts, and requires a matching approval before one provider run. It has fake-provider tests and no fallback. Real yt-dlp/TikHub URL acceptance has not yet run. |
| `ra-逐字稿提取skill` | `normalize_source_audio.py`; `transcribe_source.py`; **阿里云 DashScope Paraformer**; private artifact manifests | `PARTIAL_DEFERRED` | The adapter normalizes the locked source to canonical WAV and requires an exact plan-bound approval for one Paraformer request. Qushuiyin is not required. Fake-provider and prior isolated Paraformer integration evidence exist, but a real P2 source transcription remains gated. |
| `ra-洗稿` | `prepare_rewrite.py`; six selected review Skills; `compile_source_handoff.py`; source-free P1 handoff contract | `PARTIAL_DEFERRED` | Strict candidate/review schemas, required six-pass review, source-overlap checks, P1 parse compatibility, receipt-last publication and local recovery are implemented and tested. Human/agent review quality on the first real source remains to be accepted. |
| `ra-人话` | No external runtime for the rewrite rules | `LOCAL_READY` | `SKILL.md` and `agents/openai.yaml` are present; no relative integration files are required. |
| `ra-video-title` | Bundled `references/two-part-video-title.md`; final script or handoff context for actual candidates | `LOCAL_READY` | Skill-local reference and agent metadata are present. No final script, MP4 or title artifact was supplied or written in this installation task. |
| `dbs-ai-check` | No external runtime for diagnostic rules | `LOCAL_READY` | Self-contained `SKILL.md` is present; no relative integration files are required. |
| `dbs-hook` | No external runtime for diagnostic rules | `LOCAL_READY` | Self-contained `SKILL.md` is present; no relative integration files are required. |
| `dbs-resonate` | No external runtime for diagnostic rules | `LOCAL_READY` | Self-contained `SKILL.md` is present; no relative integration files are required. |
| `ra-video-production-director` | Production queue/archive; `automation/scripts/check_delivery.py`; downstream caption/TTS/illustration lanes; optional HyperFrames runtime; project-local downstream paths | `BLOCKED_INTERFACE` | Bundled 5 references and 13 assets are present. `automation/scripts/check_delivery.py` and `automation/config/tts-routing.json` are present; the route parses and selects local `user-indextts2-black-gold-v3` with fallback disabled. The illustration search in `SKILL.md` includes `.agents`, but other downstream paths/scripts still require project-local verification and may use the upstream `.claude` layout; `hyperframes` remains `NOT_FOUND`. Those unrelated downstream path and HyperFrames blockers still require later work. No production render or QC was run. |
| `tts-skill` | `automation/config/tts-routing.json`; local IndexTTS2 repository/model/reference WAV; FFmpeg; `uv`; pronunciation lexicon; project-local invocation path | `PARTIAL_DEFERRED` | Bundled script, references and lexicon are present; `uv` and FFmpeg resolve; the external IndexTTS2 repository and checkpoints exist at the previously prepared machine path. `automation/config/tts-routing.json` is present, parses, and selects local `user-indextts2-black-gold-v3` with its reference configured and fallback disabled. The Windows project command is `uv run python .agents/skills/tts-skill/scripts/generate_indextts2_narration.py`. No dry-run or synthesis was run in this documentation task, so runtime acceptance remains deferred rather than claimed ready. |
| `ra-audio-to-subtitles` | Final media; bundled generator; **火山引擎豆包语音 ASR**; `VOLCENGINE_RESOURCE_ID`; FFmpeg/ffprobe; project-local invocation path | `BLOCKED_INTERFACE` | Generator and artifact contract are present; FFmpeg/ffprobe resolve. Command examples hardcode `.claude/skills/ra-audio-to-subtitles/scripts/generate_subtitles.py`, but this installation is `.agents/skills/ra-audio-to-subtitles/scripts/generate_subtitles.py`; the example cannot be copy-run and needs a project-local invocation adapter. Redacted config status: `VOLCENGINE_API_KEY=SET`, `VOLCENGINE_RESOURCE_ID=SET`. **火山 ASR/豆包语音 ASR 是同一项集成，不拆成两个 Provider**. No paid/live request, final media, or caption artifact was used. |
| `skill-captions` | `ra-audio-to-subtitles` PASS artifacts; Pillow; FFmpeg/ffprobe; registered Chinese font; project-local invocation path | `BLOCKED_INTERFACE` | Style registry, 5 references, 2 scripts and 68 assets are present; FFmpeg/ffprobe resolve. Command examples hardcode `.claude/skills/skill-captions/scripts/...`, but this installation is `.agents/skills/skill-captions/scripts/...`; the examples cannot be copy-run and need a project-local invocation adapter. The bundled font registry contains macOS-only STHeiti/PingFang paths; Windows automatic resolution needs a Windows font candidate or an explicit `--font`. Pillow is `NOT_FOUND` in the workspace `uv` Python probe and no caption QC artifact or render was supplied. |
| `rn-motion-director` | Motion brief; selected renderer such as HyperFrames or Remotion; image generation when needed; anti-PPT QC | `PARTIAL_DEFERRED` | The 2 bundled references are present. This task installed the planning/director layer only; `hyperframes` was `NOT_FOUND` and no motion project or render was created. |
| `rn-replica-qc` | Reference/candidate media; FFmpeg/ffprobe; Pillow; Node Playwright for timeline capture | `PARTIAL_DEFERRED` | The 3 references, 7 scripts and 1 showcase asset are present; FFmpeg/ffprobe resolve. Pillow and Playwright were `NOT_FOUND` in the workspace probes, and no reference/candidate comparison was run. |
| `ian-xiaohei-illustrations` | Image generation and manual QA; project asset destination | `PARTIAL_DEFERRED` | The 5 references, 14 example assets, `LICENSE` and `NOTICE.md` are present. No image generation or project asset output was requested or run; the skill is retained unchanged. |
| `ra-github-skill-import` | `source_intake.py github-skill`; `acquire_github_skill.py`; private GitHub snapshot; existing rewrite/handoff compiler | `REAL_ACCEPTANCE_PASS` | Strict offline intake, parser, approval budgets, transaction recovery, manifest validation and source-free rewrite handoff are implemented. Default tests do not access GitHub. The target `chujianyun/awesome-gpt-image2-ppt-skills` completed one exact approved anonymous capture within its 48-request budget; the private manifest reports `UNDECLARED`, so no MIT/commercial/redistribution permission is inferred. |

### Missing and deferred integration summary

The following 12 rows are intentionally not called end-to-end ready: `ra-选题`, `ra-video-wash-pipeline`, `ra-video-download`, `ra-逐字稿提取skill`, `ra-洗稿`, `ra-video-production-director`, `tts-skill`, `ra-audio-to-subtitles`, `skill-captions`, `rn-motion-director`, `rn-replica-qc`, and `ian-xiaohei-illustrations`. The four P2 source/rewrite rows are now `PARTIAL_DEFERRED`: their local interfaces and offline contracts are implemented, while first-real-input/provider acceptance remains open. Other rows retain their independently recorded blockers.

The P2 source/rewrite interface blockers recorded in the original installation snapshot are closed by the project adapters; the bundled legacy scripts are no longer the operational path. Remaining adapter work is independent: (1) align `ra-选题` with the current ledger contract; (2) adapt `.claude/skills` examples to `.agents/skills` for `ra-audio-to-subtitles` and `skill-captions`, and verify production-director paths; and (3) provide a Windows font candidate or explicit `--font` for captions.

Other open items are the missing `01-内容生产/00-选题池/_选题卡模板.md`, `个人定位.md`, `hot_monitor_local_job.sh`, project-local HyperFrames command, workspace Pillow/Playwright probes, and intentionally deferred real URL, final media, QC fixtures, or image generation. Qushuiyin remains non-required and non-blocking. Authorized local-file/TikHub/yt-dlp are the acquisition paths; source transcription is the independent **阿里云 DashScope Paraformer** service. Offline interface evidence does not by itself claim real provider or finished-video acceptance.

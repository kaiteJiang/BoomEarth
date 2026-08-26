# Delivery Gates

Use these gates before calling a video done.

## Observable Resume Decision Gate

Choose exactly one row from immutable local receipts and actual files before a
provider call or expensive rerender. Evaluate the table from bottom to top and
stop at the first matching row: verified archive > verified master > completed
job with missing master > incomplete job > approved/no job > no approval. An
earlier row applies only when no later-row evidence exists. Conversation memory,
deadline pressure, subscription entitlement, and a user's earlier generation
approval are not resume evidence.

| Observable state | Required next action | External authority |
| --- | --- | --- |
| No generation approval | Continue local planning/validation only; do not create, poll, retrieve, or download a provider job. | Obtain an exact generation plan approval before generation. It grants no retrieval authority. |
| Generation approved, no job receipt | Execute only the approved generation plan and its bounded request budget; preserve the first resulting receipt. | Existing exact generation approval only. No retrieval is possible without a completed-job receipt and a later retrieval approval. |
| Job receipt exists, job incomplete | Do not create a second job. Read status only when a separate exact status-read approval is bound to this immutable job receipt; otherwise request it and stop. Generation approval is never status-read authority. | One bounded status read only. No download, retrieval, or regeneration. |
| Job complete, local master missing | Preserve the completion receipt and prepare a separate retrieval plan: one status read plus one download, zero retry/fallback. A retrieval failure stops with evidence. | Exact retrieval approval bound to the immutable receipt and target path. Generation approval is insufficient. |
| Verified project-contained master | Make no provider call. Pass that master into the canonical content finalizer; never use a standalone/sample compositor. | Local finalization only, subject to the existing locked-audio/render approval gates. |
| Verified final archive and delivery receipt | Verify the existing archive/receipt, report the recommended final, and preserve all versioned evidence. | No rerender, regeneration, or retrieval. |

A completed job is never regenerated due to retrieval failure. Reporting-only
failures after a successful render/archive first verify the existing archive
and delivery receipt; they never authorize an automatic rerender. Preserve old
receipts and numbered artifacts instead of overwriting them as a new success.

## HeyGen Generation And Retrieval

Load the installed official `heygen-video` Skill only for an approved HeyGen
provider interaction. The workspace rules in this section override that
generic Skill's Delivery and Self-Evaluation Log instructions. Keep generation
generation, incomplete-job status read, and completed-job retrieval as separate
exact plans and separate approvals:

- generation binds the locked local IndexTTS2 audio SHA-256, configured private
  avatar group, selected provider route, maximum generation requests,
  retry/fallback policy, and possible charge; it authorizes no status read
- status read binds one immutable incomplete-job receipt and one read, with zero
  retry/fallback; it authorizes neither download/retrieval nor generation
- retrieval binds the immutable completed-job receipt, one status read plus one
  download, the project-contained output path, and zero retry/fallback; it
  never authorizes a new generation

When subscription use is intended and both routes are available, prefer an
already authenticated subscription connector before API-key CLI. Never read
browser storage, cookies, or browser secrets to discover credentials. Never
print or expose private job, avatar, look, or group IDs. If the subscription
connector does not expose an Avatar III selector, it may auto-select its
supported lip-sync engine; it may not change the locked audio, the configured
avatar group, or use HeyGen TTS.

For a completed-job retrieval, validate the existing ignored private avatar
snapshot and completion receipt locally. Do not refresh or reselect the avatar
group/look, list compatible looks, resolve a stored group again, or perform any
provider read beyond the one approved status read. Those discovery steps belong
only to a separately approved new generation.

Do not run the generic ID-bearing `heygen video download <video_id>` command,
share video/session/dashboard URLs, or append IDs to a workspace-root
`heygen-video-log.jsonl`. Keep provider state in ignored private receipts and
use the stdin helper for the one approved download.

After the single approved status read returns a signed URL, save it to the
ignored private input file without printing it, then use this exact safe stdin
shape:

```powershell
Get-Content -Raw -LiteralPath '<WORKSPACE>/01-内容生产/视频工作台/.internal/heygen/retrieval-url.txt' | uv run python automation/scripts/download_private_video.py --output '<WORKSPACE>/01-内容生产/视频工作台/制作中/<project-name>/工程/provider/avatar-master.mp4' --receipt '<WORKSPACE>/01-内容生产/视频工作台/.internal/heygen/retrieval-receipt.json'
```

Both files are under the actually ignored canonical workbench private root
`01-内容生产/视频工作台/.internal/heygen/`; the receipt filename is generic and
contains no provider ID or signed URL. The signed URL is stdin data, never a
command-line argument. No URL or private ID may enter a command line, Git, a
public receipt, or the final report. The helper accepts at most 2 GiB, requires
an actual `codec_type=video` stream with positive width and height, and
publishes master and receipt with atomic no-replace semantics. A competing
destination is preserved. If receipt publication fails after a verified master
was published, preserve that master for explicit recovery; never unlink a path
whose ownership is uncertain. On a verified download, the governing route is
`automation/scripts/run_content_production.py finalize --avatar-master
<project-contained-master>`. Supply its existing required project and
locked-narration arguments in the executable command:

```powershell
uv run python automation/scripts/run_content_production.py finalize --workspace-root <workspace-root> --active-project <project-name> --audio-approved --approved-narration-sha256 <locked-narration-sha256> --avatar-master <project-contained-master>
```

Before invoking it, verify the existing final caption set and
`caption-qc.json`: `status == "pass"` and `narration_sha256` equals the
approved locked narration SHA-256. Reuse those caption files byte-for-byte. The
finalizer must not call ASR or rebuild/overwrite existing final captions.
Missing, failed, or stale captions stop local finalization and require a
separate final-audio ASR plan and approval.

This canonical finalizer is the only route that makes composition QC,
publication capture, and archive consume the composed result while retaining
the locked final audio and its verified captions. Never publish the avatar
master or provider receipt; the final MP4 is the public composition proof.

After the verified master has been used and the delivery receipt has been
captured, but before the archive move, the canonical finalizer must create and
verify a hard link at
`01-内容生产/视频工作台/.internal/heygen/archived-masters/<project-name>/<master-sha256>.mp4`,
then remove the active-project master. The private store is outside the public
archive and ignored by Git. If hard-link publication, identity/hash validation,
or active-master removal fails, stop the archive and retain a recoverable active
source; do not delete or overwrite the only verified bytes. The final archive
contains no provider master and no provider receipt, signed URL, or private ID.

The canonical public receipts are `工程/publication-manifest.json` and terminal
`工程/delivery-report.json`. Root `delivery-report.json` is transient checker
output and is deleted before archive. The terminal receipts contain only
approved public artifact hashes and redacted result categories. Neither receipt
may contain the provider master, private receipt, signed URL, or job/look/group
IDs.

## Handoff Contract Gate (待制作 queue jobs)

When the job came from `01-内容生产/视频工作台/待制作/`, this gate runs first
and is machine-checked:

```bash
python3 automation/scripts/check_delivery.py <交接文件.md> <项目目录> <成片.mp4>
```

- exit code must be 0 (privacy boundary, ratio, duration, audio stream, voice
  provenance, real-timestamp captions when contracted, Xiaohei assets all
  PASS); fix and re-run on any FAIL
- paste the checker output into the handoff file's 制作端回执, fill the
  remaining 回执 fields (project dir, voice_manifest, contact sheet, final
  path), set `status: 已完成`
- assemble `01-内容生产/视频工作台/已制作/<月上旬|月下旬>/<日期-主题>/` (folder name =
  the project folder name): clean regenerable intermediates, then move the whole
  project folder from `制作中/` — `交接稿.md` at its root, the final MP4 in
  `成片/`, key QC frames in `质检/`, minimal engineering in `工程/` (no
  `视频标题.md` — title selection stays manual with the user). Use `X月上旬`
  for delivery days 1-15 and `X月下旬` for days 16 through month-end
- the frontmatter contract values (ratio, duration, voice, visual) are the
  acceptance values; an execution skill's own defaults never override them
- when the frontmatter declares `covers: punk-cover-giant-title-3x4-v1`, core
  video delivery and archive verification pass first; the post-delivery cover
  stage then publishes its single public cover and separate QC. Private cover
  prompts, plans, provider evidence, and IDs do not enter the public receipt.

## Required Locations

- Workbench project:
  `<WORKSPACE>/01-内容生产/视频工作台/制作中/<日期-主题>/`
- Final delivery for every video production route:
  `<WORKSPACE>/01-内容生产/视频工作台/已制作/<月上旬|月下旬>/<日期-主题>/成片/<file>.mp4`
  (the archive root holds any handoff/script/plan or production note that
  exists, and the sibling `质检/` holds key QC artifacts)
- Selected publish title (non-queue jobs only; queue jobs keep candidates in
  the handoff file's 标题候选 section and the user selects manually):
  `视频标题.md` next to the final MP4
- Post-delivery cover deliverables when
  `covers: punk-cover-giant-title-3x4-v1` is declared:
  `封面/作品封面-1080x1440.png` and `质检/punk-cover-qc.json`
- `已制作/6月历史成片库/`（封存只读） and the desktop
  `<WORKSPACE>/legacy-archive/` are retired legacy history: read-only,
  no new deliverables go there. Preserve their historical v1 voice records and
  provenance; never bulk-rewrite them to v2.

Historical handoffs and already archived work without the cover marker remain
compatible: do not retrofit `covers`, fail them for missing new cover files, or
rewrite their TTS/subtitle/avatar/semantic-scene evidence.

The final MP4 keeps a clear Chinese filename inside its delivery folder.

## Component Library Placement Confirmation

This gate applies whenever a video component, video background, or
reference-video replica has a rendered, QC-passed final. It runs after normal
finished-video archiving and before anything is added to `05-视频组件/`.

- explicitly ask the user whether the final should enter the component library
- ask which existing category should contain it and whether a new category is
  required; do not infer or create a category before the user replies
- if the user declines, stop after the normal `视频工作台/已制作/` delivery
- if the user approves, use the confirmed
  `05-视频组件/<中文分类>/<中文组件名>/` names as the naming authority and make
  the component directory self-contained: portable MP4 + `manifest.json` +
  `质检/` + `工程/` (sole editable source, stripped of node_modules/renders)
- no engineering mirrors, symlinks, or second source copies may be created
  anywhere else; register the component in `05-视频组件/CATALOG.md`
- do not keep two differently named current entries for the same component

## Publish Title Handoff

待制作 queue jobs skip production-side title work entirely: `ra-video-title`
already wrote 8-12 candidates (with a Top 3 recommendation) into the handoff
file's 标题候选 section at wash time, and the final title is chosen manually
by the user at publish time. Do not generate candidates, do not apply a
title, and do not create `视频标题.md` for queue jobs. When the cover marker is
present, the first Top recommendation may seed a non-blocking cover headline;
this does not select or persist the publish title.

For non-queue jobs (no handoff contract): after the final MP4 passes media
and visual checks, generate 8-12 candidate publish titles with
`ra-video-title`.

Title candidates should be大众化 and clickable:

- do not lead with narrow tool names unless the user explicitly wants that
- prefer the audience pain, result gap, curiosity, conflict, or loss
- keep platform titles separate from spoken hooks and on-screen page titles
- offer a short recommendation, then wait for the user's choice

After the user chooses a title (non-queue jobs), create or update
`视频标题.md` next to the selected final MP4. Do not silently overwrite an
existing title unless the user has selected the replacement. A later non-queue
title change causes only a local, versioned `ra-video-cover` rerender. It never
rerenders the final MP4, regenerates scenes, or recalls a provider.

## Cover Delivery Contract

When a new handoff declares `covers: punk-cover-giant-title-3x4-v1`, load
`ra-video-cover` after core delivery and archive verification. It uses
`punk-cover` with `giant-perspective-chinese-title`, owns prompt compilation,
external approval, safe zones, local QC, and version preservation; do not
duplicate or bypass those mechanics here.

The post-delivery cover stage requires exactly these public/QC artifacts:

- `封面/作品封面-1080x1440.png`
- `质检/punk-cover-qc.json`

Do not generate a homepage preview, alternate platform crop, contact sheet, or
multi-option grid. Private prompt inputs, plans, provider artifacts, private
IDs/URLs, and ignored selection state never enter public reports. The old
`platform-defaults-v1` five-artifact set remains valid for historical archives
and is never migrated or deleted.
If the specialist fails closed, delivery remains incomplete; do not expand its
provider approval scope or substitute a generic cover.

## Media Checks

Run a metadata check on the final file and report:

- width and height
- frame rate
- duration
- video codec
- audio codec
- audio channels
- file size
- non-queue jobs only: selected title file exists in the same `成片/`
  directory as the final MP4 (queue jobs carry title candidates in the
  handoff file instead)
- narration provider and voice id, when narration exists. Every local-video
  preview, audition, and recommended final must use local IndexTTS2 voice
  `user-indextts2-black-gold-v3` and only the canonical private lossless reference SHA-256 from
  `automation/config/tts-routing.json`; generated/latest output is never a
  reference. MiniMax is an article/relay-demo provider only and fails the
  local-video gate. Windows system speech, browser/generic speech, MiniMax,
  another cloud provider, or any other fallback fails this gate for previews,
  auditions, and finals. If local IndexTTS2 v3 is unavailable, stop.
- for `captions: asr-word-timestamps`, canonical subtitle artifacts exist,
  `caption-qc.json` passes, alignment coverage is at least 0.90, and the
  renderer consumes `captions.json` without recalculating its timestamps

## Asset-Text And Semantic-Cue Gate

Run this gate for any image, screenshot, footage, or generated illustration
combined with authored labels, badges, stamps, callouts, or other UI chrome.

- inspect each source asset at its delivered scale and inventory visible words,
  labels, badges, stamps, and UI text before authoring overlays
- compare the inventory with authored overlays and chrome; do not repeat the
  same semantic label unless the repetition is intentional, visually useful,
  and recorded in the production note. Required production captions are exempt
  from this overlay-deduplication rule
- for every narrated hero term, name, number, or reveal, take the semantic cue
  from the final word timestamps and capture frames at `cue - 0.1s`, `cue`,
  `cue + 0.3s`, and `cue + 0.7s` (clamped to the media bounds)
- at the cue, the hero must already be identifiable or becoming immediately
  readable; by `cue + 0.7s`, or before the spoken phrase ends when that is
  earlier, its load-bearing text must be fully legible and its supporting
  visual settled enough to read as one composition
- reject incomplete letters, late illustrations, transient text collisions,
  crop/overflow flashes, layout jumps, or accidental duplicate labels even
  when the scene midpoint and automated layout check pass
- keep the cue frames or their contact sheet with the project QC artifacts

## Visual Checks

Always create at least one contact sheet for a new rendered video. For reference
replication, create side-by-side contact sheets at the agreed interval.

对页面式、场景式视频，数字人、字幕、标签和轻量图标都不能代替主题场景素材。除显式 `semantic-handdrawn-v3/type-led` 外，每个场景必须存在与交接稿精确主题一致、经过计划和 manifest 绑定的真实图片素材；缺少真实主题素材时必须失败，不得以左下角数字人、字幕已经可见或文字相关为理由归档。

## Default HeyGen Circle Avatar

For every new HeyGen presenter project, require an ignored private default
configuration and a fresh official-v3 look snapshot from its configured group.
The selected look must be the ready preferred look or a `same-group-only`
fallback from the ready portrait pool when one exists. Missing candidates fail;
never select a public `stock avatar` or another group. The narration manifest
must bind local IndexTTS2 voice `user-indextts2-black-gold-v3` with HeyGen TTS
disabled. The default local compositor profile is exactly
`headroom_08-circle-lower-left`: 1920x1080, one stable lower-left 210px circle
at x=70 with a 96px bottom margin, caption/content collision checks, and only
the local narration audio track.
Rectangle, 9:16-window, and full-screen profiles require an explicit override.
The circle does not count as scene illustration evidence.

Keep full contact sheets and QC frames in the engineering project, and copy
the key contact sheet/representative frames/media probe into the unified
`视频工作台/已制作/<月上旬|月下旬>/<日期-主题>/质检/` archive.

Check for:

- blank or unintended empty areas
- for 16:9 Xiaohei page videos, the whole page skeleton follows the
  `xiaohei-16x9-layout` contract: left-top title, left-middle note card,
  right-middle illustration, and lower caption
- right-side Xiaohei illustrations sitting too high or behaving like
  upper-right decoration instead of the right-middle page subject
- left note cards dropping into the lower-left area or crowding captions
- title or subtitle wrapping that creates orphan characters, short tail lines,
  strange gaps, or broken Chinese phrases
- for 16:9 Xiaohei page videos, typography uses the `mobile-readable` scale
  from `xiaohei-16x9-layout.md` by default: title, subtitle, note card,
  captions, and topbar must stay readable in reduced-size
  contact sheets and representative 1920x1080 single-frame previews. A
  `desktop-compact` exception is allowed only when the project note explicitly
  says the output is for desktop demos, projection, course-screen playback, or
  similarly large-screen viewing.
- captions that are visually heavier than the title, lack a clear bottom
  margin, span the frame instead of wrapping text, or sit too close to the
  note/illustration zones
- repeated blank zones caused by a fixed template, especially lower-left or
  lower-middle areas in right-illustration pages; when present, either add
  useful content such as input/action/output notes, role reminders, or
  acceptance criteria, or record why the blank space is intentional
- text outside containers or off-canvas
- text that is technically inside its container but too small to read on a
  phone; fix by shortening copy or changing the page variant before reducing
  font size
- wrong scene boundary timing
- missing transition frames
- decorative focus frames, scan boxes, or large outlines enclosing over 40%
  of the canvas or multiple content groups: inspect an entrance frame, the
  scene midpoint, and a late frame; reject them when total visibility exceeds
  the shorter of 2 seconds or 15% of the scene, when they remain at midpoint
  without explicit semantic purpose, or when they read as a permanent outer
  container. A semantic detection/selection frame must tightly bound one
  local target instead of boxing the whole layout.
- overly small primary subjects
- accidental repetition between text already embedded in a source asset and
  authored overlays or chrome
- hero terms, names, numbers, or reveal visuals that are still incomplete or
  unsettled after their semantic-cue deadline
- caption/audio desync
- captions covering page content
- caption styling that violates `skill-captions` and the selected registered
  style: full-width bands, heavy shadows, drifting baselines, live blur, or an
  undocumented frameless exception
- page-wide drifting, zooming, or shaking when the intended motion is
  component-level animation
- whether key components appear progressively with the narration
- adjacent pages presenting an identical set of info components, or a page
  with no component-level change within its duration (check the contact sheet
  page by page)
- `standard` pages carrying fewer than two info components besides title and
  illustration without a recorded reason in the project note

## Xiaohei Illustration Checks

When the visual direction explicitly mentions Xiaohei, 小黑配图, or the
historical `xiaohei-white-first-v1` system, the video is not complete until these checks pass:

- `ian-xiaohei-illustrations` was loaded and followed before image generation
- each page or scene either has a generated Xiaohei asset or a written reason
  for skipping the illustration
- generated images are copied into the project, usually under
  `assets/xiaohei-illustrations/`
- the render loads those image files directly; CSS/HTML drawings or generic
  hand-coded figures are not accepted as substitutes
- a contact sheet shows the Xiaohei assets in the final rendered scenes
- the project note records the asset list and the selected final MP4 path

## Completion Language

Use "recommended final" only for the file that passed the gate. Keep alternate
drafts visible but do not let them look like the selected deliverable.

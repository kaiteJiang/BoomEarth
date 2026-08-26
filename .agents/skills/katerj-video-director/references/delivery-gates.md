# KaterJ delivery and recovery gates

## Resume order

Select the highest-evidence state that exists: verified archive, verified project master, completed job without local master, incomplete job, approved generation without job, or no approval.

- A verified archive is read and reported. It is never regenerated for a reporting problem.
- A verified local master proceeds to the canonical finalizer without a provider call.
- A completed job with a missing master needs a new retrieval plan for one status read and one download.
- An incomplete job needs a separate one-read status plan.
- Generation approval never authorizes status, retrieval, or download.

## HeyGen recovery

The workspace recovery gate overrides the generic Skill's ID-bearing CLI download. Signed URLs and private IDs live only in ignored private files and enter the download helper through stdin.

The only private retrieval input and receipt locations are `01-内容生产/视频工作台/.internal/heygen/retrieval-url.txt` and `01-内容生产/视频工作台/.internal/heygen/retrieval-receipt.json`.

Retrieval reads the existing private snapshot and never refreshes or reselects the group/look. The approved final caption set must have `status == "pass"` and bind the locked narration before local composition.

After composition, create and verify a hard link at `01-内容生产/视频工作台/.internal/heygen/archived-masters/<project-name>/<master-sha256>.mp4`, then remove the active-project master. A failed preservation step leaves a recoverable active source. The final archive contains no provider master and no provider receipt, signed URL, or private ID.

The configured avatar contract uses `headroom_08-circle-lower-left`, `same-group-only`, `user-indextts2-black-gold-v3`, and never a `stock avatar`.

## Public receipts

`工程/publication-manifest.json` and terminal `工程/delivery-report.json` contain only approved public hashes and redacted categories. A root delivery report is transient and deleted before archive.

## Visual gate

数字人、字幕、标签和轻量图标都不能代替主题场景素材。每个计划场景必须出现语义对应的真实图片、截图或视频证据；缺少真实主题素材时必须失败。

Hero terms and numeric claims use final word timestamps. Capture cue - 0.1 s, cue, cue + 0.3 s, and cue + 0.7 s frames and reject incomplete text, late images, crop flashes, collisions, or unsettled layouts.

## Cover gate

When the handoff declares `punk-cover-giant-title-3x4-v1`, run the cover stage after core delivery and archive verification. Use `giant-perspective-chinese-title` and publish only `封面/作品封面-1080x1440.png` plus `质检/punk-cover-qc.json`. `platform-defaults-v1` remains a historical contract.

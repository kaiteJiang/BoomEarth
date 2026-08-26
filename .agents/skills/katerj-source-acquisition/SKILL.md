---
name: katerj-source-acquisition
description: Use when an authorized local media file or playable video URL must be captured as a private, hash-locked BoomEarth source.
---

# KaterJ Source Acquisition

## Outcome

Create one verified private source media snapshot and a normalized-audio-ready state without leaking the input URL or granting later production authority.

## Workflow

1. Register a local file with `automation/scripts/source_intake.py local ... --authorized`.
2. Register a URL through `source_intake.py url --input-file <private-file> --authorized`; never place the URL in argv.
3. Prefer `yt-dlp` for ordinary playable URLs. The plan contract is `--provider yt-dlp|tikhub`, but one run selects exactly one value. Use TikHub only through a separately approved plan when needed.
4. X Article/图文帖子交给 `ra-x-article-import`；只有 X 原生媒体才按本 Skill 的视频路线处理。
5. Bilibili 依次使用 `acquire_bilibili_source.py plan-discovery <work_id>`、条件式 `plan-playurl`、`acquire_bilibili_source.py plan-media <work_id>` 与 `acquire_bilibili_source.py finalize <work_id>`。只有发现结果无法提供可验证媒体地址时才允许条件式 playurl，每个真实网络子阶段分别绑定批准。
6. 若发现阶段已经完成但媒体规划尚未发布，只能先做 `plan-recovery <work_id>`，经绑定哈希的新批准后再执行一次 `run-recovery <work_id>`。恢复步骤不读取 API Key、不能联网，且不得覆盖原计划、原响应或历史失败证据。
7. Download only the assets named by the immutable plan. Do not follow an unplanned redirect or change provider.
8. Verify full decode, media streams, SHA-256, and a 16 kHz mono PCM source WAV before advancing.

## Privacy and safety

Keep URL, Cookie, platform IDs, media addresses, source titles, and provider payloads under the ignored work item. A provider failure is evidence, not permission to retry.

## Acceptance

- The ledger reaches `media_ready`, then `audio_ready` only after local validation.
- Public logs contain stable rule names, never the URL or provider payload.
- The exact source files and manifest agree by hash.
- The next permitted route is `katerj-source-transcription`；当前批准不授权转写，也不授权任何下游云调用。

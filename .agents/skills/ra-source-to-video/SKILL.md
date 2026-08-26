---
name: ra-source-to-video
description: Turn an authorized public link, video URL, local video, audio file, article, X post, or GitHub Skill URL into a finished BoomEarth 16:9 video product. Use when the user drops a URL or media file and asks to 做成视频、二创、洗稿后制作、自动跑完整流程、从链接到成片, including when they provide only the source and expect the established production defaults.
---

# 来源到成片

Act as the single intake-and-orchestration layer. Reuse the specialized Skills
below; do not reimplement downloading, rewriting, voice cloning, illustration,
avatar, subtitle, or rendering logic here.

## Established defaults

- Require the user to own or authorize the supplied source.
- Produce a new horizontal 16:9 video irrespective of source ratio.
- Use `xiaohei-white-first-v1` through `katerj-xiaohei-illustrations` when no
  visual theme is specified. Honor an exact registered theme when specified;
  never invent a freeform image style or migrate an older project's binding.
- Use local IndexTTS2 voice `user-indextts2-black-gold-v3` only. Do not use an
  old recording, generated WAV, system TTS, MiniMax, or any fallback.
- Default to the configured personal HeyGen Circle Avatar III composition:
  `headroom_08-circle-lower-left`. Resolve the private default configuration
  fresh for each job. The avatar is a presenter layer, never a replacement for
  scene illustrations. Omit it only when the user asks for faceless video.
- Use `anchor-dark` subtitles: punctuation-free semantic single-line cues. Keep
  one cue per frame with no coexistence of adjacent cues, and use real
  final-audio word timestamps. Keep the lower-left avatar clear of the caption
  panel.
- Prefer a complete explanatory arc: a source of six minutes or longer normally
  becomes a 150–180 second production. Include an opening hook and closing
  takeaway.

## Route the source

| Input | First-stage route |
| --- | --- |
| Local video or audio; Bilibili, YouTube, TikTok, or playable video URL | `katerj-video-wash` |
| X Article or text/image post | `ra-x-article-import`, then `katerj-script-rewrite` |
| Public GitHub Skill/repository/`SKILL.md` URL | `ra-github-skill-import`, then `katerj-script-rewrite` |
| Existing BoomEarth queue handoff | `katerj-video-director` directly |

Do not use `ra-x-article-import` for X native video. Route X native video as a
video source. Do not download a GitHub repository, install a remote Skill, or
execute repository code.

## Resume before external work or rerender

Before planning any new external call or expensive rerender, inspect immutable local receipts and actual files. This is a routing decision only: preserve the existing evidence and delegate retrieval, finalization, and rendering mechanics to their owning Skills and local helpers.

- If a final archive already passes delivery, report it instead of rerendering.
  A passing archive must be verified and reported, never rerendered. A
  post-success reporting error first triggers archive verification; do not infer
  that the completed delivery failed from the reporting error.
- If a completed provider job has a missing local master, create a separate
  status/read-plus-one-download retrieval plan. It is a retrieval route, never
  back to generation, and needs exact retrieval authorization. Speed, deadline,
  sunk cost, subscription entitlement, or "直接出片" never authorizes retrieval
  or substitutes for that authorization.
- If a provider job exists but is incomplete, stop unless a separate exact
  status-read approval is already bound to that immutable job receipt. A
  generation approval never authorizes a status read. The status-read approval
  authorizes one bounded read only; it grants neither completed-job retrieval
  nor another generation.
- Keep signed URLs and private IDs in ignored private input/state. Read them
  through stdin-based local helpers; never place them in command-line arguments
  or public reports.
- If a verified local master exists, route it to the canonical content finalizer
  with optional avatar input. Do not send it through sample/manual composition.

## End-to-end workflow

1. Create or identify the private work item. Keep source URL, provider
   response, source media, transcript, and source analysis under
   `视频工作台/.internal/`; never copy them into a public handoff, final archive,
   Git, or user-facing report.
2. Run the selected intake route. For video sources, use source intake →
   provider acquisition → local finalize → Paraformer transcript → rewrite
   preparation. For X/GitHub sources, complete the dedicated private capture.
3. Use `katerj-script-rewrite` and its review stack. Every new candidate uses the private
   schema-2 `opening_contract`: `katerj-video-hook` selects one primary hook type,
   `jl-multiplatform-titles` binds the title formula, cover hook, spoken first-
   three-second hook, and proof segments, then `katerj-hook-review` diagnoses execution.
   Require source-free rewrite to pass humanization, AI-fingerprint, hook,
   resonance, and title review. For a video source, publish only through
   `compile_source_handoff.py`; never hand-author a public `待制作` handoff or
   copy the private opening/review fields into it.
4. Hand the approved source-free contract to `katerj-video-director`.
   New source-to-video handoffs declare `covers: punk-cover-giant-title-3x4-v1`. The
   director owns the production project, canonical content finalizer, scene
   plan, motion plan, render, checks, and final archive.
5. Within that route, call specialists in this order: `ra-video-illustrations`
   or `katerj-xiaohei-illustrations` → `katerj-local-tts` → `katerj-audio-subtitles` → official `heygen-video` (when the
   presenter is enabled) → local avatar composite → `katerj-caption-rendering` → final
   render and delivery QC.
6. Do not burn subtitles before narration is locked. If a cue would wrap,
   shorten or re-segment it; never permit two lines.
7. Finish only after the delivery gate passes: final MP4 decodes; 1920×1080,
   30 fps, H.264/AAC; all planned scenes have real theme-bound visuals; voice,
   caption, avatar, and illustration provenance pass; contact sheet and key QC
   frames exist; and the project is archived under `视频工作台/已制作/`.
8. After final QC and archive verification have passed, route the post-delivery
   cover to `ra-video-cover`. The default uses `punk-cover` with
   `giant-perspective-chinese-title` and produces only
   `封面/作品封面-1080x1440.png` plus `质检/punk-cover-qc.json`; it does not
   create a homepage preview or platform crop set. This is a separate exact
   ImageGen approval bound to the immutable cover prompt and plan.

## External-action gates

Continue through all local planning, validation, editing, and rendering. Stop
only at a real external side effect without existing exact approval. Present
one concise, plan-bound request for each:

1. source acquisition or X/GitHub capture;
2. Paraformer source transcription;
3. ImageGen scene candidates;
4. final-audio Volcengine/Doubao ASR;
5. HeyGen audio/avatar video generation.
6. one status read for an incomplete provider job.
7. completed-provider status/read-plus-one-download retrieval.

For status reads and retrieval, make separate approval requests against the
exact immutable receipt. The incomplete-job request binds one read only; the
completed-job request binds the intended read plus one download. Neither turns
into generation authority or substitutes for the other.

The local cover route above is not an ImageGen scene-candidate request. If it
cannot use qualified existing visuals, let `ra-video-cover` fail closed rather
than expanding this Skill's approval scope.

Bind each request to the exact private input, SHA-256, provider, request
budget, no-retry/no-fallback policy, and possible charge. A source-capture
approval does not authorize downstream providers. On failure, preserve evidence
and stop; do not silently switch provider.

## Non-negotiable quality rules

- Do not treat generic robots, gears, industrial machines, or unrelated tech
  imagery as semantic evidence for an AI/tool topic.
- Do not let text cards or the avatar substitute for required scene images.
- Do not reduce a multi-minute source to a 50-second summary unless asked.
- Do not use source captions or estimated segment durations for final timing.
- Do not disclose credentials, private IDs, source URLs, raw transcripts, or
  private media paths in public artifacts or status summaries.
- Preserve prior artifacts. Publish each repair as a new versioned derivative;
  never overwrite an approved final, transcript, caption set, or QC record.

## Handoff response

At each completed stage, report its result and clickable local artifact links.
At final delivery, return the final MP4, subtitle artifacts, key QC report and
frames, confirmed media specs, the five cover/QC artifacts above, and any
remaining limitation. Do not ask for a style confirmation when the requested or
default registered theme has already passed its applicable visual contract; ask
only for a new style, failed semantic QC, or an explicit creative choice.

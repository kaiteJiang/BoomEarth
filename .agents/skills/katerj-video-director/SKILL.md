---
name: katerj-video-director
description: Use when a BoomEarth handoff or approved script must become a rendered, QC-passed, archived horizontal video product.
---

# KaterJ Video Director

## Outcome

Coordinate the existing specialist lanes until one recommended final decodes, passes its visual and provenance gates, and is archived under the unified workbench.

## Binding contract

For queue work, `交接稿.md` frontmatter owns ratio, duration, voice, captions, visual theme, illustration skill, cover, and avatar choice. Move the project from `待制作` to `制作中` and set status before producing. Never re-split its narration segments.

## Workflow

1. Inspect receipts and actual files. A passing archive is reported, not rendered again.
2. Write a production note covering scenes, semantic images, layout variants, motion, audio, caption safe zones, avatar, cover, and acceptance evidence.
3. Generate or qualify one real theme-bound illustration per scene through `katerj-xiaohei-illustrations` or the exact registered visual route. For Xiaohei, bind 2-4 reviewed `overlay_labels` per scene and require them to render inside the illustration frame; page titles and captions do not count as this text layer.
4. Review the final spoken script with `katerj-oral-linebreaks`, then produce the lossless narration with `katerj-local-tts`; lock its WAV and manifest before subtitles.
5. Generate six real-timestamp subtitle artifacts with `katerj-audio-subtitles`; require caption QC pass.
6. When the presenter is enabled, use the locked narration for one approved HeyGen master and composite the configured small lower-left circle. It never substitutes for scene art.
7. Render captions through `katerj-caption-rendering`, then render the final composition.
8. Build per-scene frames, semantic-cue sweeps, contact sheet, media probe, caption QC, visual QC, and delivery receipt.
9. Run `automation/scripts/check_delivery.py`; on pass, move the cleaned project to `已制作/<月上旬|月下旬>/<日期-主题>/`.
10. After archive verification, create the one standard 3:4 Punk cover through the registered cover route.

## Default production contract

- 1920×1080, 16:9, 30 fps, H.264/AAC.
- 新建页面式和场景式视频默认使用 `xiaohei-white-first-v1`（schema 1），由 `katerj-xiaohei-illustrations` 生成真实逐场景素材；历史别名 `ian-xiaohei-illustrations` 继续兼容。
- 显式 schema 4 主题继续走 `ra-video-illustrations` / `profiled-illustration-v4`；显式要求 `semantic-handdrawn-v3`（schema 3）时保留该精确绑定；显式要求 `editorial-motion-v2`（schema 2）时同样保持原合同。不得迁移旧项目。
- Voice: local IndexTTS2 `user-indextts2-black-gold-v3`, locked 1.12×.
- Captions: `anchor-dark`, punctuation-free, one line, one cue per frame.
- Presenter: configured Circle Avatar III at the lower left unless the user requests faceless. The profile is exactly `headroom_08-circle-lower-left`; fallback is `same-group-only`, never a `stock avatar`.
- `矩形数字人小窗`, `9:16 小窗`, and full-screen presenter layouts require an explicit override.
- Cover: one 1080×1440 giant-perspective Chinese-title image after delivery.

## External gates

Image generation, final-audio ASR, and HeyGen are separate exact plans. Each binds the immutable input and plan hashes, provider, request budget, possible charge, timeout, zero retry, and zero fallback. One stage never authorizes another.

## Acceptance

- Final MP4 fully decodes and matches the media contract.
- Every scene shows a semantically relevant real asset and component-level motion.
- Every Xiaohei scene shows its required local semantic text layer inside the illustration frame; an empty `.visual-overlay-labels` container fails acceptance.
- Voice, caption, illustration, and optional avatar provenance pass.
- Captions align to the final narration and stay outside the avatar/content zones.
- Contact sheet, key frames, publication manifest, delivery report, and archive path all exist.

Read `references/routing.md` when selecting a specialist lane and `references/delivery-gates.md` before any provider recovery, final render, or archive claim.

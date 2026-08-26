# Video Production Routing

Use the narrowest skill that fits. Do not stack multiple creation workflows
unless the task truly crosses boundaries.

| User intent | Primary route | Notes |
| --- | --- | --- |
| Final narration/merged video -> SRT/VTT/timeline | `ra-audio-to-subtitles` | Canonical production timing from Volcengine word timestamps. |
| Existing talking-head footage + designed captions | `embedded-captions` | For caption styling, matting, and burn-in after timing. |
| Existing footage + graphic cards/callouts | `graphic-overlays` | For designed overlays, not plain subtitles. |
| Product / SaaS launch / feature reveal | `product-launch-video` | Use product visuals and marketing structure. |
| General website showcase | `website-to-video` | Use when it is not a launch/promo. |
| Topic explainer with generated narration | `faceless-explainer` | For arbitrary text/topic to narrated video. |
| Longer custom multi-scene piece | `general-video` | Fallback when no specialized lane fits. |
| Short kinetic type/logo/stat/overlay | `motion-graphics` | Usually under 10s, no narration arc. |
| HyperFrames implementation details | `hyperframes`, `hyperframes-cli` | Use after route is chosen. |
| Remotion mentioned as target tool | `remotion:remotion-best-practices` | Use only if building in Remotion. |
| Remotion project -> HyperFrames | `remotion-to-hyperframes` | Only explicit port/migration requests. |
| Download source video/audio | `ra-video-download` | Use before analysis or transcription. |
| Extract source-video spoken content | `ra-逐字稿提取skill` | Transcript-only; not the production subtitle clock. |
| TTS / voice clone | `tts-skill` or `hyperframes-media` | Pick based on the selected workflow. |
| HeyGen generation or completed-job master recovery | Official `heygen-video` + director recovery gate | Use the official Skill only for approved provider interaction. The workspace gate overrides its generic ID-bearing download, URL delivery, and root-log behavior; generation and retrieval require separate exact approvals. |
| Recommended-final / canonical pre-publication platform covers | `ra-video-cover` | Run only after final QC or at canonical pre-publication QC. Reuse its renderer and exact artifact contract; do not duplicate cover mechanics here. |

If two routes seem plausible, pick the one closest to the user's artifact:
existing footage beats generated video; reference-video replication beats style
inspiration; captions beat overlays when the user only asked for subtitles.

For every narrated final, `ra-audio-to-subtitles` owns the timing layer even
when another skill owns caption styling or burn-in.

Provider completion does not route back to generation. When a HeyGen receipt is
complete but its local master is missing, use the recovery row in
`delivery-gates.md`; validate the existing private avatar snapshot without
refresh/reselection, then return a verified master to the canonical content
finalizer. Before the public archive move, that finalizer preserves the verified
master under the canonical ignored workbench `archived-masters` tree and removes
it from the active project; preservation failure leaves the active source
recoverable. When a new handoff declares
`covers: punk-cover-giant-title-3x4-v1`, run `ra-video-cover` after core
delivery and archive verification; it is not a replacement scene-generation
route. `platform-defaults-v1` remains a historical contract only.

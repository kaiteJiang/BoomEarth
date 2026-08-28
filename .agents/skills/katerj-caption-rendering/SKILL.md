---
name: katerj-caption-rendering
description: Use when QC-passed BoomEarth caption timing needs a styled preview, burn-in derivative, overlay, or render validation.
---

# KaterJ Caption Rendering

## Outcome

Render readable, fixed-anchor production captions without changing the canonical timing timeline.

## Workflow

1. Require the six artifacts from `katerj-audio-subtitles`, a passing `caption-qc.json`, and the closed-line contract from `katerj-oral-linebreaks`.
2. Use registered style `anchor-dark` unless the handoff explicitly selects `anchor-light`.
3. Render one representative still over the lightest and busiest background state.
4. Check font readability, tight panel geometry, lower safe margin, face/avatar clearance, and single-line fit.
5. Render the captioned derivative while preserving the clean master and portable SRT/VTT.
6. Run the caption-render validator and archive its JSON report.

## Style contract

At 1080p, use the registered STHeiti Medium typography and a shrink-wrapped rounded panel at one stable bottom-center anchor. Scale geometry from frame height. Do not add text stroke, heavy shadow, live blur, full-width bands, karaoke, or drifting baselines.

## Acceptance

- Every frame contains at most one cue and every cue remains one line.
- Every cue is a closed semantic unit; short Chinese cues are normally 6-12 characters and never exceed the 14-character review limit without a documented indivisible-name exception.
- Start/end values come directly from canonical `captions.json`.
- Captions stay inside the safe zone and do not cover content or the presenter.
- Caption-render QC binds source video, output video, caption file, frames, and hashes.

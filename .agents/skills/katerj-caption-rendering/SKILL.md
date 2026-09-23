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
4. Check font readability, text-measured tight panel geometry, lower safe margin, face/avatar clearance, and single-line fit. Reserve local backing-color emphasis for important terms or core judgments only. Most cues have no emphasis; never mark one word in every cue just to make the screen busier. A selected cue has at most one short focus phrase, and the unmarked words remain in the same panel.
   Write those choices only after the final six caption artifacts exist, in optional `工程/caption-emphasis.json`: `{"schema_version":1,"captions_sha256":"<captions.json SHA-256>","highlights":[{"index":0,"text":"原字幕中的重点词"}]}`. Index is zero-based; text must occur exactly once in that cue. The renderer binds the sidecar to the canonical captions hash, includes it in the immutable render snapshot, and rejects stale or ambiguous selections. Leave the sidecar absent when no cue needs emphasis; never edit `captions.json` to add styling.
5. Render the captioned derivative while preserving the clean master and portable SRT/VTT.
6. Run the caption-render validator and archive its JSON report.

## Style contract

At 1080p, use the registered STHeiti Medium typography and a shrink-wrapped rounded panel at one stable bottom-center anchor. Measure each cue's actual rendered text width and add consistent padding; never use a fixed-width subtitle box. Scale geometry from frame height. Optional focus emphasis is a small backing-color patch behind the selected words, using exact original spelling and unchanged caption start/end times. Keep it sparse and readable; do not add text stroke, heavy shadow, live blur, full-width bands, karaoke, or drifting baselines.

## Acceptance

- Every frame contains at most one cue and every cue remains one line.
- Every cue is a closed semantic unit; short Chinese cues are normally 6-12 characters and never exceed the 14-character review limit without a documented indivisible-name exception.
- Start/end values come directly from canonical `captions.json`.
- Captions stay inside the safe zone and do not cover content or the presenter.
- Any focus patch fits its selected words, and neither the patch nor panel extends unnecessarily beyond the text.
- Caption-render QC binds source video, output video, caption file, frames, and hashes.

---
name: katerj-audio-subtitles
description: Use when final locked narration or final merged media needs production SRT, VTT, word timing, phrase captions, and alignment QC.
---

# KaterJ Audio Subtitles

## Outcome

Publish exactly six caption artifacts whose timing comes from one ASR request against the exact locked final WAV.

## Workflow

1. Require the final PCM WAV, its manifest-recorded SHA-256, the exact `segments.jsonl` narration contract, and its manifest-recorded hash. The run command must bind that contract again with `--expected-script-sha256`.
2. Create an immutable Volcengine flash Base64 plan for one request using resource `volc.bigasr.auc_turbo`.
3. Run the approved request with zero retry, redirect, TOS upload, polling, or provider fallback.
4. Align provider words to the original narration text; provider text supplies timing, not display spelling.
5. Group punctuation-free semantic phrases using real word boundaries. Keep product names intact and connectors with their clause.
6. Publish one no-clobber directory containing `asr-result.json`, `captions_words.json`, `captions.json`, `captions.srt`, `captions.vtt`, and `caption-qc.json`. An existing destination is a hard stop, and a failed build keeps the complete diagnostic in the private attempt directory without merging partial files.

## Caption rules

Use one single-line cue at a time, at least one frame apart. Reject overlap, fragments shorter than 0.5 seconds, split connectors, excessive reading speed, or alignment coverage below 0.90.

## Acceptance

- `caption-qc.json` reports pass and `volcengine-word-timestamps`.
- `narration_sha256` matches the final WAV used by the video.
- All six files publish together without merging or overwrite.
- Renderers consume `captions.json` times directly and never interpolate.

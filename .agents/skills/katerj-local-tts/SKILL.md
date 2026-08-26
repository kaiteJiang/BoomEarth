---
name: katerj-local-tts
description: Use when a BoomEarth video, audition, subtitle source, or digital-human master needs the approved local cloned narration voice.
---

# KaterJ Local TTS

## Outcome

Create one lossless final narration and manifest from the canonical private voice reference without changing provider, reference, or speed.

## Locked route

Read `automation/config/tts-routing.json`. New productions use local IndexTTS2 voice `user-indextts2-black-gold-v3`, the canonical private lossless reference, and top-level playback speed 1.12×. Never use an old output as the next speaker reference.

## Workflow

1. Validate mixed Chinese/English names against the pronunciation lexicon before synthesis.
2. Build a JSONL segment contract containing text and intentional tail silence.
3. Run the existing IndexTTS2 helper in dry-run mode to validate environment, model, reference, and batch.
4. Render PCM WAV, preserve the pre-speed raw audit WAV, then apply pitch-preserving 1.12× tempo.
5. Publish `voice_manifest.json` with provider, model, voice ID, reference hash, segment-contract hash, output hash, speed, pronunciation contract, and `used_fallback=false`.
6. Treat the exact final WAV as the only later ASR and avatar audio source.

## Prohibited fallbacks

Do not use Windows speech, browser speech, MiniMax, an arbitrary cloud voice, MP3 reference audio, or a “latest” generated file. If the local model or canonical reference is unavailable, repair or stop.

## Acceptance

- WAV decodes as lossless PCM and matches its manifest SHA-256.
- Provider and voice ID equal the locked route.
- Every segment used the same provider and approved pronunciation.
- Playback speed is 1.12 and fallback is false.

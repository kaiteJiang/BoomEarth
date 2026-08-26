---
name: katerj-source-transcription
description: Use when a private BoomEarth source WAV needs a faithful transcript for analysis and rewriting before public production begins.
---

# KaterJ Source Transcription

## Outcome

Produce a private, hash-bound source transcript with one approved Paraformer request; do not confuse this transcript with the final-video subtitle timeline.

## Workflow

1. Require an existing `work_id` whose source media passed acquisition checks.
2. Run `automation/scripts/normalize_source_audio.py <work_id>`.
3. Create `source-transcription-plan.json` with `automation/scripts/transcribe_source.py plan <work_id>`.
4. Bind approval to the normalized WAV hash, plan hash, Paraformer provider, one request, possible usage, timeout, zero retry, and zero fallback.
5. Execute `transcribe_source.py run <work_id> --approval <private-json>` once.
6. Keep transcript text, word times, request evidence, and media identifiers inside the ignored work item.

## Separation rule

Source transcription helps understand and rewrite someone else's input. Production captions must later come from the final locked narration through `katerj-audio-subtitles`.

## Acceptance

- The ledger reaches `transcript_ready`.
- Manifest hashes match the normalized WAV and transcript.
- No source text appears in public handoffs, Git, or reports.
- Failure stops the workflow without another provider or automatic retry.

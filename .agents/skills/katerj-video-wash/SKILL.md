---
name: katerj-video-wash
description: Use when an authorized local video, audio file, or playable video URL must become a reviewed source-free BoomEarth production handoff.
---

# KaterJ Video Wash

## Outcome

Publish a source-free `交接稿.md` whose receipt and ledger state prove that private acquisition, transcription, rewrite, and review completed in order.

## Private contract

All URLs, source titles, media, transcripts, provider responses, and source analysis stay in `01-内容生产/视频工作台/.internal/洗稿/<work_id>/`.

## Workflow

1. Register authorized input with `automation/scripts/source_intake.py`.
2. For a URL, keep it in a private input file, plan exactly one provider, and run `automation/scripts/acquire_source.py` only with a matching approval. The provider selector is `--provider yt-dlp|tikhub`; one execution chooses one provider only. On failure, use the [continuous recovery loop](../../../docs/VIDEO-CONTINUOUS-ORCHESTRATION.md): diagnose, repair/test the extractor, then create a fresh immutable plan/receipt for the next read-only method. Do not re-run the failed plan.
3. Normalize the locked source audio, create a Paraformer plan, and run one approved source transcription.
4. Create the rewrite brief with `automation/scripts/prepare_rewrite.py`.
5. Astra writes the private candidate directly. Use `katerj-script-rewrite` only for factual/logic/source-free/script-integrity review evidence and compiler handoff; new review schema 3, candidate schema 1, no forced opening template or human-writing/AI-fingerprint Skill calls. Preserve older bound schemas for existing work.
6. Publish only through `automation/scripts/compile_source_handoff.py compile`.
7. Verify `publication-receipt.json` and ledger state `handoff_ready` before handing off to `katerj-video-director`.

## External gates

Acquisition and source transcription are separate provider actions. Each plan binds work ID, input hash, plan hash, provider, request limit, cost notice, timeout, zero retry, and zero fallback.

## Acceptance

- The public handoff contains no source identifier or private path.
- 不得直接写入 `待制作`; only the compiler may publish there.
- The candidate and review hashes match the receipt.
- A failed provider stage stops **that transaction**. Preserve its evidence; a new transaction may use the repaired method or another supported method under a fresh plan/receipt and budget. Do not claim a handoff until source media and transcript pass.
- No one hand-writes a public handoff around the compiler.

---
name: katerj-script-rewrite
description: Use when private source material or a project brief must become a source-free, conversational Chinese short-video script with review evidence.
---

# KaterJ Script Rewrite

## Outcome

Build an original spoken narrative with a provable opening, complete explanation, and clean closing while keeping source identity and internal review evidence private.

## Opening contract

Every new `rewrite-candidate.json` records an `opening_contract` with `hook_3s`, `audience_pain`, `value_promise`, `cta`, `bridge`, a cover hook, a title formula, and the consecutive proof segments that fulfill the promise. Save review evidence as `rewrite-review.json`.

## Workflow

1. Read the private rewrite brief and source transcript; extract facts, mechanisms, useful examples, and audience pain.
2. Rebuild the order and explanation from scratch. Do not perform sentence-by-sentence synonym replacement.
3. Use `katerj-human-writing` to remove template language while preserving facts and technical terms.
4. Select the opening with `katerj-video-hook`, bind platform assets with `jl-multiplatform-titles`, then validate it with `katerj-hook-review`.
5. Run `katerj-ai-writing-review` and `katerj-resonance-review`.
6. Generate 8–12 evidence-bound title candidates with `katerj-video-titles`.
7. Save candidate and reviews in the private work item; publish only through `compile_source_handoff.py compile`.

不得直接写入 `待制作`; the source-free compiler owns that publication boundary.

## Acceptance

- The opening order is hook → pain → value → one CTA → body bridge.
- Long sources retain a full explanatory arc rather than collapsing into a thin summary.
- No source URL, account, title, private path, or long copied passage survives.
- Every review is bound to the candidate SHA-256 and passes before publication.

---
name: katerj-oral-linebreaks
description: Use when Chinese narration, teleprompter copy, or single-line captions have awkward breaks, dangling endings, orphaned continuations, or lines that are difficult to read in one glance.
---

# KaterJ Oral Linebreaks

## Outcome

Turn Chinese spoken copy into closed, breathable semantic units. A line must land naturally on its own; the next line must not exist merely to finish its grammar.

## Route

- Narration or teleprompter: preserve wording and insert breathing boundaries, normally 8-14 Chinese characters.
- Untimed single-line captions: prefer 6-12 Chinese characters and treat 14 as a hard review limit.
- Timed SRT or VTT, or any caption bound to locked narration: preserve existing cue times and spoken wording. Re-segment only when exact word timestamps exist; otherwise report the problem without inventing timing.

If the input contains no timestamps and is not bound to locked narration, treat it as untimed. Do not infer that a plain-text subtitle draft is timed.

Read [the boundary rules](references/boundary-rules.md) before changing line breaks.

## Workflow

1. Protect product names, English sentences, numbers with units, negation pairs, and core verb-object phrases.
2. Find boundaries after a complete fact, action, condition, contrast, or result.
3. Read both sides aloud. Reject a boundary if the first line waits for completion or the second line begins as an orphaned predicate or result.
4. Apply the length target only after semantic closure. For untimed short captions, a minimal structural rewrite is allowed when no closed short form exists. It may remove a framing phrase, restore an existing subject, or turn an existing clause into a direct statement; it must reuse the original concepts and may not introduce a new abstraction, label, fact, cause, or judgment.
5. Before locking narration, run `python scripts/check_caption_lines.py --file <path> --max-cjk 14`. Fix every reported closure or length issue.

## Production boundary

Run this review before TTS whenever possible. After narration is locked, display text must remain alignable to the spoken words. Do not rewrite locked narration merely to improve a caption; use real word timing to choose a safer boundary or stop for an upstream script correction.

## Acceptance

- Each line has a natural spoken landing and stays single-line in the target frame.
- Short captions are normally 6-12 Chinese characters and never exceed 14 without an indivisible proper-name exception.
- No line ends with a dangling connector or leaves an orphaned continuation.
- Complete English sentences, names, numbers, units, and negation structures remain intact.
- Timed subtitle changes retain evidence from the locked narration's real word timestamps.

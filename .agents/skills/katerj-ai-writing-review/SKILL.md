---
name: katerj-ai-writing-review
description: Use when a Chinese public draft needs a diagnostic pass for repetitive AI-writing structures before publication or handoff.
---

# KaterJ AI Writing Review

## Outcome

Identify the exact passages that feel generated or over-engineered and return a pass/fail review without silently rewriting the author.

## Workflow

1. Identify the format: spoken video, social post, article, technical note, or formal document.
2. Scan in reading order for repeated contrast shells, uniform sentence length, perfect paragraph symmetry, fake reader objections, abstract insight markers, translation-style syntax, and slogan endings.
3. Quote only the minimum passage required to locate each issue.
4. Label each finding high, medium, or low signal and explain the concrete reading problem.
5. Distinguish a structural writing defect from a weak topic or missing evidence.
6. Return `pass` when no material pattern remains; do not manufacture findings to fill a report.

## Review record

For BoomEarth rewrite packages, store the result in the private review artifact and bind it to the candidate SHA-256. Public handoffs carry only the approved script, never the diagnostic evidence.

## Acceptance

- Every finding points to exact text and a visible pattern.
- Short-video rhythm is not penalized merely for being concise.
- Technical terms and necessary safety language are not removed as “AI-like”.
- A pass means the document can proceed to the next review gate.

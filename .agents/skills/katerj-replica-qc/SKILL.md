---
name: katerj-replica-qc
description: Use when a reference-video recreation needs a declared fidelity level, frame-accurate comparison, repair loop, or reusable component evidence.
---

# KaterJ Replica QC

## Outcome

Approve only the fidelity level proved by decoded frames, timebase evidence, media probes, and archived checksums.

## Workflow

1. Declare the reference segment, candidate, renderer, fidelity label, and evidence required.
2. Lock FPS, frame count, PTS mapping, pixel format, duration, and audio presence. Extract exact frame ranges for acceptance.
3. Choose exact replay, parametric recreation, or a clearly separated hybrid.
4. Locate mismatches with coarse samples, then inspect full-frame windows around the first or largest failure.
5. Repair one visible cause at a time and rerun neighboring boundaries.
6. Pass three gates: source-asset round-trip, real runtime timeline playback, and decoded delivery comparison.
7. At segment joins, keep one inclusive boundary frame and verify the frame before, at, and after the join.
8. Archive alignment report, patch log, metrics, worst frames, boundary frames, media probe, and checksums.

## Claim discipline

Bit-exact, frame-aligned, visually matched, and style-inspired are different outcomes. A screenshot contact sheet can locate defects but cannot prove full-frame equality.

## Acceptance

- The chosen fidelity label maps to explicit metrics.
- Runtime and delivered MP4 are both tested.
- No known earlier mismatch is hidden by a later repair.
- Failed gates report the first failing frame or timestamp and remain unapproved.

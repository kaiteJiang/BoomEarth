# Alignment Workflow

## Contract And Timebase

Before editing, record:

- reference and candidate paths
- source and candidate FPS, start time, dimensions, pixel format, and frame count
- inclusive source start and end frame
- expected frame count
- fidelity level and thresholds
- renderer and final output paths

For exact ranges, extract with an ffmpeg `select=between(n,start,end)` filter or
`extract_frame_range.py`. Timestamp seeking is useful for discovery but can land
on a neighboring decoded frame.

## Locate With Samples, Approve With Frames

Use `0.5s` samples to understand the whole piece. Use `0.1s` or explicit times
around fast typography, cursors, rails, list switching, scene cuts, or a failing
window. Describe text, subject geometry, background, transition, interaction,
and scene-boundary state.

Sampling finds repair targets. Render-frame-exact and encoded-frame-aligned
approval require every frame in the declared range.

## Repair Loop

1. Generate candidate frames at the same source identities.
2. Compare the full frame and active-component crops.
3. Name the earliest or largest mismatch.
4. Patch one visible cause: timing, geometry, asset, animation, or encoding.
5. Regenerate the same window and adjacent frames.
6. Rerun the full required range before approval.

Do not let a later visually impressive frame hide an earlier red frame.

## Architecture Decision

### Exact Replay

Use verified lossless frames or sprite rows when fixed content must be exact.
If sprite rows change dynamically:

- verify every cell round-trips to its source frame
- preload the next row while the current row is visible
- use at least two alternating buffers
- inspect the first frame of every row
- keep a standalone composition for direct segment rendering

Changing `background-image` on the frame being captured is a decode race, even
when the render command succeeds.

### Parametric Motion

Use semantic DOM/SVG/React/Three.js when text, logos, counts, or timing must be
generated from content. Verify component geometry and neighboring timestamps.

### Hybrid

Keep exact replay and parametric motion as separate deliverables. Exact replay
proves a reference; parametric motion serves future AI-generated content.

## Three Evidence Gates

### Asset Gate

- reference first and final frame identities are correct
- extracted count equals `(end_frame - start_frame) + 1`
- lossless intermediate or sprite round-trip is zero error
- no blank, missing, or stale asset frame exists

### Runtime Gate

- drive the registered production timeline sequentially
- do not invoke a test-only helper that bypasses production updates
- capture every required frame immediately after the production time update
- sample arbitrary seeks separately
- test every scene cut and dynamic-asset boundary
- rerun previously aligned adjacent ranges after integration

### Delivery Gate

- run renderer validation before render
- render a standalone segment when possible
- decode the delivered file back to a lossless frame sequence
- compare every frame and nearby temporal candidates
- report average and maximum error, worst frames, boundary maximum, and temporal mismatches
- probe dimensions, FPS, duration, frame count, pixel format, and audio
- compare working and archive copies by SHA-256

A green renderer log proves that encoding finished, not that the right pixels
were encoded.

## Codec Baseline

For lossy delivery, encode the reference frame sequence with the same codec,
CRF, pixel format, FPS, and dimensions. Compare both the control and candidate
against the lossless reference. Approve candidate error relative to that control.
When no control is available, declare the fallback thresholds used.

## Segment Extension And Joins

For adjacent inclusive segments:

1. keep the previous segment's final boundary frame
2. skip the new segment's duplicate first frame
3. check the frame before, at, and after the join
4. verify combined frame count and duration
5. rerun both segment regressions

## Component Capture

Each catalog entry records `exact-replay` or `parametric-motion`, source range,
content pattern, inputs, timing contract, stack, evidence, and limits. Never let
the catalog imply that fixed reference pixels accept arbitrary content.

## Completion Report

State separately:

- what the asset gate proved
- what production runtime proved
- what decoded delivery proved
- what codec error remains
- what previous range was regressed
- what join frames were checked
- where final and QC files were archived

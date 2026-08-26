# Replica Fidelity Levels

Name only the level proved by current evidence.

## 1. Source Bit Exact

Use when the output reuses or remuxes the source stream without changing decoded
content.

Required evidence:

- matching SHA-256 and `cmp`, when byte identity is promised
- or decoded PSNR infinity and SSIM 1.0 when container bytes may differ
- matching resolution, FPS, duration, frame count, media start, and pixel format

This does not prove a hand-authored animation was reconstructed.

## 2. Render-Frame Exact

Use when the renderer's lossless frames match the target frames exactly, but the
delivery codec changes bytes or decoded colors.

Required evidence:

- exact reference frame range and count
- every lossless candidate frame compared
- zero changed pixels, MAE 0, and maximum channel delta 0
- real runtime timeline capture, not a test-only state setter

State explicitly that the claim applies before lossy encoding.

## 3. Encoded Frame Aligned

Use when every decoded delivery frame has the correct temporal identity and only
codec-level pixel error remains.

Required evidence:

- same FPS, frame count, duration, dimensions, and media start
- per-frame metrics, worst frames, and dynamic-asset boundary metrics
- zero significant temporal mismatches against nearby reference frames
- preferably a control encode of the reference with identical codec settings

Use the control encode plus a declared margin. Without a control, the project
fallback is maximum frame MAE `2.5`, maximum boundary MAE `2.5`, and temporal
lead `0.1`; report these as project thresholds, not universal truths.

## 4. Visual Aligned

Use for a hand-authored recreation that matches timing, layout, motion,
typography, and color closely enough for human review.

Required evidence:

- reference timeline and candidate comparison reports
- side-by-side contact sheets at a declared interval
- dense checks around transitions and the first failing window
- accepted differences listed explicitly

## 5. Style Aligned

Use when the reference supplies a design language rather than exact shots.
Capture palette, background treatment, typography, motion grammar, rhythm,
component motifs, and transition vocabulary. Exact frame timing is not required.

## Promise Rules

- `pixel perfect` alone is ambiguous; resolve it to one of levels 1-3.
- Do not call an H.264 delivery bit exact because its pre-encode PNGs were exact.
- Do not call sampled screenshots frame aligned.
- Do not call an exact-replay texture layer a parameterized motion component.

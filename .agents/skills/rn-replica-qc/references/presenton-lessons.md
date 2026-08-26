# Presenton Case Notes

Use these failures as regression cases, not as a universal visual template.

## First Rebuild Failures

- Work began before a strict source timeline existed.
- Generic type animation replaced the actual intermediate text states.
- A persistent neon line was added where the reference used sparse stars and a
  weak bottom glow.
- Hero subjects were too small and distant.
- Unreferenced purple flash frames appeared.
- The first claimed pixel-level result was source-stream reuse, not a
  hand-authored reconstruction.

## Full-Frame Replica Failures

### Test Path Was Not Production Playback

A Playwright helper directly updated the exact-replay layer, so sampled tests
passed while normal GSAP playback still showed the rough DOM scene. Production
timeline `onUpdate` and `hf-seek` must reach the same render state, and the test
must drive the registered timeline rather than the helper.

### Dynamic Image Swap Raced Capture

One background buffer changed sprite rows at each seven-frame boundary.
HyperFrames sometimes captured the first frame before the new PNG decoded. The
average video looked correct, but row-boundary frames spiked. Two alternating
buffers fixed it: play one row while preloading the next into the hidden buffer,
then swap opacity at the boundary.

### Render Success Was Too Weak

HyperFrames completed and its selected self-check frames passed, but those points
did not include every row boundary. Decoding the MP4 and comparing all frames
exposed the stale-row captures.

### Zero Error Needed A Layer Name

Lossless timeline screenshots were exactly equal to reference PNGs. H.264
`yuv420p` delivery was not: it carried small codec error. Reports must say
`render-frame exact` for the lossless layer and `encoded frame aligned` for the
decoded MP4.

## Proven Pattern

1. Map the segment to exact inclusive source frame numbers.
2. Extract every reference frame.
3. Build seven-frame sprite rows below the browser texture-width limit.
4. Round-trip every sprite cell and require zero error.
5. Drive the real timeline through every frame and require zero error.
6. Use two buffers and preload the next row.
7. Render an independent segment composition.
8. Decode every MP4 frame; inspect worst, boundary, and temporal candidates.
9. Rerun the previous segment after integration.
10. Skip duplicate boundary frames when extending the continuous output and
    inspect three frames around every join.

## Durable Rules

- Start with the fidelity claim and its evidence, not the renderer choice.
- Sample to understand; compare every frame to approve exact work.
- Test the production time path.
- Treat dynamic asset decode as part of the animation contract.
- Separate exact replay from parameterized components.
- Name the evidence layer whenever saying `zero error` or `pixel aligned`.

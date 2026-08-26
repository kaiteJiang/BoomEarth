# Subtitle artifact contract

## Directory publication

The formal subtitle result is one no-clobber directory containing exactly six
canonical files:

- `asr-result.json`
- `captions_words.json`
- `captions.json`
- `captions.srt`
- `captions.vtt`
- `caption-qc.json`

All six payloads are serialized before a private same-parent stage is created.
The stage is published as one directory without replacing or merging into an
existing destination. The observable outcomes are:

- No formal directory: preflight, provider, alignment, serialization, write,
  or publication failure.
- Complete diagnostic directory plus exit 1: `caption-qc.json` has QC status
  `fail`. This result is for diagnosis only and cannot enter rendering or
  delivery.
- Complete production directory plus exit 0: `caption-qc.json` has QC status
  `pass`.

## Canonical files

`captions_words.json` is a list of ASR timing units:

```json
{"text":"字","start":1.24,"end":1.36,"isGap":false}
```

Gaps of at least 0.2 seconds may be represented with `isGap: true` and empty
text.

`captions.json` is a list consumed by renderers:

```json
{"start":1.24,"end":3.68,"text":"这是一句字幕。","source":"volcengine-word-timestamps"}
```

Renderers must use `start` and `end` directly. They must not recalculate timing
from string length, scene duration, or audio segment duration.

`caption-qc.json` contains:

- `status`: `pass` or `fail`
- `timing_source`: must be `volcengine-word-timestamps`
- `alignment_coverage`: matched original-script characters divided by total
  normalized script characters
- `script_characters`, `matched_characters`, `asr_characters`
- `word_units`, `caption_count`, `overlap_count`, `errors`, `warnings`
- `max_reading_units_per_second`, `short_fragments`, `split_connectors`
- `source_media`: the basename of the exact final media, never an absolute path
- `narration_sha256`: SHA-256 of the exact final media bytes used for timing
- `asr_resource_id`

## Delivery gate

For contracts with `captions: asr-word-timestamps`, delivery fails when any
canonical file is missing, `status` is not `pass`, timing source is not the
required value, coverage is below 0.90, captions overlap, the recorded source
media no longer exists, or its duration materially differs from the final MP4.

Readability also fails when a cue is shorter than 0.5 seconds, a one-token
fragment is isolated, a discourse connector is split across two cues, or the
maximum reading speed exceeds 12 reading units/s. English product/model tokens
count as semantic units and retain meaningful internal spaces. A speed above 9
units/s is recorded as a warning for human review.

The renderer may burn captions into the MP4 or keep them as an overlay track,
but it must archive the SRT and QC JSON next to the project media.

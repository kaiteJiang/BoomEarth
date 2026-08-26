# Source-to-Video Resumability and Platform Covers Design

Date: 2026-08-22

Status: Approved design

Scope: Harden the existing BoomEarth production workflow without replacing its thin-orchestrator and specialist-Skill architecture.

## Outcome

The next source-to-video job should resume from verified local evidence, avoid duplicate paid work, complete the HeyGen subscription path deterministically, compose the avatar through the canonical local finalizer, and deliver platform-ready Douyin and WeChat Channels covers with the archived final.

## Non-goals

- Do not replace the current professional Skills with a monolithic one-click system.
- Do not relax provider-specific authorization, privacy, provenance, or delivery gates.
- Do not expose source URLs, signed download URLs, provider identifiers, credentials, transcripts, or private media in tracked files.
- Do not require new ImageGen calls for covers when approved scene artwork already exists.

## Workflow Design

### 1. Resume before work

At every production entry, inspect the project state and immutable receipts before planning a new external call. Classify the next action from observable evidence:

| Evidence | Next action |
| --- | --- |
| No approved request receipt | Build the immutable request plan and ask for exact authorization. |
| Approved request, no provider job evidence | Execute exactly the authorized generation once. |
| Provider job exists and is incomplete | Read status only under the retrieval authorization; do not regenerate. |
| Provider job is complete but local master is absent | Download once under the retrieval authorization; do not regenerate. |
| Local master hash is verified | Continue local composition and QC without another provider call. |
| Final archive already passes delivery checks | Report the existing delivery; do not rerender. |

Failures preserve receipts and versioned derivatives. A reporting-only failure after a successful render first triggers archive verification, never an automatic rerender.

### 2. HeyGen subscription path

The production director uses the official `heygen-video` Skill and chooses the authenticated subscription connector before API-key CLI generation when both are available. The locked local IndexTTS2 narration remains the only audio input.

Generation and retrieval are two authorization scopes:

1. Generation authorization binds the project, locked WAV and hash, immutable request plan, same-group ready portrait selection, subscription budget, timeout, zero retry, and zero fallback.
2. Retrieval authorization binds the completed job evidence and permits one status read plus one download. A completed remote job must never be regenerated because local retrieval failed.

The connector may select its supported lip-sync engine when the subscription route does not expose an Avatar III engine selector. It must not switch the avatar group, narration provider, or audio asset.

### 3. Deterministic private retrieval

A local helper receives the private signed URL through standard input, never a command-line argument. It performs one bounded download, verifies that the result is a decodable video, records a redacted receipt and SHA-256 in ignored private runtime storage, and never prints or publishes the URL.

The helper owns Windows stdin/EOF behavior so production agents do not recreate ad-hoc PowerShell or PTY commands.

### 4. Avatar-aware finalization

The canonical content finalizer accepts an optional verified avatar master. When present, its mux stage invokes the existing `headroom_08-circle-lower-left` compositor:

- 1920x1080 canvas
- 210 px lower-left circle
- x=70
- bottom margin 96 px
- locked local narration as the final audio track

The avatar master and provider retrieval receipt are protected private inputs, not public publication artifacts. The final MP4 is the public evidence that the avatar was composed. All existing no-avatar finalization behavior remains unchanged.

### 5. Platform cover stage

After the recommended final passes delivery checks, a focused cover stage reuses a semantically relevant, already approved scene illustration or a QC-passed final frame. It does not call ImageGen by default.

Outputs:

| Artifact | Canvas | Purpose |
| --- | --- | --- |
| Douyin upload cover | 1080x1920 (9:16) | Upload/select-cover canvas. |
| Douyin homepage preview | 1080x1440 (3:4) | Verifies the profile-grid crop and safe zone. |
| WeChat Channels cover | 1080x1260 (6:7) | Profile and feed cover. |

The cover headline uses the top recommended title candidate. For non-queue jobs, changing the selected title only rerenders covers locally. For queue jobs, the cover headline is a separate publication asset and does not create `视频标题.md`.

Cover QC requires exact dimensions, readable headline at phone scale, subject and headline inside crop-safe zones, semantic match to the final video, no source/private identifiers, and no unapproved logo or watermark. The stage writes final cover files to `封面/` and preview/contact-sheet evidence to `质检/`.

### 6. Archive contract

The unified archive adds `封面/` beside `成片/`, `质检/`, and `工程/`. The delivery checker requires the three cover artifacts and cover-QC receipt for new projects while preserving compatibility for historical archives and projects whose contract predates the cover requirement.

## Skill Ownership

- `ra-source-to-video`: thin routing, state-resume rule, and final cover-stage handoff.
- `ra-video-production-director`: external authorization boundaries, HeyGen subscription/retrieval recovery, avatar-aware finalization, archive and delivery gates.
- `ra-video-cover` (new focused Skill): platform canvases, title binding, safe-zone composition, local rendering, QC, and archive artifacts.
- Existing professional Skills retain source acquisition, rewriting, illustrations, TTS, subtitles, caption rendering, title generation, and final media QC.

## Verification Strategy

Implementation follows test-first development:

1. Add failing tests for resume-state decisions, signed-URL stdin privacy, one-download behavior, optional avatar mux, private/public publication separation, cover dimensions, safe zones, title rebinding, and legacy archive compatibility.
2. Implement the smallest scripts and Skill changes that pass each test.
3. Run focused tests, Skill validators, then the relevant production regression suite.
4. Run only offline fixtures; no provider generation, status call, signed-URL download, TTS, ASR, ImageGen, or HeyGen charge occurs during verification.

## Acceptance

The design is complete when a future agent can determine the next step from local evidence, cannot accidentally regenerate a completed HeyGen job, can finalize with the canonical circle avatar without a private-manifest mismatch, and always archives QC-passed Douyin and WeChat Channels cover deliverables with a new recommended final.

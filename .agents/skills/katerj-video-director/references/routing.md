# KaterJ production routing

Choose the narrowest specialist that owns the requested artifact.

| Need | Route |
| --- | --- |
| Authorized source media to public handoff | `katerj-video-wash` |
| Final local narration | `katerj-local-tts` |
| Final-audio word timing and SRT/VTT | `katerj-audio-subtitles` |
| Caption appearance and burn-in | `katerj-caption-rendering` |
| Default sponge full-scene images | `ra-video-illustrations` + `katerj-xiaohei-illustrations` body rules + root sponge DESIGN.md |
| Explicit or historical Xiaohei images | `katerj-xiaohei-illustrations` |
| Source-free direct writing and review | Astra + `katerj-script-rewrite` evidence/compiler; no human-writing or AI-fingerprint Skill |
| Continuous anti-slideshow motion | `katerj-motion-director` |
| Reference recreation evidence | `katerj-replica-qc` |
| Registered schema 2/3/4 illustration themes | `ra-video-illustrations` |
| Post-delivery 3:4 work cover | `ra-video-cover` |

For every narrated final, the locked WAV is the global time axis. Provider completion never routes back to generation. A verified archive is reported rather than rendered again.

Default presenter is none. Official HeyGen is loaded only for an explicit choice or an existing bound contract. For cold starts and artifact-based recovery read the root docs/VIDEO-PRODUCTION-RUNBOOK.md; legacy ra-video-production-director references do not select new defaults.

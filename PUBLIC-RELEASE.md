# Public release provenance

This repository is a privacy-reviewed public snapshot of BoomEarth source commit
`2480620`.

The public snapshot intentionally starts with one clean commit. Internal development
history, local operator instructions, private TTS routing, `.env`, source work items,
provider responses, runtime media, transcripts, and local binaries are not included.

Before enabling local TTS, copy `automation/config/tts-routing.example.json` to the
ignored `automation/config/tts-routing.json`, replace every placeholder with paths and
hashes from your own machine, and keep the real file out of Git. Provider calls remain
opt-in and require their own authorization contracts.

Third-party materials retain the licenses and notices recorded in `LICENSE-NOTES.md`.

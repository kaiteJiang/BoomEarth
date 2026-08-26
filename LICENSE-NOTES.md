# BoomEarth Skill provenance and license notes

The maintained workflow entrypoints are the original BoomEarth instructions under
`.agents/skills/katerj-*`. Their runtime identifiers use lowercase kebab-case to
conform to the Agent Skills specification; the public brand spelling is `katerJ`.
The canonical mapping is recorded in `.agents/skills/katerj-skill-map.json`.

The legacy directories listed below now expose only thin compatibility entrypoints,
but some of those directories still contain vendored scripts, references, fonts,
examples, or other assets required by the working pipeline. Rewriting the entrypoint
instructions does not relicense those retained files. Their original notices and the
following provenance map continue to apply until a separately verified clean-room
replacement removes the dependency.

## Retained vendor provenance

This mapping is tied to the pinned `https://github.com/Pluviobyte/rnskill.git` commit `766e4eba4162630c783b531f531a3fafe6f45c81` installed on `2026-08-09`. The table has one row for each of the 18 installed skills.

The repository default license is **CC BY-NC 4.0**. The pinned repository `CREDITS.md` states that most skills are original work by `@Pluvio9yte` and explicitly maps `dbs` plus all `dbs-*` skills to CC BY-NC 4.0. The three selected `dbs-*` entries below therefore use that mapping. `ian-xiaohei-illustrations` has its own upstream `LICENSE` and `NOTICE.md`, and `CREDITS.md` maps it to MIT.

CC BY-NC components are **not cleared for commercial redistribution**. This is a provenance and permission note, not a legal conclusion. Review the applicable license, attribution, third-party assets and any commercial-use question with the rights holder or qualified counsel before commercial use.

| Skill | Mapped license | Pinned-repository evidence | Commercial-use note |
|---|---|---|---|
| `ra-选题` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` says most skills are original work by `@Pluvio9yte`; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-hook` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-video-wash-pipeline` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; `SKILL.md` has no license override. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-video-download` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; `SKILL.md` has no license override. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-逐字稿提取skill` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; `SKILL.md` has no license override. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-洗稿` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; `SKILL.md` has no license override. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-人话` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; `SKILL.md` has no license override. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-video-title` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; `SKILL.md` and its reference have no license override. | Not cleared for commercial redistribution; review before commercial use. |
| `dbs-ai-check` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` explicitly maps `dbs` plus all `dbs-*` to CC BY-NC 4.0. | Not cleared for commercial redistribution; review before commercial use. |
| `dbs-hook` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` explicitly maps `dbs` plus all `dbs-*` to CC BY-NC 4.0. | Not cleared for commercial redistribution; review before commercial use. |
| `dbs-resonate` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` explicitly maps `dbs` plus all `dbs-*` to CC BY-NC 4.0. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-video-production-director` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `tts-skill` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `ra-audio-to-subtitles` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `skill-captions` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `rn-motion-director` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `rn-replica-qc` | CC BY-NC 4.0 | Repository `LICENSE`; `CREDITS.md` default/original-work mapping; no skill-local license override found. | Not cleared for commercial redistribution; review before commercial use. |
| `ian-xiaohei-illustrations` | MIT | `skills/ian-xiaohei-illustrations/LICENSE`; its `NOTICE.md` attributes Ian and the upstream source; repository `CREDITS.md` maps this skill to MIT. | Preserve MIT notice and upstream attribution. Review bundled example-asset provenance before any commercial use. |

## Provenance mapping check

- Expected rows: 18
- Mapped rows: 18
- `license_mapping_mismatch_count=0`
- The KaterJ entrypoints are new workflow instructions; retained vendor code and assets
  are not claimed as KaterJ originals.
- No vendor license file, notice or upstream content may be removed merely because an
  old Skill name is now a compatibility bridge.

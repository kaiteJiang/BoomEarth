# BoomEarth V1 local sample design

## Visual intent

一个平静、清晰的中文解释性样片：先把零散想法收束，再落到一条可交付的本地制作路径。画布为 1920x1080；内容固定锚定在左上和中右区域，底部留给独立的 `anchor-dark` 字幕面板。两段场景以克制的暖色 focus dissolve 连续衔接，不使用跳切或高能特效。

## Palette

- `#F7F1E7` — warm-white canvas and primary scene background.
- `#17130F` — near-black primary text and anchor-dark caption panel.
- `#C63F2F` — restrained red for the opening thought marker.
- `#E47A35` — orange for focus and process highlights.
- `#2C63A3` — blue for the local-delivery marker and structural rules.
- `#DED4C4` — warm divider and secondary panel treatment.

## Typography

- Display Chinese: `SimSun`, `STSong`, `Songti SC`, serif. It gives the central statement a measured editorial voice.
- Interface and body: `Microsoft YaHei`, `Microsoft JhengHei`, `PingFang SC`, sans-serif. It keeps labels, caption text, and process annotations mobile-readable on Windows.
- Headlines are 96px or larger; body text is 32px; captions are 42px. Numerals use tabular figures where they appear.

## Layout and safe area

- Scene content uses full-size flex containers with 112px top/bottom and 140px side padding. Decorative rules and softly tinted circles may be absolute; content containers may not be.
- The fixed-bottom `anchor-dark` caption panel occupies 150px at the bottom, with 48px horizontal padding. Main content never enters that zone.
- Scene one uses a left title column and right process sketch. Scene two uses a left proof point and right local-delivery card. Both scenes retain three layers: warm background, structured content, and restrained accents.

## Motion

- Every visible content element enters after 0.18s through varied `gsap.from()` choreography: vertical lift, lateral settle, scale settle, and rule reveal.
- The related scenes connect with a 1.4-second CSS focus dissolve around 4.25s: outgoing content remains visible until the transition begins, then gently defocuses while the incoming scene resolves.
- Caption groups follow synthetic local timings, one group at a time, and each group receives a deterministic hard `tl.set()` kill at its end.
- Only the final scene fades down during the final 0.45 seconds. No ambient loop or nondeterministic behaviour is permitted.

## What not to do

- Do not use remote URLs, CDNs, API calls, source-video material, or cloud-generated media.
- Do not use black or white pure defaults, neon gradients, centered floating cards, or generic banned fonts.
- Do not overlap the main content with the `anchor-dark` caption panel or permit clipped Chinese text.
- Do not use `Math.random`, wall-clock time, asynchronous timeline construction, media playback calls, or infinite repeats.
- Do not represent the synthetic captions as a real ASR or Volcengine artifact.

---
name: katerj-xiaohuang-illustrations
description: Use when a BoomEarth scene explicitly selects the warm Xiaohuang character style to explain one concrete idea through character action and native handwritten Chinese labels.
---

# KaterJ Xiaohuang Illustrations

## Outcome

Create one warm, approachable 16:9 illustration per scene. Xiaohuang must perform the load-bearing action so an abstract idea becomes understandable at a glance.

This is BoomEarth's own production contract, inspired by the open-source Xiaohuang character workflow created by Chen Shuo. It preserves attribution while using BoomEarth's private planning, generation evidence, QC, rendering, and archive rules.

## Registered target

- Visual target: `xiaohuang-warm-first-v1`.
- Visual system: `profiled-illustration-v4`.
- Default canvas: warm white, 16:9, generous negative space and a bottom caption-safe zone.
- Text policy: `text_policy: embedded`. Every scene carries 2–4 short, reviewed native handwritten Chinese labels inside the illustration.
- 禁止系统胶囊标签、PPT 卡片、自动标题、字幕烧入、Logo、水印和与文案无关的科技装饰。

## Identity lock

小黄必须保持：暖黄不规则种子形身体、空心爱心天线、黑色竖椭圆眼、小弧线嘴、淡腮红和细黑四肢。动作、表情、场景和道具可以变化，身份锚点不能变化。详细规则见 [character-dna.md](references/character-dna.md)。

## Workflow

1. Extract the scene's single claim, audience pain, action or relationship.
2. Design a new low-tech physical metaphor; do not reuse an old scene composition.
3. Put Xiaohuang in charge of the key action. The character may guide, carry, sort, connect, repair, compare or reveal; it must not stand beside a text card as decoration.
4. Write the generation contract using [prompt-contract.md](references/prompt-contract.md). Include the exact reviewed labels and request original handwritten marks in the image.
5. Generate one candidate through the approved runtime-native ImageGen plan. Preserve the original, prompt, call evidence and SHA-256.
6. Review the real image against [qa-checklist.md](references/qa-checklist.md). Wrong identity or wrong Chinese requires a targeted repair candidate; do not cover the mistake with system labels.
7. Publish qualified assets under `工程/assets/profiled-illustrations/xiaohuang-warm-first-v1/` and bind them through the profiled manifest.
8. Render the image as native-text artwork with no duplicate overlay labels. Use a stable warm-white page and edge feathering; do not blur the character or handwriting.

## Acceptance

- Every planned scene has a real Xiaohuang image and the character performs the semantic action.
- Identity anchors remain consistent across all scenes.
- 原生手写中文 is correct, short, readable and attached to the illustrated action.
- The renderer emits no system-generated label layer for this theme.
- Contact-sheet frames prove art, handwriting, captions and safe zones coexist in the final video.
- Preserve all candidates and evidence; 不覆盖旧资产。

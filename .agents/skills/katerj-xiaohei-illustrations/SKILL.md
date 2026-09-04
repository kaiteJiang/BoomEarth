---
name: katerj-xiaohei-illustrations
description: Use when a Chinese article or BoomEarth video scene needs white-first hand-drawn black-character illustrations that explain one concrete action or mechanism.
---

# KaterJ Xiaohei Illustrations

## Outcome

Create one clean 16:9 semantic illustration per selected scene, with the black character performing the load-bearing action instead of decorating a text card.

## Visual contract

- Pure white ground, black hand-drawn lines, generous negative space.
- A solid black figure with small white eyes and simple limbs.
- Small red, orange, or blue handwritten accents only where they clarify direction, state, or feedback.
- Every new scene must declare `illustration_text_mode: embedded` and 2-4 short semantic labels in `content-plan*.json -> scenes[].overlay_labels`. The labels are prompt and QC evidence for the image's native text; they are not automatic renderer pills.
- The default prompt contract is exactly `text_policy: embedded` plus one `handwritten_labels: 标签一 | 标签二` line. Ask ImageGen to draw those exact short labels as original handwritten marks inside the illustration.
- `local-fallback` is an explicit exception after native-text QC fails. It must use a text-free qualified candidate and handwritten local text without borders, pills, cards, shadows, or UI styling. 禁止胶囊标签。
- Each Chinese label is 2-6 characters, uses exact reviewed wording, and must not duplicate the page title or bottom caption.
- No PPT diagram, commercial mascot polish, dense architecture chart, photorealism, watermark, or automatic left-corner title.

## Workflow

1. Extract each scene's claim, action, contrast, or state change.
2. Invent a scene-specific physical metaphor and assign the character the core action.
3. Write a prompt that specifies 16:9, clean white background, action, objects, color accents, and the exact original handwritten labels. Include `text_policy: embedded` and `handwritten_labels:`; never write `text_policy: none` for a new Xiaohei scene.
4. Generate one candidate per approved scene plan; preserve each returned original and SHA-256.
5. Check semantic match, character action, whitespace, native Chinese spelling, style consistency, and safe crop. One wrong label requires a targeted image edit or a newly approved candidate; do not silently replace the whole text layer with renderer pills.
6. Copy qualified images into the active project's `工程/assets/xiaohei-illustrations/` and write an illustration manifest.
7. 使用纯白画布与边缘羽化把位图边界融入页面。Do not blur the full illustration or soften its lines and original handwriting.
8. Before final rendering, verify `embedded` scenes contain no `.visual-overlay-labels` or fallback markup. Only an explicit `local-fallback` scene may render the borderless handwritten fallback layer.

## Acceptance

- Every planned scene has a real local image or a documented, contract-valid reason to omit one.
- The character performs the scene's main action.
- 原生手写文字 is short, correct, semantically attached to the character's action, and not duplicated by system-generated labels.
- The pure white canvas and edge feathering make the image boundary disappear without applying full-image blur.
- Contact-sheet frames prove each image and its native labels appear together in the final video at readable scale. Page-side titles alone do not satisfy this gate.

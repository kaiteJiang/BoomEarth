---
name: katerj-xiaohei-illustrations
description: Use when a Chinese article or BoomEarth video scene needs white-first hand-drawn black-character illustrations that explain one concrete action or mechanism.
---

# KaterJ Xiaohei Illustrations

## Outcome

Create one clean 16:9 semantic illustration per selected scene, with the black character performing the load-bearing action instead of decorating a text card.

## Visual contract

- White or warm-white ground, black hand-drawn lines, generous negative space.
- A solid black figure with small white eyes and simple limbs.
- Small red, orange, or blue handwritten accents only where they clarify direction, state, or feedback.
- Every scene must declare 2-4 short semantic labels in `content-plan*.json -> scenes[].overlay_labels`. These labels are rendered locally inside the illustration frame after image generation; an empty label list is a production failure.
- Keep cloud-generated source art text-free when needed for Chinese accuracy, but never treat a text-free source image as the finished illustrated scene. The required local labels must name the objects, states, contrast, or direction shown by the character's action.
- Each Chinese label is 2-6 characters, uses exact reviewed wording, and must not duplicate the page title or bottom caption.
- No PPT diagram, commercial mascot polish, dense architecture chart, photorealism, watermark, or automatic left-corner title.

## Workflow

1. Extract each scene's claim, action, contrast, or state change.
2. Invent a scene-specific physical metaphor and assign the character the core action.
3. Write a prompt that specifies 16:9, clean white background, action, objects, color accents, and forbidden elements. Separately write 2-4 reviewed `overlay_labels` for the local renderer; do not rely on ImageGen to spell Chinese.
4. Generate one candidate per approved scene plan; preserve each returned original and SHA-256.
5. Check semantic match, character action, whitespace, local label wording, style consistency, and safe crop.
6. Copy qualified images into the active project's `工程/assets/xiaohei-illustrations/` and write an illustration manifest.
7. Before final rendering, verify every Xiaohei scene produces non-empty `.visual-overlay-labels` markup and that labels sit inside the illustration frame rather than only in the page title card.

## Acceptance

- Every planned scene has a real local image or a documented, contract-valid reason to omit one.
- The character performs the scene's main action.
- The locally rendered Chinese labels are short, correct, semantically attached to the illustration, and not duplicated by the page title or captions.
- Contact-sheet frames prove each image and its local semantic labels appear together in the final video at readable scale. Page-side titles alone do not satisfy this gate.

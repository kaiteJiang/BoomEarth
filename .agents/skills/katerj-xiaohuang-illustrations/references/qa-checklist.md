# Xiaohuang scene QC

All eight shared semantic checks must pass, followed by exactly these theme checks:

- `xiaohuang_identity_consistent`: seed body, hollow-heart antenna, eyes, face and limbs match the identity lock.
- `character_performs_action`: Xiaohuang performs the scene's core semantic action.
- `native_labels_correct`: every planned Chinese label is present, correct and readable in the original image.
- `warm_white_canvas`: warm-white negative space and the caption-safe area remain clear.
- `not_system_label_overlay`: no renderer label pills or duplicate program text cover the artwork.

Fail the scene when the identity drifts, a label is wrong, the character is decorative, the picture becomes a poster/PPT page, or key evidence falls into the subtitle zone.

#!/usr/bin/env python3
"""Validate local V2/V3 review evidence and assemble a contact sheet."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError


SCENARIOS = ("structure", "action", "comparison")
VARIANTS = ("v2", "v3")
CHECKS = {
    "subject_match",
    "action_match",
    "evidence_complete",
    "claim_readable",
    "non_generic",
    "forbidden_absent",
    "mobile_readable",
    "caption_safe",
}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TOP_KEYS = {"schema_version", "gate_id", "reviewer_type", "scenarios"}
_SCENARIO_KEYS = {
    "scenario",
    "variants",
    "v3_more_direct",
    "comparison_rationale",
}
_VARIANT_KEYS = {
    "prompt_path",
    "prompt_sha256",
    "selected_candidate",
    "candidates",
}
_CANDIDATE_KEYS = {
    "candidate",
    "image_path",
    "image_sha256",
    "checks",
    "rationale",
}


class SemanticVisualGateError(RuntimeError):
    """A stable, source-free visual gate validation failure."""


@dataclass(frozen=True, slots=True)
class GateResult:
    gate_root: Path
    contact_sheet: Path
    selection_report: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticVisualGateError("gate-review-invalid")
        result[key] = value
    return result


def _read_review(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                SemanticVisualGateError("gate-review-invalid")
            ),
        )
    except SemanticVisualGateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SemanticVisualGateError("gate-review-invalid") from None
    if not isinstance(value, dict):
        raise SemanticVisualGateError("gate-review-invalid")
    return value


def _resolve(root: Path, value: object, *, parent: str, suffix: str) -> Path:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise SemanticVisualGateError("gate-path-invalid")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
        or len(relative.parts) != 2
        or relative.parts[0] != parent
        or relative.suffix.lower() != suffix
        or relative.as_posix() != value
    ):
        raise SemanticVisualGateError("gate-path-invalid")
    return root / Path(*relative.parts)


def _bound_file(path: Path, expected: object, *, missing: str) -> str:
    if not path.is_file():
        raise SemanticVisualGateError(missing)
    if not isinstance(expected, str) or _DIGEST.fullmatch(expected) is None:
        raise SemanticVisualGateError("gate-hash-mismatch")
    actual = _sha256(path)
    if actual != expected:
        raise SemanticVisualGateError("gate-hash-mismatch")
    return actual


def _valid_rationale(value: object) -> bool:
    return (
        isinstance(value, str)
        and value == value.strip()
        and 8 <= len(value) <= 180
        and "test-fixture" not in value.lower()
    )


def _candidate_passes(candidate: dict[str, object]) -> bool:
    checks = candidate.get("checks")
    if (
        not isinstance(checks, dict)
        or set(checks) != CHECKS
        or any(type(checks[name]) is not bool for name in CHECKS)
        or not _valid_rationale(candidate.get("rationale"))
    ):
        raise SemanticVisualGateError("gate-review-invalid")
    return all(checks[name] for name in CHECKS)


def _validate_image(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as decoded:
            width, height = decoded.size
            decoded.verify()
        with Image.open(path) as decoded:
            decoded.load()
    except (OSError, UnidentifiedImageError):
        raise SemanticVisualGateError("gate-image-invalid") from None
    if width < 960 or height < 540:
        raise SemanticVisualGateError("gate-image-unreadable")
    return width, height


def _validate_gate(root: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    review = _read_review(root / "review.json")
    if (
        set(review) != _TOP_KEYS
        or review["schema_version"] != 1
        or isinstance(review["schema_version"], bool)
        or review["gate_id"] != root.name
        or review["reviewer_type"] != "multimodal-review"
        or not isinstance(review["scenarios"], list)
        or len(review["scenarios"]) != len(SCENARIOS)
    ):
        error = (
            "gate-reviewer-invalid"
            if review.get("reviewer_type") != "multimodal-review"
            else "gate-review-invalid"
        )
        raise SemanticVisualGateError(error)

    selected: list[dict[str, object]] = []
    for scenario_name, raw_scenario in zip(SCENARIOS, review["scenarios"], strict=True):
        if (
            not isinstance(raw_scenario, dict)
            or set(raw_scenario) != _SCENARIO_KEYS
            or raw_scenario["scenario"] != scenario_name
            or raw_scenario["v3_more_direct"] is not True
            or not _valid_rationale(raw_scenario["comparison_rationale"])
            or not isinstance(raw_scenario["variants"], dict)
            or set(raw_scenario["variants"]) != set(VARIANTS)
        ):
            raise SemanticVisualGateError("gate-review-invalid")
        selection: dict[str, object] = {
            "scenario": scenario_name,
            "v3_more_direct": True,
            "comparison_rationale": raw_scenario["comparison_rationale"],
            "variants": {},
        }
        for variant_name in VARIANTS:
            raw_variant = raw_scenario["variants"][variant_name]
            if not isinstance(raw_variant, dict) or set(raw_variant) != _VARIANT_KEYS:
                raise SemanticVisualGateError("gate-review-invalid")
            prompt = _resolve(
                root, raw_variant["prompt_path"], parent="prompts", suffix=".md"
            )
            prompt_hash = _bound_file(
                prompt, raw_variant["prompt_sha256"], missing="gate-prompt-missing"
            )
            candidates = raw_variant["candidates"]
            if not isinstance(candidates, list) or not candidates:
                raise SemanticVisualGateError("gate-review-invalid")
            if len(candidates) > 2:
                raise SemanticVisualGateError("gate-repair-limit")
            expected_numbers = list(range(1, len(candidates) + 1))
            if any(
                not isinstance(item, dict) or set(item) != _CANDIDATE_KEYS
                for item in candidates
            ):
                raise SemanticVisualGateError("gate-review-invalid")
            numbers = [item["candidate"] for item in candidates]
            if numbers != expected_numbers or any(type(number) is not int for number in numbers):
                raise SemanticVisualGateError("gate-repair-limit")
            passes = [_candidate_passes(item) for item in candidates]
            if len(candidates) == 2 and passes[0]:
                raise SemanticVisualGateError("gate-repair-invalid")
            selected_number = raw_variant["selected_candidate"]
            if (
                type(selected_number) is not int
                or selected_number not in expected_numbers
                or selected_number != len(candidates)
                or not passes[selected_number - 1]
            ):
                raise SemanticVisualGateError("gate-selection-invalid")
            selected_candidate = candidates[selected_number - 1]
            expected_name = (
                f"{scenario_name}-{variant_name}-candidate-{selected_number:02d}.png"
            )
            image = _resolve(
                root,
                selected_candidate["image_path"],
                parent="images",
                suffix=".png",
            )
            if image.name != expected_name:
                raise SemanticVisualGateError("gate-path-invalid")
            image_hash = _bound_file(
                image,
                selected_candidate["image_sha256"],
                missing="gate-image-missing",
            )
            width, height = _validate_image(image)
            selection["variants"][variant_name] = {
                "candidate": selected_number,
                "image_path": image.relative_to(root).as_posix(),
                "image_sha256": image_hash,
                "prompt_path": prompt.relative_to(root).as_posix(),
                "prompt_sha256": prompt_hash,
                "width": width,
                "height": height,
                "checks": dict(selected_candidate["checks"]),
                "rationale": selected_candidate["rationale"],
            }
        selected.append(selection)
    return review, selected


def _render_contact_sheet(root: Path, selected: list[dict[str, object]]) -> Path:
    sheet = Image.new("RGB", (1920, 1620), (242, 238, 229))
    for row, selection in enumerate(selected):
        variants = selection["variants"]
        for column, variant_name in enumerate(VARIANTS):
            item = variants[variant_name]
            source_path = root / Path(*PurePosixPath(item["image_path"]).parts)
            with Image.open(source_path) as source:
                panel = ImageOps.fit(
                    source.convert("RGB"),
                    (960, 540),
                    method=Image.Resampling.LANCZOS,
                )
            sheet.paste(panel, (column * 960, row * 540))
    output = root / "contact-sheet.png"
    temporary = root / ".contact-sheet.tmp.png"
    sheet.save(temporary, format="PNG", optimize=True)
    temporary.replace(output)
    return output


def _write_report(
    root: Path,
    selected: list[dict[str, object]],
    contact_sheet: Path,
) -> Path:
    selected_images = [
        {
            "scenario": selection["scenario"],
            "variant": variant_name,
            **selection["variants"][variant_name],
        }
        for selection in selected
        for variant_name in VARIANTS
    ]
    report = {
        "schema_version": 1,
        "gate_id": root.name,
        "status": "pass",
        "reviewer_type": "multimodal-review",
        "contact_sheet_path": contact_sheet.relative_to(root).as_posix(),
        "contact_sheet_sha256": _sha256(contact_sheet),
        "contact_sheet_dimensions": [1920, 1620],
        "selected_images": selected_images,
        "selections": selected,
    }
    output = root / "selection-report.json"
    temporary = root / ".selection-report.tmp.json"
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


def build_gate(gate_root: Path, *, validate: bool = True) -> GateResult:
    root = Path(gate_root).absolute()
    if not validate or not root.is_dir():
        raise SemanticVisualGateError("gate-root-invalid")
    _, selected = _validate_gate(root)
    contact_sheet = _render_contact_sheet(root, selected)
    report = _write_report(root, selected, contact_sheet)
    with Image.open(contact_sheet) as decoded:
        if decoded.size != (1920, 1620):
            raise SemanticVisualGateError("gate-contact-sheet-invalid")
        decoded.verify()
    return GateResult(root, contact_sheet, report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate local semantic review evidence and assemble a contact sheet."
    )
    parser.add_argument("--gate-root", type=Path, required=True)
    parser.add_argument("--validate", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_gate(args.gate_root, validate=args.validate)
    except SemanticVisualGateError as error:
        print(f"SEMANTIC_VISUAL_GATE=FAIL reason={error}", file=sys.stderr)
        return 2
    print(f"SEMANTIC_VISUAL_GATE=PASS root={result.gate_root}")
    print(f"CONTACT_SHEET={result.contact_sheet}")
    print(f"SELECTION_REPORT={result.selection_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

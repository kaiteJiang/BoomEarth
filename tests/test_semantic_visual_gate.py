from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from PIL import Image


SCRIPT = (
    Path(__file__).parents[1]
    / "automation"
    / "scripts"
    / "build_semantic_visual_gate.py"
)
SCENARIOS = ("structure", "action", "comparison")
VARIANTS = ("v2", "v3")
CHECKS = (
    "subject_match",
    "action_match",
    "evidence_complete",
    "claim_readable",
    "non_generic",
    "forbidden_absent",
    "mobile_readable",
    "caption_safe",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module():
    spec = importlib.util.spec_from_file_location("semantic_visual_gate_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _gate_fixture(root: Path, *, reviewer_type: str = "multimodal-review") -> Path:
    prompts = root / "prompts"
    images = root / "images"
    prompts.mkdir(parents=True)
    images.mkdir()
    scenarios: list[dict[str, object]] = []
    for row, scenario in enumerate(SCENARIOS):
        variants: dict[str, object] = {}
        for column, variant in enumerate(VARIANTS):
            prompt = prompts / f"{scenario}-{variant}.md"
            prompt.write_text(
                f"# {scenario}-{variant}\n\nsubject action evidence 16:9 bottom-150px\n",
                encoding="utf-8",
            )
            image = images / f"{scenario}-{variant}-candidate-01.png"
            Image.new(
                "RGB",
                (960, 540),
                (225 - row * 15, 230 - column * 15, 210 + row * 10),
            ).save(image, format="PNG")
            variants[variant] = {
                "prompt_path": f"prompts/{prompt.name}",
                "prompt_sha256": _sha256(prompt),
                "selected_candidate": 1,
                "candidates": [
                    {
                        "candidate": 1,
                        "image_path": f"images/{image.name}",
                        "image_sha256": _sha256(image),
                        "checks": {name: True for name in CHECKS},
                        "rationale": "主体、动作和证据直接对应场景判断，且没有机械装饰。",
                    }
                ],
            }
        scenarios.append(
            {
                "scenario": scenario,
                "variants": variants,
                "v3_more_direct": True,
                "comparison_rationale": "V3 的主体关系更直接，移动端缩小后仍能读懂。",
            }
        )
    (root / "review.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gate_id": root.name,
                "reviewer_type": reviewer_type,
                "scenarios": scenarios,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def test_builds_three_row_v2_v3_contact_sheet_and_selection_report(
    tmp_path: Path,
) -> None:
    module = _module()
    root = _gate_fixture(tmp_path / "2026-08-14-semantic-handdrawn-v3")

    result = module.build_gate(root, validate=True)

    assert result.contact_sheet == root / "contact-sheet.png"
    with Image.open(result.contact_sheet) as sheet:
        assert sheet.size == (1920, 1620)
    report = json.loads((root / "selection-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["contact_sheet_sha256"] == _sha256(result.contact_sheet)
    assert [item["scenario"] for item in report["selections"]] == list(SCENARIOS)
    assert all(item["v3_more_direct"] is True for item in report["selections"])
    assert len(report["selected_images"]) == 6


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing-image", "gate-image-missing"),
        ("bad-hash", "gate-hash-mismatch"),
        ("missing-check", "gate-review-invalid"),
        ("second-repair", "gate-repair-limit"),
        ("repair-after-pass", "gate-repair-invalid"),
        ("test-review", "gate-reviewer-invalid"),
    ),
)
def test_rejects_incomplete_or_non_real_gate_evidence(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    module = _module()
    root = _gate_fixture(
        tmp_path / mutation,
        reviewer_type="test-fixture" if mutation == "test-review" else "multimodal-review",
    )
    review_path = root / "review.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    variant = review["scenarios"][0]["variants"]["v2"]
    candidate = variant["candidates"][0]
    if mutation == "missing-image":
        (root / candidate["image_path"]).unlink()
    elif mutation == "bad-hash":
        candidate["image_sha256"] = "0" * 64
    elif mutation == "missing-check":
        candidate["checks"].pop("caption_safe")
    elif mutation in {"second-repair", "repair-after-pass"}:
        candidate_two_path = root / "images" / "structure-v2-candidate-02.png"
        Image.new("RGB", (960, 540), (200, 210, 220)).save(
            candidate_two_path, format="PNG"
        )
        candidate_two = {
            "candidate": 2,
            "image_path": "images/structure-v2-candidate-02.png",
            "image_sha256": _sha256(candidate_two_path),
            "checks": {name: True for name in CHECKS},
            "rationale": "定向修复后的主体、动作和证据均已通过。",
        }
        variant["candidates"].append(candidate_two)
        variant["selected_candidate"] = 2
        if mutation == "second-repair":
            candidate_three_path = root / "images" / "structure-v2-candidate-03.png"
            Image.new("RGB", (960, 540), (190, 200, 210)).save(
                candidate_three_path, format="PNG"
            )
            variant["candidates"].append(
                {
                    **candidate_two,
                    "candidate": 3,
                    "image_path": "images/structure-v2-candidate-03.png",
                    "image_sha256": _sha256(candidate_three_path),
                }
            )
            variant["selected_candidate"] = 3
    if mutation != "missing-image" and mutation != "test-review":
        review_path.write_text(
            json.dumps(review, ensure_ascii=False), encoding="utf-8"
        )

    with pytest.raises(module.SemanticVisualGateError, match=message):
        module.build_gate(root, validate=True)


def test_gate_assembler_has_no_network_or_generation_dependency() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "requests" not in source
    assert "httpx" not in source
    assert "urllib" not in source
    assert "imagegen" not in source.lower()

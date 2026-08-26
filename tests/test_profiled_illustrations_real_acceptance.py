"""Explicitly gated real visual acceptance for the four profiled themes."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath

import pytest
from PIL import Image, UnidentifiedImageError

from boomearth.video.artifacts import ArtifactError, capture_regular_file
from boomearth.video.illustration_themes import THEMES
from boomearth.video.profiled_generation import (
    GENERATION_ARTIFACT_KEYS,
    ProfiledGenerationError,
    validate_profiled_generation_evidence,
)


SHARED_CHECKS = {
    "subject_match",
    "action_match",
    "evidence_complete",
    "claim_readable",
    "non_generic",
    "forbidden_absent",
    "mobile_readable",
    "caption_safe",
}


def _bound_path(
    root: Path, relative: object, digest: object, *, expected: str
) -> bytes:
    assert isinstance(relative, str)
    assert relative == expected
    assert isinstance(digest, str) and len(digest) == 64
    pure = PurePosixPath(relative)
    assert not pure.is_absolute()
    assert "." not in pure.parts and ".." not in pure.parts
    assert "\\" not in relative and ":" not in relative
    path = root.joinpath(*pure.parts)
    try:
        snapshot = capture_regular_file(path, within=root)
    except ArtifactError:
        pytest.fail("acceptance artifact is unsafe or unavailable")
    assert snapshot.sha256 == digest
    return snapshot.payload


def test_four_profiled_themes_have_hash_bound_real_visual_acceptance() -> None:
    if os.environ.get("BOOMEARTH_RUN_PROFILED_ILLUSTRATION_ACCEPTANCE") != "1":
        pytest.skip("profiled illustration acceptance is disabled by default")

    root_value = os.environ.get("BOOMEARTH_PROFILED_ILLUSTRATION_ACCEPTANCE_ROOT")
    assert root_value
    root = Path(root_value).absolute()
    acceptance_path = root / "acceptance.json"
    assert acceptance_path.is_file() and not acceptance_path.is_symlink()
    record = json.loads(acceptance_path.read_text(encoding="utf-8"))
    assert set(record) == {
        "schema_version",
        "status",
        "visual_system",
        "claim",
        "claim_sha256",
        "reviewer_type",
        "contact_sheet",
        "contact_sheet_sha256",
        "visual_distinction_review",
        "themes",
    }
    assert record["schema_version"] == 1
    assert record["status"] == "pass"
    assert record["visual_system"] == "profiled-illustration-v4"
    assert isinstance(record["claim"], str) and record["claim"].strip()
    claim_sha256 = hashlib.sha256(record["claim"].encode("utf-8")).hexdigest()
    assert record["claim_sha256"] == claim_sha256
    assert record["reviewer_type"] == "multimodal-review"
    assert set(record["themes"]) == set(THEMES)
    assert len(record["themes"]) == 4

    contact_sheet_payload = _bound_path(
        root,
        record["contact_sheet"],
        record["contact_sheet_sha256"],
        expected="contact-sheet.png",
    )
    try:
        with Image.open(io.BytesIO(contact_sheet_payload)) as sheet:
            assert sheet.width >= 1920 and sheet.height >= 1080
            sheet.verify()
    except (OSError, UnidentifiedImageError):
        pytest.fail("contact sheet is not a decodable image")

    distinction = record["visual_distinction_review"]
    assert set(distinction) == {"status", "reviewer_type", "pairs"}
    assert distinction["status"] == "pass"
    assert distinction["reviewer_type"] == "multimodal-review"
    expected_pairs = {
        f"{left}--{right}"
        for index, left in enumerate(sorted(THEMES))
        for right in sorted(THEMES)[index + 1 :]
    }
    assert set(distinction["pairs"]) == expected_pairs
    assert all(
        isinstance(reason, str) and reason.strip()
        for reason in distinction["pairs"].values()
    )

    selected_hashes: set[str] = set()
    for theme_id, theme in THEMES.items():
        item = record["themes"][theme_id]
        theme_base = f"工程/assets/profiled-illustrations/{theme_id}"
        assert set(item) == {
            "prompt_path",
            "prompt_sha256",
            "claim_sha256",
            "reviewer_type",
            "candidate_artifacts",
            "selected_asset_path",
            "selected_asset_sha256",
            "width",
            "height",
            "shared_qc_pass",
            "theme_qc_pass",
            "checks",
            "relevance_rationale",
        }
        assert item["claim_sha256"] == claim_sha256
        assert item["reviewer_type"] == "multimodal-review"
        prompt_payload = _bound_path(
            root,
            item["prompt_path"],
            item["prompt_sha256"],
            expected=f"{theme_base}/prompts/scene-01.md",
        )
        prompt_text = prompt_payload.decode("utf-8")
        assert "visual_system: profiled-illustration-v4" in prompt_text
        assert f"visual_theme: {theme_id}" in prompt_text
        assert f"claim_sha256: {claim_sha256}" in prompt_text
        assert all(f"{index}. " in prompt_text for index in range(1, 11))

        artifacts = item["candidate_artifacts"]
        assert isinstance(artifacts, list) and 1 <= len(artifacts) <= 2
        candidate_hashes: set[str] = set()
        for index, artifact in enumerate(artifacts, 1):
            assert set(artifact) == GENERATION_ARTIFACT_KEYS
            assert artifact["path"].endswith(
                f"{theme_id}/candidates/scene-01-candidate-{index:02d}.png"
            )
            candidate_payload = _bound_path(
                root,
                artifact["path"],
                artifact["sha256"],
                expected=(
                    f"{theme_base}/candidates/"
                    f"scene-01-candidate-{index:02d}.png"
                ),
            )
            with Image.open(io.BytesIO(candidate_payload)) as image:
                assert image.size == (3840, 2160)
                assert image.format == "PNG"
                image.verify()
            try:
                validate_profiled_generation_evidence(
                    project_root=root,
                    theme_id=theme_id,
                    scene_id="scene-01",
                    candidate_index=index,
                    prompt_path=artifact["generation_prompt_path"],
                    prompt_sha256=artifact["generation_prompt_sha256"],
                    candidate_path=artifact["path"],
                    candidate_sha256=artifact["sha256"],
                    artifact=artifact,
                )
            except ProfiledGenerationError:
                pytest.fail("candidate generation evidence is invalid")
            candidate_hashes.add(artifact["sha256"])

        selected_payload = _bound_path(
            root,
            item["selected_asset_path"],
            item["selected_asset_sha256"],
            expected=f"{theme_base}/scene-01.png",
        )
        with Image.open(io.BytesIO(selected_payload)) as image:
            assert image.size == (3840, 2160)
            assert image.format == "PNG"
            image.verify()
        assert item["width"] == 3840 and item["height"] == 2160
        assert item["selected_asset_sha256"] in candidate_hashes
        selected_hashes.add(item["selected_asset_sha256"])

        checks = item["checks"]
        expected_checks = SHARED_CHECKS | set(theme.required_qc)
        assert set(checks) == expected_checks
        assert all(checks[name] is True for name in expected_checks)
        assert item["shared_qc_pass"] is True
        assert item["theme_qc_pass"] is True
        assert (
            isinstance(item["relevance_rationale"], str)
            and item["relevance_rationale"].strip()
        )

    assert len(selected_hashes) == 4

"""Explicit real visual gate for the three-candidate illustration comparison."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image


def _acceptance_root() -> Path:
    if os.environ.get("BOOMEARTH_RUN_ILLUSTRATION_ACCEPTANCE") != "1":
        pytest.skip("real illustration acceptance is disabled by default")
    value = os.environ.get("BOOMEARTH_ILLUSTRATION_ACCEPTANCE_ROOT")
    if not value:
        pytest.fail("BOOMEARTH_ILLUSTRATION_ACCEPTANCE_ROOT is required")
    root = Path(value).absolute()
    assert root.is_dir()
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_three_candidate_visual_gate_selects_a_hash_bound_v2_style() -> None:
    root = _acceptance_root()
    record_path = root / "acceptance.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))

    assert record["schema_version"] == 1
    assert record["status"] == "pass"
    assert record["selected_style"] in {"editorial-scene", "minimal-vector"}
    assert len(record["candidates"]) == 3
    assert {item["style"] for item in record["candidates"]} == {
        "xiaohei-baseline",
        "editorial-scene",
        "minimal-vector",
    }
    selected = root / record["selected_asset"]
    assert selected.is_file()
    with Image.open(selected) as image:
        assert image.size == (1920, 1080)
        image.verify()

    for candidate in record["candidates"]:
        asset = root / candidate["asset"]
        assert _sha256(asset) == candidate["asset_sha256"]
        with Image.open(asset) as image:
            assert image.size == (1920, 1080)
            image.verify()
        if candidate["prompt"] is not None:
            prompt = root / candidate["prompt"]
            assert _sha256(prompt) == candidate["prompt_sha256"]
            assert prompt.stat().st_mtime_ns <= asset.stat().st_mtime_ns

    contact = root / record["contact_sheet"]
    assert _sha256(contact) == record["contact_sheet_sha256"]
    with Image.open(contact) as image:
        assert image.size == (1920, 412)
        image.verify()
    assert all(record["manual_qc"].values())

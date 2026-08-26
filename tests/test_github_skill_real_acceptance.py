"""Explicitly gated verification of an already acquired GitHub Skill snapshot."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from boomearth.providers.github_skill import parse_skill_document


EXPECTED_SKILLS = {
    "comic-explainer-illustration",
    "engineering-sketch-illustration",
    "four-panel-comic-explainer-illustration",
    "whiteboard-handdrawn-explainer-illustration",
}


def _acceptance_root() -> Path:
    if os.environ.get("BOOMEARTH_RUN_GITHUB_SKILL_ACCEPTANCE") != "1":
        pytest.skip("real GitHub Skill acceptance is disabled by default")
    value = os.environ.get("BOOMEARTH_GITHUB_SKILL_ACCEPTANCE_ROOT")
    if not value:
        pytest.fail("BOOMEARTH_GITHUB_SKILL_ACCEPTANCE_ROOT is required")
    root = Path(value).absolute()
    assert root.is_dir()
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_real_public_repository_snapshot_is_hash_bound_and_complete() -> None:
    root = _acceptance_root()
    manifest_path = root / "github-skill-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))

    assert manifest["repository"] == "chujianyun/awesome-gpt-image2-ppt-skills"
    assert manifest["source_kind"] == "github-skill"
    assert manifest["license_status"] == "UNDECLARED"
    assert len(manifest["resolved_commit"]) == 40
    assert manifest["request_count"] <= 48
    repository = root / manifest["repository_markdown_path"]
    assert _sha256(repository) == manifest["repository_markdown_sha256"]

    discovered: set[str] = set()
    total_bytes = 0
    for item in manifest["files"]:
        path = root / item["local_path"]
        assert path.stat().st_size == item["bytes"]
        assert _sha256(path) == item["sha256"]
        total_bytes += item["bytes"]
        if item["role"] == "skill":
            descriptor = parse_skill_document(
                item["path"], item["blob_sha"], path.read_text("utf-8")
            )
            discovered.add(descriptor.name)
    assert total_bytes == manifest["total_text_bytes"]
    assert EXPECTED_SKILLS <= discovered

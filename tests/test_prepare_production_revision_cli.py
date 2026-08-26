from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from test_production_revision import (
    V1_SLUG,
    V2_SLUG,
    ProductionStartedFixture,
    _publish_v2_inputs,
    production_started_fixture,
)
from test_source_handoff_compiler import WORK_ID


ROOT = Path(__file__).parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_revision_cli_emits_only_redacted_status(
    production_started_fixture: ProductionStartedFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load(
        "prepare_production_revision_cli",
        "automation/scripts/prepare_production_revision.py",
    )

    result = module.main(
        [
            WORK_ID,
            "--original-active-project",
            V1_SLUG,
            "--archive-slug",
            V2_SLUG,
            "--duration-target-s",
            "165",
        ],
        root=production_started_fixture.root,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == "work=000000000000 revision=2 status=brief-created\n"
    assert captured.err == ""
    assert str(production_started_fixture.root) not in captured.out


def test_prepare_revision_cli_rejects_invalid_arguments_without_usage_leak(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load(
        "prepare_production_revision_invalid_cli",
        "automation/scripts/prepare_production_revision.py",
    )

    result = module.main([WORK_ID], root=tmp_path)

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == "error=invalid-arguments\n"


def test_compile_revision_cli_emits_only_redacted_revision_status(
    production_started_fixture: ProductionStartedFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate, review = _publish_v2_inputs(production_started_fixture)
    module = _load(
        "compile_source_handoff_revision_cli",
        "automation/scripts/compile_source_handoff.py",
    )

    result = module.main(
        [
            "revise",
            WORK_ID,
            "--candidate",
            str(candidate),
            "--review",
            str(review),
            "--original-active-project",
            V1_SLUG,
        ],
        root=production_started_fixture.root,
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == "work=000000000000 revision=2 status=created\n"
    assert captured.err == ""
    assert str(production_started_fixture.root) not in captured.out

from __future__ import annotations

import importlib.util
from pathlib import Path

from test_production_policy import SLUG, _legacy_active_project
from test_source_handoff_compiler import WORK_ID


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "automation"
    / "scripts"
    / "apply_production_policy.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "apply_production_policy_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_applies_then_verifies_with_redacted_status(
    tmp_path: Path, capsys
) -> None:
    _legacy_active_project(tmp_path)
    module = _load_script()
    common = [
        WORK_ID,
        "--active-project",
        SLUG,
        "--workspace-root",
        str(tmp_path),
    ]

    assert module.main(["apply", *common]) == 0
    applied = capsys.readouterr()
    assert applied.out == "work=000000000000 stage=production_started status=applied\n"
    assert applied.err == ""

    assert module.main(["verify", *common]) == 0
    verified = capsys.readouterr()
    assert verified.out == "work=000000000000 stage=production_started status=verified\n"
    assert verified.err == ""


def test_cli_reports_fixed_error_for_wrong_project(tmp_path: Path, capsys) -> None:
    _legacy_active_project(tmp_path)
    module = _load_script()

    assert module.main(
        [
            "apply",
            WORK_ID,
            "--active-project",
            "wrong-project",
            "--workspace-root",
            str(tmp_path),
        ]
    ) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=production-policy-invalid\n"
    assert str(tmp_path) not in captured.err

from __future__ import annotations

import importlib.util
from pathlib import Path

from test_production_archive import ARCHIVE_RELATIVE, _archived_project
from test_source_handoff_compiler import WORK_ID


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "automation"
    / "scripts"
    / "complete_production_archive.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "complete_production_archive_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_applies_then_verifies_terminal_archive(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    _archived_project(tmp_path)
    module = _load_script()
    monkeypatch.setattr(
        "boomearth.workbench.production_archive._historical_delivery_pass",
        lambda *_args: True,
    )
    common = [
        WORK_ID,
        "--archive-project",
        ARCHIVE_RELATIVE,
        "--workspace-root",
        str(tmp_path),
    ]

    assert module.main(["apply", *common]) == 0
    assert capsys.readouterr().out == (
        "work=000000000000 stage=production_archived status=applied\n"
    )

    assert module.main(["verify", *common]) == 0
    assert capsys.readouterr().out == (
        "work=000000000000 stage=production_archived status=verified\n"
    )

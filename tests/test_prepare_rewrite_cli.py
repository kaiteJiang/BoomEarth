from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "automation"
    / "scripts"
    / "prepare_rewrite.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("prepare_rewrite_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parser_uses_fixed_horizontal_profile_without_ratio_argument() -> None:
    module = _load_script()

    arguments = module._parser().parse_args(
        [
            "00000000-0000-4000-8000-000000000006",
            "--platform",
            "douyin",
            "--duration-target-s",
            "60",
            "--archive-slug",
            "first-principles-rewrite",
        ]
    )

    assert not hasattr(arguments, "ratio")


def test_cli_rejects_legacy_ratio_option_with_fixed_safe_error(
    tmp_path: Path, capsys
) -> None:
    module = _load_script()

    assert module.main(
        [
            "00000000-0000-4000-8000-000000000006",
            "--platform",
            "douyin",
            "--ratio",
            "9:16",
            "--duration-target-s",
            "60",
            "--archive-slug",
            "first-principles-rewrite",
        ],
        root=tmp_path,
    ) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error=invalid-arguments\n"


def test_cli_reports_the_unchanged_source_stage_after_brief_creation(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    module = _load_script()

    monkeypatch.setattr(module, "prepare_rewrite_brief", lambda *args, **kwargs: None)

    class FakeLedger:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path

        def current(self, work_id: str) -> SimpleNamespace:
            assert work_id == "5309960e-b38e-4bef-be42-225534a57b86"
            return SimpleNamespace(stage="github_skill_ready")

    monkeypatch.setattr(module, "WashEventLedger", FakeLedger)

    assert module.main(
        [
            "5309960e-b38e-4bef-be42-225534a57b86",
            "--platform",
            "douyin",
            "--duration-target-s",
            "90",
            "--archive-slug",
            "visual-style-library-overview",
        ],
        root=tmp_path,
    ) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == (
        "work=5309960eb38e stage=github_skill_ready status=brief-created\n"
    )

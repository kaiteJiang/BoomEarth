from __future__ import annotations

import importlib.util
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.workbench.source_artifacts import SourceContractError
from boomearth.workbench.source_intake import (
    create_github_skill_intake,
    load_work_order,
)


WORK_ID = "11111111-1111-4111-8111-111111111111"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)
GITHUB_URL = "https://github.com/acme/skills"
SOURCE_INTAKE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "automation" / "scripts" / "source_intake.py"
)


def _load_source_intake_script():
    spec = importlib.util.spec_from_file_location(
        "github_skill_source_intake_script_under_test", SOURCE_INTAKE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _private_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def test_github_skill_intake_copies_only_one_private_canonical_url(
    tmp_path: Path,
) -> None:
    url_file = tmp_path / "github-url.txt"
    url_file.write_text(GITHUB_URL + "\n", encoding="utf-8")

    order = create_github_skill_intake(
        tmp_path,
        url_file,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )

    assert order.source_kind == "github-skill"
    assert order.stage == "source_registered"
    assert (order.private_root / "source-input.txt").read_text("utf-8") == (
        GITHUB_URL + "\n"
    )
    assert "github.com" not in repr(order)
    assert load_work_order(tmp_path, WORK_ID) == order
    intake = json.loads((_private_root(tmp_path) / "intake.json").read_text("utf-8"))
    assert intake["source_kind"] == "github-skill"
    assert intake["source_locator"] == "source-input.txt"
    assert GITHUB_URL not in json.dumps(intake)


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/acme/skills",
        "https://user:pass@github.com/acme/skills",
        "https://github.com:443/acme/skills",
        "https://gist.github.com/acme/1",
        "https://github.com/acme/skills?token=secret",
        "https://github.com/acme/skills#readme",
        "https://github.com/acme/../skills",
    ],
)
def test_github_skill_intake_rejects_noncanonical_url(
    tmp_path: Path, value: str
) -> None:
    path = tmp_path / "input.txt"
    path.write_text(value + "\n", encoding="utf-8")

    with pytest.raises(SourceContractError, match="^source-input-invalid$"):
        create_github_skill_intake(
            tmp_path,
            path,
            authorized=True,
            uuid_factory=lambda: FIXED_UUID,
        )

    assert not _private_root(tmp_path).exists()


def test_github_skill_cli_uses_private_file_and_redacted_output(tmp_path: Path) -> None:
    supplied = tmp_path / "github-url.txt"
    supplied.write_text(GITHUB_URL + "\n", encoding="utf-8")
    script = _load_source_intake_script()
    stdout = StringIO()
    stderr = StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = script.main(
            ["github-skill", "--input-file", str(supplied), "--authorized"],
            root=tmp_path,
        )

    assert result == 0
    assert stderr.getvalue() == ""
    assert re.fullmatch(
        r"work=[0-9a-f]{12} stage=source_registered status=created\n",
        stdout.getvalue(),
    )
    assert GITHUB_URL not in stdout.getvalue()


def test_github_skill_intake_requires_authorization_before_state(
    tmp_path: Path,
) -> None:
    supplied = tmp_path / "github-url.txt"
    supplied.write_text(GITHUB_URL + "\n", encoding="utf-8")

    with pytest.raises(
        SourceContractError, match="^source-authorization-required$"
    ):
        create_github_skill_intake(
            tmp_path,
            supplied,
            authorized=False,
            uuid_factory=lambda: FIXED_UUID,
        )

    assert not (tmp_path / "01-内容生产").exists()

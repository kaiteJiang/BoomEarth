from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.workbench.source_artifacts import SourceContractError, sha256_file
from boomearth.workbench.source_intake import (
    create_local_intake,
    create_url_intake,
    create_x_article_intake,
    load_work_order,
)


WORK_ID = "00000000-0000-4000-8000-000000000001"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 13, 4, 5, 6, tzinfo=timezone.utc)
PRIVATE_URL = "https://example.invalid/private-item"
X_ARTICLE_URL = "https://x.com/fixture_author/status/1234567890123456789"
SOURCE_INTAKE_SCRIPT = (
    Path(__file__).resolve().parents[1] / "automation" / "scripts" / "source_intake.py"
)


def _load_source_intake_script():
    spec = importlib.util.spec_from_file_location(
        "source_intake_script_under_test", SOURCE_INTAKE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


source_intake_script = _load_source_intake_script()


def _private_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def test_local_intake_publishes_private_exact_work_order(tmp_path: Path) -> None:
    source = tmp_path / "authorized" / "source.wav"
    source.parent.mkdir()
    source.write_bytes(b"authorized-local-media")

    order = create_local_intake(
        tmp_path,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )

    assert order.work_id == WORK_ID
    assert order.source_kind == "local"
    assert order.stage == "source_registered"
    assert order.source_input_sha256 == sha256_file(source)
    assert order.private_root == _private_root(tmp_path)
    assert repr(order) == "SourceWorkOrder(<redacted>)"
    intake = json.loads((_private_root(tmp_path) / "intake.json").read_text("utf-8"))
    assert intake == {
        "authorized": True,
        "created_at": "2026-08-13T04:05:06Z",
        "schema_version": 1,
        "source_input_sha256": sha256_file(source),
        "source_kind": "local",
        "source_locator": str(source.absolute()),
        "stage": "source_registered",
        "work_id": WORK_ID,
    }


def test_url_intake_copies_exact_private_input_without_leaking_into_identity(
    tmp_path: Path,
) -> None:
    supplied = tmp_path / "private-url.txt"
    supplied.write_text(PRIVATE_URL + "\n", encoding="utf-8")

    order = create_url_intake(
        tmp_path,
        supplied,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )

    copied = _private_root(tmp_path) / "source-input.txt"
    assert copied.read_bytes() == supplied.read_bytes()
    assert order.source_input_sha256 == sha256_file(copied)
    assert PRIVATE_URL not in order.work_id
    assert "example.invalid" not in repr(order)
    intake_text = (_private_root(tmp_path) / "intake.json").read_text("utf-8")
    assert PRIVATE_URL not in intake_text
    assert json.loads(intake_text)["source_locator"] == "source-input.txt"


def test_x_article_intake_publishes_private_redacted_work_order(tmp_path: Path) -> None:
    supplied = tmp_path / "private-x-article.txt"
    supplied.write_text(X_ARTICLE_URL + "\n", encoding="utf-8")

    order = create_x_article_intake(
        tmp_path,
        supplied,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )

    copied = _private_root(tmp_path) / "source-input.txt"
    assert copied.read_bytes() == supplied.read_bytes()
    assert order.source_kind == "x-article"
    assert load_work_order(tmp_path, WORK_ID).source_kind == "x-article"
    intake_text = (_private_root(tmp_path) / "intake.json").read_text("utf-8")
    assert X_ARTICLE_URL not in intake_text
    assert json.loads(intake_text)["source_locator"] == "source-input.txt"


def test_x_article_cli_accepts_private_file_and_prints_only_safe_identity(
    tmp_path: Path,
) -> None:
    supplied = tmp_path / "private-x-article.txt"
    supplied.write_text(X_ARTICLE_URL + "\n", encoding="utf-8")
    stdout = StringIO()
    stderr = StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = source_intake_script.main(
            ["x-article", "--input-file", str(supplied), "--authorized"],
            root=tmp_path,
        )

    assert result == 0
    assert stderr.getvalue() == ""
    assert stdout.getvalue().startswith("work=")
    assert stdout.getvalue().endswith(" stage=source_registered status=created\n")
    assert X_ARTICLE_URL not in stdout.getvalue()


def test_load_work_order_reconstructs_exact_private_contract(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"media")
    expected = create_local_intake(
        tmp_path,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )

    loaded = load_work_order(tmp_path, WORK_ID)

    assert loaded == expected
    assert loaded.private_root == _private_root(tmp_path)


def test_intake_requires_explicit_authorization_before_creating_state(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"media")

    with pytest.raises(SourceContractError, match="^source-authorization-required$"):
        create_local_intake(
            tmp_path,
            source,
            authorized=False,
            uuid_factory=lambda: FIXED_UUID,
        )

    assert not (tmp_path / "01-内容生产").exists()


@pytest.mark.parametrize(
    "payload",
    [
        b"\n",
        b"https://example.invalid/one\nhttps://example.invalid/two\n",
        b"ftp://example.invalid/item\n",
        b"https://user:password@example.invalid/item\n",
        b"https:///missing-host\n",
        b"https://example.invalid/a\x00b\n",
        b"\xff\xfe\n",
    ],
    ids=["empty", "two-lines", "ftp", "userinfo", "missing-host", "nul", "utf8"],
)
def test_url_intake_rejects_invalid_private_input_without_echoing_it(
    tmp_path: Path, payload: bytes
) -> None:
    supplied = tmp_path / "private-url.txt"
    supplied.write_bytes(payload)

    with pytest.raises(SourceContractError) as raised:
        create_url_intake(
            tmp_path,
            supplied,
            authorized=True,
            uuid_factory=lambda: FIXED_UUID,
        )

    assert str(raised.value) == "source-input-invalid"
    assert "example.invalid" not in str(raised.value)
    assert not _private_root(tmp_path).exists()


def test_local_intake_rejects_public_workbench_source(tmp_path: Path) -> None:
    source = (
        tmp_path
        / "01-内容生产"
        / "视频工作台"
        / "待制作"
        / "project"
        / "source.wav"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"media")

    with pytest.raises(SourceContractError, match="^source-input-invalid$"):
        create_local_intake(
            tmp_path,
            source,
            authorized=True,
            uuid_factory=lambda: FIXED_UUID,
        )

    assert not _private_root(tmp_path).exists()


def test_uuid_collision_never_overwrites_existing_work_order(tmp_path: Path) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    create_local_intake(
        tmp_path,
        first,
        authorized=True,
        uuid_factory=lambda: FIXED_UUID,
    )
    before = (_private_root(tmp_path) / "intake.json").read_bytes()

    with pytest.raises(SourceContractError, match="^work-order-exists$"):
        create_local_intake(
            tmp_path,
            second,
            authorized=True,
            uuid_factory=lambda: FIXED_UUID,
        )

    assert (_private_root(tmp_path) / "intake.json").read_bytes() == before


def test_load_work_order_rejects_unknown_fields_and_hash_drift(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"media")
    create_local_intake(
        tmp_path,
        source,
        authorized=True,
        uuid_factory=lambda: FIXED_UUID,
    )
    intake_path = _private_root(tmp_path) / "intake.json"
    intake = json.loads(intake_path.read_text("utf-8"))
    intake["source_title"] = "private title"
    intake_path.write_text(json.dumps(intake), encoding="utf-8")

    with pytest.raises(SourceContractError) as raised:
        load_work_order(tmp_path, WORK_ID)

    assert str(raised.value) == "work-order-invalid"
    assert "private title" not in repr(raised.value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_input_sha256", "z" * 64),
        ("created_at", "not-a-utc-time"),
        ("source_locator", ""),
    ],
)
def test_load_work_order_rejects_invalid_contract_values(
    tmp_path: Path, field: str, replacement: str
) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"media")
    create_local_intake(
        tmp_path,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    intake_path = _private_root(tmp_path) / "intake.json"
    intake = json.loads(intake_path.read_text("utf-8"))
    intake[field] = replacement
    intake_path.write_text(json.dumps(intake), encoding="utf-8")

    with pytest.raises(SourceContractError, match="^work-order-invalid$"):
        load_work_order(tmp_path, WORK_ID)


@pytest.mark.skipif(os.name != "nt", reason="Windows symbolic link semantics required")
def test_local_intake_rejects_symlink_source_when_available(tmp_path: Path) -> None:
    target = tmp_path / "target.wav"
    link = tmp_path / "link.wav"
    target.write_bytes(b"media")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")

    with pytest.raises(SourceContractError, match="^source-input-invalid$"):
        create_local_intake(
            tmp_path,
            link,
            authorized=True,
            uuid_factory=lambda: FIXED_UUID,
        )


def test_naive_clock_is_rejected_without_creating_state(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"media")

    with pytest.raises(SourceContractError, match="^source-clock-invalid$"):
        create_local_intake(
            tmp_path,
            source,
            authorized=True,
            now=lambda: datetime(2026, 8, 13, 4, 5, 6),
            uuid_factory=lambda: FIXED_UUID,
        )

    assert not _private_root(tmp_path).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_intake_rejects_private_junction_escape_with_fixed_error(tmp_path: Path) -> None:
    root = tmp_path / "root"
    workbench = root / "01-内容生产" / "视频工作台"
    outside = tmp_path / "outside"
    workbench.mkdir(parents=True)
    outside.mkdir()
    internal = workbench / ".internal"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(internal), str(outside)],
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    source = root / "source.wav"
    source.write_bytes(b"media")
    try:
        with pytest.raises(SourceContractError) as raised:
            create_local_intake(
                root,
                source,
                authorized=True,
                uuid_factory=lambda: FIXED_UUID,
            )
        assert str(raised.value) == "source-workspace-invalid"
        assert not list(outside.rglob("intake.json"))
    finally:
        if internal.exists():
            os.rmdir(internal)

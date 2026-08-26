from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from boomearth.workbench.source_artifacts import (
    ActionPlan,
    ApprovalReceipt,
    ArtifactRecord,
    SourceContractError,
    SourceWorkOrder,
    canonical_json_bytes,
    load_action_plan,
    load_exact_json,
    new_work_id,
    publish_json_exclusive,
    sha256_file,
    verify_approval,
    verify_private_relative,
)


PLAN_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "request_count",
        "network_required",
        "fee_possible",
        "no_retry",
        "no_fallback",
    }
)


def _valid_plan_dict() -> dict[str, object]:
    return {
        "schema_version": 1,
        "work_id": "00000000-0000-4000-8000-000000000001",
        "provider": "paraformer",
        "action": "transcribe-source",
        "input_sha256": "1" * 64,
        "request_count": 1,
        "network_required": True,
        "fee_possible": True,
        "no_retry": True,
        "no_fallback": True,
    }


def _valid_receipt_dict(*, plan_sha256: str) -> dict[str, object]:
    plan = _valid_plan_dict()
    return {
        "approved": True,
        "plan_sha256": plan_sha256,
        "work_id": plan["work_id"],
        "provider": plan["provider"],
        "action": plan["action"],
        "input_sha256": plan["input_sha256"],
        "request_count": plan["request_count"],
        "no_retry": True,
        "no_fallback": True,
    }


def test_new_work_id_is_uuid4_and_not_derived_from_source_values() -> None:
    first = new_work_id()
    second = new_work_id()

    assert UUID(first).version == 4
    assert UUID(second).version == 4
    assert first != second
    assert "source-title" not in first


def test_canonical_json_bytes_has_stable_utf8_key_order_and_newline() -> None:
    value = {"z": "中文", "a": {"b": 2, "a": 1}}

    assert canonical_json_bytes(value) == (
        b'{"a":{"a":1,"b":2},"z":"\xe4\xb8\xad\xe6\x96\x87"}\n'
    )


def test_canonical_json_rejects_nonfinite_numbers_and_non_string_keys() -> None:
    with pytest.raises(SourceContractError, match="^json-value-invalid$"):
        canonical_json_bytes({"value": float("nan")})

    with pytest.raises(SourceContractError, match="^json-value-invalid$"):
        canonical_json_bytes({1: "value"})  # type: ignore[dict-item]


def test_publish_json_is_exclusive_and_returns_exact_byte_hash(tmp_path: Path) -> None:
    target = tmp_path / "private" / "plan.json"
    value = _valid_plan_dict()

    digest = publish_json_exclusive(tmp_path, target, value)

    expected = canonical_json_bytes(value)
    assert target.read_bytes() == expected
    assert digest == hashlib.sha256(expected).hexdigest()
    with pytest.raises(SourceContractError, match="^artifact-exists$"):
        publish_json_exclusive(tmp_path, target, value)
    assert target.read_bytes() == expected


def test_publish_json_cleans_temporary_file_when_atomic_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "private" / "plan.json"

    def fail_link(_source: Path, _target: Path) -> None:
        raise OSError("hostile private path")

    monkeypatch.setattr(os, "link", fail_link)

    with pytest.raises(SourceContractError, match="^artifact-unavailable$"):
        publish_json_exclusive(tmp_path, target, _valid_plan_dict())

    assert not target.exists()
    assert not list(target.parent.glob(".plan.json.*.tmp"))


def test_publish_json_preserves_competing_target_created_at_publish_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "private" / "plan.json"
    competing = b"competing-private-artifact"

    def create_competitor_then_fail(_source: Path, destination: Path) -> None:
        destination.write_bytes(competing)
        raise FileExistsError

    monkeypatch.setattr(os, "link", create_competitor_then_fail)

    with pytest.raises(SourceContractError, match="^artifact-exists$"):
        publish_json_exclusive(tmp_path, target, _valid_plan_dict())

    assert target.read_bytes() == competing
    assert not list(target.parent.glob(".plan.json.*.tmp"))


def test_load_exact_json_rejects_unknown_keys_without_echoing_values(tmp_path: Path) -> None:
    target = tmp_path / "document.json"
    private_value = "private-source-value"
    target.write_text(
        json.dumps({"allowed": 1, "unexpected": private_value}),
        encoding="utf-8",
    )

    with pytest.raises(SourceContractError) as raised:
        load_exact_json(target, frozenset({"allowed"}))

    assert str(raised.value) == "json-schema-invalid"
    assert private_value not in str(raised.value)
    assert private_value not in repr(raised.value)


def test_verify_private_relative_accepts_contained_file_and_rejects_escape(
    tmp_path: Path,
) -> None:
    private = tmp_path / "work"
    private.mkdir()
    expected = private / "manifest.json"

    assert verify_private_relative(private, "manifest.json") == expected

    for invalid in ("../outside.json", str(tmp_path / "absolute.json"), "", "."):
        with pytest.raises(SourceContractError, match="^private-path-invalid$"):
            verify_private_relative(private, invalid)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_verify_private_relative_rejects_windows_junction(tmp_path: Path) -> None:
    private = tmp_path / "private"
    outside = tmp_path / "outside"
    private.mkdir()
    outside.mkdir()
    junction = private / "linked"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip("junction creation is unavailable")
    try:
        with pytest.raises(SourceContractError, match="^private-path-invalid$"):
            verify_private_relative(private, "linked/artifact.json")
    finally:
        os.rmdir(junction)


def test_sha256_file_streams_exact_bytes_and_rejects_directory(tmp_path: Path) -> None:
    source = tmp_path / "artifact.bin"
    source.write_bytes(b"abc" * 500_000)

    assert sha256_file(source) == hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(SourceContractError, match="^artifact-unavailable$"):
        sha256_file(tmp_path)


def test_load_action_plan_rejects_wrong_types_and_unknown_fields(tmp_path: Path) -> None:
    wrong_type = _valid_plan_dict()
    wrong_type["request_count"] = True
    wrong_type_path = tmp_path / "wrong-type.json"
    wrong_type_path.write_bytes(canonical_json_bytes(wrong_type))

    with pytest.raises(SourceContractError, match="^action-plan-invalid$"):
        load_action_plan(wrong_type_path)

    unknown = _valid_plan_dict()
    unknown["source_url"] = "https://example.invalid/private"
    unknown_path = tmp_path / "unknown.json"
    unknown_path.write_bytes(canonical_json_bytes(unknown))
    with pytest.raises(SourceContractError) as raised:
        load_action_plan(unknown_path)
    assert str(raised.value) == "action-plan-invalid"
    assert "example.invalid" not in repr(raised.value)


def test_approval_must_bind_exact_plan_hash(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    publish_json_exclusive(tmp_path, plan_path, _valid_plan_dict())
    receipt_path = tmp_path / "approval.json"
    publish_json_exclusive(
        tmp_path,
        receipt_path,
        _valid_receipt_dict(plan_sha256="0" * 64),
    )

    with pytest.raises(SourceContractError, match="^approval-plan-mismatch$"):
        verify_approval(load_action_plan(plan_path), receipt_path)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("approved", False),
        ("work_id", "00000000-0000-4000-8000-000000000002"),
        ("provider", "tikhub"),
        ("action", "download-source"),
        ("input_sha256", "2" * 64),
        ("request_count", 2),
        ("no_retry", False),
        ("no_fallback", False),
    ],
)
def test_approval_rejects_every_scope_mismatch(
    tmp_path: Path, field: str, replacement: object
) -> None:
    plan_value = _valid_plan_dict()
    plan_path = tmp_path / "plan.json"
    plan_sha256 = publish_json_exclusive(tmp_path, plan_path, plan_value)
    receipt = _valid_receipt_dict(plan_sha256=plan_sha256)
    receipt[field] = replacement
    receipt_path = tmp_path / "approval.json"
    publish_json_exclusive(tmp_path, receipt_path, receipt)

    with pytest.raises(SourceContractError, match="^approval-scope-mismatch$"):
        verify_approval(load_action_plan(plan_path), receipt_path)


def test_valid_approval_returns_redacted_immutable_receipt(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_sha256 = publish_json_exclusive(tmp_path, plan_path, _valid_plan_dict())
    receipt_path = tmp_path / "approval.json"
    publish_json_exclusive(
        tmp_path,
        receipt_path,
        _valid_receipt_dict(plan_sha256=plan_sha256),
    )

    plan = load_action_plan(plan_path)
    receipt = verify_approval(plan, receipt_path)

    assert isinstance(plan, ActionPlan)
    assert isinstance(receipt, ApprovalReceipt)
    assert receipt.approved is True
    assert repr(plan) == "ActionPlan(<redacted>)"
    assert repr(receipt) == "ApprovalReceipt(<redacted>)"


def test_public_dataclasses_hide_private_paths_and_source_values(tmp_path: Path) -> None:
    private_path = tmp_path / "private-source-title.wav"
    record = ArtifactRecord(
        relative_path="source-media/original.wav",
        sha256="3" * 64,
        size_bytes=42,
        path=private_path,
    )
    order = SourceWorkOrder(
        work_id="00000000-0000-4000-8000-000000000001",
        source_kind="local",
        authorized=True,
        created_at="2026-08-13T00:00:00Z",
        stage="source_registered",
        source_input_sha256="4" * 64,
        private_root=tmp_path / "private-work-root",
    )

    assert repr(record) == "ArtifactRecord(<redacted>)"
    assert repr(order) == "SourceWorkOrder(<redacted>)"
    assert str(private_path) not in repr(record)
    assert "private-work-root" not in repr(order)


def test_plan_key_fixture_matches_loader_contract() -> None:
    assert frozenset(_valid_plan_dict()) == PLAN_KEYS

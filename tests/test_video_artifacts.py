"""Stable, no-clobber artifact boundary tests for content production."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from boomearth.video.artifacts import (
    ArtifactError,
    capture_regular_file,
    encode_canonical_json,
    load_json_snapshot,
    publish_bytes_no_clobber,
    snapshot_matches,
)


def test_capture_regular_file_returns_the_exact_opened_payload(tmp_path: Path) -> None:
    """Would fail if capture hashed metadata or a different read from its returned bytes."""

    source = tmp_path / "工程" / "content-plan.candidate.json"
    source.parent.mkdir()
    payload = b'{"schema_version":1}\n'
    source.write_bytes(payload)

    snapshot = capture_regular_file(source, within=tmp_path)

    assert snapshot.path == source.absolute()
    assert snapshot.payload == payload
    assert snapshot.size == len(payload)
    assert snapshot.sha256 == hashlib.sha256(payload).hexdigest()
    assert snapshot_matches(snapshot)


def test_snapshot_no_longer_matches_after_bytes_change(tmp_path: Path) -> None:
    """Would fail if downstream stages trusted an old digest after source replacement."""

    source = tmp_path / "content-plan.json"
    source.write_bytes(b"first")
    snapshot = capture_regular_file(source, within=tmp_path)

    source.write_bytes(b"second")

    assert not snapshot_matches(snapshot)


def test_canonical_json_is_deterministic_unicode_and_one_newline() -> None:
    """Would fail if formal artifact bytes varied with insertion order or ASCII escaping."""

    assert encode_canonical_json({"b": 2, "a": "中文"}) == (
        '{"a":"中文","b":2}\n'.encode("utf-8")
    )
    with pytest.raises(ArtifactError, match="artifact JSON is invalid"):
        encode_canonical_json({"not_finite": float("nan")})


def test_load_json_snapshot_rejects_duplicate_keys(tmp_path: Path) -> None:
    """Would fail if two parsers could interpret one formal artifact differently."""

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}\n', encoding="utf-8")

    with pytest.raises(ArtifactError, match="artifact JSON is invalid"):
        load_json_snapshot(duplicate, within=tmp_path)


def test_load_json_snapshot_returns_payload_bound_to_the_file_snapshot(
    tmp_path: Path,
) -> None:
    """Would fail if parsed JSON and its reported hash came from separate reads."""

    source = tmp_path / "content-plan.json"
    source.write_bytes('{"schema_version":1,"title":"中文"}\n'.encode("utf-8"))

    value, snapshot = load_json_snapshot(source, within=tmp_path)

    assert value == {"schema_version": 1, "title": "中文"}
    assert snapshot.payload == source.read_bytes()
    assert snapshot.sha256 == hashlib.sha256(snapshot.payload).hexdigest()


def test_capture_rejects_a_path_outside_its_declared_root(tmp_path: Path) -> None:
    """Would fail if a caller could smuggle an arbitrary local file into production."""

    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ArtifactError, match="artifact source is unavailable"):
        capture_regular_file(outside, within=project)


def test_publish_is_project_contained_and_no_clobber(tmp_path: Path) -> None:
    """Would fail if formal review artifacts could be overwritten or escape the project."""

    target = tmp_path / "工程" / "content-plan.json"
    first = publish_bytes_no_clobber(target, b"{}\n", within=tmp_path)

    assert target.read_bytes() == b"{}\n"
    assert first.sha256 == hashlib.sha256(b"{}\n").hexdigest()
    with pytest.raises(ArtifactError, match="artifact target is unavailable"):
        publish_bytes_no_clobber(target, b'{"changed":true}\n', within=tmp_path)
    with pytest.raises(ArtifactError, match="artifact target is unavailable"):
        publish_bytes_no_clobber(
            tmp_path.parent / "outside.json",
            b"{}\n",
            within=tmp_path,
        )


def test_publish_failure_leaves_no_formal_or_temporary_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if an interrupted publish left an ambiguous formal or temp file."""

    import boomearth.video.artifacts as artifacts

    target = tmp_path / "工程" / "content-plan.json"

    def reject_link(source: Path, destination: Path) -> None:
        raise OSError("synthetic local failure")

    monkeypatch.setattr(artifacts.os, "link", reject_link)

    with pytest.raises(ArtifactError, match="artifact target is unavailable"):
        publish_bytes_no_clobber(target, b"{}\n", within=tmp_path)

    assert not target.exists()
    assert list(target.parent.glob(".content-plan.json-*.tmp")) == []


def test_publish_rejects_same_bytes_when_target_is_not_the_staged_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Would fail if equal bytes could disguise a different concurrent target file."""

    import boomearth.video.artifacts as artifacts

    target = tmp_path / "工程" / "content-plan.json"

    def create_competing_target(source: Path, destination: Path) -> None:
        destination.write_bytes(source.read_bytes())

    monkeypatch.setattr(artifacts.os, "link", create_competing_target)

    with pytest.raises(ArtifactError, match="artifact target is unavailable"):
        publish_bytes_no_clobber(target, b"{}\n", within=tmp_path)

    assert target.read_bytes() == b"{}\n"
    assert list(target.parent.glob(".content-plan.json-*.tmp")) == []


def test_capture_rejects_a_symlink_component_when_supported(tmp_path: Path) -> None:
    """Would fail if a project-relative lexical path could redirect outside the project."""

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "content-plan.json").write_text("{}\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    linked = project / "工程"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable for this Windows account")

    with pytest.raises(ArtifactError, match="artifact source is unavailable"):
        capture_regular_file(linked / "content-plan.json", within=project)


def test_json_loader_rejects_non_utf8_without_echoing_bytes(tmp_path: Path) -> None:
    """Would fail if malformed private bytes leaked through a decoder exception."""

    source = tmp_path / "private.json"
    source.write_bytes(b'\xff\xfe{"secret":"never-echo"}')

    with pytest.raises(ArtifactError, match="^artifact JSON is invalid$") as error:
        load_json_snapshot(source, within=tmp_path)

    assert "never-echo" not in str(error.value)


def test_canonical_json_round_trips_without_nonstandard_values() -> None:
    """Would fail if the encoder emitted bytes the strict loader could not consume."""

    encoded = encode_canonical_json(
        {"schema_version": 1, "scenes": [{"id": "scene-01"}]}
    )

    assert json.loads(encoded.decode("utf-8")) == {
        "schema_version": 1,
        "scenes": [{"id": "scene-01"}],
    }

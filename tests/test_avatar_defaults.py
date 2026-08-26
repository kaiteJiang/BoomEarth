from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence
from datetime import datetime, timedelta, timezone

import pytest

from boomearth.video.avatar_defaults import (
    AvatarDefaultError,
    AvatarLook,
    DefaultAvatarConfig,
    load_default_avatar_config,
    select_default_look,
)


GROUP_ID = "a" * 32
PREFERRED_ID = "b" * 32
PROFILE = "headroom_08-circle-lower-left"
ROOT = Path(__file__).resolve().parents[1]
CONFIGURE_SCRIPT = ROOT / "automation" / "scripts" / "configure_default_avatar.py"
RESOLVE_SCRIPT = ROOT / "automation" / "scripts" / "resolve_default_avatar.py"
DIRECTOR_SKILL = ROOT / ".agents" / "skills" / "katerj-video-director" / "SKILL.md"
DELIVERY_GATES = (
    ROOT
    / ".agents"
    / "skills"
    / "katerj-video-director"
    / "references"
    / "delivery-gates.md"
)
OLDER_HEYGEN_SPEC = (
    ROOT / "docs" / "superpowers" / "specs" / "2026-08-15-heygen-personal-avatar-design.md"
)


def _canonical_hash(payload: dict[str, object]) -> str:
    rendered = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _write_config(
    path: Path,
    *,
    group_id: str = GROUP_ID,
    preferred_look_id: str = PREFERRED_ID,
) -> Path:
    payload: dict[str, object] = {
        "schema_version": 2,
        "owner": "user",
        "display_name": "User",
        "group_id": group_id,
        "preferred_look_id": preferred_look_id,
        "preferred_engine": "avatar_iii",
        "composite_profile": PROFILE,
    }
    payload["payload_sha256"] = _canonical_hash(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_looks(path: Path, *, age_minutes: int = 0) -> Path:
    captured_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    payload: dict[str, object] = {
        "schema_version": 1,
        "operation": "heygen.avatar.looks.list.v3",
        "group_id": GROUP_ID,
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "looks": [
            {
                "look_id": PREFERRED_ID,
                "group_id": GROUP_ID,
                "status": "completed",
                "orientation": "portrait",
                "preview_available": True,
            }
        ],
    }
    payload["payload_sha256"] = _canonical_hash(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path: Path) -> DefaultAvatarConfig:
    return load_default_avatar_config(_write_config(tmp_path / "default-avatar.json"))


def test_private_config_loads_exact_contract_without_repr_leak(tmp_path: Path) -> None:
    config = load_default_avatar_config(_write_config(tmp_path / "default-avatar.json"))

    assert config.owner == "user"
    assert config.group_id == GROUP_ID
    assert config.preferred_look_id == PREFERRED_ID
    assert config.preferred_engine == "avatar_iii"
    assert config.composite_profile == PROFILE
    assert GROUP_ID not in repr(config)
    assert PREFERRED_ID not in repr(config)


@pytest.mark.parametrize("change", ["unknown-key", "wrong-owner", "wrong-profile", "hash"])
def test_private_config_rejects_any_contract_drift(tmp_path: Path, change: str) -> None:
    path = _write_config(tmp_path / "default-avatar.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if change == "unknown-key":
        payload["extra"] = True
    elif change == "wrong-owner":
        payload["owner"] = "stock"
    elif change == "wrong-profile":
        payload["composite_profile"] = "rectangle"
    else:
        payload["payload_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AvatarDefaultError, match="avatar-config-invalid"):
        load_default_avatar_config(path)


def test_private_config_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "default-avatar.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(AvatarDefaultError, match="avatar-config-invalid"):
        load_default_avatar_config(path)


def test_preferred_ready_look_wins_without_random_choice(
    config: DefaultAvatarConfig,
) -> None:
    preferred = AvatarLook(PREFERRED_ID, GROUP_ID, "completed", "portrait", True)
    selection = select_default_look(
        config,
        [preferred, AvatarLook("c" * 32, GROUP_ID, "completed", "portrait", True)],
        chooser=lambda _items: pytest.fail("chooser must not run"),
    )

    assert selection.look_id == preferred.look_id
    assert selection.reason == "preferred"
    assert selection.candidate_count == 2
    assert PREFERRED_ID not in repr(selection)


def test_missing_preferred_randomizes_only_portrait_ready_same_group(
    config: DefaultAvatarConfig,
) -> None:
    chosen = AvatarLook("c" * 32, GROUP_ID, "completed", "portrait", True)
    other_group = AvatarLook("d" * 32, "e" * 32, "completed", "portrait", True)
    landscape = AvatarLook("f" * 32, GROUP_ID, "completed", "landscape", True)
    unavailable = AvatarLook("1" * 32, GROUP_ID, "processing", "portrait", True)
    no_preview = AvatarLook("2" * 32, GROUP_ID, "completed", "portrait", False)
    seen: list[AvatarLook] = []

    def choose(items: Sequence[AvatarLook]) -> AvatarLook:
        seen.extend(items)
        return items[0]

    result = select_default_look(
        config,
        [other_group, landscape, unavailable, no_preview, chosen],
        chooser=choose,
    )

    assert seen == [chosen]
    assert result.look_id == chosen.look_id
    assert result.reason == "same-group-random"
    assert result.candidate_count == 1


def test_landscape_pool_is_used_only_when_no_portrait_is_ready(
    config: DefaultAvatarConfig,
) -> None:
    landscape = AvatarLook("c" * 32, GROUP_ID, "completed", "landscape", True)
    seen: list[AvatarLook] = []

    result = select_default_look(
        config,
        [landscape],
        chooser=lambda items: seen.extend(items) or items[0],
    )

    assert seen == [landscape]
    assert result.look_id == landscape.look_id


def test_no_ready_same_group_look_fails(config: DefaultAvatarConfig) -> None:
    with pytest.raises(AvatarDefaultError, match="avatar-look-unavailable"):
        select_default_look(config, [])


def test_chooser_cannot_return_a_look_outside_the_filtered_pool(
    config: DefaultAvatarConfig,
) -> None:
    eligible = AvatarLook("c" * 32, GROUP_ID, "completed", "portrait", True)
    outside = AvatarLook("d" * 32, "e" * 32, "completed", "portrait", True)

    with pytest.raises(AvatarDefaultError, match="avatar-look-unavailable"):
        select_default_look(config, [eligible, outside], chooser=lambda _items: outside)


def test_configure_cli_writes_no_clobber_config_without_echoing_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure = _load_script(CONFIGURE_SCRIPT, "configure_avatar_under_test")
    values = iter([GROUP_ID, PREFERRED_ID])
    monkeypatch.setattr(configure.getpass, "getpass", lambda _prompt: next(values))
    monkeypatch.setattr(configure, "_is_private_ignored_target", lambda _path: True)
    target = tmp_path / "default-avatar.json"

    result = configure.main(
        ["--config", str(target), "--display-name", "User"]
    )

    assert result == 0
    assert load_default_avatar_config(target).group_id == GROUP_ID
    assert capsys.readouterr().out.splitlines() == ["status=configured"]
    before = target.read_bytes()
    assert configure.main(
        ["--config", str(target), "--display-name", "User"]
    ) == 2
    assert target.read_bytes() == before
    output = capsys.readouterr().out
    assert GROUP_ID not in output and PREFERRED_ID not in output


def test_configure_cli_rejects_invalid_masked_ids_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure = _load_script(CONFIGURE_SCRIPT, "configure_invalid_avatar_under_test")
    values = iter(["invalid", PREFERRED_ID])
    monkeypatch.setattr(configure.getpass, "getpass", lambda _prompt: next(values))
    monkeypatch.setattr(configure, "_is_private_ignored_target", lambda _path: True)
    target = tmp_path / "default-avatar.json"

    assert configure.main(
        ["--config", str(target), "--display-name", "User"]
    ) == 2
    assert not target.exists()
    assert capsys.readouterr().out == "status=failed\n"


def test_resolver_writes_fresh_private_selection_receipt_without_echoing_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    resolver = _load_script(RESOLVE_SCRIPT, "resolve_avatar_under_test")
    monkeypatch.setattr(resolver, "_is_private_ignored_target", lambda _path: True)
    config_path = _write_config(tmp_path / "default-avatar.json")
    looks_path = _write_looks(tmp_path / "looks.json")
    receipt_path = tmp_path / "selection.json"

    result = resolver.main(
        [
            "--config",
            str(config_path),
            "--looks",
            str(looks_path),
            "--receipt",
            str(receipt_path),
        ]
    )

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result == 0
    assert set(receipt) == {
        "schema_version",
        "group_id",
        "selected_look_id",
        "reason",
        "candidate_count",
        "looks_captured_at",
        "selected_at",
        "payload_sha256",
    }
    assert receipt["group_id"] == GROUP_ID
    assert receipt["selected_look_id"] == PREFERRED_ID
    assert receipt["payload_sha256"] == _canonical_hash(
        {key: value for key, value in receipt.items() if key != "payload_sha256"}
    )
    output = capsys.readouterr().out
    assert output.splitlines() == ["status=selected", "reason=preferred"]
    assert GROUP_ID not in output and PREFERRED_ID not in output


def test_resolver_rejects_stale_look_snapshot_without_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    resolver = _load_script(RESOLVE_SCRIPT, "resolve_stale_avatar_under_test")
    monkeypatch.setattr(resolver, "_is_private_ignored_target", lambda _path: True)
    receipt = tmp_path / "selection.json"

    result = resolver.main(
        [
            "--config",
            str(_write_config(tmp_path / "default-avatar.json")),
            "--looks",
            str(_write_looks(tmp_path / "looks.json", age_minutes=6)),
            "--receipt",
            str(receipt),
        ]
    )

    assert result == 2
    assert not receipt.exists()
    assert capsys.readouterr().out == "status=failed\n"


def test_director_and_delivery_gate_lock_same_group_circle_default() -> None:
    director = DIRECTOR_SKILL.read_text(encoding="utf-8")
    gates = DELIVERY_GATES.read_text(encoding="utf-8")

    for text in (director, gates):
        assert "headroom_08-circle-lower-left" in text
        assert "same-group-only" in text
        assert "stock avatar" in text
        assert "user-indextts2-black-gold-v3" in text
    assert "矩形数字人小窗" in director
    assert "9:16 小窗" in director
    assert "explicit override" in director


def test_older_rectangle_design_is_marked_historical_for_new_projects() -> None:
    design = OLDER_HEYGEN_SPEC.read_text(encoding="utf-8")

    assert "布局状态：历史" in design
    assert "headroom_08-circle-lower-left" in design
    assert "不再是新项目默认" in design


def test_private_default_avatar_path_is_ignored_and_untracked() -> None:
    relative = "01-内容生产/视频工作台/.internal/heygen/default-avatar.json"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", relative],
        cwd=ROOT,
        check=False,
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )

    assert ignored.returncode == 0
    assert tracked.returncode != 0

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from boomearth.video.artifacts import (  # noqa: E402
    ArtifactError,
    capture_regular_file,
    publish_bytes_no_clobber,
)
from boomearth.video.avatar_defaults import (  # noqa: E402
    AvatarDefaultError,
    AvatarLook,
    load_default_avatar_config,
    select_default_look,
)

from automation.scripts.configure_default_avatar import (  # noqa: E402
    _is_private_ignored_target,
)


_SNAPSHOT_KEYS = {
    "schema_version",
    "operation",
    "group_id",
    "captured_at",
    "looks",
    "payload_sha256",
}
_LOOK_KEYS = {
    "look_id",
    "group_id",
    "status",
    "orientation",
    "preview_available",
}


def _reject_duplicate_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    document: dict[object, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _canonical_hash(document: dict[str, object]) -> str:
    payload = {key: value for key, value in document.items() if key != "payload_sha256"}
    rendered = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _canonical_json(document: dict[str, object]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AvatarDefaultError("avatar-look-snapshot-invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise AvatarDefaultError("avatar-look-snapshot-invalid") from None
    if parsed.tzinfo is None:
        raise AvatarDefaultError("avatar-look-snapshot-invalid")
    return parsed.astimezone(timezone.utc)


def _load_look_snapshot(path: Path, *, expected_group_id: str) -> tuple[str, tuple[AvatarLook, ...]]:
    try:
        snapshot = capture_regular_file(path, within=path.parent)
        document = json.loads(
            snapshot.payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (ArtifactError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        raise AvatarDefaultError("avatar-look-snapshot-invalid") from None
    if (
        not isinstance(document, dict)
        or set(document) != _SNAPSHOT_KEYS
        or document.get("schema_version") != 1
        or document.get("operation") != "heygen.avatar.looks.list.v3"
        or document.get("group_id") != expected_group_id
        or document.get("payload_sha256") != _canonical_hash(document)
        or not isinstance(document.get("looks"), list)
        or not all(isinstance(item, dict) and set(item) == _LOOK_KEYS for item in document["looks"])
    ):
        raise AvatarDefaultError("avatar-look-snapshot-invalid")
    captured = _parse_utc(document["captured_at"])
    age = (datetime.now(timezone.utc) - captured).total_seconds()
    if age < -30 or age > 300:
        raise AvatarDefaultError("avatar-look-snapshot-stale")
    try:
        looks = tuple(
            AvatarLook(
                look_id=item["look_id"],
                group_id=item["group_id"],
                status=item["status"],
                orientation=item["orientation"],
                preview_available=item["preview_available"],
            )
            for item in document["looks"]
        )
    except (KeyError, TypeError, AvatarDefaultError):
        raise AvatarDefaultError("avatar-look-snapshot-invalid") from None
    if any(look.group_id != expected_group_id for look in looks):
        raise AvatarDefaultError("avatar-look-snapshot-invalid")
    return document["captured_at"], looks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--looks", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        config_path = arguments.config.absolute()
        looks_path = arguments.looks.absolute()
        receipt_path = arguments.receipt.absolute()
        if (
            receipt_path.exists()
            or receipt_path.name != "selection.json"
            or not all(
                _is_private_ignored_target(path)
                for path in (config_path, looks_path, receipt_path)
            )
        ):
            raise AvatarDefaultError("avatar-selection-invalid")
        config = load_default_avatar_config(config_path)
        captured_at, looks = _load_look_snapshot(
            looks_path, expected_group_id=config.group_id
        )
        selection = select_default_look(config, looks)
        selected_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        receipt: dict[str, object] = {
            "schema_version": 1,
            "group_id": config.group_id,
            "selected_look_id": selection.look_id,
            "reason": selection.reason,
            "candidate_count": selection.candidate_count,
            "looks_captured_at": captured_at,
            "selected_at": selected_at,
        }
        receipt["payload_sha256"] = _canonical_hash(receipt)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        publish_bytes_no_clobber(
            receipt_path, _canonical_json(receipt), within=receipt_path.parent
        )
    except (ArtifactError, AvatarDefaultError, OSError, RuntimeError, SystemExit):
        print("status=failed")
        return 2
    print("status=selected")
    print(f"reason={selection.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

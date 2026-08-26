from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import secrets
import stat
from typing import Callable, Sequence


DEFAULT_COMPOSITE_PROFILE = "headroom_08-circle-lower-left"
_PRIVATE_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "owner",
        "display_name",
        "group_id",
        "preferred_look_id",
        "preferred_engine",
        "composite_profile",
        "payload_sha256",
    }
)
_ENGINES = frozenset({"avatar_iii", "avatar_iv", "avatar_v"})


class AvatarDefaultError(ValueError):
    """A fixed, redacted private-avatar contract failure."""


@dataclass(frozen=True, slots=True, repr=False)
class DefaultAvatarConfig:
    schema_version: int
    owner: str
    display_name: str
    group_id: str
    preferred_look_id: str
    preferred_engine: str
    composite_profile: str
    payload_sha256: str

    def __repr__(self) -> str:
        return "DefaultAvatarConfig(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AvatarLook:
    look_id: str
    group_id: str
    status: str
    orientation: str
    preview_available: bool

    def __post_init__(self) -> None:
        if (
            _PRIVATE_ID.fullmatch(self.look_id) is None
            or _PRIVATE_ID.fullmatch(self.group_id) is None
            or not isinstance(self.status, str)
            or self.orientation not in {"portrait", "landscape", "square"}
            or not isinstance(self.preview_available, bool)
        ):
            raise AvatarDefaultError("avatar-look-invalid")

    def __repr__(self) -> str:
        return "AvatarLook(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class AvatarSelection:
    look_id: str
    reason: str
    candidate_count: int

    def __repr__(self) -> str:
        return "AvatarSelection(<redacted>)"


def _reject_duplicate_keys(
    pairs: list[tuple[object, object]],
) -> dict[object, object]:
    document: dict[object, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON key")
        document[key] = value
    return document


def _canonical_payload(document: dict[str, object]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def config_payload_sha256(document: dict[str, object]) -> str:
    payload = {key: value for key, value in document.items() if key != "payload_sha256"}
    return hashlib.sha256(_canonical_payload(payload)).hexdigest()


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attributes & 0x400)


def load_default_avatar_config(path: Path) -> DefaultAvatarConfig:
    path = Path(path)
    try:
        file_stat = path.lstat()
        if _is_reparse_point(path) or not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("unsafe file")
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
        raise AvatarDefaultError("avatar-config-invalid") from None

    if (
        not isinstance(document, dict)
        or set(document) != _CONFIG_KEYS
        or document.get("schema_version") != 2
        or document.get("owner") != "user"
        or not isinstance(document.get("display_name"), str)
        or not document["display_name"].strip()
        or len(document["display_name"]) > 80
        or _PRIVATE_ID.fullmatch(document.get("group_id", "")) is None
        or _PRIVATE_ID.fullmatch(document.get("preferred_look_id", "")) is None
        or document.get("preferred_engine") not in _ENGINES
        or document.get("composite_profile") != DEFAULT_COMPOSITE_PROFILE
        or _SHA256.fullmatch(document.get("payload_sha256", "")) is None
        or document["payload_sha256"] != config_payload_sha256(document)
    ):
        raise AvatarDefaultError("avatar-config-invalid")

    return DefaultAvatarConfig(
        schema_version=2,
        owner="user",
        display_name=document["display_name"],
        group_id=document["group_id"],
        preferred_look_id=document["preferred_look_id"],
        preferred_engine=document["preferred_engine"],
        composite_profile=DEFAULT_COMPOSITE_PROFILE,
        payload_sha256=document["payload_sha256"],
    )


def select_default_look(
    config: DefaultAvatarConfig,
    looks: Sequence[AvatarLook],
    *,
    chooser: Callable[[Sequence[AvatarLook]], AvatarLook] = secrets.choice,
) -> AvatarSelection:
    if config.composite_profile != DEFAULT_COMPOSITE_PROFILE:
        raise AvatarDefaultError("avatar-config-invalid")
    look_list = tuple(looks)
    if len({look.look_id for look in look_list}) != len(look_list):
        raise AvatarDefaultError("avatar-look-invalid")
    eligible = tuple(
        look
        for look in look_list
        if look.group_id == config.group_id
        and look.status == "completed"
        and look.preview_available
    )
    preferred = next(
        (look for look in eligible if look.look_id == config.preferred_look_id),
        None,
    )
    if preferred is not None:
        return AvatarSelection(
            look_id=preferred.look_id,
            reason="preferred",
            candidate_count=len(eligible),
        )
    if not eligible:
        raise AvatarDefaultError("avatar-look-unavailable")
    portrait = tuple(look for look in eligible if look.orientation == "portrait")
    pool = portrait or eligible
    try:
        selected = chooser(pool)
    except Exception as exc:
        if isinstance(exc, AvatarDefaultError):
            raise
        raise AvatarDefaultError("avatar-look-unavailable") from None
    if not isinstance(selected, AvatarLook) or selected not in pool:
        raise AvatarDefaultError("avatar-look-unavailable")
    return AvatarSelection(
        look_id=selected.look_id,
        reason="same-group-random",
        candidate_count=len(pool),
    )

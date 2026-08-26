from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from boomearth.video.artifacts import ArtifactError, publish_bytes_no_clobber  # noqa: E402
from boomearth.video.avatar_defaults import (  # noqa: E402
    DEFAULT_COMPOSITE_PROFILE,
    AvatarDefaultError,
    config_payload_sha256,
)


def _canonical_workspace_root(source_root: Path) -> Path:
    marker = source_root / ".git"
    if not marker.is_file():
        return source_root
    try:
        prefix, separator, raw_git_dir = marker.read_text(
            encoding="utf-8"
        ).strip().partition(":")
        if prefix.casefold() != "gitdir" or separator != ":":
            return source_root
        git_dir = Path(raw_git_dir.strip())
        if not git_dir.is_absolute():
            git_dir = marker.parent / git_dir
        common_dir = Path((git_dir / "commondir").read_text(encoding="utf-8").strip())
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir
        candidate = common_dir.resolve(strict=True).parent
        if (candidate / "automation" / "config").is_dir():
            return candidate
    except (OSError, RuntimeError, UnicodeDecodeError):
        pass
    return source_root


WORKSPACE = _canonical_workspace_root(ROOT)
PRIVATE_HEYGEN_DIRECTORY = (
    WORKSPACE / "01-内容生产" / "视频工作台" / ".internal" / "heygen"
)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attributes & 0x400)


def _has_reparse_component(path: Path) -> bool:
    absolute = Path(os.path.abspath(path))
    for candidate in (absolute, *absolute.parents):
        if candidate.exists() or candidate.is_symlink():
            if _is_reparse_point(candidate):
                return True
    return False


def _is_private_ignored_target(path: Path) -> bool:
    path = Path(path).absolute()
    try:
        relative_private = path.relative_to(PRIVATE_HEYGEN_DIRECTORY.absolute())
        relative_workspace = path.relative_to(WORKSPACE.resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return False
    if not relative_private.parts or _has_reparse_component(path):
        return False
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", str(relative_workspace)],
        cwd=WORKSPACE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(relative_workspace)],
        cwd=WORKSPACE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return ignored.returncode == 0 and tracked.returncode != 0


def _canonical_json(document: dict[str, object]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--engine",
        choices=("avatar_iii", "avatar_iv", "avatar_v"),
        default="avatar_iii",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        target = arguments.config.absolute()
        display_name = arguments.display_name.strip()
        if (
            target.exists()
            or target.name != "default-avatar.json"
            or not display_name
            or len(display_name) > 80
            or not _is_private_ignored_target(target)
        ):
            raise AvatarDefaultError("avatar-config-invalid")
        group_id = getpass.getpass("Group ID: ").strip()
        preferred_look_id = getpass.getpass("Preferred look ID: ").strip()
        if (
            re.fullmatch(r"[0-9a-f]{32}", group_id) is None
            or re.fullmatch(r"[0-9a-f]{32}", preferred_look_id) is None
        ):
            raise AvatarDefaultError("avatar-config-invalid")
        document: dict[str, object] = {
            "schema_version": 2,
            "owner": "user",
            "display_name": display_name,
            "group_id": group_id,
            "preferred_look_id": preferred_look_id,
            "preferred_engine": arguments.engine,
            "composite_profile": DEFAULT_COMPOSITE_PROFILE,
        }
        document["payload_sha256"] = config_payload_sha256(document)
        target.parent.mkdir(parents=True, exist_ok=True)
        publish_bytes_no_clobber(target, _canonical_json(document), within=target.parent)
    except (ArtifactError, AvatarDefaultError, OSError, RuntimeError, SystemExit):
        print("status=failed")
        return 2
    print("status=configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

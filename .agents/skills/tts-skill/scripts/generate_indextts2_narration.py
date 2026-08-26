#!/usr/bin/env python3
"""Run the locked local IndexTTS2 narrator through its canonical API."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def find_workspace() -> Path:
    for parent in Path(__file__).resolve().parents:
        has_workspace_marker = any(
            (parent / marker).is_file() for marker in ("AGENTS.md", "CLAUDE.md")
        )
        has_tts_config = (parent / "automation" / "config" / "tts-routing.json").is_file()
        if has_workspace_marker and has_tts_config:
            return parent
    raise SystemExit("Unable to locate workspace root")


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
        common_value = (git_dir / "commondir").read_text(encoding="utf-8").strip()
        common_dir = Path(common_value)
        if not common_dir.is_absolute():
            common_dir = git_dir / common_dir
        candidate = common_dir.resolve(strict=True).parent
        if (candidate / "automation" / "config" / "tts-routing.json").is_file():
            return candidate
    except (OSError, RuntimeError, UnicodeDecodeError):
        pass
    return source_root


WORKSPACE = find_workspace()
CANONICAL_WORKSPACE = _canonical_workspace_root(WORKSPACE)
SOURCE_ROOT = WORKSPACE / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from boomearth.audio.indextts2 import (  # noqa: E402
    CURRENT_VOICE_ID,
    IndexTTS2Narrator,
    IndexTTS2ValidationError,
    TTSRouting,
    VoiceManifest,
)


DEFAULT_CONFIG = WORKSPACE / "automation" / "config" / "tts-routing.json"
DEFAULT_PRONUNCIATION_LEXICON = (
    Path(__file__).resolve().parents[1] / "references" / "pronunciation-lexicon.json"
)


def _canonical_path(path: Path) -> str:
    return str(path.resolve(strict=False)).casefold()


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


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _route_signature(route: TTSRouting) -> tuple[object, ...]:
    return (
        route.provider,
        route.model,
        route.voice_id,
        _canonical_path(route.reference_audio_path),
        route.reference_audio_sha256,
        _canonical_path(route.interpreter_path),
        _canonical_path(route.cli_script_path),
        _canonical_path(route.model_dir),
        route.playback_speed,
        route.fp16,
        route.deepspeed,
        route.cuda_kernel,
        route.accel,
        route.torch_compile,
        route.used_fallback,
        _canonical_path(route.provenance_ledger_path),
    )


def load_locked_route(config_path: Path) -> TTSRouting:
    """Load only the canonical local-v3 route or an exactly equivalent copy."""
    try:
        canonical = TTSRouting.load(DEFAULT_CONFIG)
        route = TTSRouting.load(config_path)
    except (OSError, ValueError):
        raise SystemExit("TTS routing config violates the locked local route") from None
    if not _is_lower_sha256(route.reference_audio_sha256):
        raise SystemExit("TTS routing config violates the locked local route")
    if route.voice_id != CURRENT_VOICE_ID or _route_signature(route) != _route_signature(canonical):
        raise SystemExit("TTS routing config violates the locked local route")
    return route


def validate_path_topology(
    *,
    batch: Path,
    config: Path,
    lexicon: Path,
    reference: Path,
    provenance: Path,
    output: Path,
    manifest: Path,
    production: bool,
    force: bool,
) -> None:
    """Keep helper-owned aliases and reparse traversal out of canonical rendering."""
    del force
    raw_audit = output.with_name(f"{output.stem}.raw.wav")
    paths = (batch, config, lexicon, reference, provenance, output, manifest, raw_audit)
    if any(_has_reparse_component(path) for path in paths):
        raise SystemExit("unsafe narration path topology")
    if len({_canonical_path(path) for path in paths}) != len(paths):
        raise SystemExit("unsafe narration path topology")
    if any(not path.is_file() for path in (batch, config, lexicon, reference, provenance)):
        raise SystemExit("narration input is unavailable")
    if production and any(path.exists() for path in (output, manifest, raw_audit)):
        raise SystemExit("narration publication target already exists")


def path_is_git_ignored(path: Path) -> bool:
    for workspace in dict.fromkeys((WORKSPACE, CANONICAL_WORKSPACE)):
        try:
            relative = path.resolve(strict=False).relative_to(
                workspace.resolve(strict=True)
            )
            result = subprocess.run(
                ["git", "check-ignore", "-q", "--", str(relative)],
                cwd=workspace,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, ValueError):
            continue
        if result.returncode == 0:
            return True
    return False


def validate_publication_destination(output: Path, manifest: Path) -> None:
    raw_audit = output.with_name(f"{output.stem}.raw.wav")
    if (
        output.name != "narration.wav"
        or manifest.name != "voice_manifest.json"
        or _canonical_path(output.parent) != _canonical_path(manifest.parent)
        or _canonical_path(output.parent) != _canonical_path(raw_audit.parent)
        or not path_is_git_ignored(output)
        or not path_is_git_ignored(manifest)
        or not path_is_git_ignored(raw_audit)
    ):
        raise SystemExit("publication destination is not private")


def create_narrator(route: TTSRouting, lexicon: Path) -> IndexTTS2Narrator:
    return IndexTTS2Narrator(route, pronunciation_lexicon_path=lexicon)


def validate_canonical_manifest(manifest_path: Path, manifest: VoiceManifest) -> None:
    def reject_duplicate_json_keys(
        pairs: list[tuple[object, object]],
    ) -> dict[object, object]:
        document: dict[object, object] = {}
        for key, value in pairs:
            if key in document:
                raise ValueError("duplicate JSON key")
            document[key] = value
        return document

    try:
        with manifest_path.open("rb") as source:
            snapshot = source.read()
        persisted = json.loads(
            snapshot.decode("utf-8"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise SystemExit("canonical voice manifest is invalid") from None
    expected = manifest.to_dict()
    if (
        not isinstance(persisted, dict)
        or set(persisted) != set(VoiceManifest.__dataclass_fields__)
        or persisted != expected
    ):
        raise SystemExit("canonical voice manifest is invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--pronunciation-lexicon",
        type=Path,
        default=DEFAULT_PRONUNCIATION_LEXICON,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.force:
        raise SystemExit("--force is unavailable for locked no-clobber publication")
    if args.output.suffix.casefold() != ".wav":
        raise SystemExit("Local production output must be a lossless .wav file")
    if any(".." in path.parts for path in (args.batch_file, args.output, args.config, args.pronunciation_lexicon)):
        raise SystemExit("unsafe narration path topology")
    args.batch_file = args.batch_file.resolve(strict=False)
    args.output = args.output.resolve(strict=False)
    args.config = args.config.resolve(strict=False)
    args.pronunciation_lexicon = args.pronunciation_lexicon.resolve(strict=False)
    manifest_path = args.manifest or args.output.with_name("voice_manifest.json")
    if ".." in manifest_path.parts:
        raise SystemExit("unsafe narration path topology")
    manifest_path = manifest_path.resolve(strict=False)
    route = load_locked_route(args.config)
    validate_path_topology(
        batch=args.batch_file,
        config=args.config,
        lexicon=args.pronunciation_lexicon,
        reference=route.reference_audio_path,
        provenance=route.provenance_ledger_path,
        output=args.output,
        manifest=manifest_path,
        production=not args.dry_run,
        force=args.force,
    )
    narrator = create_narrator(route, args.pronunciation_lexicon)
    try:
        if args.dry_run:
            narrator.dry_run(args.batch_file)
            print("Pronunciation contract: PASS")
            print("IndexTTS2 local narration contract: PASS")
            return 0

        validate_publication_destination(args.output, manifest_path)
        manifest = narrator.render(args.batch_file, args.output, manifest_path)
        validate_canonical_manifest(manifest_path, manifest)
        narrator.probe_committed_wav(args.output, manifest_path)
    except IndexTTS2ValidationError:
        raise SystemExit("locked local narration validation failed") from None
    print("Local narration publication: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

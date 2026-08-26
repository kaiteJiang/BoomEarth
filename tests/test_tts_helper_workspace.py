"""Workspace discovery regression tests for the installed narration helper."""

from __future__ import annotations

import json
import subprocess
import sys
import types
import wave
from types import SimpleNamespace
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    REPO_ROOT
    / ".agents"
    / "skills"
    / "tts-skill"
    / "scripts"
    / "generate_indextts2_narration.py"
)


def _helper_location(root: Path) -> Path:
    return root / ".agents" / "skills" / "tts-skill" / "scripts" / HELPER.name


def _write_tts_config(root: Path) -> None:
    config = root / "automation" / "config" / "tts-routing.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")


def _load_helper_at(script_location: Path) -> types.ModuleType:
    """Execute the real helper source with a controlled installed location."""
    module = types.ModuleType("tts_helper_workspace_test")
    module.__file__ = str(script_location)
    exec(compile(HELPER.read_text(encoding="utf-8"), str(HELPER), "exec"), module.__dict__)
    return module


ROUTING_CONFIG = REPO_ROOT / "automation" / "config" / "tts-routing.json"


def _write_pcm_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24_000)
        audio.writeframes(b"\0\0" * 240)


def _make_directory_reparse_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as error:
        if getattr(error, "winerror", None) != 1314:
            raise
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        pytest.skip("reparse point creation is unavailable")


def _path_set(root: Path) -> dict[str, Path]:
    paths = {
        "batch": root / "batch.jsonl",
        "config": root / "tts-routing.json",
        "lexicon": root / "lexicon.json",
        "reference": root / "reference.wav",
        "provenance": root / "ledger.json",
        "output": root / "output" / "narration.wav",
        "manifest": root / "output" / "voice_manifest.json",
    }
    paths["batch"].write_text('{"text":"safe"}\n', encoding="utf-8")
    paths["config"].write_text("{}\n", encoding="utf-8")
    paths["lexicon"].write_text('{"terms": {}}\n', encoding="utf-8")
    _write_pcm_wav(paths["reference"])
    paths["provenance"].write_text("{}\n", encoding="utf-8")
    return paths


def test_actual_helper_finds_repo_workspace_from_outside_cwd(tmp_path: Path) -> None:
    """Would fail if the installed helper ignored this repo's AGENTS.md marker."""
    outside_cwd = tmp_path / "outside-cwd"
    outside_cwd.mkdir()

    result = subprocess.run(
        [sys.executable, str(HELPER), "--help"],
        cwd=outside_cwd,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout


def test_find_workspace_accepts_agents_marker_with_tts_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if AGENTS.md workspaces with the helper's required config were rejected."""
    root = tmp_path / "agents-workspace"
    root.mkdir()
    (root / "AGENTS.md").write_text("workspace instructions\n", encoding="utf-8")
    _write_tts_config(root)
    outside_cwd = tmp_path / "outside-cwd"
    outside_cwd.mkdir()
    monkeypatch.chdir(outside_cwd)

    helper = _load_helper_at(_helper_location(root))

    assert helper.WORKSPACE == root
    assert helper.DEFAULT_CONFIG == root / "automation" / "config" / "tts-routing.json"


def test_find_workspace_keeps_legacy_claude_marker_compatibility(tmp_path: Path) -> None:
    """Would fail if existing CLAUDE.md helper workspaces stopped being recognized."""
    root = tmp_path / "legacy-workspace"
    root.mkdir()
    (root / "CLAUDE.md").write_text("legacy workspace instructions\n", encoding="utf-8")
    _write_tts_config(root)

    helper = _load_helper_at(_helper_location(root))

    assert helper.WORKSPACE == root


def test_find_workspace_rejects_marker_only_parent(tmp_path: Path) -> None:
    """Would fail if an arbitrary ancestor with only AGENTS.md became a workspace root."""
    false_parent = tmp_path / "marker-only-parent"
    false_parent.mkdir()
    (false_parent / "AGENTS.md").write_text("not a workspace\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="Unable to locate workspace root"):
        _load_helper_at(_helper_location(false_parent))


def test_custom_route_must_be_identical_to_the_locked_route(tmp_path: Path) -> None:
    """Would fail if --config could redirect the helper away from its canonical route."""
    helper = _load_helper_at(HELPER)
    copied = tmp_path / "tts-routing.json"
    copied.write_bytes(ROUTING_CONFIG.read_bytes())

    assert helper.load_locked_route(copied).voice_id == helper.CURRENT_VOICE_ID
    payload = json.loads(copied.read_text(encoding="utf-8"))
    payload["schema_version"] = 2
    copied.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="locked local route") as error:
        helper.load_locked_route(copied)
    assert str(copied) not in str(error.value)


def test_preflight_rejects_manifest_alias_to_reference_before_publication(
    tmp_path: Path,
) -> None:
    """Would fail if a manifest destination could overwrite canonical reference audio."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    paths["manifest"] = paths["reference"]

    with pytest.raises(SystemExit, match="unsafe narration path topology"):
        helper.validate_path_topology(**paths, production=True, force=False)


def test_preflight_rejects_manifest_alias_to_output_before_publication(
    tmp_path: Path,
) -> None:
    """Would fail if a manifest could overwrite or be replaced by narration audio."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    paths["manifest"] = paths["output"]

    with pytest.raises(SystemExit, match="unsafe narration path topology"):
        helper.validate_path_topology(**paths, production=True, force=False)


def test_preflight_rejects_existing_publication_targets_even_with_force(tmp_path: Path) -> None:
    """Would fail if --force allowed a final artifact to be overwritten."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    paths["output"].parent.mkdir()
    paths["output"].write_bytes(b"existing")

    with pytest.raises(SystemExit, match="publication target already exists"):
        helper.validate_path_topology(**paths, production=True, force=True)


def test_preflight_rejects_existing_manifest_even_with_force(tmp_path: Path) -> None:
    """Would fail if --force allowed a private manifest to be replaced."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    paths["manifest"].parent.mkdir()
    paths["manifest"].write_text("{}\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="publication target already exists"):
        helper.validate_path_topology(**paths, production=True, force=True)


def test_preflight_rejects_a_reparse_publication_ancestor(tmp_path: Path) -> None:
    """Would fail if a symlink or junction could redirect a narration publication."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = tmp_path / "redirected-output"
    _make_directory_reparse_link(redirected, outside)
    for name in ("output", "manifest"):
        paths[name] = redirected / paths[name].name

    with pytest.raises(SystemExit, match="unsafe narration path topology"):
        helper.validate_path_topology(**paths, production=True, force=False)


def test_force_cannot_turn_the_reference_into_an_output_target(tmp_path: Path) -> None:
    """Would fail if --force could overwrite a canonical protected input."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    paths["output"] = paths["reference"]

    with pytest.raises(SystemExit, match="unsafe narration path topology"):
        helper.validate_path_topology(**paths, production=True, force=True)


def test_manifest_destination_must_be_ignored_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a manifest containing private route data could be tracked."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    monkeypatch.setattr(helper, "path_is_git_ignored", lambda path: False)

    with pytest.raises(SystemExit, match="publication destination is not private"):
        helper.validate_publication_destination(paths["output"], paths["manifest"])


def test_active_project_narration_outputs_are_private_but_noncanonical_media_is_rejected() -> None:
    """Would fail if active-project narration could be tracked or unrelated media were whitelisted."""
    helper = _load_helper_at(HELPER)
    media = (
        REPO_ROOT
        / "01-内容生产"
        / "视频工作台"
        / "制作中"
        / "private-narration-fixture"
        / "工程"
        / "media"
    )
    output = media / "narration.wav"
    manifest = media / "voice_manifest.json"
    raw_audit = media / "narration.raw.wav"
    noncanonical_output = media / "alternate.wav"

    for path in (output, manifest, raw_audit):
        assert helper.path_is_git_ignored(path)
    helper.validate_publication_destination(output, manifest)

    assert helper.path_is_git_ignored(noncanonical_output)
    with pytest.raises(SystemExit, match="publication destination is not private"):
        helper.validate_publication_destination(noncanonical_output, manifest)


def test_linked_worktree_accepts_private_output_in_the_canonical_main_workspace(
    tmp_path: Path,
) -> None:
    """Would fail if an isolated implementation could not publish durable private evidence."""
    helper = _load_helper_at(HELPER)
    main = tmp_path / "main"
    worktree = main / ".worktrees" / "voice-migration"
    git_dir = main / ".git" / "worktrees" / "voice-migration"
    (main / ".git").mkdir(parents=True)
    (main / "automation" / "config").mkdir(parents=True)
    (main / "automation" / "config" / "tts-routing.json").write_text(
        "{}\n", encoding="utf-8"
    )
    git_dir.mkdir(parents=True)
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")

    assert helper._canonical_workspace_root(worktree) == main


def test_helper_preflight_rejects_existing_raw_audit_target(tmp_path: Path) -> None:
    """Would fail if the helper left raw-audit collision checks to model execution."""
    helper = _load_helper_at(HELPER)
    paths = _path_set(tmp_path)
    paths["output"].parent.mkdir()
    paths["output"].with_name("narration.raw.wav").write_bytes(b"preserve")

    with pytest.raises(SystemExit, match="publication target already exists"):
        helper.validate_path_topology(**paths, production=True, force=False)


def test_dry_run_delegates_to_the_canonical_narrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if helper dry-run bypassed canonical locked validation."""
    helper = _load_helper_at(HELPER)
    batch_file = tmp_path / "batch.jsonl"
    batch_file.write_text('{"text":"dry run only"}\n', encoding="utf-8")
    output = tmp_path / "new-output" / "narration.wav"
    calls: list[tuple[str, Path]] = []

    class FakeNarrator:
        def dry_run(self, batch: Path) -> object:
            calls.append(("dry_run", batch))
            return object()

        def render(self, *args: object) -> object:
            pytest.fail("dry-run must not render")

    monkeypatch.setattr(helper, "create_narrator", lambda route, lexicon: FakeNarrator())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "helper",
            "--batch-file",
            str(batch_file),
            "--output",
            str(output),
            "--config",
            str(ROUTING_CONFIG),
            "--dry-run",
        ],
    )

    assert helper.main() == 0
    assert not output.exists()
    assert calls == [("dry_run", batch_file)]


def test_production_delegates_render_and_probe_with_a_canonical_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if helper emitted a second manifest schema or skipped committed probe."""
    helper = _load_helper_at(HELPER)
    batch_file = tmp_path / "batch.jsonl"
    batch_file.write_text('{"text":"offline fake"}\n', encoding="utf-8")
    output = tmp_path / "narration.wav"
    manifest = tmp_path / "voice_manifest.json"
    calls: list[tuple[str, tuple[Path, ...]]] = []
    payload = {
        field: (False if field == "used_fallback" else 1 if field == "segment_count" else "fixture")
        for field in helper.VoiceManifest.__dataclass_fields__
    }

    class FakeNarrator:
        def dry_run(self, *args: object) -> object:
            pytest.fail("production must not use dry_run")

        def render(self, batch: Path, output_path: Path, manifest_path: Path) -> object:
            calls.append(("render", (batch, output_path, manifest_path)))
            output_path.write_bytes(b"offline canonical fixture")
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            return SimpleNamespace(to_dict=lambda: payload)

        def probe_committed_wav(self, output_path: Path, manifest_path: Path) -> object:
            calls.append(("probe", (output_path, manifest_path)))
            return object()

    monkeypatch.setattr(helper, "create_narrator", lambda route, lexicon: FakeNarrator())
    monkeypatch.setattr(helper, "validate_publication_destination", lambda output, manifest: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "helper",
            "--batch-file",
            str(batch_file),
            "--output",
            str(output),
            "--manifest",
            str(manifest),
            "--config",
            str(ROUTING_CONFIG),
        ],
    )

    assert helper.main() == 0
    assert calls == [
        ("render", (batch_file, output, manifest)),
        ("probe", (output, manifest)),
    ]
    assert set(json.loads(manifest.read_text(encoding="utf-8"))) == set(
        helper.VoiceManifest.__dataclass_fields__
    )


def test_helper_rejects_duplicate_canonical_manifest_keys(
    tmp_path: Path,
) -> None:
    """The helper cannot normalize duplicate manifest keys after canonical render."""
    helper = _load_helper_at(HELPER)
    manifest_path = tmp_path / "voice_manifest.json"
    payload = {
        field: (False if field == "used_fallback" else 1 if field == "segment_count" else "fixture")
        for field in helper.VoiceManifest.__dataclass_fields__
    }
    duplicate_bytes = (
        "{"
        + ",".join(
            [
                f'"voice_id":{json.dumps(payload["voice_id"])}',
                f'"voice_id":{json.dumps(payload["voice_id"])}',
                *[
                    f"{json.dumps(key)}:{json.dumps(value)}"
                    for key, value in payload.items()
                    if key != "voice_id"
                ],
            ]
        )
        + "}"
    )
    manifest_path.write_text(duplicate_bytes, encoding="utf-8")

    with pytest.raises(SystemExit, match="canonical voice manifest is invalid"):
        helper.validate_canonical_manifest(
            manifest_path,
            SimpleNamespace(to_dict=lambda: payload),
        )

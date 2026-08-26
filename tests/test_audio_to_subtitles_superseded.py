from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from boomearth.workbench.source_artifacts import canonical_json_bytes, sha256_file


SCRIPT = (
    Path(__file__).parents[1]
    / ".agents"
    / "skills"
    / "ra-audio-to-subtitles"
    / "scripts"
    / "generate_subtitles.py"
)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    project = tmp_path / "制作中" / "v1-project"
    media_dir = project / "工程" / "media"
    qc_dir = project / "工程" / "qc"
    media_dir.mkdir(parents=True)
    qc_dir.mkdir(parents=True)
    media = media_dir / "narration.wav"
    media.write_bytes(b"RIFF-superseded-audio")
    script = project / "工程" / "tts-segments.jsonl"
    script.write_text(
        json.dumps({"segment_id": "segment-001", "text": "测试旁白"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
        newline="",
    )
    return project, media, script, qc_dir / "superseded-by-v2.json"


def _sidecar(project: Path, media: Path) -> dict[str, object]:
    return {
        "old_asr_plan_sha256": "a" * 64,
        "old_content_plan_sha256": "b" * 64,
        "old_narration_sha256": sha256_file(media),
        "schema_version": 1,
        "status": "superseded-before-network",
        "v1_project_id": project.name,
        "v2_project_id": f"{project.name}-v2",
        "v2_publication_revision_sha256": "c" * 64,
    }


def _invoke_live(
    module,
    monkeypatch: pytest.MonkeyPatch,
    media: Path,
    script: Path,
) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            str(media),
            "--script",
            str(script),
            "--expected-sha256",
            sha256_file(media),
            "--expected-script-sha256",
            sha256_file(script),
            "--out-dir",
            str(media.parent / "captions"),
        ],
    )
    return module.main()


def test_live_asr_rejects_superseded_audio_before_loading_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, media, script, sidecar = _project(tmp_path)
    sidecar.write_bytes(canonical_json_bytes(_sidecar(project, media)))
    module = _load("subtitles_superseded_valid")
    monkeypatch.setattr(
        module,
        "load_config",
        lambda *_args, **_kwargs: pytest.fail("credentials must not be loaded"),
    )

    with pytest.raises(RuntimeError, match="^FINAL_AUDIO_SUPERSEDED$"):
        _invoke_live(module, monkeypatch, media, script)

    assert not (media.parent / "captions").exists()


@pytest.mark.parametrize("mutation", ["malformed", "wrong_hash", "wrong_project"])
def test_live_asr_fails_closed_on_invalid_superseded_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    project, media, script, sidecar = _project(tmp_path)
    value = _sidecar(project, media)
    if mutation == "malformed":
        sidecar.write_bytes(b"not-json")
    else:
        if mutation == "wrong_hash":
            value["old_narration_sha256"] = "d" * 64
        else:
            value["v1_project_id"] = "different-project"
        sidecar.write_bytes(canonical_json_bytes(value))
    module = _load(f"subtitles_superseded_{mutation}")
    monkeypatch.setattr(
        module,
        "load_config",
        lambda *_args, **_kwargs: pytest.fail("credentials must not be loaded"),
    )

    with pytest.raises(RuntimeError, match="^SUPERSEDED_RECEIPT_INVALID$"):
        _invoke_live(module, monkeypatch, media, script)


def test_live_asr_without_superseded_receipt_preserves_existing_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project_root, media, script, _sidecar_path = _project(tmp_path)
    module = _load("subtitles_superseded_absent")

    def reached_existing_config_preflight(*_args, **_kwargs):
        raise RuntimeError("EXISTING_CONFIG_PREFLIGHT_REACHED")

    monkeypatch.setattr(module, "load_config", reached_existing_config_preflight)

    with pytest.raises(RuntimeError, match="^EXISTING_CONFIG_PREFLIGHT_REACHED$"):
        _invoke_live(module, monkeypatch, media, script)

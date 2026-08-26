from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3840, 2160), (242, 235, 220)).save(output, format="PNG")
    return output.getvalue()


def test_imported_profiled_candidate_binds_local_copy_to_verified_source_receipt(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        import_profiled_generation_reuse,
        prepare_profiled_generation_call,
        run_profiled_generation_call,
        validate_profiled_reuse_evidence,
    )

    workspace = tmp_path / "workspace"
    source = workspace / "制作中" / "source-project"
    target = workspace / "制作中" / "target-project"
    theme = "vivid-comic-explainer"
    source_base = source / "工程/assets/profiled-illustrations" / theme
    target_base = target / "工程/assets/profiled-illustrations" / theme
    source_prompt = source_base / "prompts/scene-01.md"
    source_candidate = source_base / "candidates/scene-01-candidate-01.png"
    source_intent = source_base / "generation/scene-01-candidate-01-intent.json"
    source_receipt = source_base / "generation/scene-01-candidate-01-receipt.json"
    target_prompt = target_base / "prompts/scene-02.md"
    payload = _png_bytes()
    source_artifact = run_profiled_generation_call(
        project_root=source,
        theme_id=theme,
        scene_id="scene-01",
        prompt_payload=b"source prompt\n",
        candidate_index=1,
        invoke=lambda _prompt, _call_id: payload,
        call_id="11111111-1111-4111-8111-111111111111",
    )
    target_ticket = prepare_profiled_generation_call(
        project_root=target,
        theme_id=theme,
        scene_id="scene-02",
        prompt_payload=b"target prompt\n",
        candidate_index=1,
        call_id="22222222-2222-4222-8222-222222222222",
    )

    artifact = import_profiled_generation_reuse(
        project_root=target,
        workspace_root=workspace,
        theme_id=theme,
        scene_id="scene-02",
        candidate_index=1,
        prompt_payload=(target / target_ticket.prompt_path).read_bytes(),
        source_project_root=source,
        source_scene_id="scene-01",
        source_candidate_path=source / source_artifact["path"],
        source_prompt_path=source / source_artifact["generation_prompt_path"],
        source_intent_path=source / source_artifact["generation_intent_path"],
        source_receipt_path=source / source_artifact["generation_receipt_path"],
        reuse_reason="same remote-workflow evidence",
    )

    copied_candidate = target / Path(artifact["path"])
    receipt = target / Path(artifact["reuse_receipt_path"])
    assert copied_candidate.read_bytes() == payload
    assert artifact["sha256"] == hashlib.sha256(payload).hexdigest()
    assert artifact["generation_prompt_sha256"] == hashlib.sha256(
        target_prompt.read_bytes()
    ).hexdigest()
    assert receipt.is_file()
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_value["event"] == "profiled-illustration-reuse-imported"
    assert receipt_value["source_project_path"] == "制作中/source-project"
    assert receipt_value["source_scene_id"] == "scene-01"
    assert receipt_value["source_candidate_sha256"] == artifact["sha256"]

    validate_profiled_reuse_evidence(
        project_root=target,
        theme_id=theme,
        scene_id="scene-02",
        candidate_index=1,
        prompt_path=artifact["generation_prompt_path"],
        prompt_sha256=artifact["generation_prompt_sha256"],
        candidate_path=artifact["path"],
        candidate_sha256=artifact["sha256"],
        artifact=artifact,
    )


def test_imported_profiled_candidate_rejects_tampered_local_copy(tmp_path: Path) -> None:
    from boomearth.video.profiled_generation import (
        ProfiledGenerationError,
        import_profiled_generation_reuse,
        prepare_profiled_generation_call,
        run_profiled_generation_call,
        validate_profiled_reuse_evidence,
    )

    workspace = tmp_path / "workspace"
    source = workspace / "制作中" / "source"
    target = workspace / "制作中" / "target"
    theme = "vivid-comic-explainer"
    base = source / "工程/assets/profiled-illustrations" / theme
    prompt = base / "prompts/scene-01.md"
    candidate = base / "candidates/scene-01-candidate-01.png"
    intent = base / "generation/scene-01-candidate-01-intent.json"
    receipt = base / "generation/scene-01-candidate-01-receipt.json"
    target_prompt = target / "工程/assets/profiled-illustrations" / theme / "prompts/scene-02.md"
    source_artifact = run_profiled_generation_call(
        project_root=source,
        theme_id=theme,
        scene_id="scene-01",
        prompt_payload=b"p",
        candidate_index=1,
        invoke=lambda _prompt, _call_id: _png_bytes(),
        call_id="11111111-1111-4111-8111-111111111111",
    )
    target_ticket = prepare_profiled_generation_call(
        project_root=target,
        theme_id=theme,
        scene_id="scene-02",
        prompt_payload=b"t",
        candidate_index=1,
        call_id="22222222-2222-4222-8222-222222222222",
    )
    artifact = import_profiled_generation_reuse(
        project_root=target,
        workspace_root=workspace,
        theme_id=theme,
        scene_id="scene-02",
        candidate_index=1,
        prompt_payload=(target / target_ticket.prompt_path).read_bytes(),
        source_project_root=source,
        source_scene_id="scene-01",
        source_candidate_path=source / source_artifact["path"],
        source_prompt_path=source / source_artifact["generation_prompt_path"],
        source_intent_path=source / source_artifact["generation_intent_path"],
        source_receipt_path=source / source_artifact["generation_receipt_path"],
        reuse_reason="same evidence",
    )
    (target / Path(artifact["path"])).write_bytes(b"tampered")

    try:
        validate_profiled_reuse_evidence(
            project_root=target,
            theme_id=theme,
            scene_id="scene-02",
            candidate_index=1,
            prompt_path=artifact["generation_prompt_path"],
            prompt_sha256=artifact["generation_prompt_sha256"],
            candidate_path=artifact["path"],
            candidate_sha256=artifact["sha256"],
            artifact=artifact,
        )
    except ProfiledGenerationError as error:
        assert str(error) == "profiled-generation-evidence-invalid"
    else:
        raise AssertionError("tampered candidate was accepted")


def test_imported_profiled_candidate_rejects_changed_source_evidence(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        ProfiledGenerationError,
        import_profiled_generation_reuse,
        prepare_profiled_generation_call,
        run_profiled_generation_call,
        validate_profiled_reuse_evidence,
    )

    workspace = tmp_path / "workspace"
    source = workspace / "制作中" / "source"
    target = workspace / "制作中" / "target"
    theme = "vivid-comic-explainer"
    generated = run_profiled_generation_call(
        project_root=source,
        theme_id=theme,
        scene_id="scene-01",
        prompt_payload=b"source prompt",
        candidate_index=1,
        invoke=lambda _prompt, _call_id: _png_bytes(),
        call_id="11111111-1111-4111-8111-111111111111",
    )
    ticket = prepare_profiled_generation_call(
        project_root=target,
        theme_id=theme,
        scene_id="scene-02",
        prompt_payload=b"target prompt",
        candidate_index=1,
        call_id="22222222-2222-4222-8222-222222222222",
    )
    artifact = import_profiled_generation_reuse(
        project_root=target,
        workspace_root=workspace,
        theme_id=theme,
        scene_id="scene-02",
        candidate_index=1,
        prompt_payload=(target / ticket.prompt_path).read_bytes(),
        source_project_root=source,
        source_scene_id="scene-01",
        source_candidate_path=source / generated["path"],
        source_prompt_path=source / generated["generation_prompt_path"],
        source_intent_path=source / generated["generation_intent_path"],
        source_receipt_path=source / generated["generation_receipt_path"],
        reuse_reason="same evidence",
    )
    (source / generated["generation_receipt_path"]).write_bytes(b"changed")

    try:
        validate_profiled_reuse_evidence(
            project_root=target,
            theme_id=theme,
            scene_id="scene-02",
            candidate_index=1,
            prompt_path=artifact["generation_prompt_path"],
            prompt_sha256=artifact["generation_prompt_sha256"],
            candidate_path=artifact["path"],
            candidate_sha256=artifact["sha256"],
            artifact=artifact,
        )
    except ProfiledGenerationError as error:
        assert str(error) == "profiled-generation-evidence-invalid"
    else:
        raise AssertionError("changed source evidence was accepted")


def test_user_approved_history_adaptation_preserves_original_and_revalidates(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        import_user_approved_history_adaptation,
        prepare_profiled_generation_call,
        validate_user_approved_history_adaptation_evidence,
    )

    project = tmp_path / "project"
    original = tmp_path / "history.png"
    raw = io.BytesIO()
    Image.new("RGB", (1672, 941), (242, 235, 220)).save(raw, format="PNG")
    original.write_bytes(raw.getvalue())
    ticket = prepare_profiled_generation_call(
        project_root=project,
        theme_id="vivid-comic-explainer",
        scene_id="scene-02",
        prompt_payload=b"approved prompt\n",
        candidate_index=1,
        call_id="22222222-2222-4222-8222-222222222222",
    )

    artifact = import_user_approved_history_adaptation(
        project_root=project,
        theme_id="vivid-comic-explainer",
        scene_id="scene-02",
        candidate_index=1,
        prompt_payload=(project / ticket.prompt_path).read_bytes(),
        source_original_path=original,
        source_origin="locally retained historical ImageGen candidate",
        approval_basis="user approved historical-image reuse and local 4K canvas adaptation",
    )

    receipt = project / artifact["adaptation_receipt_path"]
    value = json.loads(receipt.read_text(encoding="utf-8"))
    assert value["provider"] == "local-image-adaptation"
    assert value["source_original_width"] == 1672
    assert value["source_original_height"] == 941
    assert (project / value["source_original_path"]).read_bytes() == original.read_bytes()
    with Image.open(project / artifact["path"]) as image:
        assert image.size == (3840, 2160)
    validate_user_approved_history_adaptation_evidence(
        project_root=project,
        theme_id="vivid-comic-explainer",
        scene_id="scene-02",
        candidate_index=1,
        prompt_path=artifact["generation_prompt_path"],
        prompt_sha256=artifact["generation_prompt_sha256"],
        candidate_path=artifact["path"],
        candidate_sha256=artifact["sha256"],
        artifact=artifact,
    )

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3840, 2160), (242, 235, 220)).save(output, format="PNG")
    return output.getvalue()


def test_generation_path_publishes_prompt_and_intent_before_invocation(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import run_profiled_generation_call

    theme_id = "vivid-comic-explainer"
    scene_id = "scene-01"
    prompt_payload = b"approved immutable prompt\n"
    base = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
    )
    prompt = base / "prompts" / f"{scene_id}.md"
    candidate = base / "candidates" / f"{scene_id}-candidate-01.png"
    intent = base / "generation" / f"{scene_id}-candidate-01-intent.json"
    receipt = base / "generation" / f"{scene_id}-candidate-01-receipt.json"
    observed_call_ids: list[str] = []

    def invoke(prompt_text: str, call_id: str) -> bytes:
        observed_call_ids.append(call_id)
        assert prompt_text.encode("utf-8") == prompt_payload
        assert prompt.read_bytes() == prompt_payload
        intent_value = json.loads(intent.read_text(encoding="utf-8"))
        assert intent_value["event"] == "imagegen-call-prepared"
        assert intent_value["sequence"] == 1
        assert intent_value["call_id"] == call_id
        assert intent_value["prompt_sha256"] == hashlib.sha256(
            prompt_payload
        ).hexdigest()
        assert not candidate.exists()
        assert not receipt.exists()
        return _png_bytes()

    artifact = run_profiled_generation_call(
        project_root=tmp_path,
        theme_id=theme_id,
        scene_id=scene_id,
        prompt_payload=prompt_payload,
        candidate_index=1,
        invoke=invoke,
        call_id="11111111-1111-4111-8111-111111111111",
    )

    assert observed_call_ids == ["11111111-1111-4111-8111-111111111111"]
    receipt_value = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_value["event"] == "imagegen-call-completed"
    assert receipt_value["sequence"] == 2
    assert receipt_value["call_id"] == observed_call_ids[0]
    assert receipt_value["prompt_sha256"] == hashlib.sha256(prompt_payload).hexdigest()
    assert receipt_value["candidate_sha256"] == hashlib.sha256(
        candidate.read_bytes()
    ).hexdigest()
    assert artifact == {
        "path": candidate.relative_to(tmp_path).as_posix(),
        "sha256": receipt_value["candidate_sha256"],
        "generation_prompt_path": prompt.relative_to(tmp_path).as_posix(),
        "generation_prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        "generation_intent_path": intent.relative_to(tmp_path).as_posix(),
        "generation_intent_sha256": hashlib.sha256(intent.read_bytes()).hexdigest(),
        "generation_receipt_path": receipt.relative_to(tmp_path).as_posix(),
        "generation_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
    }


def test_generation_path_fails_before_invocation_when_prompt_already_exists(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        ProfiledGenerationError,
        run_profiled_generation_call,
    )

    theme_id = "vivid-comic-explainer"
    base = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
    )
    prompt = base / "prompts" / "scene-01.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_bytes(b"existing user bytes\n")
    calls: list[str] = []

    with pytest.raises(
        ProfiledGenerationError,
        match="^profiled-generation-publication-failed$",
    ):
        run_profiled_generation_call(
            project_root=tmp_path,
            theme_id=theme_id,
            scene_id="scene-01",
            prompt_payload=b"replacement bytes\n",
            candidate_index=1,
            invoke=lambda _prompt, call_id: calls.append(call_id) or _png_bytes(),
            call_id="11111111-1111-4111-8111-111111111111",
        )

    assert calls == []
    assert prompt.read_bytes() == b"existing user bytes\n"
    assert not (base / "generation" / "scene-01-candidate-01-intent.json").exists()
    assert not (base / "candidates" / "scene-01-candidate-01.png").exists()


def test_second_candidate_uses_an_independent_no_clobber_repair_prompt(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import run_profiled_generation_call

    theme_id = "vivid-comic-explainer"
    base = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / theme_id
    )
    first_prompt = b"approved base prompt\n"
    repair_prompt = b"approved directed repair prompt\n"

    first = run_profiled_generation_call(
        project_root=tmp_path,
        theme_id=theme_id,
        scene_id="scene-01",
        prompt_payload=first_prompt,
        candidate_index=1,
        invoke=lambda _prompt, _call_id: _png_bytes(),
        call_id="11111111-1111-4111-8111-111111111111",
    )
    second = run_profiled_generation_call(
        project_root=tmp_path,
        theme_id=theme_id,
        scene_id="scene-01",
        prompt_payload=repair_prompt,
        candidate_index=2,
        invoke=lambda _prompt, _call_id: _png_bytes(),
        call_id="22222222-2222-4222-8222-222222222222",
    )

    assert first["generation_prompt_path"].endswith("/prompts/scene-01.md")
    assert second["generation_prompt_path"].endswith(
        "/prompts/scene-01-repair-02.md"
    )
    assert (base / "prompts" / "scene-01.md").read_bytes() == first_prompt
    assert (
        base / "prompts" / "scene-01-repair-02.md"
    ).read_bytes() == repair_prompt


def test_adapter_exception_is_replaced_by_a_fixed_redacted_failure(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        ProfiledGenerationError,
        run_profiled_generation_call,
    )

    def fail(_prompt: str, _call_id: str) -> bytes:
        raise RuntimeError("SENTINEL_SECRET provider diagnostics")

    with pytest.raises(ProfiledGenerationError) as caught:
        run_profiled_generation_call(
            project_root=tmp_path,
            theme_id="vivid-comic-explainer",
            scene_id="scene-01",
            prompt_payload=b"approved prompt\n",
            candidate_index=1,
            invoke=fail,
            call_id="11111111-1111-4111-8111-111111111111",
        )

    assert str(caught.value) == "profiled-generation-provider-failed"
    chain = (caught.value, caught.value.__cause__, caught.value.__context__)
    assert all("SENTINEL_SECRET" not in str(item) for item in chain if item)


def test_two_phase_production_api_places_external_tool_between_receipts(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        complete_profiled_generation_call,
        prepare_profiled_generation_call,
    )

    ticket = prepare_profiled_generation_call(
        project_root=tmp_path,
        theme_id="vivid-comic-explainer",
        scene_id="scene-01",
        prompt_payload=b"approved prompt\n",
        candidate_index=1,
        call_id="11111111-1111-4111-8111-111111111111",
    )
    assert ticket.sequence == 1
    assert (tmp_path / Path(ticket.prompt_path)).is_file()
    assert (tmp_path / Path(ticket.intent_path)).is_file()
    assert not (tmp_path / Path(ticket.candidate_path)).exists()

    artifact = complete_profiled_generation_call(
        project_root=tmp_path,
        theme_id="vivid-comic-explainer",
        scene_id="scene-01",
        candidate_index=1,
        call_id=ticket.call_id,
        intent_sha256=ticket.intent_sha256,
        candidate_payload=_png_bytes(),
    )

    assert artifact["generation_intent_sha256"] == ticket.intent_sha256
    assert (tmp_path / Path(artifact["path"])).is_file()
    assert (tmp_path / Path(artifact["generation_receipt_path"])).is_file()


def test_two_phase_runtime_native_adaptation_preserves_source_and_publishes_4k(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        complete_profiled_generation_call,
        prepare_profiled_generation_call,
        validate_profiled_generation_evidence,
    )

    source_buffer = io.BytesIO()
    Image.new("RGB", (1672, 941), (21, 42, 84)).save(source_buffer, format="PNG")
    source_payload = source_buffer.getvalue()
    ticket = prepare_profiled_generation_call(
        project_root=tmp_path,
        theme_id="vivid-comic-explainer",
        scene_id="scene-01",
        prompt_payload=b"approved prompt\n",
        candidate_index=1,
        call_id="11111111-1111-4111-8111-111111111111",
    )

    artifact = complete_profiled_generation_call(
        project_root=tmp_path,
        theme_id="vivid-comic-explainer",
        scene_id="scene-01",
        candidate_index=1,
        call_id=ticket.call_id,
        intent_sha256=ticket.intent_sha256,
        candidate_payload=source_payload,
        adapt_runtime_native=True,
    )

    original = (
        tmp_path
        / "工程"
        / "assets"
        / "profiled-illustrations"
        / "vivid-comic-explainer"
        / "originals"
        / "scene-01-candidate-01-source.png"
    )
    candidate = tmp_path / Path(artifact["path"])
    receipt = json.loads(
        (tmp_path / Path(artifact["generation_receipt_path"])).read_text(
            encoding="utf-8"
        )
    )
    assert original.read_bytes() == source_payload
    with Image.open(candidate) as image:
        assert image.format == "PNG"
        assert image.size == (3840, 2160)
    assert receipt["source_original_path"] == original.relative_to(tmp_path).as_posix()
    assert receipt["source_original_sha256"] == hashlib.sha256(source_payload).hexdigest()
    assert receipt["source_original_width"] == 1672
    assert receipt["source_original_height"] == 941
    assert receipt["transform"] == "ImageOps.fit RGB 3840x2160 LANCZOS"
    validate_profiled_generation_evidence(
        project_root=tmp_path,
        theme_id="vivid-comic-explainer",
        scene_id="scene-01",
        candidate_index=1,
        prompt_path=artifact["generation_prompt_path"],
        prompt_sha256=artifact["generation_prompt_sha256"],
        candidate_path=artifact["path"],
        candidate_sha256=artifact["sha256"],
        artifact=artifact,
    )


def test_runtime_native_adaptation_rejects_sub_720p_or_non_widescreen_source(
    tmp_path: Path,
) -> None:
    from boomearth.video.profiled_generation import (
        ProfiledGenerationError,
        complete_profiled_generation_call,
        prepare_profiled_generation_call,
    )

    for index, size in enumerate(((1024, 576), (1280, 960)), start=1):
        source_buffer = io.BytesIO()
        Image.new("RGB", size, (21, 42, 84)).save(source_buffer, format="PNG")
        scene_id = f"scene-{index:02d}"
        ticket = prepare_profiled_generation_call(
            project_root=tmp_path,
            theme_id="vivid-comic-explainer",
            scene_id=scene_id,
            prompt_payload=f"approved prompt {index}\n".encode(),
            candidate_index=1,
            call_id=f"{index:08d}-1111-4111-8111-111111111111",
        )

        with pytest.raises(
            ProfiledGenerationError,
            match="^profiled-generation-result-invalid$",
        ):
            complete_profiled_generation_call(
                project_root=tmp_path,
                theme_id="vivid-comic-explainer",
                scene_id=scene_id,
                candidate_index=1,
                call_id=ticket.call_id,
                intent_sha256=ticket.intent_sha256,
                candidate_payload=source_buffer.getvalue(),
                adapt_runtime_native=True,
            )

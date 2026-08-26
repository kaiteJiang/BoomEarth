"""Offline contract tests for the guarded joint live-smoke runner."""

from __future__ import annotations

import functools
import json
import importlib.util
import os
import subprocess
import sys
from datetime import UTC, datetime
from types import ModuleType
from pathlib import Path

import pytest

from boomearth.config import MissingSettingsError

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_TRANSCRIPT = "synthetic private transcript"
SYNTHETIC_SECRET = "synthetic-secret-value"


def _load_live_smokes_module() -> ModuleType:
    path = ROOT / "automation" / "scripts" / "run_live_smokes.py"
    spec = importlib.util.spec_from_file_location("boomearth_live_smokes_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_live_smokes = _load_live_smokes_module()


def _settings_loader(root: Path) -> object:
    assert root.is_dir()
    return type("SafeSettings", (), {"redacted_status": lambda self: {
        "TIKHUB_API_KEY": "SET",
        "DASHSCOPE_API_KEY": "SET",
        "VOLCENGINE_API_KEY": "SET",
    }})()


def _locked_probe(audio: Path, manifest: Path) -> object:
    assert manifest == audio.with_name("voice_manifest.json")
    return object()


def _external_audio(tmp_path: Path) -> Path:
    audio = tmp_path / "approved-external" / "source.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"synthetic-audio")
    return audio


def _isolate_selected_service_environment(
    monkeypatch: pytest.MonkeyPatch,
    values: dict[str, str],
) -> None:
    for name in (
        "TIKHUB_API_KEY",
        "DASHSCOPE_API_KEY",
        "VOLCENGINE_API_KEY",
        "TIKHUB_API_BASE",
        "PARAFORMER_MODEL",
        "VOLCENGINE_RESOURCE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    "argv",
    [
        ["plan"],
        ["plan", "--services", "unknown"],
        ["plan", "--services", "tikhub", "tikhub"],
    ],
)
def test_parser_rejects_missing_unknown_and_duplicate_services(argv: list[str]) -> None:
    """Would fail if service selection became implicit or ambiguous."""

    with pytest.raises(SystemExit) as raised:
        run_live_smokes.main(argv)

    assert raised.value.code == 2


def test_plan_is_local_and_never_calls_runner(tmp_path: Path) -> None:
    """Would fail if planning constructed a runner or contacted a provider."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    calls: list[object] = []

    def forbidden_runner(argv: list[str]) -> run_live_smokes.SmokeInvocationResult:
        calls.append(argv)
        raise AssertionError("plan must not call a smoke runner")

    report = run_live_smokes.build_plan(
        root=root,
        services=("paraformer", "tikhub"),
        paraformer_audio=audio,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        smoke_runner=forbidden_runner,
    )

    assert report.command == "plan"
    assert report.services == ("tikhub", "paraformer")
    assert report.preflight.outcome == "READY"
    assert report.network_attempted is False
    assert calls == []


def test_run_requires_network_authorization_before_runner_creation(tmp_path: Path) -> None:
    """Would fail if an unapproved network smoke could reach the runner."""

    root = tmp_path / "workspace"
    root.mkdir()
    calls: list[object] = []

    report = run_live_smokes.execute_run(
        root=root,
        services=("tikhub",),
        authorized_network=False,
        authorized_audio_upload=False,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        smoke_runner=lambda argv: calls.append(argv),  # type: ignore[arg-type]
    )

    assert report.preflight.status == "AUTHORIZATION_REQUIRED"
    assert report.service_results == ()
    assert calls == []

def test_unauthorized_run_performs_no_path_identity_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Would fail if authorization denial touched supplied audio paths."""
    root = tmp_path / "workspace"; root.mkdir()
    monkeypatch.setattr(run_live_smokes, "_identity", lambda path: (_ for _ in ()).throw(AssertionError("no identity")))
    report = run_live_smokes.execute_run(root=root, services=("tikhub",), paraformer_audio=Path("C:/bad"), volcengine_audio=Path("C:/also-bad"), authorized_network=False, authorized_audio_upload=False)
    assert report.preflight.status == "AUTHORIZATION_REQUIRED"


def test_authorized_volcengine_anchor_path_writes_input_error_report_without_runner(
    tmp_path: Path,
) -> None:
    """Would fail if a selected anchor path derived a manifest before safe input preflight."""

    root = tmp_path / "workspace"
    root.mkdir()
    factory_calls: list[Path] = []
    runner_calls: list[list[str]] = []
    now = lambda: datetime(2026, 8, 14, tzinfo=UTC)
    report_id = lambda: "anchor-path"

    def runner(argv: list[str]) -> run_live_smokes.SmokeInvocationResult:
        runner_calls.append(argv)
        return run_live_smokes.SmokeInvocationResult("OK")

    def factory(bound_root: Path) -> object:
        factory_calls.append(bound_root)
        return runner

    report = run_live_smokes.execute_run(
        root=root,
        services=("volcengine",),
        volcengine_audio=Path(root.anchor),
        authorized_network=True,
        authorized_audio_upload=True,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        runner_factory=factory,  # type: ignore[arg-type]
        now_factory=now,
        uuid_factory=report_id,
    )

    expected_report = run_live_smokes.report_path(
        root,
        now_factory=now,
        uuid_factory=report_id,
    )
    assert report.preflight.outcome == "FAIL"
    assert report.preflight.status == "INPUT_ERROR"
    assert report.service_results == ()
    assert factory_calls == []
    assert runner_calls == []
    assert json.loads(expected_report.read_text(encoding="utf-8"))["preflight"]["status"] == "INPUT_ERROR"


def test_cli_authorized_volcengine_anchor_path_emits_json_input_error(tmp_path: Path) -> None:
    """Would fail if the real CLI printed a traceback instead of its safe local report."""

    root = tmp_path / "workspace"
    root.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "automation" / "scripts" / "run_live_smokes.py"),
            "run",
            "--services",
            "volcengine",
            "--volcengine-audio",
            str(Path(root.anchor)),
            "--workspace-root",
            str(root),
            "--authorized-network",
            "--authorized-audio-upload",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["preflight"]["status"] == "INPUT_ERROR"
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("service", ["paraformer", "volcengine"])
def test_preflight_callback_replacement_blocks_runner(service: str, tmp_path: Path) -> None:
    """Would fail if a settings callback could replace selected local input after baseline."""
    root = tmp_path / "workspace"; root.mkdir(); audio = _external_audio(tmp_path)
    manifest = audio.with_name("voice_manifest.json"); manifest.write_text("synthetic", encoding="utf-8")
    calls: list[object] = []
    def replace_settings(_: Path) -> object:
        target = manifest if service == "volcengine" else audio
        target.unlink(); target.write_bytes(b"replacement")
        return _settings_loader(root)
    report = run_live_smokes.execute_run(root=root, services=(service,), paraformer_audio=audio, volcengine_audio=audio, authorized_network=True, authorized_audio_upload=True, settings_loader=replace_settings, locked_wav_probe=_locked_probe, runner_factory=lambda root: calls.append(root))
    assert report.preflight.outcome == "FAIL"
    assert calls == []

def test_runner_factory_replacement_blocks_paraformer_runner(tmp_path: Path) -> None:
    """Would fail if factory-time replacement crossed the final identity gate."""

    root = tmp_path / "workspace"; root.mkdir(); audio = _external_audio(tmp_path)
    factory_calls: list[Path] = []
    returned_runner_calls: list[list[str]] = []

    def returned_runner(argv: list[str]) -> run_live_smokes.SmokeInvocationResult:
        returned_runner_calls.append(argv)
        return run_live_smokes.SmokeInvocationResult("OK")

    def factory(_: Path):
        factory_calls.append(_)
        audio.unlink(); audio.write_bytes(b"replacement")
        return returned_runner

    report = run_live_smokes.execute_run(root=root, services=("paraformer",), paraformer_audio=audio, authorized_network=True, authorized_audio_upload=True, settings_loader=_settings_loader, locked_wav_probe=_locked_probe, runner_factory=factory)
    assert report.service_results[0].status == "INPUT_ERROR"
    assert factory_calls == [root]
    assert returned_runner_calls == []

def test_empty_preflight_mappings_are_immutable(tmp_path: Path) -> None:
    """Would fail if authorization or local failure retained a mutable empty dict."""
    root = tmp_path / "workspace"; root.mkdir()
    denied = run_live_smokes.execute_run(root=root, services=("tikhub",), authorized_network=False, authorized_audio_upload=False)
    local = run_live_smokes.build_plan(root=root, services=("paraformer",))
    for report in (denied, local):
        with pytest.raises(TypeError): report.preflight.credential_status["x"] = "SET"  # type: ignore[index]

@pytest.mark.parametrize(
    ("service", "credential", "nonsecret"),
    [
        ("tikhub", "TIKHUB_API_KEY", "TIKHUB_API_BASE"),
        ("paraformer", "DASHSCOPE_API_KEY", "PARAFORMER_MODEL"),
        ("volcengine", "VOLCENGINE_API_KEY", "VOLCENGINE_RESOURCE_ID"),
    ],
)
def test_selected_plan_nonsecret_defaults_and_blank_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    service: str,
    credential: str,
    nonsecret: str,
) -> None:
    """Would fail if an omitted selected default stopped working or a blank selected value passed."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path) if service != "tikhub" else None
    if service == "volcengine":
        assert audio is not None
        audio.with_name("voice_manifest.json").write_text("synthetic", encoding="utf-8")
    inputs = {
        "paraformer_audio": audio if service == "paraformer" else None,
        "volcengine_audio": audio if service == "volcengine" else None,
    }

    selected_values = {credential: "synthetic"}
    _isolate_selected_service_environment(monkeypatch, selected_values)
    ready = run_live_smokes.build_plan(
        root=root,
        services=(service,),
        locked_wav_probe=_locked_probe,
        **inputs,
    )
    assert ready.preflight.status == "READY"

    for blank in ("", "   "):
        _isolate_selected_service_environment(
            monkeypatch,
            {**selected_values, nonsecret: blank},
        )
        rejected = run_live_smokes.build_plan(
            root=root,
            services=(service,),
            locked_wav_probe=_locked_probe,
            **inputs,
        )
        assert rejected.preflight.status == "CONFIG_ERROR"


def test_blank_unselected_nonsecret_values_do_not_block_selected_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if an unselected service's blank local setting blocked TikHub planning."""

    root = tmp_path / "workspace"
    root.mkdir()
    _isolate_selected_service_environment(
        monkeypatch,
        {
            "TIKHUB_API_KEY": "synthetic",
            "PARAFORMER_MODEL": " ",
            "VOLCENGINE_RESOURCE_ID": "",
        },
    )

    report = run_live_smokes.build_plan(root=root, services=("tikhub",))

    assert report.preflight.status == "READY"
    assert report.preflight.credential_status == {"TIKHUB_API_KEY": "SET"}


@pytest.mark.parametrize(
    ("command", "credential", "nonsecret"),
    [
        ("tikhub-account", "TIKHUB_API_KEY", "TIKHUB_API_BASE"),
        ("paraformer", "DASHSCOPE_API_KEY", "PARAFORMER_MODEL"),
        ("volcengine", "VOLCENGINE_API_KEY", "VOLCENGINE_RESOURCE_ID"),
    ],
)
def test_selected_api_settings_rejects_blank_selected_nonsecret_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
    credential: str,
    nonsecret: str,
) -> None:
    """Would fail if a preflight-to-run config change made a blank setting reach a provider."""

    root = tmp_path / "workspace"
    root.mkdir()
    for blank in ("", "   "):
        _isolate_selected_service_environment(
            monkeypatch,
            {credential: "synthetic", nonsecret: blank},
        )

        with pytest.raises(MissingSettingsError):
            run_live_smokes._selected_api_settings(root, command)


def test_selected_only_plan_and_real_api_smoke_reach_fake_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Would fail if the default adapter stopped invoking the dynamically loaded real main."""

    root = tmp_path / "workspace"
    root.mkdir()
    _isolate_selected_service_environment(monkeypatch, {"TIKHUB_API_KEY": "synthetic"})
    assert run_live_smokes.build_plan(root=root, services=("tikhub",)).preflight.status == "READY"
    api_path = ROOT / "automation" / "scripts" / "api_smoke.py"; spec = importlib.util.spec_from_file_location("real_api_smoke_selected_test", api_path); assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    calls: list[object] = []
    class FakeClient:
        last_http_status = 200
        def __init__(self, settings: object): calls.append(settings)
        def check_account(self): return type("Status", (), {"active": True, "quota_present": True})()
        def close(self): pass
    real_main = functools.partial(module.main, client_factory=FakeClient)
    result = run_live_smokes.make_default_smoke_runner(root, api_smoke_loader=lambda: real_main)(["tikhub-account"])
    assert result.status == "OK"
    assert calls

@pytest.mark.parametrize("entry", ["TIKHUB_API_KEY", "TIKHUB_API_KEY=", "TIKHUB_API_KEY=   "])
def test_selected_settings_treats_bare_empty_and_blank_dotenv_as_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, entry: str) -> None:
    """Would fail if dotenv None or whitespace became a credential presence signal."""
    monkeypatch.delenv("TIKHUB_API_KEY", raising=False)
    root = tmp_path / "workspace"; root.mkdir(); (root / ".env").write_text(entry, encoding="utf-8")
    report = run_live_smokes.build_plan(root=root, services=("tikhub",))
    assert report.preflight.credential_status == {"TIKHUB_API_KEY": "UNSET"}
    assert report.preflight.status == "CONFIG_ERROR"


def test_volcengine_plan_is_ready_with_turbo_contract_and_no_remote_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if a valid turbo plan still required retired remote-audio configuration."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    audio.with_name("voice_manifest.json").write_text("synthetic", encoding="utf-8")
    _isolate_selected_service_environment(
        monkeypatch,
        {
            "VOLCENGINE_API_KEY": "synthetic",
            "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
        },
    )

    report = run_live_smokes.build_plan(
        root=root,
        services=("volcengine",),
        volcengine_audio=audio,
        locked_wav_probe=_locked_probe,
    )

    assert report.preflight.status == "READY"
    assert report.preflight.credential_status == {"VOLCENGINE_API_KEY": "SET"}
    assert report.network_attempted is False
    assert report.service_results == ()


def test_volcengine_plan_rejects_non_turbo_resource_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if a non-turbo resource could create a live smoke runner."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    audio.with_name("voice_manifest.json").write_text("synthetic", encoding="utf-8")
    _isolate_selected_service_environment(
        monkeypatch,
        {
            "VOLCENGINE_API_KEY": "synthetic",
            "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc",
        },
    )
    factory_calls: list[Path] = []

    report = run_live_smokes.execute_run(
        root=root,
        services=("volcengine",),
        volcengine_audio=audio,
        authorized_network=True,
        authorized_audio_upload=True,
        locked_wav_probe=_locked_probe,
        runner_factory=lambda bound_root: factory_calls.append(bound_root),
    )

    assert report.preflight.status == "CONFIG_ERROR"
    assert report.network_attempted is False
    assert factory_calls == []


def test_non_volcengine_selected_plans_remain_ready_without_retired_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if removed Volcengine configuration globally blocked other services."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    _isolate_selected_service_environment(
        monkeypatch,
        {"TIKHUB_API_KEY": "synthetic", "DASHSCOPE_API_KEY": "synthetic"},
    )

    report = run_live_smokes.build_plan(
        root=root,
        services=("tikhub", "paraformer"),
        paraformer_audio=audio,
    )

    assert report.preflight.status == "READY"
    assert report.preflight.credential_status == {
        "TIKHUB_API_KEY": "SET",
        "DASHSCOPE_API_KEY": "SET",
    }


def test_default_volcengine_smoke_passes_only_turbo_settings_to_selected_api_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if the default smoke injected retired settings or a non-turbo resource."""

    root = tmp_path / "workspace"
    root.mkdir()
    _isolate_selected_service_environment(
        monkeypatch,
        {
            "VOLCENGINE_API_KEY": "synthetic",
            "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
        },
    )
    received: list[object] = []

    def fake_main(argv: list[str], *, root: Path, settings_loader: object) -> int:
        settings = settings_loader(root)  # type: ignore[operator]
        received.append(settings)
        print("status=CONFIG_ERROR")
        print("request_id=UNAVAILABLE")
        print("timing_granularity=UNAVAILABLE")
        print("duration=UNAVAILABLE")
        print("token_count=UNAVAILABLE")
        return 2

    result = run_live_smokes.make_default_smoke_runner(
        root,
        api_smoke_loader=lambda: fake_main,
    )(["volcengine", "--audio", "ignored"])

    assert result.status == "CONFIG_ERROR"
    assert len(received) == 1
    assert getattr(received[0], "volcengine_resource_id") == "volc.bigasr.auc_turbo"
    assert not hasattr(received[0], "volcengine_" + "audio_url")
    assert "synthetic" not in repr(received[0])


def test_run_requires_audio_upload_authorization_before_runner_creation(
    tmp_path: Path,
) -> None:
    """Would fail if audio could be submitted without its distinct approval."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    calls: list[object] = []

    report = run_live_smokes.execute_run(
        root=root,
        services=("paraformer",),
        paraformer_audio=audio,
        authorized_network=True,
        authorized_audio_upload=False,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        smoke_runner=lambda argv: calls.append(argv),  # type: ignore[arg-type]
    )

    assert report.preflight.status == "AUDIO_UPLOAD_AUTHORIZATION_REQUIRED"
    assert report.service_results == ()
    assert calls == []


def test_audio_services_require_explicit_safe_inputs(tmp_path: Path) -> None:
    """Would fail if an audio service ran without its required local proof."""

    root = tmp_path / "workspace"
    root.mkdir()

    paraformer = run_live_smokes.build_plan(
        root=root,
        services=("paraformer",),
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
    )
    volcengine = run_live_smokes.build_plan(
        root=root,
        services=("volcengine",),
        volcengine_audio=_external_audio(tmp_path),
        settings_loader=_settings_loader,
        locked_wav_probe=lambda audio, manifest: (_ for _ in ()).throw(
            ValueError("missing manifest")
        ),
    )

    assert paraformer.preflight.status == "INPUT_ERROR"
    assert volcengine.preflight.status == "LOCAL_CONTRACT_ERROR"


def test_external_absolute_audio_requires_safe_regular_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Would fail if traversal, unsafe ancestors, or non-files reached a runner."""

    root = tmp_path / "workspace"
    root.mkdir()
    approved = _external_audio(tmp_path)

    allowed = run_live_smokes.build_plan(
        root=root,
        services=("paraformer",),
        paraformer_audio=approved,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
    )
    relative = run_live_smokes.build_plan(
        root=root,
        services=("paraformer",),
        paraformer_audio=Path("..") / "private.wav",
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
    )
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    non_regular = run_live_smokes.build_plan(
        root=root,
        services=("paraformer",),
        paraformer_audio=directory,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
    )
    monkeypatch.setattr(
        run_live_smokes,
        "_is_unsafe_path_component",
        lambda path: path == approved.parent,
    )
    unsafe_ancestor = run_live_smokes.build_plan(
        root=root,
        services=("paraformer",),
        paraformer_audio=approved,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
    )

    assert allowed.preflight.outcome == "READY"
    assert relative.preflight.status == "INPUT_ERROR"
    assert non_regular.preflight.status == "INPUT_ERROR"
    assert unsafe_ancestor.preflight.status == "INPUT_ERROR"


def test_run_uses_canonical_order_and_skips_after_first_failure(tmp_path: Path) -> None:
    """Would fail if caller order won or a failure permitted later requests."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> run_live_smokes.SmokeInvocationResult:
        calls.append(argv)
        return run_live_smokes.SmokeInvocationResult(
            status="SERVICE_ERROR",
            request_id="safe-request-id",
        )

    report = run_live_smokes.execute_run(
        root=root,
        services=("volcengine", "paraformer", "tikhub"),
        paraformer_audio=audio,
        volcengine_audio=audio,
        authorized_network=True,
        authorized_audio_upload=True,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        smoke_runner=runner,
        now_factory=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        uuid_factory=lambda: "safe-report-id",
    )

    assert calls == [["tikhub-account"]]
    assert [(item.service, item.outcome) for item in report.service_results] == [
        ("tikhub", "FAIL"),
        ("paraformer", "SKIPPED"),
        ("volcengine", "SKIPPED"),
    ]
    assert report.fail_closed_early is True


def test_report_uses_fixed_schema_and_redacts_sensitive_inputs(tmp_path: Path) -> None:
    """Would fail if report data could contain private source or runner output."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    report = run_live_smokes.execute_run(
        root=root,
        services=("paraformer",),
        paraformer_audio=audio,
        authorized_network=True,
        authorized_audio_upload=True,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        smoke_runner=lambda argv: run_live_smokes.SmokeInvocationResult(
            status="OK",
            request_id="safe-request-id",
            network_attempted=True,
        ),
        now_factory=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        uuid_factory=lambda: "safe-report-id",
    )

    assert report.network_attempted is True
    files = list((root / "runtime" / "live-smokes").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema",
        "created_at_utc",
        "report_id",
        "command",
        "services",
        "preflight",
            "service_results",
            "network_attempted",
        "fail_closed_early",
    }
    assert set(payload["preflight"]) == {"outcome", "status", "credential_status"}
    assert set(payload["service_results"][0]) == {
        "service", "outcome", "status", "request_id", "network_attempted",
    }
    encoded = json.dumps(payload, sort_keys=True)
    assert PRIVATE_TRANSCRIPT not in encoded
    assert SYNTHETIC_SECRET not in encoded
    assert str(audio) not in encoded
    assert "sha256" not in encoded.lower()


def test_predicted_report_collision_preserves_file_and_blocks_runner(tmp_path: Path) -> None:
    """Would fail if a generated report could overwrite data or still run a smoke."""

    root = tmp_path / "workspace"
    root.mkdir()
    now = lambda: datetime(2026, 8, 12, tzinfo=UTC)
    uuid_factory = lambda: "safe-report-id"
    predicted = run_live_smokes.report_path(root, now_factory=now, uuid_factory=uuid_factory)
    predicted.parent.mkdir(parents=True)
    predicted.write_text("preserve-this", encoding="utf-8")
    calls: list[object] = []

    report = run_live_smokes.execute_run(
        root=root,
        services=("tikhub",),
        authorized_network=True,
        authorized_audio_upload=False,
        settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        smoke_runner=lambda argv: calls.append(argv),  # type: ignore[arg-type]
        now_factory=now,
        uuid_factory=uuid_factory,
    )

    assert report.preflight.status == "REPORT_ERROR"
    assert calls == []
    assert predicted.read_text(encoding="utf-8") == "preserve-this"


def test_live_smoke_reports_stay_ignored_without_ignore_rule_changes() -> None:
    """Would fail if generated live-smoke reports became tracked artifacts."""

    result = subprocess.run(
        ["git", "check-ignore", "runtime/live-smokes/example.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0


def test_default_runner_factory_is_not_constructed_before_all_local_gates(tmp_path: Path) -> None:
    """Would fail if runner construction or module loading preceded authorization or reservation."""

    root = tmp_path / "workspace"
    root.mkdir()
    calls: list[Path] = []
    factory = lambda bound_root: calls.append(bound_root) or (lambda argv: None)

    denied = run_live_smokes.execute_run(
        root=root, services=("tikhub",), authorized_network=False,
        authorized_audio_upload=False, settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe, runner_factory=factory,
    )
    assert denied.preflight.status == "AUTHORIZATION_REQUIRED"
    assert calls == []

    ready = run_live_smokes.execute_run(
        root=root, services=("tikhub",), authorized_network=True,
        authorized_audio_upload=False, settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe, runner_factory=factory,
        now_factory=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        uuid_factory=lambda: "factory-seam",
    )
    assert calls == [root]
    assert ready.service_results[0].status == "INTERNAL_ERROR"


def test_default_runner_captures_only_strict_safe_service_schema(
    capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """Would fail if legacy smoke output leaked or malformed output was accepted."""

    private = "private stdout and stderr"

    def fake_main(argv: list[str], *, root: Path) -> int:
        print("status=NETWORK_ERROR")
        print("http_status=UNAVAILABLE")
        print("account_active=false")
        print("quota_present=false")
        print(private, file=sys.stderr)
        return 1

    runner = run_live_smokes.make_default_smoke_runner(
        tmp_path, api_smoke_loader=lambda: fake_main,
    )
    result = runner(["tikhub-account"])
    terminal = capsys.readouterr()

    assert result.status == "INTERNAL_ERROR"
    assert result.request_id == "UNAVAILABLE"
    assert result.network_attempted is False
    assert private not in terminal.out + terminal.err


@pytest.mark.parametrize(
    ("argv", "lines", "expected_status", "expected_request", "attempted"),
    [
        (
            ["paraformer", "--audio", "ignored", "--authorized"],
            ["status=TIMEOUT", "duration=UNAVAILABLE", "segments=UNAVAILABLE", "request_id=safe-id"],
            "TIMEOUT", "safe-id", True,
        ),
        (
            ["volcengine", "--audio", "ignored"],
            ["status=CONFIG_ERROR", "request_id=UNAVAILABLE", "timing_granularity=UNAVAILABLE", "duration=UNAVAILABLE", "token_count=UNAVAILABLE"],
            "CONFIG_ERROR", "UNAVAILABLE", False,
        ),
    ],
)
def test_default_runner_strictly_parses_safe_service_output(
    tmp_path: Path, argv: list[str], lines: list[str], expected_status: str,
    expected_request: str, attempted: bool,
) -> None:
    """Would fail if safe api_smoke fields were discarded or attempt state was guessed."""

    def fake_main(argv: list[str], *, root: Path) -> int:
        print("\n".join(lines))
        return {"OK": 0, "CONFIG_ERROR": 2}.get(expected_status, 1)

    result = run_live_smokes.make_default_smoke_runner(
        tmp_path, api_smoke_loader=lambda: fake_main,
    )(argv)
    assert (result.status, result.request_id, result.network_attempted) == (
        expected_status, expected_request, attempted,
    )


def test_plan_records_only_selected_redacted_credential_states(tmp_path: Path) -> None:
    """Would fail if preflight omitted or widened credential diagnostics."""

    root = tmp_path / "workspace"
    root.mkdir()
    settings = type("Settings", (), {"redacted_status": lambda self: {
        "TIKHUB_API_KEY": "SET", "DASHSCOPE_API_KEY": "UNSET", "VOLCENGINE_API_KEY": "SET",
    }})()
    report = run_live_smokes.build_plan(
        root=root, services=("paraformer", "tikhub"), paraformer_audio=_external_audio(tmp_path),
        settings_loader=lambda root: settings, locked_wav_probe=_locked_probe,
    )
    assert report.preflight.credential_status == {
        "TIKHUB_API_KEY": "SET", "DASHSCOPE_API_KEY": "UNSET",
    }
    assert set(report.preflight.payload()) == {"outcome", "status", "credential_status"}


def test_each_audio_service_is_revalidated_immediately_before_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Would fail if audio changed after plan preflight yet still reached the runner."""

    root = tmp_path / "workspace"
    root.mkdir()
    audio = _external_audio(tmp_path)
    checks = iter((True, False))
    monkeypatch.setattr(run_live_smokes, "_safe_absolute_audio_path", lambda path: next(checks))
    calls: list[object] = []
    report = run_live_smokes.execute_run(
        root=root, services=("paraformer",), paraformer_audio=audio,
        authorized_network=True, authorized_audio_upload=True,
        settings_loader=_settings_loader, locked_wav_probe=_locked_probe,
        smoke_runner=lambda argv: calls.append(argv),  # type: ignore[arg-type]
    )
    assert report.service_results[0].status == "INPUT_ERROR"
    assert report.service_results[0].network_attempted is False
    assert calls == []


def test_report_failure_preserves_known_results_and_closes_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Would fail if a local final-write error erased audit state or leaked its handle."""

    root = tmp_path / "workspace"
    root.mkdir()
    closed: list[bool] = []

    class BrokenHandle:
        def write(self, text: str) -> int:
            raise RuntimeError("private write failure")

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(run_live_smokes, "_reserve_report", lambda root, report: (root / "x", BrokenHandle()))
    report = run_live_smokes.execute_run(
        root=root, services=("tikhub",), authorized_network=True,
        authorized_audio_upload=False, settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe,
        smoke_runner=lambda argv: run_live_smokes.SmokeInvocationResult("OK", "safe-id", True),
    )
    assert report.preflight.status == "REPORT_ERROR"
    assert [(item.outcome, item.network_attempted) for item in report.service_results] == [("PASS", True)]
    assert report.network_attempted is True
    assert closed == [True]


def test_main_uses_distinct_success_service_and_local_failure_exit_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Would fail if a failed selected service exited as a successful plan."""

    root = tmp_path / "workspace"
    root.mkdir()
    ready = run_live_smokes.RunReport(
        "2026-08-13T00:00:00Z", "id", "plan", ("tikhub",),
        run_live_smokes.PreflightResult("READY", "READY", {"TIKHUB_API_KEY": "SET"}), (), False, False,
    )
    failed = run_live_smokes.RunReport(
        "2026-08-13T00:00:00Z", "id", "run", ("tikhub",),
        run_live_smokes.PreflightResult("READY", "READY", {"TIKHUB_API_KEY": "SET"}),
        (run_live_smokes.ServiceResult("tikhub", "FAIL", "SERVICE_ERROR", "safe-id", True),), True, False,
    )
    monkeypatch.setattr(run_live_smokes, "build_plan", lambda **kwargs: ready)
    monkeypatch.setattr(run_live_smokes, "_persist_report", lambda root, report: True)
    assert run_live_smokes.main(["plan", "--services", "tikhub", "--workspace-root", str(root)]) == 0
    monkeypatch.setattr(run_live_smokes, "execute_run", lambda **kwargs: failed)
    assert run_live_smokes.main(["run", "--services", "tikhub", "--workspace-root", str(root), "--authorized-network"]) == 1


@pytest.mark.parametrize(
    "lines",
    [
        ["status=OK", "status=OK", "http_status=200", "account_active=true"],
        ["status=OK", "http_status=200", "account_active=true"],
        ["status=OK", "http_status=200", "account_active=true", "unknown=value"],
        ["status=OK", "http_status=200", "account_active=true", "quota_present=false", "x" * 5000],
    ],
)
def test_default_runner_rejects_duplicate_missing_unknown_and_oversized_output(
    tmp_path: Path, lines: list[str],
) -> None:
    """Would fail if a non-exact or unbounded legacy response became a safe result."""

    def fake_main(argv: list[str], *, root: Path) -> int:
        print("\n".join(lines))
        return 0

    result = run_live_smokes.make_default_smoke_runner(
        tmp_path, api_smoke_loader=lambda: fake_main,
    )(["tikhub-account"])
    assert result == run_live_smokes.SmokeInvocationResult("INTERNAL_ERROR")


def test_unsafe_report_root_blocks_runner_before_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Would fail if a reparse root or report ancestor allowed a runner invocation."""

    root = tmp_path / "workspace"
    root.mkdir()
    calls: list[object] = []
    monkeypatch.setattr(run_live_smokes, "_is_unsafe_path_component", lambda path: path == root)
    report = run_live_smokes.execute_run(
        root=root, services=("tikhub",), authorized_network=True,
        authorized_audio_upload=False, settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe, runner_factory=lambda root: calls.append(root),  # type: ignore[arg-type]
    )
    assert report.preflight.status == "REPORT_ERROR"
    assert calls == []


def test_real_workspace_symlink_is_rejected_when_platform_allows_links(tmp_path: Path) -> None:
    """Would fail if a real workspace symlink bypassed report-parent hardening."""

    target = tmp_path / "real-workspace"
    target.mkdir()
    linked_root = tmp_path / "linked-workspace"
    try:
        os.symlink(target, linked_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable or not permitted")

    calls: list[object] = []
    report = run_live_smokes.execute_run(
        root=linked_root, services=("tikhub",), authorized_network=True,
        authorized_audio_upload=False, settings_loader=_settings_loader,
        locked_wav_probe=_locked_probe, runner_factory=lambda root: calls.append(root),  # type: ignore[arg-type]
    )
    assert report.preflight.status == "REPORT_ERROR"
    assert calls == []


@pytest.mark.parametrize("status, code", [("OK", 1), ("SERVICE_ERROR", 0)])
def test_default_runner_rejects_return_code_status_contradictions(tmp_path: Path, status: str, code: int) -> None:
    """Would fail if api_smoke's exit result could contradict its safe status."""
    def fake_main(argv: list[str], *, root: Path) -> int:
        print(f"status={status}\nhttp_status=200\naccount_active=true\nquota_present=true")
        return code
    assert run_live_smokes.make_default_smoke_runner(tmp_path, api_smoke_loader=lambda: fake_main)(["tikhub-account"]).status == "INTERNAL_ERROR"


def test_preflight_credentials_are_immutable_and_selected_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Would fail if unselected credentials blocked plan or mutable state escaped preflight."""
    monkeypatch.setenv("TIKHUB_API_KEY", "synthetic")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    root = tmp_path / "workspace"; root.mkdir()
    report = run_live_smokes.build_plan(root=root, services=("tikhub",))
    assert report.preflight.status == "READY"
    with pytest.raises(TypeError): report.preflight.credential_status["TIKHUB_API_KEY"] = "UNSET"  # type: ignore[index]
    missing = run_live_smokes.build_plan(root=root, services=("paraformer",), paraformer_audio=_external_audio(tmp_path))
    assert missing.preflight.credential_status == {"DASHSCOPE_API_KEY": "UNSET"}
    assert missing.preflight.status == "CONFIG_ERROR"


@pytest.mark.parametrize("status, code", [("OK", 0), ("LOCAL_CONTRACT_ERROR", 1), ("NETWORK_ERROR", 1), ("SERVICE_ERROR", 1), ("TIMEOUT", 1), ("INTERNAL_ERROR", 1), ("AUTHORIZATION_REQUIRED", 2), ("CONFIG_ERROR", 2), ("INPUT_ERROR", 2)])
def test_default_runner_accepts_exact_return_code_status_matrix(tmp_path: Path, status: str, code: int) -> None:
    """Would fail if api smoke's three exit classes were not exact."""
    def fake_main(argv: list[str], *, root: Path) -> int:
        print(f"status={status}\nhttp_status=200\naccount_active=true\nquota_present=true")
        return code
    assert run_live_smokes.make_default_smoke_runner(tmp_path, api_smoke_loader=lambda: fake_main)(["tikhub-account"]).status == status

@pytest.mark.parametrize("status, code", [("SERVICE_ERROR", 2), ("CONFIG_ERROR", 1), ("OK", True), ("TIMEOUT", -1), ("OK", 3)])
def test_default_runner_rejects_noncanonical_return_code_status_matrix(tmp_path: Path, status: str, code: object) -> None:
    """Would fail if boolean or noncanonical API smoke exits were accepted."""
    def fake_main(argv: list[str], *, root: Path) -> object:
        print(f"status={status}\nhttp_status=200\naccount_active=true\nquota_present=true")
        return code
    assert run_live_smokes.make_default_smoke_runner(tmp_path, api_smoke_loader=lambda: fake_main)(["tikhub-account"]).status == "INTERNAL_ERROR"

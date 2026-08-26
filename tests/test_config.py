import importlib.util
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from dotenv import dotenv_values

from boomearth.config import DEFAULTS, REQUIRED_VARIABLES, MissingSettingsError, Settings


ENV_NAMES = (
    "TIKHUB_API_KEY",
    "DASHSCOPE_API_KEY",
    "VOLCENGINE_API_KEY",
    "TIKHUB_API_BASE",
    "PARAFORMER_MODEL",
    "VOLCENGINE_RESOURCE_ID",
    "INDEXTTS2_ROOT",
    "INDEXTTS2_PYTHON",
    "INDEXTTS2_REFERENCE_AUDIO",
    "YT_DLP_PATH",
    "VOICE_PLAYBACK_SPEED",
)

EXPECTED_DEFAULTS = {
    "TIKHUB_API_BASE": "https://api.tikhub.dev",
    "PARAFORMER_MODEL": "paraformer-realtime-v2",
    "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
    "INDEXTTS2_ROOT": r"C:\BoomEarthLocal\IndexTTS2",
    "INDEXTTS2_PYTHON": r"C:\BoomEarthLocal\IndexTTS2\.venv\Scripts\python.exe",
    "INDEXTTS2_REFERENCE_AUDIO": r"C:\BoomEarthLocal\voice\reference.wav",
    "YT_DLP_PATH": r"C:\BoomEarth\yt-dlp.exe",
    "VOICE_PLAYBACK_SPEED": "1.12",
}

FAKE_SECRETS = {
    "TIKHUB_API_KEY": "fake-tikhub-secret",
    "DASHSCOPE_API_KEY": "fake-dashscope-secret",
    "VOLCENGINE_API_KEY": "fake-volcengine-secret",
}

LEGACY_AUDIO_URL = "https://audio.example.invalid/final.wav?signature=synthetic-sensitive-url"
LEGACY_AUDIO_ENV = "VOLCENGINE_" + "AUDIO_URL"


def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _write_env(
    root: Path,
    *,
    missing: set[str] | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    values = {**FAKE_SECRETS, **DEFAULTS, **(overrides or {})}
    for name in missing or set():
        values.pop(name, None)
    (root / ".env").write_text(
        "\n".join(
            [*(f"{name}={value}" for name, value in values.items()), f"{LEGACY_AUDIO_ENV}={LEGACY_AUDIO_URL}"]
        )
        + "\n",
        encoding="utf-8",
    )
    return values


def _load_check_env_module():
    script = Path(__file__).resolve().parents[1] / "automation" / "scripts" / "check_env.py"
    spec = importlib.util.spec_from_file_location("boomearth_check_env_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_check_env_fixture(
    root: Path,
    *,
    create_paths: bool,
    missing: set[str] | None = None,
    overrides: dict[str, str] | None = None,
) -> None:
    indextts2_root = root / "index-tts"
    indextts2_python = root / "index-python.exe"
    reference_audio = root / "reference.wav"
    yt_dlp = root / "yt-dlp.exe"

    if create_paths:
        indextts2_root.mkdir()
        indextts2_python.write_bytes(b"fake python")
        reference_audio.write_bytes(b"fake audio")
        yt_dlp.write_bytes(b"fake yt-dlp")

    path_overrides = {
        "INDEXTTS2_ROOT": str(indextts2_root),
        "INDEXTTS2_PYTHON": str(indextts2_python),
        "INDEXTTS2_REFERENCE_AUDIO": str(reference_audio),
        "YT_DLP_PATH": str(yt_dlp),
    }
    _write_env(
        root,
        missing=missing,
        overrides={**path_overrides, **(overrides or {})},
    )


def test_defaults_and_env_example_match_expected_contract() -> None:
    assert DEFAULTS == EXPECTED_DEFAULTS

    env_example = dotenv_values(Path(__file__).resolve().parents[1] / ".env.example")
    credential_names = (
        "TIKHUB_API_KEY",
        "DASHSCOPE_API_KEY",
        "VOLCENGINE_API_KEY",
    )

    assert {name: env_example.get(name) for name in credential_names} == {
        name: "" for name in credential_names
    }
    assert {
        name: value
        for name, value in env_example.items()
        if name not in credential_names
    } == EXPECTED_DEFAULTS
    assert set(env_example) == set(Settings.ENV_NAMES)
    assert LEGACY_AUDIO_ENV not in env_example


def test_process_environment_overrides_dotenv_and_stays_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_environment(monkeypatch)
    values = _write_env(tmp_path)
    process_value = "https://process-env.invalid"
    monkeypatch.setenv("TIKHUB_API_BASE", process_value)

    settings = Settings.load(tmp_path)

    assert values["TIKHUB_API_BASE"] != process_value
    assert settings.tikhub_api_base == process_value
    with pytest.raises(FrozenInstanceError):
        settings.tikhub_api_base = "https://changed.invalid"  # type: ignore[misc]


def test_legacy_volcengine_audio_setting_is_ignored_by_runtime_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if an obsolete dotenv setting re-entered loaded settings or diagnostics."""

    _clear_environment(monkeypatch)
    _write_env(tmp_path)

    settings = Settings.load(tmp_path)
    rendered = repr(settings) + repr(settings.redacted_status())

    assert LEGACY_AUDIO_ENV not in settings.redacted_status()
    assert LEGACY_AUDIO_URL not in rendered


def test_check_env_omits_legacy_audio_setting_when_required_settings_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Would fail if diagnostics exposed a removed configuration boundary."""

    _clear_environment(monkeypatch)
    root = tmp_path / "missing-required-and-legacy-audio"
    root.mkdir()
    _write_check_env_fixture(
        root,
        create_paths=False,
        missing={"TIKHUB_API_KEY"},
        overrides={"VOLCENGINE_RESOURCE_ID": "volc.seedasr.auc"},
    )
    monkeypatch.setenv("VOLCENGINE_RESOURCE_ID", "volc.bigasr.auc_turbo")

    exit_code = _load_check_env_module().main(root=root)
    output = capsys.readouterr().out

    assert exit_code == 2
    assert "TIKHUB_API_KEY=UNSET" in output
    assert "DASHSCOPE_API_KEY=SET" in output
    assert "VOLCENGINE_API_KEY=SET" in output
    assert "VOLCENGINE_RESOURCE_ID=volc.bigasr.auc_turbo" in output
    assert LEGACY_AUDIO_ENV not in output
    assert all(secret not in output for secret in FAKE_SECRETS.values())


@pytest.mark.parametrize(
    ("case", "expected_exit", "expected_status"),
    [
        ("success", 0, "status=OK"),
        ("missing", 2, "status=REQUIRED_SETTINGS_MISSING"),
        ("invalid", 2, "status=INVALID_SETTING:VOICE_PLAYBACK_SPEED"),
        ("missing_path", 3, "status=CONFIGURED_PATH_MISSING"),
    ],
)
def test_check_env_exit_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    expected_exit: int,
    expected_status: str,
) -> None:
    _clear_environment(monkeypatch)
    root = tmp_path / case
    root.mkdir()

    if case == "success":
        _write_check_env_fixture(root, create_paths=True)
    elif case == "missing":
        _write_check_env_fixture(root, create_paths=False, missing={"TIKHUB_API_KEY"})
    elif case == "invalid":
        _write_check_env_fixture(
            root,
            create_paths=False,
            overrides={"VOICE_PLAYBACK_SPEED": "not-a-number"},
        )
    else:
        _write_check_env_fixture(root, create_paths=False)

    check_env = _load_check_env_module()
    exit_code = check_env.main(root=root)
    output = capsys.readouterr().out

    assert exit_code == expected_exit
    assert expected_status in output
    assert all(secret not in output for secret in FAKE_SECRETS.values())


def test_missing_variable_error_lists_names_without_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_environment(monkeypatch)
    values = _write_env(tmp_path, missing={"TIKHUB_API_KEY", "VOLCENGINE_API_KEY"})

    with pytest.raises(MissingSettingsError) as exc_info:
        Settings.load(tmp_path)

    message = str(exc_info.value)
    assert "TIKHUB_API_KEY" in message
    assert "VOLCENGINE_API_KEY" in message
    assert all(secret not in message for secret in FAKE_SECRETS.values())
    assert values["DASHSCOPE_API_KEY"] not in message


def test_redacted_diagnostics_never_contain_supplied_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_environment(monkeypatch)
    _write_env(tmp_path)

    settings = Settings.load(tmp_path)
    diagnostics = settings.redacted_status()
    rendered = repr(diagnostics)

    assert all(secret not in rendered for secret in FAKE_SECRETS.values())
    assert diagnostics["TIKHUB_API_KEY"] == "SET"
    assert diagnostics["DASHSCOPE_API_KEY"] == "SET"
    assert diagnostics["VOLCENGINE_API_KEY"] == "SET"


def test_v1_defaults_are_exact_and_settings_are_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_environment(monkeypatch)
    _write_env(tmp_path)

    settings = Settings.load(tmp_path)

    assert settings.tikhub_api_base == DEFAULTS["TIKHUB_API_BASE"]
    assert settings.paraformer_model == DEFAULTS["PARAFORMER_MODEL"]
    assert settings.volcengine_resource_id == DEFAULTS["VOLCENGINE_RESOURCE_ID"]
    assert str(settings.indextts2_root) == DEFAULTS["INDEXTTS2_ROOT"]
    assert str(settings.indextts2_python) == DEFAULTS["INDEXTTS2_PYTHON"]
    assert str(settings.indextts2_reference_audio) == DEFAULTS["INDEXTTS2_REFERENCE_AUDIO"]
    assert str(settings.yt_dlp_path) == DEFAULTS["YT_DLP_PATH"]
    assert settings.voice_playback_speed == 1.12

    with pytest.raises(FrozenInstanceError):
        settings.tikhub_api_base = "https://example.invalid"  # type: ignore[misc]


def test_heygen_is_absent_from_the_settings_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_environment(monkeypatch)
    _write_env(tmp_path)

    settings = Settings.load(tmp_path)
    diagnostics = settings.redacted_status()

    assert "HEYGEN_API_KEY" not in Settings.ENV_NAMES
    assert not hasattr(settings, "heygen_api_key")
    assert all("HEYGEN" not in name for name in diagnostics)

"""Print redacted V1 environment diagnostics and return a contract status."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from boomearth.config import (  # noqa: E402
    InvalidSettingError,
    MissingSettingsError,
    Settings,
    redacted_environment_status,
)


EXPECTED_CONTRACT = {
    "TIKHUB_API_BASE": "https://api.tikhub.dev",
    "PARAFORMER_MODEL": "paraformer-realtime-v2",
    "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
}


def _print_status(status: dict[str, str]) -> None:
    for name in Settings.ENV_NAMES:
        print(f"{name}={status[name]}")


def _selected_volcengine_resource(root: Path) -> str:
    value = os.environ.get("VOLCENGINE_RESOURCE_ID")
    if value is None:
        value = dotenv_values(Path(root) / ".env").get("VOLCENGINE_RESOURCE_ID")
    return value.strip() if isinstance(value, str) and value.strip() else "UNSET"


def _missing_status(root: Path) -> int:
    status = redacted_environment_status(root)
    status["VOLCENGINE_RESOURCE_ID"] = _selected_volcengine_resource(root)
    _print_status(status)
    print("status=REQUIRED_SETTINGS_MISSING")
    return 2


def _path_failures(settings: Settings) -> list[str]:
    checks = (
        ("INDEXTTS2_ROOT", settings.indextts2_root.is_dir()),
        ("INDEXTTS2_PYTHON", settings.indextts2_python.is_file()),
        ("INDEXTTS2_REFERENCE_AUDIO", settings.indextts2_reference_audio.is_file()),
        ("YT_DLP_PATH", settings.yt_dlp_path.is_file()),
    )
    return [name for name, exists in checks if not exists]


def main(root: Path = ROOT) -> int:
    try:
        settings = Settings.load(root)
    except MissingSettingsError:
        return _missing_status(root)
    except InvalidSettingError as error:
        print(f"status=INVALID_SETTING:{error.name}")
        return 2

    status = settings.redacted_status()
    _print_status(status)

    invalid_contract = [
        name for name, expected in EXPECTED_CONTRACT.items() if status[name] != expected
    ]
    if invalid_contract:
        print("status=INVALID_CONTRACT")
        return 2

    missing_paths = _path_failures(settings)
    if missing_paths:
        print("missing_paths=" + ",".join(missing_paths))
        print("status=CONFIGURED_PATH_MISSING")
        return 3

    print("status=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

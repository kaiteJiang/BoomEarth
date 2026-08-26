"""Validated, immutable configuration for the BoomEarth V1 environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Iterable

from dotenv import dotenv_values


DEFAULTS: dict[str, str] = {
    "TIKHUB_API_BASE": "https://api.tikhub.dev",
    "PARAFORMER_MODEL": "paraformer-realtime-v2",
    "VOLCENGINE_RESOURCE_ID": "volc.bigasr.auc_turbo",
    "INDEXTTS2_ROOT": r"C:\BoomEarthLocal\IndexTTS2",
    "INDEXTTS2_PYTHON": r"C:\BoomEarthLocal\IndexTTS2\.venv\Scripts\python.exe",
    "INDEXTTS2_REFERENCE_AUDIO": r"C:\BoomEarthLocal\voice\reference.wav",
    "YT_DLP_PATH": r"C:\BoomEarth\yt-dlp.exe",
    "VOICE_PLAYBACK_SPEED": "1.12",
}

REQUIRED_VARIABLES: tuple[str, ...] = (
    "TIKHUB_API_KEY",
    "DASHSCOPE_API_KEY",
    "VOLCENGINE_API_KEY",
    *DEFAULTS,
)

ENV_VARIABLES: tuple[str, ...] = (
    "TIKHUB_API_KEY",
    "DASHSCOPE_API_KEY",
    "VOLCENGINE_API_KEY",
    *DEFAULTS,
)

CREDENTIAL_VARIABLES: tuple[str, ...] = (
    "TIKHUB_API_KEY",
    "DASHSCOPE_API_KEY",
    "VOLCENGINE_API_KEY",
)


class MissingSettingsError(ValueError):
    """Raised when one or more required settings are absent or empty."""

    def __init__(self, missing: Iterable[str]) -> None:
        self.missing = tuple(missing)
        names = ", ".join(self.missing)
        super().__init__(f"Missing required settings: {names}")


class InvalidSettingError(ValueError):
    """Raised when a present setting cannot be converted safely."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Invalid setting: {name}")


def _is_present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _read_environment(root: Path) -> dict[str, str | None]:
    env_file = Path(root) / ".env"
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    return {
        name: os.environ.get(name, file_values.get(name))
        for name in ENV_VARIABLES
    }


def redacted_environment_status(root: Path) -> dict[str, str]:
    """Return presence-only diagnostics even when required settings are missing."""

    values = _read_environment(Path(root))
    return {
        name: "SET" if _is_present(values[name]) else "UNSET"
        for name in ENV_VARIABLES
    }


def _required_value(values: dict[str, str | None], name: str) -> str:
    value = values[name]
    if not isinstance(value, str):
        raise InvalidSettingError(name)
    return value.strip()


@dataclass(frozen=True, slots=True, repr=False)
class Settings:
    """The complete V1 environment contract.

    Credential fields are deliberately excluded from the generated repr. Use
    :meth:`redacted_status` when diagnostics are needed.
    """

    tikhub_api_key: str = field(repr=False)
    dashscope_api_key: str = field(repr=False)
    volcengine_api_key: str = field(repr=False)
    tikhub_api_base: str
    paraformer_model: str
    volcengine_resource_id: str
    indextts2_root: Path
    indextts2_python: Path
    indextts2_reference_audio: Path
    yt_dlp_path: Path
    voice_playback_speed: float

    ENV_NAMES: ClassVar[tuple[str, ...]] = ENV_VARIABLES
    CREDENTIAL_NAMES: ClassVar[tuple[str, ...]] = CREDENTIAL_VARIABLES

    @classmethod
    def load(cls, root: Path) -> "Settings":
        """Load and validate the root ``.env`` plus matching process variables."""

        values = _read_environment(Path(root))
        missing = [name for name in REQUIRED_VARIABLES if not _is_present(values[name])]
        if missing:
            raise MissingSettingsError(missing)

        try:
            playback_speed = float(_required_value(values, "VOICE_PLAYBACK_SPEED"))
        except (InvalidSettingError, ValueError):
            raise InvalidSettingError("VOICE_PLAYBACK_SPEED") from None

        return cls(
            tikhub_api_key=_required_value(values, "TIKHUB_API_KEY"),
            dashscope_api_key=_required_value(values, "DASHSCOPE_API_KEY"),
            volcengine_api_key=_required_value(values, "VOLCENGINE_API_KEY"),
            tikhub_api_base=_required_value(values, "TIKHUB_API_BASE"),
            paraformer_model=_required_value(values, "PARAFORMER_MODEL"),
            volcengine_resource_id=_required_value(values, "VOLCENGINE_RESOURCE_ID"),
            indextts2_root=Path(_required_value(values, "INDEXTTS2_ROOT")),
            indextts2_python=Path(_required_value(values, "INDEXTTS2_PYTHON")),
            indextts2_reference_audio=Path(
                _required_value(values, "INDEXTTS2_REFERENCE_AUDIO")
            ),
            yt_dlp_path=Path(_required_value(values, "YT_DLP_PATH")),
            voice_playback_speed=playback_speed,
        )

    def redacted_status(self) -> dict[str, str]:
        """Return diagnostics containing no credential material or lengths."""

        return {
            "TIKHUB_API_KEY": "SET" if self.tikhub_api_key else "UNSET",
            "DASHSCOPE_API_KEY": "SET" if self.dashscope_api_key else "UNSET",
            "VOLCENGINE_API_KEY": "SET" if self.volcengine_api_key else "UNSET",
            "TIKHUB_API_BASE": self.tikhub_api_base,
            "PARAFORMER_MODEL": self.paraformer_model,
            "VOLCENGINE_RESOURCE_ID": self.volcengine_resource_id,
            "INDEXTTS2_ROOT": str(self.indextts2_root),
            "INDEXTTS2_PYTHON": str(self.indextts2_python),
            "INDEXTTS2_REFERENCE_AUDIO": str(self.indextts2_reference_audio),
            "YT_DLP_PATH": str(self.yt_dlp_path),
            "VOICE_PLAYBACK_SPEED": "SET",
        }

    def __repr__(self) -> str:
        return "Settings(<redacted>)"

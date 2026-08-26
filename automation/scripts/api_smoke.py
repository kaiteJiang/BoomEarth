"""Opt-in, redacted API smoke commands for BoomEarth integrations."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from boomearth.config import InvalidSettingError, MissingSettingsError, Settings  # noqa: E402
from boomearth.audio.indextts2 import (  # noqa: E402
    IndexTTS2Narrator,
    IndexTTS2ValidationError,
    TTSRouting,
)
from boomearth.providers.tikhub import (  # noqa: E402
    AccountStatus,
    AuthorizationRequiredError,
    InvalidTikHubBaseError,
    TikHubClient,
    TikHubCredentialError,
    TikHubError,
    TikHubServiceError,
    TikHubTimeoutError,
    TikHubTransportError,
)
from boomearth.providers.paraformer import (  # noqa: E402
    ParaformerClient,
    ParaformerCredentialError,
    ParaformerError,
    ParaformerInputError,
    ParaformerTimeoutError,
    ParaformerTransportError,
    validate_local_audio,
)
from boomearth.providers.volcengine_asr import (  # noqa: E402
    VolcengineASRClient,
    VolcengineCredentialError,
    VolcengineASRError,
    VolcengineServiceError,
    VolcengineTimeoutError,
    VolcengineTransportError,
)


ClientFactory = Callable[[Settings], TikHubClient]
ParaformerClientFactory = Callable[[Settings, Path], object]
VolcengineClientFactory = Callable[[Settings], object]
VolcengineNarratorFactory = Callable[[Path], object]
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SMOKE_STATUSES = frozenset(
    {
        "AUTHORIZATION_REQUIRED",
        "CONFIG_ERROR",
        "INPUT_ERROR",
        "LOCAL_CONTRACT_ERROR",
        "NETWORK_ERROR",
        "SERVICE_ERROR",
        "TIMEOUT",
        "INTERNAL_ERROR",
        "OK",
    }
)

def _smoke_exit_code(status: str) -> int:
    if status == "OK":
        return 0
    if status in {"AUTHORIZATION_REQUIRED", "CONFIG_ERROR", "INPUT_ERROR"}:
        return 2
    return 1


def _safe_smoke_status(status: object) -> str:
    if isinstance(status, str) and status in SMOKE_STATUSES:
        return status
    return "INTERNAL_ERROR"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BoomEarth opt-in API smoke checks")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "tikhub-account",
        help="check the authorized TikHub account without printing account details",
    )
    paraformer_description = (
        "Upload and transcribe one explicitly supplied local audio file via "
        "DashScope; requires DASHSCOPE_API_KEY and may consume paid quota or "
        "incur charges."
    )
    paraformer = subparsers.add_parser(
        "paraformer",
        help=paraformer_description,
        description=paraformer_description,
    )
    paraformer.add_argument("--audio", required=True, type=Path)
    paraformer.add_argument(
        "--authorized",
        action="store_true",
        help="confirm you own or have lawful rights to submit this audio to DashScope",
    )
    volcengine_description = (
        "Submit one explicitly supplied Task 6 locked final WAV to "
        "Volcengine/Doubao recording-file turbo HTTP ASR; requires VOLCENGINE_API_KEY and may consume quota "
        "or incur charges."
    )
    volcengine = subparsers.add_parser(
        "volcengine",
        help=volcengine_description,
        description=volcengine_description,
    )
    volcengine.add_argument("--audio", required=True, type=Path)
    return parser


def _print_account_status(
    *,
    status: str,
    http_status: int | str,
    account_active: bool,
    quota_present: bool,
) -> None:
    print(f"status={_safe_smoke_status(status)}")
    print(f"http_status={http_status}")
    print(f"account_active={str(account_active).lower()}")
    print(f"quota_present={str(quota_present).lower()}")


def _print_paraformer_status(
    *,
    status: str,
    duration: float | str,
    segments: int | str,
    request_id: str,
) -> None:
    print(f"status={_safe_smoke_status(status)}")
    print(f"duration={duration}")
    print(f"segments={segments}")
    print(f"request_id={request_id}")


def _print_volcengine_status(
    *,
    status: str,
    request_id: str,
    timing_granularity: str,
    duration: float | str,
    token_count: int | str,
) -> None:
    print(f"status={_safe_smoke_status(status)}")
    print(f"request_id={request_id}")
    print(f"timing_granularity={timing_granularity}")
    print(f"duration={duration}")
    print(f"token_count={token_count}")


def _safe_smoke_request_id(client: object | None) -> str:
    try:
        value = getattr(client, "last_request_id", None)
    except Exception:
        return "UNAVAILABLE"
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return "UNAVAILABLE"
    request_id = str(value)
    if _SAFE_REQUEST_ID.fullmatch(request_id) is None:
        return "UNAVAILABLE"
    return request_id


def _safe_smoke_http_status(client: object | None) -> int | str:
    try:
        value = getattr(client, "last_http_status", None)
    except Exception:
        return "UNAVAILABLE"
    if not isinstance(value, int) or isinstance(value, bool):
        return "UNAVAILABLE"
    return value


def _classify_smoke_error(error: BaseException) -> str:
    if isinstance(error, (httpx.TimeoutException, TikHubTimeoutError, ParaformerTimeoutError, VolcengineTimeoutError)):
        return "TIMEOUT"
    if isinstance(
        error,
        (
            httpx.RequestError,
            TikHubTransportError,
            ParaformerTransportError,
            VolcengineTransportError,
        ),
    ):
        return "NETWORK_ERROR"
    if isinstance(error, AuthorizationRequiredError):
        return "AUTHORIZATION_REQUIRED"
    if isinstance(
        error,
        (
            InvalidTikHubBaseError,
            TikHubCredentialError,
            ParaformerCredentialError,
            VolcengineCredentialError,
        ),
    ):
        return "CONFIG_ERROR"
    if isinstance(error, ParaformerInputError):
        return "INPUT_ERROR"
    if isinstance(
        error,
        (
            TikHubServiceError,
            TikHubError,
            ParaformerError,
            VolcengineServiceError,
            VolcengineASRError,
        ),
    ):
        return "SERVICE_ERROR"
    return "INTERNAL_ERROR"


def _locked_narrator(root: Path) -> IndexTTS2Narrator:
    route = TTSRouting.load(Path(root) / "automation" / "config" / "tts-routing.json")
    return IndexTTS2Narrator(route)


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = ROOT,
    client_factory: ClientFactory = TikHubClient,
    paraformer_client_factory: ParaformerClientFactory | None = None,
    volcengine_client_factory: VolcengineClientFactory | None = None,
    volcengine_narrator_factory: VolcengineNarratorFactory | None = None,
    settings_loader: Callable[[Path], object] | None = None,
) -> int:
    settings_loader = settings_loader or Settings.load
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "volcengine":
        try:
            manifest_path = args.audio.with_name("voice_manifest.json")
        except ValueError:
            _print_volcengine_status(
                status="INPUT_ERROR",
                request_id="UNAVAILABLE",
                timing_granularity="UNAVAILABLE",
                duration="UNAVAILABLE",
                token_count="UNAVAILABLE",
            )
            return 2
        narrator_factory = volcengine_narrator_factory or _locked_narrator
        try:
            narrator = narrator_factory(root)
            probe = getattr(narrator, "probe_committed_wav", None)
            if not callable(probe):
                raise RuntimeError("locked final-audio probe is unavailable")
            committed_probe = probe(args.audio, manifest_path)
            expected_sha256 = getattr(committed_probe, "sha256", None)
            if not isinstance(expected_sha256, str):
                raise RuntimeError("locked final-audio hash is unavailable")
        except IndexTTS2ValidationError:
            _print_volcengine_status(
                status="LOCAL_CONTRACT_ERROR",
                request_id="UNAVAILABLE",
                timing_granularity="UNAVAILABLE",
                duration="UNAVAILABLE",
                token_count="UNAVAILABLE",
            )
            return 1
        except Exception:
            _print_volcengine_status(
                status="INTERNAL_ERROR",
                request_id="UNAVAILABLE",
                timing_granularity="UNAVAILABLE",
                duration="UNAVAILABLE",
                token_count="UNAVAILABLE",
            )
            return 1

        try:
            settings = settings_loader(root)
        except (MissingSettingsError, InvalidSettingError):
            _print_volcengine_status(
                status="CONFIG_ERROR",
                request_id="UNAVAILABLE",
                timing_granularity="UNAVAILABLE",
                duration="UNAVAILABLE",
                token_count="UNAVAILABLE",
            )
            return 2
        except Exception:
            _print_volcengine_status(
                status="INTERNAL_ERROR",
                request_id="UNAVAILABLE",
                timing_granularity="UNAVAILABLE",
                duration="UNAVAILABLE",
                token_count="UNAVAILABLE",
            )
            return 1

        factory = volcengine_client_factory or VolcengineASRClient
        client: object | None = None
        try:
            client = factory(settings)
            try:
                result = client.transcribe_file(
                    args.audio,
                    expected_sha256=expected_sha256,
                )
                timing_granularity = str(
                    getattr(result, "timing_granularity", "UNAVAILABLE")
                )
                duration = getattr(result, "duration", "UNAVAILABLE")
                token_count = getattr(result, "token_count", "UNAVAILABLE")
            finally:
                closer = getattr(client, "close", None)
                if callable(closer):
                    closer()
        except Exception as error:
            smoke_status = _classify_smoke_error(error)
            _print_volcengine_status(
                status=smoke_status,
                request_id=_safe_smoke_request_id(client),
                timing_granularity="UNAVAILABLE",
                duration="UNAVAILABLE",
                token_count="UNAVAILABLE",
            )
            return _smoke_exit_code(smoke_status)

        _print_volcengine_status(
            status="OK",
            request_id=_safe_smoke_request_id(client),
            timing_granularity=timing_granularity,
            duration=duration,
            token_count=token_count,
        )
        return 0

    if args.command == "paraformer":
        if not args.authorized:
            _print_paraformer_status(
                status="AUTHORIZATION_REQUIRED",
                duration="UNAVAILABLE",
                segments="UNAVAILABLE",
                request_id="UNAVAILABLE",
            )
            return 2

        try:
            validate_local_audio(args.audio)
        except ParaformerInputError:
            _print_paraformer_status(
                status="INPUT_ERROR",
                duration="UNAVAILABLE",
                segments="UNAVAILABLE",
                request_id="UNAVAILABLE",
            )
            return 2
        except Exception:
            _print_paraformer_status(
                status="INTERNAL_ERROR",
                duration="UNAVAILABLE",
                segments="UNAVAILABLE",
                request_id="UNAVAILABLE",
            )
            return 1

        try:
            settings = settings_loader(root)
        except (MissingSettingsError, InvalidSettingError):
            _print_paraformer_status(
                status="CONFIG_ERROR",
                duration="UNAVAILABLE",
                segments="UNAVAILABLE",
                request_id="UNAVAILABLE",
            )
            return 2
        except Exception:
            _print_paraformer_status(
                status="INTERNAL_ERROR",
                duration="UNAVAILABLE",
                segments="UNAVAILABLE",
                request_id="UNAVAILABLE",
            )
            return 1

        factory = paraformer_client_factory
        if factory is None:
            factory = ParaformerClient

        client: object | None = None
        try:
            client = factory(settings, root=root)
            result = client.transcribe_file(args.audio)
        except Exception as error:
            smoke_status = _classify_smoke_error(error)
            _print_paraformer_status(
                status=smoke_status,
                duration="UNAVAILABLE",
                segments="UNAVAILABLE",
                request_id=_safe_smoke_request_id(client),
            )
            return _smoke_exit_code(smoke_status)

        _print_paraformer_status(
            status="OK",
            duration=result.duration,
            segments=result.segment_count,
            request_id=_safe_smoke_request_id(client),
        )
        return 0

    if args.command != "tikhub-account":
        _parser().print_usage(sys.stderr)
        return 2

    try:
        settings = settings_loader(root)
    except (MissingSettingsError, InvalidSettingError):
        _print_account_status(
            status="CONFIG_ERROR",
            http_status="UNAVAILABLE",
            account_active=False,
            quota_present=False,
        )
        return 2
    except Exception:
        _print_account_status(
            status="INTERNAL_ERROR",
            http_status="UNAVAILABLE",
            account_active=False,
            quota_present=False,
        )
        return 1

    client: TikHubClient | None = None
    try:
        client = client_factory(settings)
        try:
            status: AccountStatus = client.check_account()
        finally:
            if client is not None:
                client.close()
    except Exception as error:
        smoke_status = _classify_smoke_error(error)
        _print_account_status(
            status=smoke_status,
            http_status=_safe_smoke_http_status(client),
            account_active=False,
            quota_present=False,
        )
        return _smoke_exit_code(smoke_status)

    _print_account_status(
        status="OK",
        http_status=_safe_smoke_http_status(client),
        account_active=status.active,
        quota_present=status.quota_present,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

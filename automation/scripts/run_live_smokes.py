"""Offline-first, fail-closed orchestration for explicitly approved API smokes."""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import inspect
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from dotenv import dotenv_values
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from boomearth.config import DEFAULTS, InvalidSettingError, MissingSettingsError, Settings  # noqa: E402

ServiceName = Literal["tikhub", "paraformer", "volcengine"]
SERVICE_ORDER: tuple[ServiceName, ...] = ("tikhub", "paraformer", "volcengine")
REPORT_SCHEMA = "boomearth-live-smoke-v1"
_REQUEST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_STATUSES = frozenset({"OK", "AUTHORIZATION_REQUIRED", "CONFIG_ERROR", "INPUT_ERROR", "LOCAL_CONTRACT_ERROR", "NETWORK_ERROR", "SERVICE_ERROR", "TIMEOUT", "INTERNAL_ERROR"})
_ATTEMPTED = frozenset({"OK", "NETWORK_ERROR", "SERVICE_ERROR", "TIMEOUT"})
_LOCAL = frozenset({"AUTHORIZATION_REQUIRED", "CONFIG_ERROR", "INPUT_ERROR", "LOCAL_CONTRACT_ERROR"})
_CREDENTIAL = {"tikhub": "TIKHUB_API_KEY", "paraformer": "DASHSCOPE_API_KEY", "volcengine": "VOLCENGINE_API_KEY"}
_SERVICE_REQUIRED_SETTINGS = {
    "tikhub": ("TIKHUB_API_KEY",),
    "paraformer": ("DASHSCOPE_API_KEY",),
    "volcengine": ("VOLCENGINE_API_KEY",),
}
_MAX_CAPTURE = 4096
_EMPTY_CREDENTIAL_STATUS: Mapping[str, str] = MappingProxyType({})

@dataclass(frozen=True, slots=True)
class PreflightResult:
    outcome: str
    status: str
    credential_status: Mapping[str, str]
    def payload(self) -> dict[str, object]:
        return {"outcome": self.outcome, "status": self.status, "credential_status": dict(self.credential_status)}

@dataclass(frozen=True, slots=True)
class ServiceResult:
    service: ServiceName; outcome: str; status: str; request_id: str; network_attempted: bool
    def payload(self) -> dict[str, object]:
        return {"service": self.service, "outcome": self.outcome, "status": self.status, "request_id": self.request_id, "network_attempted": self.network_attempted}

@dataclass(frozen=True, slots=True)
class RunReport:
    created_at_utc: str; report_id: str; command: str; services: tuple[ServiceName, ...]
    preflight: PreflightResult; service_results: tuple[ServiceResult, ...]
    network_attempted: bool; fail_closed_early: bool
    def payload(self) -> dict[str, object]:
        return {"schema": REPORT_SCHEMA, "created_at_utc": self.created_at_utc, "report_id": self.report_id,
                "command": self.command, "services": list(self.services), "preflight": self.preflight.payload(),
                "service_results": [item.payload() for item in self.service_results], "network_attempted": self.network_attempted,
                "fail_closed_early": self.fail_closed_early}

@dataclass(frozen=True, slots=True)
class SmokeInvocationResult:
    status: str; request_id: str = "UNAVAILABLE"; network_attempted: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class _VolcengineAPISettings:
    volcengine_api_key: str = field(repr=False)
    volcengine_resource_id: str

    def __repr__(self) -> str:
        return "_VolcengineAPISettings(<redacted>)"

SmokeRunner = Callable[[list[str]], SmokeInvocationResult]
RunnerFactory = Callable[[Path], SmokeRunner]
SettingsLoader = Callable[[Path], object]
LockedWavProbe = Callable[[Path, Path], object]
Clock = Callable[[], datetime]
Identifier = Callable[[], str]
SMOKE_ARGV: dict[ServiceName, Callable[[SimpleNamespace], list[str]]] = {
    "tikhub": lambda args: ["tikhub-account"],
    "paraformer": lambda args: ["paraformer", "--audio", str(args.paraformer_audio), "--authorized"],
    "volcengine": lambda args: ["volcengine", "--audio", str(args.volcengine_audio)],
}

def _canonical_services(services: Sequence[str]) -> tuple[ServiceName, ...]:
    values = tuple(services)
    if not values or len(values) != len(set(values)) or set(values).difference(SERVICE_ORDER):
        raise ValueError("services must be present, known, and non-duplicate")
    return tuple(item for item in SERVICE_ORDER if item in values)

def _unsafe(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return True

_is_unsafe_path_component = _unsafe

def _safe_existing(path: Path, directory: bool) -> bool:
    if not path.is_absolute() or ".." in path.parts:
        return False
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    if (directory and not stat.S_ISDIR(mode)) or (not directory and not stat.S_ISREG(mode)):
        return False
    current = path
    while True:
        if _is_unsafe_path_component(current): return False
        if current.parent == current: return True
        current = current.parent

def _safe_absolute_audio_path(audio: Path | None) -> bool:
    return audio is not None and _safe_existing(audio, False)

def _manifest_path(audio: Path | None) -> Path | None:
    try:
        return audio.with_name("voice_manifest.json") if audio is not None else None
    except ValueError:
        return None

def _identity(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        value = path.lstat()
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_mode)
    except OSError:
        return None

def _parent_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        value = path.lstat()
        return (value.st_dev, value.st_ino, value.st_mode)
    except OSError:
        return None

def _safe_report_parent(root: Path) -> Path | None:
    root = Path(root)
    if not _safe_existing(root, True): return None
    current = root
    for name in ("runtime", "live-smokes"):
        current = current / name
        try:
            if current.exists():
                if not _safe_existing(current, True): return None
            else:
                current.mkdir()
                if not _safe_existing(current, True): return None
        except OSError:
            return None
    return current

def _default_locked_wav_probe(root: Path, audio: Path, manifest: Path) -> object:
    from boomearth.audio.indextts2 import IndexTTS2Narrator, TTSRouting
    return IndexTTS2Narrator(TTSRouting.load(root / "automation" / "config" / "tts-routing.json")).probe_committed_wav(audio, manifest)

def _credential_states(settings: object, services: tuple[ServiceName, ...]) -> dict[str, str] | None:
    try: raw = getattr(settings, "redacted_status")()
    except Exception: return None
    if not isinstance(raw, Mapping): return None
    selected = {
        name
        for item in services
        for name in _SERVICE_REQUIRED_SETTINGS[item]
    }
    values = {name: raw.get(name) for name in selected}
    return values if all(value in {"SET", "UNSET"} for value in values.values()) else None

def _selected_settings(root: Path, services: tuple[ServiceName, ...]) -> object:
    values = {**dotenv_values(Path(root) / ".env"), **os.environ}
    selected = {
        name
        for item in services
        for name in _SERVICE_REQUIRED_SETTINGS[item]
    }
    states = {
        name: "SET"
        if isinstance(values.get(name), str) and bool(values[name].strip())
        else "UNSET"
        for name in selected
    }
    nonsecret = {"tikhub": "TIKHUB_API_BASE", "paraformer": "PARAFORMER_MODEL", "volcengine": "VOLCENGINE_RESOURCE_ID"}
    valid = all(isinstance(values.get(nonsecret[item], DEFAULTS[nonsecret[item]]), str) and bool(values.get(nonsecret[item], DEFAULTS[nonsecret[item]]).strip()) for item in services)
    if "volcengine" in services:
        resource = values.get("VOLCENGINE_RESOURCE_ID", DEFAULTS["VOLCENGINE_RESOURCE_ID"])
        valid = valid and isinstance(resource, str) and resource.strip() == DEFAULTS["VOLCENGINE_RESOURCE_ID"]
    return type("SelectedSettings", (), {"redacted_status": lambda self: states, "local_config_ok": valid})()

def _selected_api_settings(root: Path, command: str) -> object:
    service = "tikhub" if command == "tikhub-account" else command
    values = {**dotenv_values(Path(root) / ".env"), **os.environ}
    key = _CREDENTIAL[service]
    secret = values.get(key)
    if not isinstance(secret, str) or not secret.strip():
        raise MissingSettingsError((key,))
    nonsecret = {"tikhub": "TIKHUB_API_BASE", "paraformer": "PARAFORMER_MODEL", "volcengine": "VOLCENGINE_RESOURCE_ID"}[service]
    value = values.get(nonsecret, DEFAULTS[nonsecret])
    if not isinstance(value, str) or not value.strip():
        raise MissingSettingsError((nonsecret,))
    if service == "tikhub":
        return SimpleNamespace(tikhub_api_key=secret.strip(), tikhub_api_base=value.strip())
    if service == "paraformer":
        return SimpleNamespace(dashscope_api_key=secret.strip(), paraformer_model=value.strip())
    if value.strip() != DEFAULTS["VOLCENGINE_RESOURCE_ID"]:
        raise InvalidSettingError("VOLCENGINE_RESOURCE_ID")
    return _VolcengineAPISettings(
        volcengine_api_key=secret.strip(),
        volcengine_resource_id=value.strip(),
    )

def _local_preflight(*, root: Path, services: tuple[ServiceName, ...], paraformer_audio: Path | None, volcengine_audio: Path | None, settings_loader: SettingsLoader, locked_wav_probe: LockedWavProbe | None) -> PreflightResult:
    empty = _EMPTY_CREDENTIAL_STATUS
    if "paraformer" in services and not _safe_absolute_audio_path(paraformer_audio): return PreflightResult("FAIL", "INPUT_ERROR", empty)
    if "volcengine" in services:
        if not _safe_absolute_audio_path(volcengine_audio): return PreflightResult("FAIL", "INPUT_ERROR", empty)
        manifest = _manifest_path(volcengine_audio)
        if manifest is None: return PreflightResult("FAIL", "INPUT_ERROR", empty)
        try: (locked_wav_probe or (lambda a, m: _default_locked_wav_probe(root, a, m)))(volcengine_audio, manifest)  # type: ignore[arg-type]
        except Exception: return PreflightResult("FAIL", "LOCAL_CONTRACT_ERROR", empty)
    try: settings = settings_loader(root)
    except Exception: return PreflightResult("FAIL", "CONFIG_ERROR", empty)
    states = _credential_states(settings, services)
    if states is None: return PreflightResult("FAIL", "CONFIG_ERROR", empty)
    frozen = MappingProxyType(dict(states))
    if "UNSET" in frozen.values() or getattr(settings, "local_config_ok", True) is not True: return PreflightResult("FAIL", "CONFIG_ERROR", frozen)
    return PreflightResult("READY", "READY", frozen)

def _metadata(now: Clock, identifier: Identifier) -> tuple[str, str]:
    timestamp = now().astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return timestamp, str(identifier())

def _report_path_for(root: Path, created: str, report_id: str) -> Path:
    return Path(root) / "runtime" / "live-smokes" / f"live-smoke-{created.replace('-', '').replace(':', '')}-{re.sub(r'[^A-Za-z0-9-]', '-', report_id)}.json"

def report_path(root: Path, *, now_factory: Clock = lambda: datetime.now(UTC), uuid_factory: Identifier = lambda: str(uuid.uuid4())) -> Path:
    created, report_id = _metadata(now_factory, uuid_factory); return _report_path_for(root, created, report_id)

def _new(command: str, services: tuple[ServiceName, ...], preflight: PreflightResult, now: Clock, identifier: Identifier) -> RunReport:
    created, report_id = _metadata(now, identifier); return RunReport(created, report_id, command, services, preflight, (), False, False)

def _reserve_report(root: Path, report: RunReport) -> tuple[Path, object]:
    parent = _safe_report_parent(root)
    if parent is None: raise OSError("unsafe report parent")
    target = _report_path_for(root, report.created_at_utc, report.report_id)
    before = _parent_identity(parent)
    if target.parent != parent or before is None or not _safe_report_parent(root): raise OSError("report parent changed")
    handle = target.open("x", encoding="utf-8")
    if _parent_identity(parent) != before:
        handle.close(); raise OSError("report parent changed")
    return target, handle

def _write(handle: object, report: RunReport) -> bool:
    try: getattr(handle, "write")(json.dumps(report.payload(), sort_keys=True, separators=(",", ":"))); return True
    except Exception: return False
    finally:
        try: getattr(handle, "close")()
        except Exception: pass

def _report_error(report: RunReport) -> RunReport:
    return replace(report, preflight=PreflightResult("FAIL", "REPORT_ERROR", report.preflight.credential_status))

def build_plan(*, root: Path, services: Sequence[str], paraformer_audio: Path | None = None, volcengine_audio: Path | None = None, settings_loader: SettingsLoader | None = None, locked_wav_probe: LockedWavProbe | None = None, smoke_runner: SmokeRunner | None = None, now_factory: Clock = lambda: datetime.now(UTC), uuid_factory: Identifier = lambda: str(uuid.uuid4())) -> RunReport:
    del smoke_runner
    canonical = _canonical_services(services)
    loader = settings_loader or (lambda local_root: _selected_settings(local_root, canonical))
    return _new("plan", canonical, _local_preflight(root=Path(root), services=canonical, paraformer_audio=paraformer_audio, volcengine_audio=volcengine_audio, settings_loader=loader, locked_wav_probe=locked_wav_probe), now_factory, uuid_factory)

class _BoundedSink(io.StringIO):
    def __init__(self) -> None: super().__init__(); self.overflow = False
    def write(self, text: str) -> int:
        if self.tell() + len(text) > _MAX_CAPTURE:
            self.overflow = True; text = text[:max(0, _MAX_CAPTURE - self.tell())]
        return super().write(text)

def _load_api_smoke_main() -> Callable[..., int]:
    path = Path(__file__).with_name("api_smoke.py")
    spec = importlib.util.spec_from_file_location("boomearth_live_smoke_api", path)
    if spec is None or spec.loader is None: raise RuntimeError("api smoke unavailable")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    main = getattr(module, "main", None)
    if not callable(main): raise RuntimeError("api smoke unavailable")
    return main

def _parse_output(argv: list[str], stdout: str, stderr: str, overflow: bool) -> SmokeInvocationResult:
    if overflow or stderr or not argv: return SmokeInvocationResult("INTERNAL_ERROR")
    service = "tikhub" if argv[0] == "tikhub-account" else argv[0]
    expected = {"tikhub": ("status", "http_status", "account_active", "quota_present"), "paraformer": ("status", "duration", "segments", "request_id"), "volcengine": ("status", "request_id", "timing_granularity", "duration", "token_count")}.get(service)
    if expected is None: return SmokeInvocationResult("INTERNAL_ERROR")
    pairs = stdout.splitlines()
    if len(pairs) != len(expected): return SmokeInvocationResult("INTERNAL_ERROR")
    values: dict[str, str] = {}
    for line in pairs:
        if line.count("=") != 1: return SmokeInvocationResult("INTERNAL_ERROR")
        key, value = line.split("=", 1)
        if key not in expected or key in values: return SmokeInvocationResult("INTERNAL_ERROR")
        values[key] = value
    if tuple(values) != expected or values.get("status") not in _STATUSES: return SmokeInvocationResult("INTERNAL_ERROR")
    status = values["status"]; request_id = values.get("request_id", "UNAVAILABLE")
    if request_id != "UNAVAILABLE" and _REQUEST.fullmatch(request_id) is None: return SmokeInvocationResult("INTERNAL_ERROR")
    if service == "tikhub" and (not re.fullmatch(r"(?:[1-5]\d\d|UNAVAILABLE)", values["http_status"]) or values["account_active"] not in {"true", "false"} or values["quota_present"] not in {"true", "false"}): return SmokeInvocationResult("INTERNAL_ERROR")
    if service in {"paraformer", "volcengine"} and values["duration"] != "UNAVAILABLE" and re.fullmatch(r"\d+(?:\.\d+)?", values["duration"]) is None: return SmokeInvocationResult("INTERNAL_ERROR")
    if service == "paraformer" and values["segments"] != "UNAVAILABLE" and not values["segments"].isdigit(): return SmokeInvocationResult("INTERNAL_ERROR")
    if service == "volcengine" and (values["timing_granularity"] not in {"word", "UNAVAILABLE"} or (values["token_count"] != "UNAVAILABLE" and not values["token_count"].isdigit())): return SmokeInvocationResult("INTERNAL_ERROR")
    return SmokeInvocationResult(status, request_id, status in _ATTEMPTED)

def make_default_smoke_runner(root: Path, *, api_smoke_loader: Callable[[], Callable[..., int]] = _load_api_smoke_main) -> SmokeRunner:
    def runner(argv: list[str]) -> SmokeInvocationResult:
        out, err = _BoundedSink(), _BoundedSink()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                api_main = api_smoke_loader()
                kwargs = {"root": Path(root)}
                if "settings_loader" in inspect.signature(api_main).parameters:
                    kwargs["settings_loader"] = lambda local_root: _selected_api_settings(local_root, argv[0])
                return_code = api_main(argv, **kwargs)
        except Exception: return SmokeInvocationResult("INTERNAL_ERROR")
        parsed = _parse_output(argv, out.getvalue(), err.getvalue(), out.overflow or err.overflow)
        allowed_codes = {
            "OK": 0,
            "LOCAL_CONTRACT_ERROR": 1, "NETWORK_ERROR": 1, "SERVICE_ERROR": 1, "TIMEOUT": 1, "INTERNAL_ERROR": 1,
            "AUTHORIZATION_REQUIRED": 2, "CONFIG_ERROR": 2, "INPUT_ERROR": 2,
        }
        if isinstance(return_code, bool) or not isinstance(return_code, int) or allowed_codes.get(parsed.status) != return_code:
            return SmokeInvocationResult("INTERNAL_ERROR")
        return parsed
    return runner

def _safe_result(value: object) -> SmokeInvocationResult:
    status, request = getattr(value, "status", None), getattr(value, "request_id", None)
    if status not in _STATUSES or (request != "UNAVAILABLE" and (not isinstance(request, str) or _REQUEST.fullmatch(request) is None)): return SmokeInvocationResult("INTERNAL_ERROR")
    attempted = status in _ATTEMPTED
    return SmokeInvocationResult(status, request, attempted)

def _revalidate_audio(service: ServiceName, root: Path, paraformer_audio: Path | None, volcengine_audio: Path | None, probe: LockedWavProbe | None) -> str | None:
    if service == "paraformer": return None if _safe_absolute_audio_path(paraformer_audio) else "INPUT_ERROR"
    if service == "volcengine":
        if not _safe_absolute_audio_path(volcengine_audio): return "INPUT_ERROR"
        manifest = _manifest_path(volcengine_audio)
        if manifest is None: return "INPUT_ERROR"
        try: (probe or (lambda a, m: _default_locked_wav_probe(root, a, m)))(volcengine_audio, manifest)  # type: ignore[arg-type]
        except Exception: return "LOCAL_CONTRACT_ERROR"
    return None

def execute_run(*, root: Path, services: Sequence[str], paraformer_audio: Path | None = None, volcengine_audio: Path | None = None, authorized_network: bool, authorized_audio_upload: bool, settings_loader: SettingsLoader | None = None, locked_wav_probe: LockedWavProbe | None = None, smoke_runner: SmokeRunner | None = None, runner_factory: RunnerFactory = make_default_smoke_runner, now_factory: Clock = lambda: datetime.now(UTC), uuid_factory: Identifier = lambda: str(uuid.uuid4())) -> RunReport:
    canonical = _canonical_services(services)
    if not authorized_network: report = _new("run", canonical, PreflightResult("FAIL", "AUTHORIZATION_REQUIRED", _EMPTY_CREDENTIAL_STATUS), now_factory, uuid_factory)
    elif any(item in canonical for item in ("paraformer", "volcengine")) and not authorized_audio_upload: report = _new("run", canonical, PreflightResult("FAIL", "AUDIO_UPLOAD_AUTHORIZATION_REQUIRED", _EMPTY_CREDENTIAL_STATUS), now_factory, uuid_factory)
    else:
        manifest = _manifest_path(volcengine_audio) if "volcengine" in canonical else None
        identities = {"paraformer": _identity(paraformer_audio) if "paraformer" in canonical and paraformer_audio else None, "volcengine": _identity(volcengine_audio) if "volcengine" in canonical and volcengine_audio else None, "manifest": _identity(manifest) if manifest is not None else None}
        report = replace(build_plan(root=root, services=canonical, paraformer_audio=paraformer_audio, volcengine_audio=volcengine_audio, settings_loader=settings_loader, locked_wav_probe=locked_wav_probe, now_factory=now_factory, uuid_factory=uuid_factory), command="run")
        if ("paraformer" in canonical and identities["paraformer"] != _identity(paraformer_audio)) or ("volcengine" in canonical and (manifest is None or identities["volcengine"] != _identity(volcengine_audio) or identities["manifest"] != _identity(manifest))):
            report = replace(report, preflight=PreflightResult("FAIL", "INPUT_ERROR", report.preflight.credential_status))
    if report.preflight.outcome != "READY":
        identities = {"paraformer": None, "volcengine": None, "manifest": None}
    try: _, handle = _reserve_report(Path(root), report)
    except Exception: return _report_error(report)
    if report.preflight.outcome != "READY": return report if _write(handle, report) else _report_error(report)
    try:
        runner = smoke_runner or runner_factory(Path(root))
    except Exception:
        failed_report = _report_error(report)
        return failed_report if _write(handle, failed_report) else failed_report
    args = SimpleNamespace(paraformer_audio=paraformer_audio, volcengine_audio=volcengine_audio)
    results: list[ServiceResult] = []; failed = False
    for service in canonical:
        if failed: results.append(ServiceResult(service, "SKIPPED", "SKIPPED", "UNAVAILABLE", False)); continue
        local = _revalidate_audio(service, Path(root), paraformer_audio, volcengine_audio, locked_wav_probe)
        if service == "paraformer" and identities["paraformer"] != _identity(paraformer_audio): local = "INPUT_ERROR"
        if service == "volcengine" and (manifest is None or identities["volcengine"] != _identity(volcengine_audio) or identities["manifest"] != _identity(manifest)): local = "INPUT_ERROR" if manifest is None else "LOCAL_CONTRACT_ERROR"
        if local: invocation = SmokeInvocationResult(local)
        else:
            try: invocation = _safe_result(runner(SMOKE_ARGV[service](args)))
            except Exception: invocation = SmokeInvocationResult("INTERNAL_ERROR")
        outcome = "PASS" if invocation.status == "OK" else "FAIL"; failed = outcome == "FAIL"
        results.append(ServiceResult(service, outcome, invocation.status, invocation.request_id, invocation.network_attempted))
    completed = replace(report, service_results=tuple(results), network_attempted=any(item.network_attempted for item in results), fail_closed_early=any(item.outcome == "SKIPPED" for item in results))
    return completed if _write(handle, completed) else _report_error(completed)

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded, opt-in BoomEarth joint live smokes"); sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "run"):
        item = sub.add_parser(command); item.add_argument("--services", nargs="+", required=True, choices=SERVICE_ORDER); item.add_argument("--paraformer-audio", type=Path); item.add_argument("--volcengine-audio", type=Path); item.add_argument("--workspace-root", type=Path, default=ROOT)
        if command == "run": item.add_argument("--authorized-network", action="store_true"); item.add_argument("--authorized-audio-upload", action="store_true")
    return parser

def _persist_report(root: Path, report: RunReport) -> bool:
    try: _, handle = _reserve_report(root, report)
    except Exception: return False
    return _write(handle, report)

def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser(); args = parser.parse_args(list(argv) if argv is not None else None)
    try: _canonical_services(args.services)
    except ValueError as error: parser.error(str(error))
    if args.command == "plan":
        report = build_plan(root=args.workspace_root, services=args.services, paraformer_audio=args.paraformer_audio, volcengine_audio=args.volcengine_audio)
        if not _persist_report(args.workspace_root, report): report = _report_error(report)
    else: report = execute_run(root=args.workspace_root, services=args.services, paraformer_audio=args.paraformer_audio, volcengine_audio=args.volcengine_audio, authorized_network=args.authorized_network, authorized_audio_upload=args.authorized_audio_upload)
    print(json.dumps(report.payload(), sort_keys=True, separators=(",", ":")))
    if report.preflight.outcome != "READY": return 2
    return 0 if report.command == "plan" or all(item.outcome == "PASS" for item in report.service_results) else 1

if __name__ == "__main__": raise SystemExit(main())

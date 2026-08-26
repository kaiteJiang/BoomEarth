"""Versioned, source-redacted P2 stage event ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from uuid import UUID

if os.name == "nt":
    import msvcrt

from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.source_artifacts import canonical_json_bytes


LEDGER_FILENAME = "wash-ledger.jsonl"
LOCK_FILENAME = "wash-ledger.lock"
LOCK_TIMEOUT_SECONDS = 1.0
LOCK_INITIAL_DELAY_SECONDS = 0.025
LOCK_MAX_DELAY_SECONDS = 0.100
LEDGER_THREAD_LOCK = threading.Lock()

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_STAGES = frozenset(
    {
        "source_registered",
        "github_skill_planned",
        "github_skill_ready",
        "article_ready",
        "media_ready",
        "audio_ready",
        "transcript_ready",
        "rewrite_ready",
        "handoff_ready",
        "production_started",
        "production_archived",
        "skipped",
    }
)
_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"source_registered"}),
    "source_registered": frozenset({"article_ready", "media_ready", "skipped"}),
    "github_skill_planned": frozenset({"github_skill_ready", "skipped"}),
    "github_skill_ready": frozenset({"rewrite_ready", "skipped"}),
    "article_ready": frozenset({"rewrite_ready", "skipped"}),
    "media_ready": frozenset({"audio_ready", "skipped"}),
    "audio_ready": frozenset({"transcript_ready", "skipped"}),
    "transcript_ready": frozenset({"rewrite_ready", "skipped"}),
    "rewrite_ready": frozenset({"handoff_ready", "skipped"}),
    "handoff_ready": frozenset({"production_started", "skipped"}),
    "production_started": frozenset({"production_archived", "skipped"}),
    "production_archived": frozenset(),
    "skipped": frozenset(),
}


class SourceLedgerError(ValueError):
    """A fixed-message private ledger failure."""

    def __repr__(self) -> str:
        return "SourceLedgerError(<redacted>)"


def _is_uuid4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None


@dataclass(frozen=True, slots=True, repr=False)
class StageEvent:
    schema_version: int
    work_id: str
    source_id: str
    source_kind: str
    stage: str
    result: str
    artifact_label: str
    artifact_sha256: str
    timestamp: str
    previous_event_sha256: str | None

    KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "schema_version",
            "work_id",
            "source_id",
            "source_kind",
            "stage",
            "result",
            "artifact_label",
            "artifact_sha256",
            "timestamp",
            "previous_event_sha256",
        }
    )

    def __repr__(self) -> str:
        return "StageEvent(<redacted>)"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "work_id": self.work_id,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "stage": self.stage,
            "result": self.result,
            "artifact_label": self.artifact_label,
            "artifact_sha256": self.artifact_sha256,
            "timestamp": self.timestamp,
            "previous_event_sha256": self.previous_event_sha256,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StageEvent":
        if not isinstance(value, dict) or set(value) != set(cls.KEYS):
            raise SourceLedgerError("stage-event-invalid")
        if not (
            type(value["schema_version"]) is int
            and value["schema_version"] == 2
            and _is_uuid4(value["work_id"])
            and _is_digest(value["source_id"])
            and value["source_kind"] in {"local", "url", "x-article", "github-skill"}
            and value["stage"] in _STAGES
            and value["result"] in {"ok", "skipped"}
            and isinstance(value["artifact_label"], str)
            and _LABEL_RE.fullmatch(value["artifact_label"]) is not None
            and _is_digest(value["artifact_sha256"])
            and _is_timestamp(value["timestamp"])
            and (
                value["previous_event_sha256"] is None
                or _is_digest(value["previous_event_sha256"])
            )
        ):
            raise SourceLedgerError("stage-event-invalid")
        return cls(**value)  # type: ignore[arg-type]


def event_sha256(event: StageEvent) -> str:
    return hashlib.sha256(canonical_json_bytes(event.to_dict())).hexdigest()


def _transition_allowed(previous: StageEvent | None, event: StageEvent) -> bool:
    if previous is None:
        return event.stage == "source_registered"
    if previous.source_kind == "github-skill":
        if previous.stage == "source_registered":
            return event.stage in {"github_skill_planned", "skipped"}
        if previous.stage == "github_skill_planned":
            return event.stage in {"github_skill_ready", "skipped"}
        if previous.stage == "github_skill_ready":
            return event.stage in {"rewrite_ready", "skipped"}
        if event.stage in {
            "article_ready",
            "media_ready",
            "audio_ready",
            "transcript_ready",
            "github_skill_planned",
            "github_skill_ready",
        }:
            return False
    elif previous.source_kind == "x-article":
        if previous.stage == "source_registered":
            return event.stage in {"article_ready", "skipped"}
        if previous.stage == "article_ready":
            return event.stage in {"rewrite_ready", "skipped"}
        if event.stage in {"media_ready", "audio_ready", "transcript_ready", "article_ready"}:
            return False
    elif event.stage in {"article_ready", "github_skill_planned", "github_skill_ready"}:
        return False
    return event.stage in _TRANSITIONS[previous.stage]


def _retry_ledger_lock(
    locking,
    *,
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> None:
    deadline = monotonic() + LOCK_TIMEOUT_SECONDS
    delay = LOCK_INITIAL_DELAY_SECONDS
    while True:
        try:
            locking()
            return
        except OSError:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise SourceLedgerError("ledger-lock-unavailable") from None
            sleep(min(delay, remaining))
            delay = min(delay * 2, LOCK_MAX_DELAY_SECONDS)


@contextmanager
def _ledger_lock(lock_path: Path):
    with LEDGER_THREAD_LOCK:
        if os.name != "nt":
            yield
            return
        lock_file = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = lock_path.open("a+b")
            lock_file.seek(0, 2)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            _retry_ledger_lock(
                lambda: msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            )
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except SourceLedgerError:
            raise
        except OSError:
            raise SourceLedgerError("ledger-lock-unavailable") from None
        finally:
            if lock_file is not None:
                try:
                    lock_file.close()
                except OSError:
                    pass


def _legacy_row(value: object) -> str | None:
    if (
        isinstance(value, dict)
        and set(value) == {"id", "status"}
        and value["status"] == "present"
        and _is_digest(value["id"])
    ):
        return value["id"]
    return None


def _read_rows(path: Path) -> tuple[list[str], list[StageEvent]]:
    if not path.exists():
        return [], []
    legacy: list[str] = []
    events: list[StageEvent] = []
    last_by_work: dict[str, StageEvent] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            for line in stream:
                if not line.strip():
                    raise SourceLedgerError("ledger-malformed")
                value = json.loads(line)
                legacy_id = _legacy_row(value)
                if legacy_id is not None:
                    legacy.append(legacy_id)
                    continue
                event = StageEvent.from_dict(value)
                previous = last_by_work.get(event.work_id)
                if not _transition_allowed(previous, event):
                    raise SourceLedgerError("ledger-malformed")
                expected_previous = event_sha256(previous) if previous else None
                if event.previous_event_sha256 != expected_previous:
                    raise SourceLedgerError("ledger-malformed")
                if previous and (
                    event.source_id != previous.source_id
                    or event.source_kind != previous.source_kind
                ):
                    raise SourceLedgerError("ledger-malformed")
                events.append(event)
                last_by_work[event.work_id] = event
    except SourceLedgerError:
        raise SourceLedgerError("ledger-malformed") from None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SourceLedgerError("ledger-malformed") from None
    return legacy, events


def read_source_identifiers(path: Path) -> set[str]:
    legacy, events = _read_rows(path)
    return set(legacy) | {event.source_id for event in events}


class WashEventLedger:
    """Append legal, hash-chained P2 stage events to the private wash ledger."""

    def __init__(self, root: Path) -> None:
        private_wash = WorkbenchPaths(root).private_wash
        self.ledger_path = private_wash / LEDGER_FILENAME
        self._lock_path = private_wash / LOCK_FILENAME

    def append(self, event: StageEvent) -> bool:
        if not isinstance(event, StageEvent):
            raise SourceLedgerError("stage-event-invalid")
        StageEvent.from_dict(event.to_dict())
        with _ledger_lock(self._lock_path):
            _, events = _read_rows(self.ledger_path)
            work_events = [item for item in events if item.work_id == event.work_id]
            previous = work_events[-1] if work_events else None
            if previous is not None and event.to_dict() == previous.to_dict():
                return False
            if previous is not None and event.stage == previous.stage:
                raise SourceLedgerError("stage-event-conflict")
            if not _transition_allowed(previous, event):
                raise SourceLedgerError("stage-transition-invalid")
            expected_previous = event_sha256(previous) if previous else None
            if event.previous_event_sha256 != expected_previous:
                raise SourceLedgerError("stage-chain-invalid")
            if previous is not None and (
                event.source_id != previous.source_id
                or event.source_kind != previous.source_kind
            ):
                raise SourceLedgerError("stage-event-conflict")
            try:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with self.ledger_path.open("ab") as stream:
                    stream.write(canonical_json_bytes(event.to_dict()))
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                raise SourceLedgerError("ledger-unavailable") from None
            return True

    def status(self, work_id: str) -> str:
        return self.current(work_id).stage

    def current(self, work_id: str) -> StageEvent:
        if not _is_uuid4(work_id):
            raise SourceLedgerError("work-id-invalid")
        with _ledger_lock(self._lock_path):
            _, events = _read_rows(self.ledger_path)
            work_events = [event for event in events if event.work_id == work_id]
        if not work_events:
            raise SourceLedgerError("work-not-found")
        return work_events[-1]


__all__ = [
    "LEDGER_THREAD_LOCK",
    "SourceLedgerError",
    "StageEvent",
    "WashEventLedger",
    "event_sha256",
    "read_source_identifiers",
]

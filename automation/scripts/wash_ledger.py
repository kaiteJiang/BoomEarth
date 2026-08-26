"""Track washed source URLs without retaining source content."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, NoReturn, Sequence
from urllib.parse import urlsplit

if os.name == "nt":
    import msvcrt


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = SCRIPT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boomearth.workbench.paths import WorkbenchPaths  # noqa: E402
from boomearth.workbench.source_ledger import (  # noqa: E402
    LEDGER_THREAD_LOCK,
    SourceLedgerError,
    WashEventLedger,
    read_source_identifiers,
)


LEDGER_FILENAME = "wash-ledger.jsonl"
LOCK_FILENAME = "wash-ledger.lock"
LEDGER_STATUS = "present"
INVALID_URL_ERROR = "error=invalid-url"
MALFORMED_LEDGER_ERROR = "error=malformed-ledger"
LEDGER_UNAVAILABLE_ERROR = "error=ledger-unavailable"
LEDGER_LOCK_ERROR = "error=ledger-lock-unavailable"
INVALID_ARGUMENTS_ERROR = "error=invalid-arguments"
LOCK_TIMEOUT_SECONDS = 1.0
LOCK_INITIAL_DELAY_SECONDS = 0.025
LOCK_MAX_DELAY_SECONDS = 0.100
_LEDGER_THREAD_LOCK = LEDGER_THREAD_LOCK


class WashLedgerError(ValueError):
    """A fixed, source-redacted wash-ledger error."""


def _retry_ledger_lock(
    locking: Callable[[], None],
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
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
                raise WashLedgerError(LEDGER_LOCK_ERROR) from None
            sleep(min(delay, remaining))
            delay = min(delay * 2, LOCK_MAX_DELAY_SECONDS)


def _normalize_url(url: str) -> str:
    if not isinstance(url, str):
        raise WashLedgerError(INVALID_URL_ERROR)

    normalized = url.strip()
    if not normalized:
        raise WashLedgerError(INVALID_URL_ERROR)

    try:
        parsed = urlsplit(normalized)
    except ValueError:
        raise WashLedgerError(INVALID_URL_ERROR) from None

    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise WashLedgerError(INVALID_URL_ERROR)
    return normalized


def _url_digest(url: str) -> str:
    normalized = _normalize_url(url)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _close_lock_file(lock_file) -> None:
    try:
        lock_file.close()
    except OSError:
        pass


def _unlock_lock_file(lock_file) -> None:
    try:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        lock_file.close()
    except OSError:
        _close_lock_file(lock_file)
        raise WashLedgerError(LEDGER_LOCK_ERROR) from None


@contextmanager
def _ledger_lock(lock_path: Path):
    with _LEDGER_THREAD_LOCK:
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
        except OSError:
            if lock_file is not None:
                _close_lock_file(lock_file)
            raise WashLedgerError(LEDGER_LOCK_ERROR) from None

        try:
            _retry_ledger_lock(
                lambda: msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1),
            )
        except BaseException:
            _close_lock_file(lock_file)
            raise

        try:
            yield
        finally:
            _unlock_lock_file(lock_file)


class WashLedger:
    """A JSONL ledger containing only URL digests and a fixed status."""

    def __init__(self, root: Path) -> None:
        private_wash = WorkbenchPaths(root).private_wash
        self.ledger_path = private_wash / LEDGER_FILENAME
        self._lock_path = private_wash / LOCK_FILENAME

    def check(self, url: str) -> bool:
        """Return whether the normalized URL is already in the ledger."""
        digest = _url_digest(url)
        with _ledger_lock(self._lock_path):
            return digest in self._read_digests()

    def add(self, url: str) -> bool:
        """Add a URL digest once and return whether a row was appended."""
        digest = _url_digest(url)
        with _ledger_lock(self._lock_path):
            digests = self._read_digests()
            if digest in digests:
                return False

            try:
                self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with self.ledger_path.open("a", encoding="utf-8", newline="") as ledger:
                    record = {"id": digest, "status": LEDGER_STATUS}
                    ledger.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")))
                    ledger.write("\n")
            except OSError:
                raise WashLedgerError(LEDGER_UNAVAILABLE_ERROR) from None
            return True

    def _read_digests(self) -> set[str]:
        try:
            return read_source_identifiers(self.ledger_path)
        except (OSError, SourceLedgerError):
            raise WashLedgerError(MALFORMED_LEDGER_ERROR) from None


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise WashLedgerError(INVALID_ARGUMENTS_ERROR)


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="wash_ledger.py",
        description="Check or add a redacted wash-ledger URL identifier.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check_parser = commands.add_parser("check", help="check a URL identifier")
    check_parser.add_argument("url", help="HTTP(S) URL")

    add_parser = commands.add_parser("add", help="add a URL identifier")
    add_parser.add_argument("url", help="HTTP(S) URL")

    status_parser = commands.add_parser("status", help="show one P2 work stage")
    status_parser.add_argument("work_id", help="UUID4 work identifier")
    return parser


def main(argv: Sequence[str] | None = None, *, root: Path = SCRIPT_ROOT) -> int:
    parser = _build_parser()
    try:
        arguments = parser.parse_args(argv)
        ledger = WashLedger(root)

        if arguments.command == "status":
            safe_id = arguments.work_id.replace("-", "")[:12]
            try:
                stage = WashEventLedger(root).status(arguments.work_id)
                status = "present"
                exit_code = 0
            except SourceLedgerError as error:
                if str(error) != "work-not-found":
                    raise WashLedgerError(LEDGER_UNAVAILABLE_ERROR) from None
                stage = "missing"
                status = "missing"
                exit_code = 1
            print(f"work={safe_id} stage={stage} status={status}")
            return exit_code

        if arguments.command == "check":
            present = ledger.check(arguments.url)
            status = "present" if present else "missing"
            exit_code = 0 if present else 1
        else:
            added = ledger.add(arguments.url)
            status = "added" if added else "duplicate"
            exit_code = 0

        digest = _url_digest(arguments.url)
        print(f"id={digest[:12]} status={status}")
        return exit_code
    except WashLedgerError as error:
        print(str(error), file=sys.stderr)
        return 2
    except ValueError:
        print(LEDGER_UNAVAILABLE_ERROR, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

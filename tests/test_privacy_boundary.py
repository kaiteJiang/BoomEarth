import hashlib
import importlib.util
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import unicodedata
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError, fields
from io import StringIO
from pathlib import Path
import re

import pytest

from boomearth.audio.indextts2 import CURRENT_VOICE_ID
from boomearth.contracts import HandoffContract
from boomearth.workbench.handoff import validate_public_handoff
from boomearth.workbench.paths import WorkbenchPaths


HANDOFF_FIELDS = (
    "status",
    "platform",
    "ratio",
    "duration_target_s",
    "word_count",
    "voice",
    "voice_provider",
    "captions",
    "caption_style",
    "visual",
    "illustration_skill",
    "archive_slug",
)
TEST_URL = "https://example.invalid/fictitious-source-42"
TEST_TITLE = "明显虚构的测试标题-仅供测试"
SOURCE_PHRASE = "明显虚构的源短语-仅供测试"
MISSING_URL = "https://example.invalid/missing-fictitious-source-43"
INVALID_URL = "not-a-url"
INVALID_URL_ERROR = "error=invalid-url"
MALFORMED_LEDGER_ERROR = "error=malformed-ledger"
LEDGER_UNAVAILABLE_ERROR = "error=ledger-unavailable"
WASH_LEDGER_SCRIPT = Path(__file__).resolve().parents[1] / "automation" / "scripts" / "wash_ledger.py"

_WASH_LEDGER_SPEC = importlib.util.spec_from_file_location(
    "boomearth_wash_ledger_under_test",
    WASH_LEDGER_SCRIPT,
)
assert _WASH_LEDGER_SPEC is not None
assert _WASH_LEDGER_SPEC.loader is not None
wash_ledger = importlib.util.module_from_spec(_WASH_LEDGER_SPEC)
sys.modules[_WASH_LEDGER_SPEC.name] = wash_ledger
_WASH_LEDGER_SPEC.loader.exec_module(wash_ledger)
WashLedger = wash_ledger.WashLedger
WashLedgerError = wash_ledger.WashLedgerError
HANDOFF_ERROR_RE = re.compile(
    r"line \d+: (?:URL scheme|source metadata key|\.internal path|"
    r"transcript path marker|source phrase)"
)


def _assert_redacted_errors(errors: list[str]) -> None:
    assert all(HANDOFF_ERROR_RE.fullmatch(error) for error in errors)
    assert all(TEST_URL not in error for error in errors)
    assert all(TEST_TITLE not in error for error in errors)
    assert all(SOURCE_PHRASE not in error for error in errors)


def test_handoff_contract_has_exact_frozen_field_order() -> None:
    assert tuple(field.name for field in fields(HandoffContract)) == HANDOFF_FIELDS
    assert HandoffContract.required_fields() == HANDOFF_FIELDS

    contract = HandoffContract(
        status="draft",
        platform="fictional-platform",
        ratio="9:16",
        duration_target_s=10.0,
        word_count=42,
        voice="fictional-voice",
        voice_provider="local",
        captions="word-level",
        caption_style="fictional-style",
        visual="fictional-visual",
        illustration_skill="fictional-skill",
        archive_slug="fictional-project",
    )
    with pytest.raises(FrozenInstanceError):
        contract.status = "changed"  # type: ignore[misc]


def test_create_skeleton_creates_all_planned_directories_and_private_wash_location(
    tmp_path: Path,
) -> None:
    paths = WorkbenchPaths(tmp_path)
    expected_directories = (
        paths.content_root,
        paths.topic_pool,
        paths.benchmark_library,
        paths.material_library,
        paths.data_stats,
        paths.workbench_root,
        paths.pending,
        paths.active,
        paths.archived,
        paths.private_wash,
        paths.components_root,
    )

    assert paths.create_skeleton() == expected_directories
    assert all(directory.is_dir() for directory in expected_directories)
    assert paths.private_wash == paths.workbench_root / ".internal" / "洗稿"


@pytest.fixture
def symlinked_internal_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    workbench_root = root / "01-内容生产" / "视频工作台"
    workbench_root.mkdir(parents=True)

    try:
        (workbench_root / ".internal").symlink_to(
            outside,
            target_is_directory=True,
        )
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are not supported or permitted")

    return root, outside


def test_private_source_rejects_symlinked_internal_root(
    symlinked_internal_root: tuple[Path, Path],
) -> None:
    root, _ = symlinked_internal_root

    with pytest.raises(ValueError):
        WorkbenchPaths(root).private_source("probe.txt")


def test_create_skeleton_rejects_symlinked_internal_root_without_writing_outside(
    symlinked_internal_root: tuple[Path, Path],
) -> None:
    root, outside = symlinked_internal_root

    with pytest.raises(Exception):
        WorkbenchPaths(root).create_skeleton()

    assert not (outside / "洗稿").exists()


@pytest.mark.parametrize(
    ("state", "root_name"),
    [
        ("pending", "pending"),
        ("active", "active"),
        ("archived", "archived"),
    ],
    ids=["pending", "active", "archived"],
)
def test_public_project_places_each_state_under_its_public_root(
    tmp_path: Path,
    state: str,
    root_name: str,
) -> None:
    paths = WorkbenchPaths(tmp_path)
    project = paths.public_project(state, "fictional-project")
    public_root = getattr(paths, root_name)

    assert project == public_root / "fictional-project"
    assert project.is_relative_to(public_root)


def test_private_source_places_safe_relative_path_under_private_wash(
    tmp_path: Path,
) -> None:
    paths = WorkbenchPaths(tmp_path)
    source = paths.private_source("media/fictional-source.mp4")

    assert source == paths.private_wash / "media" / "fictional-source.mp4"
    assert source.is_relative_to(paths.private_wash)


@pytest.mark.parametrize("relative", ["", "   "], ids=["empty", "whitespace"])
def test_public_project_rejects_empty_relative_values(
    tmp_path: Path,
    relative: str,
) -> None:
    with pytest.raises(ValueError):
        WorkbenchPaths(tmp_path).public_project("pending", relative)


@pytest.mark.parametrize("relative", ["", "   "], ids=["empty", "whitespace"])
def test_private_source_rejects_empty_relative_values(
    tmp_path: Path,
    relative: str,
) -> None:
    with pytest.raises(ValueError):
        WorkbenchPaths(tmp_path).private_source(relative)


def test_public_project_rejects_unknown_state(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        WorkbenchPaths(tmp_path).public_project("unknown-state", "fictional-project")


@pytest.mark.parametrize(
    "relative",
    [
        "..",
        "../escape",
        r"..\escape",
        r"nested/..\escape",
        r"nested\../escape",
        r"C:\absolute\fixture",
        r"C:escape",
    ],
    ids=[
        "parent",
        "slash-traversal",
        "backslash-traversal",
        "mixed-traversal-forward-first",
        "mixed-traversal-backslash-first",
        "absolute-windows",
        "drive-relative",
    ],
)
def test_public_project_rejects_unsafe_relative_paths(
    tmp_path: Path,
    relative: str,
) -> None:
    with pytest.raises(ValueError):
        WorkbenchPaths(tmp_path).public_project("pending", relative)


@pytest.mark.parametrize(
    "relative",
    [
        "..",
        "../escape",
        r"..\escape",
        r"nested/..\escape",
        r"nested\../escape",
        r"C:\absolute\fixture",
        r"C:escape",
    ],
    ids=[
        "parent",
        "slash-traversal",
        "backslash-traversal",
        "mixed-traversal-forward-first",
        "mixed-traversal-backslash-first",
        "absolute-windows",
        "drive-relative",
    ],
)
def test_private_source_rejects_unsafe_relative_paths(
    tmp_path: Path,
    relative: str,
) -> None:
    with pytest.raises(ValueError):
        WorkbenchPaths(tmp_path).private_source(relative)


@pytest.mark.parametrize("relative", [None, 42, Path("fictional")], ids=["none", "integer", "path"])
def test_path_helpers_reject_non_string_relative_values(
    tmp_path: Path,
    relative: object,
) -> None:
    paths = WorkbenchPaths(tmp_path)

    with pytest.raises(TypeError):
        paths.public_project("pending", relative)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        paths.private_source(relative)  # type: ignore[arg-type]


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path semantics required")
@pytest.mark.parametrize(
    ("root", "child"),
    [
        (Path(r"C:\repo"), Path(r"\\?\C:\repo\child")),
        (Path(r"\\server\share\repo"), Path(r"\\?\UNC\server\share\repo\child")),
    ],
    ids=["extended-drive", "extended-unc"],
)
def test_containment_handles_windows_extended_path_equivalence(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    child: Path,
) -> None:
    def identity_resolve(path: Path, strict: bool = False) -> Path:
        return path

    monkeypatch.setattr(Path, "resolve", identity_resolve)
    instance = object.__new__(WorkbenchPaths)
    object.__setattr__(instance, "root", root)

    assert instance._ensure_contained(child) == child


def test_valid_public_handoff_returns_no_violations() -> None:
    text = "状态：ready\n平台：fictional-platform\n备注：仅用于测试"

    assert validate_public_handoff(text) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"参考网址：{TEST_URL}", ["line 1: URL scheme"]),
        (f"source_title: {TEST_TITLE}", ["line 1: source metadata key"]),
        ("artifact path: .internal/洗稿/input.md", ["line 1: .internal path"]),
        ("artifact path: 逐字稿/片段.txt", ["line 1: transcript path marker"]),
        ("artifact path: transcript/segment.txt", ["line 1: transcript path marker"]),
        (f"备注：{SOURCE_PHRASE}", ["line 1: source phrase"]),
    ],
    ids=["url", "source-metadata", "internal-path", "chinese-transcript-path", "transcript-path", "source-phrase"],
)
def test_public_handoff_rejects_each_privacy_violation(
    text: str,
    expected: list[str],
) -> None:
    errors = validate_public_handoff(text, source_phrases=(SOURCE_PHRASE,))

    assert errors == expected
    _assert_redacted_errors(errors)


def test_same_line_privacy_violations_are_stable_and_fixed_order() -> None:
    text = (
        "交接稿：fictional-project\n"
        f"source_title: {TEST_TITLE} | {TEST_URL} | "
        f".internal/洗稿/transcript/segment.txt | {SOURCE_PHRASE}"
    )
    expected = [
        "line 2: URL scheme",
        "line 2: source metadata key",
        "line 2: .internal path",
        "line 2: transcript path marker",
        "line 2: source phrase",
    ]

    errors = validate_public_handoff(text, source_phrases=(SOURCE_PHRASE,))

    assert errors == expected
    assert validate_public_handoff(text, source_phrases=(SOURCE_PHRASE,)) == errors
    _assert_redacted_errors(errors)


def test_public_handoff_rejects_protocol_relative_source_url_without_echoing_it() -> None:
    source_url = "//source.example.invalid/video"

    errors = validate_public_handoff(f"参考网址：{source_url}")

    assert errors == ["line 1: URL scheme"]
    assert all(source_url not in error for error in errors)
    _assert_redacted_errors(errors)


def test_public_handoff_rejects_source_phrase_with_zero_width_insertion() -> None:
    handoff_phrase = f"{SOURCE_PHRASE[:4]}\u200b{SOURCE_PHRASE[4:]}"

    errors = validate_public_handoff(
        f"备注：{handoff_phrase}",
        source_phrases=(SOURCE_PHRASE,),
    )

    assert errors == ["line 1: source phrase"]
    _assert_redacted_errors(errors)


def test_public_handoff_rejects_nfkc_fullwidth_source_phrase() -> None:
    handoff_phrase = "明显虚构的源短语－仅供测试"
    assert unicodedata.normalize("NFKC", handoff_phrase) == SOURCE_PHRASE

    errors = validate_public_handoff(
        f"备注：{handoff_phrase}",
        source_phrases=(SOURCE_PHRASE,),
    )

    assert errors == ["line 1: source phrase"]
    _assert_redacted_errors(errors)


def test_public_handoff_rejects_source_phrase_split_across_lines_with_start_line() -> None:
    split_at = len(SOURCE_PHRASE) // 2
    text = f"备注：{SOURCE_PHRASE[:split_at]}\n{SOURCE_PHRASE[split_at:]}"

    errors = validate_public_handoff(text, source_phrases=(SOURCE_PHRASE,))

    assert errors == ["line 1: source phrase"]
    _assert_redacted_errors(errors)


@pytest.mark.parametrize("value", [None, 42, b"fictional"], ids=["none", "integer", "bytes"])
def test_handoff_text_rejects_non_string_values(value: object) -> None:
    with pytest.raises(TypeError):
        validate_public_handoff(value)  # type: ignore[arg-type]


def test_handoff_source_phrases_reject_non_string_values() -> None:
    with pytest.raises(TypeError):
        validate_public_handoff("fictional handoff", source_phrases=(SOURCE_PHRASE, 42))  # type: ignore[list-item]


def _expected_wash_digest(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def _spawn_wash_ledger_add(
    root: str,
    url: str,
    ready_queue,
    release_event,
    result_queue,
) -> None:
    ledger = WashLedger(Path(root))
    ready_queue.put(True)
    release_event.wait()
    try:
        result_queue.put(ledger.add(url))
    except WashLedgerError as error:
        result_queue.put(str(error))


def _hold_wash_ledger_lock(
    lock_path: str,
    ready_queue,
    release_event,
) -> None:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        lock_file.seek(0, 2)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        wash_ledger.msvcrt.locking(
            lock_file.fileno(),
            wash_ledger.msvcrt.LK_NBLCK,
            1,
        )
        ready_queue.put(True)
        release_event.wait()
        lock_file.seek(0)
        wash_ledger.msvcrt.locking(
            lock_file.fileno(),
            wash_ledger.msvcrt.LK_UNLCK,
            1,
        )


def _run_wash_ledger_cli(
    root: Path,
    command: str,
    url: str,
) -> subprocess.CompletedProcess[str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        returncode = wash_ledger.main([command, url], root=root)
    return subprocess.CompletedProcess(
        args=[command, url],
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
    )


def _assert_no_wash_ledger_leaks(output: str, *urls: str) -> None:
    assert TEST_URL not in output
    assert TEST_TITLE not in output
    for url in urls:
        if url.strip():
            assert _expected_wash_digest(url) not in output


def test_lock_retry_succeeds_after_transient_contention() -> None:
    outcomes = iter([OSError(), OSError(), None])
    attempts = 0
    now = 0.0
    sleeps: list[float] = []

    def locking() -> None:
        nonlocal attempts
        attempts += 1
        outcome = next(outcomes)
        if outcome is not None:
            raise outcome

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    helper = getattr(wash_ledger, "_retry_ledger_lock", None)
    assert helper is not None
    helper(locking, monotonic=monotonic, sleep=sleep)

    assert attempts == 3
    assert sleeps == [0.025, 0.05]


def test_lock_retry_stops_at_deadline_without_oversleep() -> None:
    attempts = 0
    now = 0.0
    sleeps: list[float] = []

    def locking() -> None:
        nonlocal attempts
        attempts += 1
        raise OSError()

    def monotonic() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    helper = getattr(wash_ledger, "_retry_ledger_lock", None)
    assert helper is not None
    with pytest.raises(WashLedgerError, match="ledger-lock-unavailable"):
        helper(locking, monotonic=monotonic, sleep=sleep)

    assert sleeps[:-1] == [0.025, 0.05] + [0.1] * 9
    assert sleeps[-1] == pytest.approx(0.025)
    assert sum(sleeps) == pytest.approx(1.0)
    assert attempts == len(sleeps) + 1


def test_lock_retry_returns_without_sleep_when_lock_is_immediately_available() -> None:
    sleeps: list[float] = []

    helper = getattr(wash_ledger, "_retry_ledger_lock", None)
    assert helper is not None
    helper(lambda: None, monotonic=lambda: 0.0, sleep=sleeps.append)

    assert sleeps == []


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt locking semantics required")
def test_ledger_lock_uses_nonblocking_acquisition_and_preserves_unlock_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    modes: list[int] = []

    def record_locking(file_descriptor: int, mode: int, byte_count: int) -> None:
        modes.append(mode)

    monkeypatch.setattr(wash_ledger.msvcrt, "locking", record_locking)

    with wash_ledger._ledger_lock(tmp_path / "wash-ledger.lock"):
        pass

    assert modes == [wash_ledger.msvcrt.LK_NBLCK, wash_ledger.msvcrt.LK_UNLCK]


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt locking semantics required")
def test_ledger_lock_closes_file_and_preserves_unexpected_acquisition_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tracked_path = tmp_path / "tracked-lock-file"
    lock_file = tracked_path.open("a+b")

    def return_lock_file(self: Path, *args: object, **kwargs: object):
        return lock_file

    def fail_unexpectedly(*args: object) -> None:
        raise RuntimeError("unexpected-lock-error")

    monkeypatch.setattr(Path, "open", return_lock_file)
    monkeypatch.setattr(wash_ledger.msvcrt, "locking", fail_unexpectedly)

    try:
        with pytest.raises(RuntimeError, match="^unexpected-lock-error$") as raised:
            with wash_ledger._ledger_lock(tmp_path / "wash-ledger.lock"):
                pass

        assert str(raised.value) == "unexpected-lock-error"
        assert lock_file.closed
    finally:
        if not lock_file.closed:
            lock_file.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt locking semantics required")
def test_ledger_lock_expires_during_sustained_process_contention(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    release_event = context.Event()
    lock_path = WashLedger(tmp_path)._lock_path
    holder = context.Process(
        target=_hold_wash_ledger_lock,
        args=(str(lock_path), ready_queue, release_event),
    )

    try:
        holder.start()
        ready_queue.get(timeout=10)
        release_timer = threading.Timer(2.25, release_event.set)
        release_timer.start()
        started = time.monotonic()

        with pytest.raises(WashLedgerError) as raised:
            with wash_ledger._ledger_lock(lock_path):
                pass

        elapsed = time.monotonic() - started
        assert str(raised.value) == "error=ledger-lock-unavailable"
        assert elapsed < 2.0
    finally:
        release_event.set()
        holder.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
        assert not holder.is_alive()


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt locking semantics required")
def test_ledger_lock_succeeds_when_process_contention_releases_before_deadline(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    release_event = context.Event()
    lock_path = WashLedger(tmp_path)._lock_path
    holder = context.Process(
        target=_hold_wash_ledger_lock,
        args=(str(lock_path), ready_queue, release_event),
    )

    try:
        holder.start()
        ready_queue.get(timeout=10)
        release_timer = threading.Timer(0.25, release_event.set)
        release_timer.start()
        started = time.monotonic()

        with wash_ledger._ledger_lock(lock_path):
            pass

        assert time.monotonic() - started < 2.0
    finally:
        release_event.set()
        holder.join(timeout=5)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
        assert not holder.is_alive()


def test_wash_ledger_add_is_idempotent_and_stores_only_redacted_fields(tmp_path: Path) -> None:
    ledger = WashLedger(tmp_path)
    padded_url = f"  {TEST_URL}\n"

    assert ledger.add(padded_url) is True
    assert ledger.add(TEST_URL) is False
    assert ledger.check(TEST_URL) is True
    assert ledger.check(padded_url) is True

    ledger_lines = ledger.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    record = json.loads(ledger_lines[0])
    assert set(record) == {"id", "status"}
    assert record == {"id": _expected_wash_digest(TEST_URL), "status": "present"}

    ledger_text = ledger.ledger_path.read_text(encoding="utf-8")
    assert TEST_URL not in ledger_text
    assert TEST_TITLE not in ledger_text


def test_wash_ledger_add_is_atomic_under_threads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_read_digests = WashLedger._read_digests

    def delayed_read_digests(self) -> set[str]:
        digests = original_read_digests(self)
        time.sleep(0.1)
        return digests

    monkeypatch.setattr(WashLedger, "_read_digests", delayed_read_digests)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(WashLedger(tmp_path).add, TEST_URL)
            for _ in range(8)
        ]
        outcomes = [future.result() for future in futures]

    ledger_path = WashLedger(tmp_path).ledger_path
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt locking semantics required")
def test_wash_ledger_add_is_atomic_across_spawned_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    release_event = context.Event()
    ready_queue = context.Queue()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_spawn_wash_ledger_add,
            args=(str(tmp_path), TEST_URL, ready_queue, release_event, result_queue),
        )
        for _ in range(6)
    ]
    started_processes = []

    try:
        for process in processes:
            process.start()
            started_processes.append(process)

        for _ in started_processes:
            ready_queue.get(timeout=10)
        release_event.set()

        outcomes = [
            result_queue.get(timeout=10)
            for _ in started_processes
        ]
        for process in started_processes:
            process.join(timeout=10)
            assert not process.is_alive()
            assert process.exitcode == 0

        ledger_path = WashLedger(tmp_path).ledger_path
        assert outcomes.count(True) == 1
        assert outcomes.count(False) == 5
        ledger_text = ledger_path.read_text(encoding="utf-8")
        records = [json.loads(line) for line in ledger_text.splitlines()]
        assert len(records) == 1
        assert all(set(record) == {"id", "status"} for record in records)
        assert all(record["status"] == "present" for record in records)
        assert TEST_URL not in ledger_text
    finally:
        release_event.set()
        for process in started_processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            assert not process.is_alive()


@pytest.mark.parametrize("url", [INVALID_URL, "", "   "], ids=["invalid", "empty", "whitespace"])
def test_wash_ledger_rejects_invalid_urls_with_fixed_redacted_error(
    tmp_path: Path,
    url: str,
) -> None:
    with pytest.raises(WashLedgerError) as raised:
        WashLedger(tmp_path).add(url)

    error = str(raised.value)
    assert error == INVALID_URL_ERROR
    if url.strip():
        assert url not in error
    _assert_no_wash_ledger_leaks(error, TEST_URL)


@pytest.mark.parametrize(
    "ledger_text",
    [
        "{not-json}\n",
        f'{{"id":"{TEST_URL}","status":"present","title":"{TEST_TITLE}"}}\n',
    ],
    ids=["invalid-json", "invalid-schema"],
)
def test_wash_ledger_rejects_malformed_jsonl_with_fixed_redacted_error(
    tmp_path: Path,
    ledger_text: str,
) -> None:
    ledger = WashLedger(tmp_path)
    ledger.ledger_path.parent.mkdir(parents=True)
    ledger.ledger_path.write_text(ledger_text, encoding="utf-8")

    with pytest.raises(WashLedgerError) as raised:
        ledger.check(TEST_URL)

    error = str(raised.value)
    assert error == MALFORMED_LEDGER_ERROR
    _assert_no_wash_ledger_leaks(error, TEST_URL)


def _assert_cli_status(
    result: subprocess.CompletedProcess[str],
    *,
    url: str,
    status: str,
    returncode: int,
) -> None:
    digest = _expected_wash_digest(url)
    assert result.returncode == returncode
    assert result.stdout == f"id={digest[:12]} status={status}\n"
    assert result.stderr == ""
    _assert_no_wash_ledger_leaks(result.stdout + result.stderr, url)


def test_wash_ledger_cli_exposes_only_id_prefix_and_status(tmp_path: Path) -> None:
    first = _run_wash_ledger_cli(tmp_path, "add", f"  {TEST_URL}  ")
    _assert_cli_status(first, url=TEST_URL, status="added", returncode=0)

    duplicate = _run_wash_ledger_cli(tmp_path, "add", TEST_URL)
    _assert_cli_status(duplicate, url=TEST_URL, status="duplicate", returncode=0)

    present = _run_wash_ledger_cli(tmp_path, "check", TEST_URL)
    _assert_cli_status(present, url=TEST_URL, status="present", returncode=0)

    missing = _run_wash_ledger_cli(tmp_path, "check", MISSING_URL)
    _assert_cli_status(missing, url=MISSING_URL, status="missing", returncode=1)

    invalid = _run_wash_ledger_cli(tmp_path, "add", INVALID_URL)
    assert invalid.returncode == 2
    assert invalid.stdout == ""
    assert invalid.stderr == f"{INVALID_URL_ERROR}\n"
    assert INVALID_URL not in invalid.stdout + invalid.stderr
    _assert_no_wash_ledger_leaks(invalid.stdout + invalid.stderr, TEST_URL, MISSING_URL)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics required")
def test_wash_ledger_rejects_windows_junction_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "sibling-target"
    workbench_root = root / "01-内容生产" / "视频工作台"
    workbench_root.mkdir(parents=True)
    outside.mkdir()
    internal_root = workbench_root / ".internal"

    try:
        subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                str(internal_root),
                str(outside),
            ],
            check=True,
            capture_output=True,
        )

        with pytest.raises(ValueError) as raised:
            WashLedger(root).add(TEST_URL)

        assert str(raised.value) == "path escapes its root"
        assert not (outside / "洗稿" / "wash-ledger.lock").exists()
        assert not (outside / "洗稿" / "wash-ledger.jsonl").exists()

        result = _run_wash_ledger_cli(root, "add", TEST_URL)
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr == f"{LEDGER_UNAVAILABLE_ERROR}\n"
        _assert_no_wash_ledger_leaks(result.stdout + result.stderr, TEST_URL)
    finally:
        if internal_root.exists():
            os.rmdir(internal_root)


@pytest.mark.skipif(os.name != "nt", reason="Windows msvcrt locking semantics required")
def test_wash_ledger_cli_handles_lock_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_locking(*args: object) -> None:
        raise OSError("simulated lock failure")

    monkeypatch.setattr(wash_ledger.msvcrt, "locking", fail_locking)

    result = _run_wash_ledger_cli(tmp_path, "check", TEST_URL)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "error=ledger-lock-unavailable\n"
    _assert_no_wash_ledger_leaks(result.stdout + result.stderr, TEST_URL)


def test_wash_ledger_cli_rejects_root_option_as_invalid_arguments(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(WASH_LEDGER_SCRIPT),
            "--root",
            str(tmp_path),
            "check",
            TEST_URL,
        ],
        cwd=WASH_LEDGER_SCRIPT.parents[2],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "error=invalid-arguments\n"
    assert not list(tmp_path.rglob("wash-ledger.jsonl"))


def test_handoff_template_contract_is_stable() -> None:
    repo_root = WASH_LEDGER_SCRIPT.parents[2]
    template_path = repo_root / "01-内容生产" / "视频工作台" / "_交接模板.md"
    lines = template_path.read_text(encoding="utf-8").splitlines()

    delimiter_indexes = [
        index for index, line in enumerate(lines) if line.strip() == "---"
    ]
    assert delimiter_indexes[0] == 0
    assert len(delimiter_indexes) >= 2
    second_delimiter = delimiter_indexes[1]

    raw_fields: list[tuple[str, str]] = []
    for line in lines[1:second_delimiter]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        assert separator == ":"
        raw_fields.append((key.strip(), value.strip()))

    keys = [key for key, _ in raw_fields]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(HANDOFF_FIELDS)

    values = dict(raw_fields)

    def without_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    assert {
        key: without_quotes(values[key])
        for key in (
            "status",
            "ratio",
            "duration_target_s",
            "word_count",
            "voice",
            "voice_provider",
            "captions",
            "caption_style",
            "archive_slug",
        )
    } == {
        "status": "待制作",
        "ratio": "16:9",
        "duration_target_s": "0",
        "word_count": "0",
        "voice": CURRENT_VOICE_ID,
        "voice_provider": "indextts2-local",
        "captions": "asr-word-timestamps",
        "caption_style": "anchor-dark",
        "archive_slug": "pending-slug",
    }

    headings = [
        line[3:]
        for line in lines[second_delimiter + 1:]
        if line.startswith("## ")
    ]
    assert headings == [
        "标题候选",
        "新稿分段",
        "分段视觉意图",
        "内容画面计划",
        "制作回执",
        "QC结果",
    ]


def test_gitignore_keeps_private_state_out_and_public_skeleton_tracked() -> None:
    repo_root = WASH_LEDGER_SCRIPT.parents[2]
    ignored_paths = (
        "01-内容生产/视频工作台/.internal/洗稿/probe.txt",
        "01-内容生产/视频工作台/制作中/private-production/交接稿.md",
        "01-内容生产/视频工作台/已制作/probe.txt",
    )
    for relative_path in ignored_paths:
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative_path],
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    public_paths = (
        "01-内容生产/视频工作台/待制作/.gitkeep",
        "01-内容生产/视频工作台/制作中/.gitkeep",
        "05-视频组件/.gitkeep",
    )
    for relative_path in public_paths:
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--", relative_path],
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        assert ignored.returncode == 1, ignored.stderr

        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative_path],
            cwd=repo_root,
            capture_output=True,
            check=False,
            text=True,
        )
        assert tracked.returncode == 0, tracked.stderr

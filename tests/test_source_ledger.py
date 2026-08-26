from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from boomearth.workbench.source_artifacts import canonical_json_bytes
from boomearth.workbench.source_ledger import (
    SourceLedgerError,
    StageEvent,
    WashEventLedger,
)


WORK_ID = "00000000-0000-4000-8000-000000000001"
SOURCE_ID = "a" * 64
ARTIFACT_SHA256 = "b" * 64
PRIVATE_URL = "https://example.invalid/private-item"
WASH_LEDGER_SCRIPT = Path(__file__).resolve().parents[1] / "automation" / "scripts" / "wash_ledger.py"


def _load_wash_ledger():
    spec = importlib.util.spec_from_file_location("p2_wash_ledger_under_test", WASH_LEDGER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wash_ledger = _load_wash_ledger()
WashLedger = wash_ledger.WashLedger


def _event(
    stage: str = "source_registered",
    *,
    source_kind: str = "url",
    previous_event_sha256: str | None = None,
    artifact_sha256: str = ARTIFACT_SHA256,
) -> StageEvent:
    return StageEvent(
        schema_version=2,
        work_id=WORK_ID,
        source_id=SOURCE_ID,
        source_kind=source_kind,
        stage=stage,
        result="ok",
        artifact_label=f"{stage}-manifest",
        artifact_sha256=artifact_sha256,
        timestamp="2026-08-13T04:05:06Z",
        previous_event_sha256=previous_event_sha256,
    )


def _event_hash(event: StageEvent) -> str:
    return hashlib.sha256(canonical_json_bytes(event.to_dict())).hexdigest()


def _run_cli(root: Path, argv: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = wash_ledger.main(argv, root=root)
    return code, stdout.getvalue(), stderr.getvalue()


def test_event_ledger_appends_first_stage_and_reports_status(tmp_path: Path) -> None:
    ledger = WashEventLedger(tmp_path)
    event = _event()

    assert ledger.append(event) is True
    assert ledger.status(WORK_ID) == "source_registered"
    row = json.loads(ledger.ledger_path.read_text("utf-8"))
    assert row == event.to_dict()
    serialized = ledger.ledger_path.read_text("utf-8")
    assert PRIVATE_URL not in serialized
    assert "private title" not in serialized


def test_identical_event_is_idempotent_but_conflict_fails(tmp_path: Path) -> None:
    ledger = WashEventLedger(tmp_path)
    event = _event()
    assert ledger.append(event) is True
    assert ledger.append(event) is False

    conflict = _event(artifact_sha256="c" * 64)
    with pytest.raises(SourceLedgerError, match="^stage-event-conflict$"):
        ledger.append(conflict)
    assert len(ledger.ledger_path.read_text("utf-8").splitlines()) == 1


def test_ledger_requires_exact_legal_transition_and_previous_hash(tmp_path: Path) -> None:
    ledger = WashEventLedger(tmp_path)
    first = _event()
    ledger.append(first)

    with pytest.raises(SourceLedgerError, match="^stage-transition-invalid$"):
        ledger.append(_event("transcript_ready", previous_event_sha256=_event_hash(first)))

    with pytest.raises(SourceLedgerError, match="^stage-chain-invalid$"):
        ledger.append(_event("media_ready", previous_event_sha256="0" * 64))

    second = _event("media_ready", previous_event_sha256=_event_hash(first))
    assert ledger.append(second) is True
    assert ledger.status(WORK_ID) == "media_ready"


def test_x_article_branch_reaches_rewrite_ready_without_media_stages(
    tmp_path: Path,
) -> None:
    ledger = WashEventLedger(tmp_path)
    registered = _event(source_kind="x-article")
    article = _event(
        "article_ready",
        source_kind="x-article",
        previous_event_sha256=_event_hash(registered),
    )
    rewrite = _event(
        "rewrite_ready",
        source_kind="x-article",
        previous_event_sha256=_event_hash(article),
    )

    ledger.append(registered)
    ledger.append(article)
    ledger.append(rewrite)

    assert ledger.status(WORK_ID) == "rewrite_ready"


def test_github_skill_uses_only_its_article_style_stages(tmp_path: Path) -> None:
    ledger = WashEventLedger(tmp_path)
    registered = _event(source_kind="github-skill")
    planned = _event(
        "github_skill_planned",
        source_kind="github-skill",
        previous_event_sha256=_event_hash(registered),
    )
    ready = _event(
        "github_skill_ready",
        source_kind="github-skill",
        previous_event_sha256=_event_hash(planned),
    )
    rewrite = _event(
        "rewrite_ready",
        source_kind="github-skill",
        previous_event_sha256=_event_hash(ready),
    )
    handoff = _event(
        "handoff_ready",
        source_kind="github-skill",
        previous_event_sha256=_event_hash(rewrite),
    )

    for event in (registered, planned, ready, rewrite, handoff):
        ledger.append(event)

    assert ledger.status(WORK_ID) == "handoff_ready"


def test_github_skill_and_existing_sources_cannot_cross_ledger_lanes(
    tmp_path: Path,
) -> None:
    github_ledger = WashEventLedger(tmp_path / "github")
    registered = _event(source_kind="github-skill")
    planned = _event(
        "github_skill_planned",
        source_kind="github-skill",
        previous_event_sha256=_event_hash(registered),
    )
    ready = _event(
        "github_skill_ready",
        source_kind="github-skill",
        previous_event_sha256=_event_hash(planned),
    )
    github_ledger.append(registered)
    github_ledger.append(planned)
    github_ledger.append(ready)
    with pytest.raises(SourceLedgerError, match="^stage-transition-invalid$"):
        github_ledger.append(
            _event(
                "media_ready",
                source_kind="github-skill",
                previous_event_sha256=_event_hash(ready),
            )
        )

    url_ledger = WashEventLedger(tmp_path / "url")
    url_registered = _event(source_kind="url")
    url_ledger.append(url_registered)
    with pytest.raises(SourceLedgerError, match="^stage-transition-invalid$"):
        url_ledger.append(
            _event(
                "github_skill_planned",
                source_kind="url",
                previous_event_sha256=_event_hash(url_registered),
            )
        )


def test_article_and_media_sources_cannot_cross_ledger_branches(tmp_path: Path) -> None:
    media_ledger = WashEventLedger(tmp_path / "media")
    media_registered = _event(source_kind="url")
    media_ledger.append(media_registered)
    with pytest.raises(SourceLedgerError, match="^stage-transition-invalid$"):
        media_ledger.append(
            _event(
                "article_ready",
                source_kind="url",
                previous_event_sha256=_event_hash(media_registered),
            )
        )

    article_ledger = WashEventLedger(tmp_path / "article")
    article_registered = _event(source_kind="x-article")
    article_ledger.append(article_registered)
    with pytest.raises(SourceLedgerError, match="^stage-transition-invalid$"):
        article_ledger.append(
            _event(
                "media_ready",
                source_kind="x-article",
                previous_event_sha256=_event_hash(article_registered),
            )
        )


def test_skipped_is_terminal_from_any_nonterminal_stage(tmp_path: Path) -> None:
    ledger = WashEventLedger(tmp_path)
    first = _event()
    ledger.append(first)
    skipped = _event("skipped", previous_event_sha256=_event_hash(first))
    ledger.append(skipped)

    with pytest.raises(SourceLedgerError, match="^stage-transition-invalid$"):
        ledger.append(_event("media_ready", previous_event_sha256=_event_hash(skipped)))


def test_malformed_or_unknown_event_row_fails_closed(tmp_path: Path) -> None:
    ledger = WashEventLedger(tmp_path)
    ledger.ledger_path.parent.mkdir(parents=True)
    malformed = _event().to_dict()
    malformed["source_url"] = PRIVATE_URL
    ledger.ledger_path.write_text(json.dumps(malformed) + "\n", encoding="utf-8")

    with pytest.raises(SourceLedgerError) as raised:
        ledger.status(WORK_ID)

    assert str(raised.value) == "ledger-malformed"
    assert PRIVATE_URL not in repr(raised.value)


def test_event_append_is_atomic_under_threads(tmp_path: Path) -> None:
    event = _event()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: WashEventLedger(tmp_path).append(event), range(8)))

    assert results.count(True) == 1
    assert results.count(False) == 7
    assert len(WashEventLedger(tmp_path).ledger_path.read_text("utf-8").splitlines()) == 1


def test_legacy_url_rows_and_v2_url_event_remain_check_add_compatible(
    tmp_path: Path,
) -> None:
    legacy_url = "https://example.invalid/legacy"
    assert WashLedger(tmp_path).add(legacy_url) is True
    ledger = WashEventLedger(tmp_path)
    event = _event()
    ledger.append(event)

    assert WashLedger(tmp_path).check(legacy_url) is True
    assert WashLedger(tmp_path).check(PRIVATE_URL) is False
    rows = [json.loads(line) for line in ledger.ledger_path.read_text("utf-8").splitlines()]
    assert set(rows[0]) == {"id", "status"}
    assert set(rows[1]) == set(event.to_dict())


def test_wash_ledger_status_cli_is_redacted_and_legacy_cli_is_unchanged(
    tmp_path: Path,
) -> None:
    ledger = WashEventLedger(tmp_path)
    ledger.append(_event())

    code, stdout, stderr = _run_cli(tmp_path, ["status", WORK_ID])
    assert (code, stderr) == (0, "")
    assert stdout == "work=000000000000 stage=source_registered status=present\n"
    assert SOURCE_ID not in stdout

    code, stdout, stderr = _run_cli(tmp_path, ["add", PRIVATE_URL])
    expected_id = hashlib.sha256(PRIVATE_URL.encode()).hexdigest()[:12]
    assert (code, stdout, stderr) == (0, f"id={expected_id} status=added\n", "")
    assert PRIVATE_URL not in stdout + stderr


def test_status_for_missing_work_is_fixed_and_redacted(tmp_path: Path) -> None:
    code, stdout, stderr = _run_cli(tmp_path, ["status", WORK_ID])

    assert code == 1
    assert stdout == "work=000000000000 stage=missing status=missing\n"
    assert stderr == ""


def test_event_schema_rejects_invalid_uuid_digest_and_label() -> None:
    invalid_values = (
        {"work_id": "not-a-uuid"},
        {"source_id": "short"},
        {"artifact_sha256": "short"},
        {"artifact_label": "private/source/path"},
        {"timestamp": "not-a-time"},
    )
    for changes in invalid_values:
        values = _event().to_dict()
        values.update(changes)
        with pytest.raises(SourceLedgerError, match="^stage-event-invalid$"):
            StageEvent.from_dict(values)

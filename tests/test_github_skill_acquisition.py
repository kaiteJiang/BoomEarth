from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from io import StringIO
from uuid import UUID

import httpx
import pytest

from boomearth.workbench.github_skill_acquisition import (
    GitHubSkillAcquisitionError,
    plan_github_skill_acquisition,
    recover_github_skill_acquisition,
    run_github_skill_acquisition,
)
import boomearth.workbench.github_skill_acquisition as acquisition_module
from boomearth.workbench.source_artifacts import (
    canonical_json_bytes,
    sha256_file,
)
from boomearth.workbench.source_intake import create_github_skill_intake
from boomearth.workbench.source_ledger import WashEventLedger


WORK_ID = "22222222-2222-4222-8222-222222222222"
FIXED_UUID = UUID(WORK_ID)
FIXED_NOW = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
COMMIT_SHA = "a" * 40
REPOSITORY = "acme/skills"
GITHUB_URL = f"https://github.com/{REPOSITORY}"
ACQUIRE_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "automation"
    / "scripts"
    / "acquire_github_skill.py"
)


def _private_root(root: Path) -> Path:
    return (
        root
        / "01-内容生产"
        / "视频工作台"
        / ".internal"
        / "洗稿"
        / WORK_ID
    )


def _load_cli_module():
    spec = importlib.util.spec_from_file_location(
        "github_skill_acquisition_cli_under_test", ACQUIRE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _intake(root: Path) -> None:
    source = root / "github-url.txt"
    source.write_text(GITHUB_URL + "\n", encoding="utf-8")
    create_github_skill_intake(
        root,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )


def _approval(root: Path, *, request_count: int = 48, plan_sha: str | None = None) -> Path:
    private_root = _private_root(root)
    plan_path = private_root / "github-skill-acquisition-plan.json"
    plan_value = json.loads(plan_path.read_text("utf-8"))
    receipt = {
        "approved": True,
        "plan_sha256": plan_sha or hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "work_id": WORK_ID,
        "provider": "github-public-rest-v1",
        "action": "github-skill-acquisition",
        "input_sha256": plan_value["input_sha256"],
        "request_count": request_count,
        "no_retry": True,
        "no_fallback": True,
    }
    target = private_root / "github-skill-acquisition-approval.json"
    target.write_bytes(canonical_json_bytes(receipt))
    return target


def _git_blob_sha(payload: bytes) -> str:
    prefix = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(prefix + payload).hexdigest()


README = b"# Skill collection\n"
COMIC = (
    b"---\nname: comic-explainer\n"
    b"description: Explain a topic with vivid sequential scenes\n---\n# Comic\n"
)
ENGINEERING = (
    b"---\nname: engineering-sketch\n"
    b"description: Explain real structures and flows\n---\n# Engineering\n"
)


def _tree() -> dict[str, object]:
    return {
        "sha": "b" * 40,
        "url": "https://api.github.com/repos/acme/skills/git/trees/" + COMMIT_SHA,
        "tree": [
            {
                "path": "README.md",
                "mode": "100644",
                "type": "blob",
                "sha": _git_blob_sha(README),
                "size": len(README),
                "url": "https://api.github.com/blob/readme",
            },
            {
                "path": "comic",
                "mode": "040000",
                "type": "tree",
                "sha": "c" * 40,
                "url": "https://api.github.com/tree/comic",
            },
            {
                "path": "comic/SKILL.md",
                "mode": "100644",
                "type": "blob",
                "sha": _git_blob_sha(COMIC),
                "size": len(COMIC),
                "url": "https://api.github.com/blob/comic",
            },
            {
                "path": "engineering/SKILL.md",
                "mode": "100644",
                "type": "blob",
                "sha": _git_blob_sha(ENGINEERING),
                "size": len(ENGINEERING),
                "url": "https://api.github.com/blob/engineering",
            },
        ],
        "truncated": False,
    }


class FakeGitHubTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.responses = deque(
            [
                (
                    "json",
                    "https://api.github.com/repos/acme/skills",
                    {
                        "full_name": REPOSITORY,
                        "description": "Fixture skills",
                        "default_branch": "main",
                        "license": None,
                    },
                    200,
                ),
                (
                    "json",
                    "https://api.github.com/repos/acme/skills/commits/main",
                    {
                        "sha": COMMIT_SHA,
                        "url": "https://api.github.com/repos/acme/skills/commits/"
                        + COMMIT_SHA,
                        "commit": {"message": "fixture"},
                    },
                    200,
                ),
                (
                    "json",
                    "https://api.github.com/repos/acme/skills/license",
                    {},
                    404,
                ),
                (
                    "json",
                    "https://api.github.com/repos/acme/skills/git/trees/"
                    + COMMIT_SHA
                    + "?recursive=1",
                    _tree(),
                    200,
                ),
                (
                    "text",
                    "https://raw.githubusercontent.com/acme/skills/"
                    + COMMIT_SHA
                    + "/README.md",
                    README,
                    200,
                ),
                (
                    "text",
                    "https://raw.githubusercontent.com/acme/skills/"
                    + COMMIT_SHA
                    + "/comic/SKILL.md",
                    COMIC,
                    200,
                ),
                (
                    "text",
                    "https://raw.githubusercontent.com/acme/skills/"
                    + COMMIT_SHA
                    + "/engineering/SKILL.md",
                    ENGINEERING,
                    200,
                ),
            ]
        )

    def _next(self, kind: str, url: str) -> tuple[object, int]:
        expected_kind, expected_url, payload, status = self.responses.popleft()
        assert (kind, url) == (expected_kind, expected_url)
        self.calls.append((kind, url))
        return payload, status

    def get_json(self, url: str) -> tuple[object, int]:
        return self._next("json", url)

    def get_text(self, url: str) -> tuple[bytes, int]:
        payload, status = self._next("text", url)
        assert isinstance(payload, bytes)
        return payload, status


class CountingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.yielded = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


def test_http_transport_stops_stream_at_limit_plus_one() -> None:
    stream = CountingStream([b"abc", b"de", b"must-not-be-read"])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            stream=stream,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = acquisition_module._HttpGitHubTransport(client)
        with pytest.raises(
            GitHubSkillAcquisitionError,
            match="^github-skill-byte-budget-exceeded$",
        ):
            transport._get(
                "https://raw.githubusercontent.com/acme/skills/file.md",
                accept="text/plain",
                limit=4,
            )

    assert stream.yielded == 2


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/plain", "content-encoding": "gzip"},
        {"content-type": "text/plain", "content-length": "999"},
    ],
)
def test_http_transport_rejects_compression_and_oversized_declared_length(
    headers: dict[str, str],
) -> None:
    stream = CountingStream([b"ok"])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = acquisition_module._HttpGitHubTransport(client)
        with pytest.raises(GitHubSkillAcquisitionError):
            transport._get(
                "https://raw.githubusercontent.com/acme/skills/file.md",
                accept="text/plain",
                limit=4,
            )

    assert stream.yielded == 0


def test_acquisition_rejects_oversized_selected_tree_blob_before_raw_request(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    tree = json.loads(json.dumps(responses[3][2]))
    tree["tree"][0]["size"] = acquisition_module.MAX_FILE_BYTES + 1
    responses[3] = (responses[3][0], responses[3][1], tree, responses[3][3])
    transport.responses = deque(responses)

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-byte-budget-exceeded$",
    ):
        run_github_skill_acquisition(
            tmp_path,
            WORK_ID,
            _approval(tmp_path),
            transport=transport,
        )

    assert len(transport.calls) == 4


def test_acquisition_rejects_oversized_referenced_blob_before_raw_request(
    tmp_path: Path,
) -> None:
    source = tmp_path / "github-url.txt"
    source.write_text(
        GITHUB_URL + "/tree/main/comic\n",
        encoding="utf-8",
    )
    create_github_skill_intake(
        tmp_path,
        source,
        authorized=True,
        now=lambda: FIXED_NOW,
        uuid_factory=lambda: FIXED_UUID,
    )
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    skill = COMIC + b"\n[Details](details.md)\n"
    tree = json.loads(json.dumps(responses[3][2]))
    for entry in tree["tree"]:
        if entry["path"] == "comic/SKILL.md":
            entry["sha"] = _git_blob_sha(skill)
            entry["size"] = len(skill)
    tree["tree"].append(
        {
            "path": "comic/details.md",
            "mode": "100644",
            "type": "blob",
            "sha": "d" * 40,
            "size": acquisition_module.MAX_FILE_BYTES + 1,
            "url": "https://api.github.com/blob/details",
        }
    )
    responses[3] = (responses[3][0], responses[3][1], tree, responses[3][3])
    responses[5] = (responses[5][0], responses[5][1], skill, responses[5][3])
    transport.responses = deque(responses)

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-byte-budget-exceeded$",
    ):
        run_github_skill_acquisition(
            tmp_path,
            WORK_ID,
            _approval(tmp_path),
            transport=transport,
        )

    assert len(transport.calls) == 6
    assert all("/comic/details.md" not in url for _, url in transport.calls)


def test_plan_binds_input_network_policy_and_all_budgets(tmp_path: Path) -> None:
    _intake(tmp_path)

    plan = plan_github_skill_acquisition(tmp_path, WORK_ID)

    assert plan.provider == "github-public-rest-v1"
    assert plan.action == "github-skill-acquisition"
    assert plan.request_budget == 48
    assert plan.max_tree_entries == 5000
    assert plan.max_skill_files == 40
    assert plan.max_markdown_files == 44
    assert plan.max_reference_files == 12
    assert plan.max_file_bytes == 256 * 1024
    assert plan.max_total_text_bytes == 4 * 1024 * 1024
    assert plan.max_path_depth == 12
    assert plan.allowed_hosts == ("api.github.com", "raw.githubusercontent.com")
    assert plan.follow_redirects is False
    assert plan.no_retry is True and plan.no_fallback is True
    value = json.loads(
        (_private_root(tmp_path) / "github-skill-acquisition-plan.json").read_text(
            "utf-8"
        )
    )
    assert set(value) == {
        "schema_version",
        "work_id",
        "provider",
        "action",
        "input_sha256",
        "network_required",
        "fee_possible",
        "request_budget",
        "max_tree_entries",
        "max_skill_files",
        "max_markdown_files",
        "max_reference_files",
        "max_file_bytes",
        "max_total_text_bytes",
        "max_path_depth",
        "allowed_hosts",
        "follow_redirects",
        "no_retry",
        "no_fallback",
    }
    assert WashEventLedger(tmp_path).status(WORK_ID) == "github_skill_planned"


def test_run_publishes_hash_bound_private_repository_snapshot(tmp_path: Path) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    transport = FakeGitHubTransport()

    result = run_github_skill_acquisition(
        tmp_path, WORK_ID, approval, transport=transport
    )

    private_root = _private_root(tmp_path)
    manifest = json.loads(result.path.read_text("utf-8"))
    repository_markdown = private_root / "github-skill-source" / "repository.md"
    assert result.relative_path == "github-skill-manifest.json"
    assert result.sha256 == sha256_file(result.path)
    assert manifest["repository"] == REPOSITORY
    assert manifest["requested_scope"] == "repository"
    assert manifest["requested_path"] == ""
    assert manifest["resolved_commit"] == COMMIT_SHA
    assert manifest["default_branch"] == "main"
    assert manifest["license_status"] == "UNDECLARED"
    assert manifest["request_count"] == 7
    assert manifest["repository_markdown_sha256"] == sha256_file(
        repository_markdown
    )
    assert [item["path"] for item in manifest["files"]] == [
        "README.md",
        "comic/SKILL.md",
        "engineering/SKILL.md",
    ]
    digest_text = repository_markdown.read_text("utf-8")
    assert "comic-explainer" in digest_text
    assert "engineering-sketch" in digest_text
    assert "2 Skill documents" in digest_text
    assert WashEventLedger(tmp_path).status(WORK_ID) == "github_skill_ready"
    assert len(transport.calls) == 7
    assert not transport.responses


def test_repository_scope_accepts_readme_only_software_repository(
    tmp_path: Path,
) -> None:
    """A normal software repository must not require a SKILL.md file."""

    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    tree = json.loads(json.dumps(responses[3][2]))
    tree["tree"] = [tree["tree"][0]]
    responses[3] = (responses[3][0], responses[3][1], tree, responses[3][3])
    transport.responses = deque(responses[:5])

    result = run_github_skill_acquisition(
        tmp_path, WORK_ID, approval, transport=transport
    )

    manifest = json.loads(result.path.read_text("utf-8"))
    assert manifest["request_count"] == 5
    assert [item["path"] for item in manifest["files"]] == ["README.md"]
    assert manifest["files"][0]["role"] == "root-readme"
    assert WashEventLedger(tmp_path).status(WORK_ID) == "github_skill_ready"
    assert len(transport.calls) == 5
    assert not transport.responses


@pytest.mark.parametrize(
    ("request_count", "plan_sha"),
    [(47, None), (48, "0" * 64)],
)
def test_run_rejects_wrong_approval_before_transport(
    tmp_path: Path, request_count: int, plan_sha: str | None
) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    approval = _approval(
        tmp_path, request_count=request_count, plan_sha=plan_sha
    )
    transport = FakeGitHubTransport()

    with pytest.raises(
        GitHubSkillAcquisitionError, match="^github-skill-not-approved$"
    ):
        run_github_skill_acquisition(
            tmp_path, WORK_ID, approval, transport=transport
        )

    assert transport.calls == []


def test_run_rejects_changed_input_and_second_run_before_transport(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    source = _private_root(tmp_path) / "source-input.txt"
    source.write_text("https://github.com/acme/changed\n", encoding="utf-8")
    first_transport = FakeGitHubTransport()

    with pytest.raises(
        GitHubSkillAcquisitionError, match="^github-skill-input-changed$"
    ):
        run_github_skill_acquisition(
            tmp_path, WORK_ID, approval, transport=first_transport
        )
    assert first_transport.calls == []

    source.write_text(GITHUB_URL + "\n", encoding="utf-8")
    successful = FakeGitHubTransport()
    run_github_skill_acquisition(
        tmp_path, WORK_ID, approval, transport=successful
    )
    second_transport = FakeGitHubTransport()
    with pytest.raises(
        GitHubSkillAcquisitionError, match="^github-skill-not-approved$"
    ):
        run_github_skill_acquisition(
            tmp_path, WORK_ID, approval, transport=second_transport
        )
    assert second_transport.calls == []


def _planned_run(root: Path, transport: FakeGitHubTransport) -> None:
    _intake(root)
    plan_github_skill_acquisition(root, WORK_ID)
    approval = _approval(root)
    run_github_skill_acquisition(root, WORK_ID, approval, transport=transport)


def _assert_no_formal_snapshot(root: Path) -> None:
    private_root = _private_root(root)
    assert not (private_root / "github-skill-source").exists()
    assert not (private_root / "github-skill-manifest.json").exists()
    assert WashEventLedger(root).status(WORK_ID) == "github_skill_planned"


def test_run_records_redacted_stage_for_invalid_document_response(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    private_root = _private_root(tmp_path)
    plan_path = private_root / "github-skill-acquisition-plan.json"
    plan_value = json.loads(plan_path.read_text("utf-8"))
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    payload = b"\xff"
    tree = _tree()
    readme_entry = tree["tree"][0]
    assert isinstance(readme_entry, dict)
    readme_entry["sha"] = _git_blob_sha(payload)
    readme_entry["size"] = len(payload)
    responses[3] = (responses[3][0], responses[3][1], tree, responses[3][3])
    responses[4] = (responses[4][0], responses[4][1], payload, 200)
    transport.responses = deque(responses)

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-response-invalid$",
    ):
        run_github_skill_acquisition(
            tmp_path,
            WORK_ID,
            _approval(tmp_path),
            transport=transport,
        )

    failure_path = private_root / "github-skill-acquisition-failure.json"
    failure = json.loads(failure_path.read_text("utf-8"))
    assert set(failure) == {
        "schema_version",
        "work_id",
        "input_sha256",
        "plan_sha256",
        "stage",
        "error",
        "request_count",
        "created_at",
    }
    assert failure["schema_version"] == 1
    assert failure["work_id"] == WORK_ID
    assert failure["input_sha256"] == plan_value["input_sha256"]
    assert failure["plan_sha256"] == hashlib.sha256(plan_path.read_bytes()).hexdigest()
    assert failure["stage"] == "document-fetch"
    assert failure["error"] == "github-skill-response-invalid"
    assert failure["request_count"] == 5
    serialized = failure_path.read_text("utf-8")
    assert GITHUB_URL not in serialized
    assert REPOSITORY not in serialized
    assert COMMIT_SHA not in serialized
    assert "README.md" not in serialized
    _assert_no_formal_snapshot(tmp_path)


@pytest.mark.parametrize(
    ("response_index", "payload", "stage", "request_count"),
    [
        (0, [], "repository-metadata", 1),
        (3, {}, "tree", 4),
    ],
    ids=["metadata", "tree"],
)
def test_run_records_redacted_stage_for_invalid_api_response(
    tmp_path: Path,
    response_index: int,
    payload: object,
    stage: str,
    request_count: int,
) -> None:
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    kind, url, _, status = responses[response_index]
    responses[response_index] = (kind, url, payload, status)
    transport.responses = deque(responses)

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-response-invalid$",
    ):
        _planned_run(tmp_path, transport)

    failure_path = _private_root(tmp_path) / "github-skill-acquisition-failure.json"
    failure = json.loads(failure_path.read_text("utf-8"))
    assert failure["stage"] == stage
    assert failure["error"] == "github-skill-response-invalid"
    assert failure["request_count"] == request_count
    _assert_no_formal_snapshot(tmp_path)


def test_run_does_not_overwrite_failure_receipt(tmp_path: Path) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    failure_path = (
        _private_root(tmp_path) / "github-skill-acquisition-failure.json"
    )
    original = b'{"sentinel":true}\n'
    failure_path.write_bytes(original)
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    kind, url, _, status = responses[0]
    responses[0] = (kind, url, [], status)
    transport.responses = deque(responses)

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-response-invalid$",
    ):
        run_github_skill_acquisition(
            tmp_path,
            WORK_ID,
            _approval(tmp_path),
            transport=transport,
        )

    assert failure_path.read_bytes() == original
    _assert_no_formal_snapshot(tmp_path)


def test_provider_error_receipt_matches_authoritative_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeGitHubTransport()

    def reject_tree(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        raise acquisition_module.GitHubSkillProviderError(
            "github-skill-input-invalid"
        )

    monkeypatch.setattr(acquisition_module, "parse_tree_response", reject_tree)

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-input-invalid$",
    ):
        _planned_run(tmp_path, transport)

    failure_path = _private_root(tmp_path) / "github-skill-acquisition-failure.json"
    failure = json.loads(failure_path.read_text("utf-8"))
    assert failure["stage"] == "tree"
    assert failure["error"] == "github-skill-input-invalid"
    assert failure["request_count"] == 4
    _assert_no_formal_snapshot(tmp_path)


def test_run_rejects_truncated_tree_without_partial_publication(
    tmp_path: Path,
) -> None:
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    truncated = _tree()
    truncated["truncated"] = True
    kind, url, _, status = responses[3]
    responses[3] = (kind, url, truncated, status)
    transport.responses = deque(responses)

    with pytest.raises(
        GitHubSkillAcquisitionError, match="^github-skill-tree-truncated$"
    ):
        _planned_run(tmp_path, transport)

    assert len(transport.calls) == 4
    _assert_no_formal_snapshot(tmp_path)


def test_run_enforces_tree_and_skill_count_budgets_before_file_requests(
    tmp_path: Path,
) -> None:
    for name, entries in (
        (
            "tree",
            [
                {
                    "path": f"directory-{index}",
                    "mode": "040000",
                    "type": "tree",
                    "sha": f"{index:040x}",
                    "url": f"https://api.github.com/tree/{index}",
                }
                for index in range(5001)
            ],
        ),
        (
            "skills",
            [
                {
                    "path": f"skill-{index}/SKILL.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": f"{index:040x}",
                    "size": 1,
                    "url": f"https://api.github.com/blob/{index}",
                }
                for index in range(41)
            ],
        ),
    ):
        root = tmp_path / name
        root.mkdir()
        transport = FakeGitHubTransport()
        responses = list(transport.responses)
        oversized_tree = _tree()
        oversized_tree["tree"] = entries
        kind, url, _, status = responses[3]
        responses[3] = (kind, url, oversized_tree, status)
        transport.responses = deque(responses)

        with pytest.raises(
            GitHubSkillAcquisitionError,
            match="^github-skill-request-budget-exceeded$",
        ):
            _planned_run(root, transport)

        assert len(transport.calls) == 4
        _assert_no_formal_snapshot(root)


@pytest.mark.parametrize(
    ("payload", "status", "error"),
    [
        (b"x" * (256 * 1024 + 1), 200, "github-skill-byte-budget-exceeded"),
        (b"a\x00b", 200, "github-skill-byte-budget-exceeded"),
        (b"\xff", 200, "github-skill-response-invalid"),
        (
            b"version https://git-lfs.github.com/spec/v1\n",
            200,
            "github-skill-byte-budget-exceeded",
        ),
        (README, 302, "github-skill-repository-unavailable"),
    ],
    ids=["oversized", "nul", "invalid-utf8", "lfs-pointer", "redirect"],
)
def test_run_rejects_oversized_binary_invalid_lfs_or_redirected_markdown(
    tmp_path: Path, payload: bytes, status: int, error: str
) -> None:
    transport = FakeGitHubTransport()
    responses = list(transport.responses)
    tree = _tree()
    readme_entry = tree["tree"][0]
    assert isinstance(readme_entry, dict)
    readme_entry["sha"] = _git_blob_sha(payload)
    readme_entry["size"] = len(payload)
    tree_kind, tree_url, _, tree_status = responses[3]
    responses[3] = (tree_kind, tree_url, tree, tree_status)
    text_kind, text_url, _, _ = responses[4]
    responses[4] = (text_kind, text_url, payload, status)
    transport.responses = deque(responses)

    with pytest.raises(GitHubSkillAcquisitionError, match=f"^{error}$"):
        _planned_run(tmp_path, transport)

    assert len(transport.calls) == (4 if len(payload) > 256 * 1024 else 5)
    _assert_no_formal_snapshot(tmp_path)


def test_run_enforces_total_text_budget_without_retry(tmp_path: Path) -> None:
    transport = FakeGitHubTransport()
    prefix = (
        b"---\nname: budget-skill\n"
        b"description: A valid budget fixture\n---\n"
    )
    payloads = [
        prefix + bytes([97 + index]) * (256 * 1024 - len(prefix))
        for index in range(17)
    ]
    entries = [
        {
            "path": f"skill-{index:02d}/SKILL.md",
            "mode": "100644",
            "type": "blob",
            "sha": _git_blob_sha(payload),
            "size": len(payload),
            "url": f"https://api.github.com/blob/{index}",
        }
        for index, payload in enumerate(payloads)
    ]
    tree = _tree()
    tree["tree"] = entries
    base = list(transport.responses)[:4]
    base[3] = (base[3][0], base[3][1], tree, base[3][3])
    base.extend(
        (
            "text",
            "https://raw.githubusercontent.com/acme/skills/"
            + COMMIT_SHA
            + f"/skill-{index:02d}/SKILL.md",
            payload,
            200,
        )
        for index, payload in enumerate(payloads)
    )
    transport.responses = deque(base)

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-byte-budget-exceeded$",
    ):
        _planned_run(tmp_path, transport)

    assert len(transport.calls) == 21
    _assert_no_formal_snapshot(tmp_path)


class SimulatedProcessExit(BaseException):
    pass


@pytest.mark.parametrize(
    ("hook_name", "recoverable"),
    [
        ("_after_staging_files", False),
        ("_after_pending_manifest", True),
        ("_after_formal_directory_move", True),
        ("_before_ledger_append", True),
    ],
)
def test_interrupted_publication_recovers_locally_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hook_name: str,
    recoverable: bool,
) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    transport = FakeGitHubTransport()

    def interrupt(*_args: object) -> None:
        raise SimulatedProcessExit

    monkeypatch.setattr(acquisition_module, hook_name, interrupt)
    with pytest.raises(SimulatedProcessExit):
        run_github_skill_acquisition(
            tmp_path, WORK_ID, approval, transport=transport
        )
    monkeypatch.undo()

    assert len(transport.calls) == 7
    assert WashEventLedger(tmp_path).status(WORK_ID) == "github_skill_planned"
    if recoverable:
        record = recover_github_skill_acquisition(tmp_path, WORK_ID)
        assert record.relative_path == "github-skill-manifest.json"
        assert record.sha256 == sha256_file(record.path)
        assert WashEventLedger(tmp_path).status(WORK_ID) == "github_skill_ready"
        assert not list(
            _private_root(tmp_path).glob(".github-skill-staging-*")
        )
    else:
        with pytest.raises(
            GitHubSkillAcquisitionError,
            match="^github-skill-publication-failed$",
        ):
            recover_github_skill_acquisition(tmp_path, WORK_ID)
        _assert_no_formal_snapshot(tmp_path)
        assert not list(
            _private_root(tmp_path).glob(".github-skill-staging-*")
        )


def test_recover_is_idempotent_after_ready_without_network(tmp_path: Path) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    run_github_skill_acquisition(
        tmp_path, WORK_ID, approval, transport=FakeGitHubTransport()
    )

    first = recover_github_skill_acquisition(tmp_path, WORK_ID)
    second = recover_github_skill_acquisition(tmp_path, WORK_ID)

    assert first == second
    assert WashEventLedger(tmp_path).status(WORK_ID) == "github_skill_ready"


def test_ready_recovery_rejects_manifest_that_no_longer_matches_ledger(
    tmp_path: Path,
) -> None:
    _intake(tmp_path)
    plan_github_skill_acquisition(tmp_path, WORK_ID)
    approval = _approval(tmp_path)
    record = run_github_skill_acquisition(
        tmp_path, WORK_ID, approval, transport=FakeGitHubTransport()
    )
    value = json.loads(record.path.read_text("utf-8"))
    value["created_at"] = "2026-08-16T00:00:00Z"
    record.path.write_bytes(canonical_json_bytes(value))

    with pytest.raises(
        GitHubSkillAcquisitionError,
        match="^github-skill-publication-failed$",
    ):
        recover_github_skill_acquisition(tmp_path, WORK_ID)


def test_cli_plan_run_and_recover_outputs_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _intake(tmp_path)
    cli = _load_cli_module()
    stdout = StringIO()
    stderr = StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = cli.main(["plan", WORK_ID], root=tmp_path)
    assert result == 0
    assert stderr.getvalue() == ""
    assert stdout.getvalue() == (
        "work=222222222222 action=github-skill-acquisition status=planned\n"
    )
    assert GITHUB_URL not in stdout.getvalue()

    monkeypatch.setattr(cli, "run_github_skill_acquisition", lambda *_args: None)
    monkeypatch.setattr(cli, "recover_github_skill_acquisition", lambda *_args: None)
    for command, status in (("run", "created"), ("recover", "recovered")):
        stdout = StringIO()
        stderr = StringIO()
        argv = [command, WORK_ID]
        if command == "run":
            argv += ["--approval", str(_approval(tmp_path))]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli.main(argv, root=tmp_path)
        assert result == 0
        assert stderr.getvalue() == ""
        assert stdout.getvalue() == (
            f"work=222222222222 action=github-skill-acquisition status={status}\n"
        )
        assert GITHUB_URL not in stdout.getvalue()

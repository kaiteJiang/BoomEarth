"""Plan and execute one approved public GitHub Skill acquisition."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from boomearth.providers.github_skill import (
    GitHubSkillDescriptor,
    GitHubSkillProviderError,
    GitHubSkillTarget,
    GitTreeEntry,
    discover_markdown_references,
    parse_github_skill_url,
    parse_skill_document,
    parse_tree_response,
    render_repository_markdown,
)
from boomearth.workbench.paths import WorkbenchPaths
from boomearth.workbench.source_artifacts import (
    ArtifactRecord,
    SourceContractError,
    canonical_json_bytes,
    load_exact_json,
    publish_json_exclusive,
    sha256_file,
    verify_approval_payload,
    verify_private_relative,
)
from boomearth.workbench.source_intake import load_work_order
from boomearth.workbench.source_ledger import (
    SourceLedgerError,
    StageEvent,
    WashEventLedger,
    event_sha256,
)


PROVIDER = "github-public-rest-v1"
ACTION = "github-skill-acquisition"
REQUEST_BUDGET = 48
MAX_TREE_ENTRIES = 5_000
MAX_SKILL_FILES = 40
MAX_MARKDOWN_FILES = 44
MAX_REFERENCE_FILES = 12
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_TEXT_BYTES = 4 * 1024 * 1024
MAX_PATH_DEPTH = 12
ALLOWED_HOSTS = ("api.github.com", "raw.githubusercontent.com")
RECOVERY_MANIFEST = ".github-skill-manifest.pending.json"
FAILURE_RECEIPT = "github-skill-acquisition-failure.json"
_FAILURE_STAGES = frozenset(
    {
        "preflight",
        "repository-metadata",
        "commit",
        "license",
        "tree",
        "selection",
        "document-fetch",
        "skill-parse",
        "reference-discovery",
        "snapshot-render",
        "publication",
    }
)
_FAILURE_ERRORS = frozenset(
    {
        "github-skill-input-invalid",
        "github-skill-not-approved",
        "github-skill-input-changed",
        "github-skill-repository-unavailable",
        "github-skill-ref-invalid",
        "github-skill-response-invalid",
        "github-skill-path-invalid",
        "github-skill-tree-truncated",
        "github-skill-request-budget-exceeded",
        "github-skill-byte-budget-exceeded",
        "github-skill-skill-missing",
        "github-skill-publication-failed",
    }
)
_API_MAX_BYTES = 8 * 1024 * 1024
_SHA1_RE = re.compile(r"[0-9a-f]{40}")
_SPDX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_PLAN_KEYS = frozenset(
    {
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
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "work_id",
        "source_kind",
        "source_input_sha256",
        "repository",
        "requested_scope",
        "requested_path",
        "resolved_commit",
        "default_branch",
        "license_status",
        "tree_sha256",
        "repository_markdown_path",
        "repository_markdown_sha256",
        "request_count",
        "total_text_bytes",
        "files",
        "created_at",
    }
)
_MANIFEST_FILE_KEYS = frozenset(
    {"path", "blob_sha", "local_path", "sha256", "bytes", "role"}
)


class GitHubSkillAcquisitionError(RuntimeError):
    """A fixed-message acquisition failure that never renders source data."""

    def __repr__(self) -> str:
        return "GitHubSkillAcquisitionError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class GitHubSkillActionPlan:
    schema_version: int
    work_id: str
    provider: str
    action: str
    input_sha256: str
    network_required: bool
    fee_possible: bool
    request_budget: int
    max_tree_entries: int
    max_skill_files: int
    max_markdown_files: int
    max_reference_files: int
    max_file_bytes: int
    max_total_text_bytes: int
    max_path_depth: int
    allowed_hosts: tuple[str, str]
    follow_redirects: bool
    no_retry: bool
    no_fallback: bool

    def __repr__(self) -> str:
        return "GitHubSkillActionPlan(<redacted>)"


class GitHubSkillTransport(Protocol):
    def get_json(self, url: str) -> tuple[object, int]: ...

    def get_text(self, url: str) -> tuple[bytes, int]: ...


def _plan_value(plan: GitHubSkillActionPlan) -> dict[str, object]:
    return {
        "action": plan.action,
        "allowed_hosts": list(plan.allowed_hosts),
        "fee_possible": plan.fee_possible,
        "follow_redirects": plan.follow_redirects,
        "input_sha256": plan.input_sha256,
        "max_file_bytes": plan.max_file_bytes,
        "max_markdown_files": plan.max_markdown_files,
        "max_path_depth": plan.max_path_depth,
        "max_reference_files": plan.max_reference_files,
        "max_skill_files": plan.max_skill_files,
        "max_total_text_bytes": plan.max_total_text_bytes,
        "max_tree_entries": plan.max_tree_entries,
        "network_required": plan.network_required,
        "no_fallback": plan.no_fallback,
        "no_retry": plan.no_retry,
        "provider": plan.provider,
        "request_budget": plan.request_budget,
        "schema_version": plan.schema_version,
        "work_id": plan.work_id,
    }


def _expected_plan(work_id: str, input_sha256: str) -> GitHubSkillActionPlan:
    return GitHubSkillActionPlan(
        schema_version=1,
        work_id=work_id,
        provider=PROVIDER,
        action=ACTION,
        input_sha256=input_sha256,
        network_required=True,
        fee_possible=False,
        request_budget=REQUEST_BUDGET,
        max_tree_entries=MAX_TREE_ENTRIES,
        max_skill_files=MAX_SKILL_FILES,
        max_markdown_files=MAX_MARKDOWN_FILES,
        max_reference_files=MAX_REFERENCE_FILES,
        max_file_bytes=MAX_FILE_BYTES,
        max_total_text_bytes=MAX_TOTAL_TEXT_BYTES,
        max_path_depth=MAX_PATH_DEPTH,
        allowed_hosts=ALLOWED_HOSTS,
        follow_redirects=False,
        no_retry=True,
        no_fallback=True,
    )


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _registered_input(
    root: Path, work_id: str
) -> tuple[Path, str, Path, GitHubSkillTarget]:
    try:
        order = load_work_order(root, work_id)
        current = WashEventLedger(root).current(work_id)
        if (
            order.source_kind != "github-skill"
            or current.source_kind != "github-skill"
            or current.stage != "source_registered"
            or current.source_id != order.source_input_sha256
        ):
            raise ValueError
        source = verify_private_relative(order.private_root, "source-input.txt")
        digest = sha256_file(source)
        lines = source.read_text("utf-8").splitlines()
        if digest != order.source_input_sha256 or len(lines) != 1:
            raise ValueError
        target = parse_github_skill_url(lines[0])
        return source, digest, order.private_root, target
    except (
        GitHubSkillProviderError,
        OSError,
        SourceContractError,
        SourceLedgerError,
        UnicodeError,
        ValueError,
    ):
        raise GitHubSkillAcquisitionError("github-skill-input-invalid") from None


def plan_github_skill_acquisition(
    root: Path, work_id: str
) -> GitHubSkillActionPlan:
    """Publish one immutable offline acquisition plan and planned ledger event."""

    _, digest, private_root, _ = _registered_input(Path(root), work_id)
    plan = _expected_plan(work_id, digest)
    plan_path = verify_private_relative(
        private_root, "github-skill-acquisition-plan.json"
    )
    published = False
    try:
        plan_sha = publish_json_exclusive(private_root, plan_path, _plan_value(plan))
        published = True
        current = WashEventLedger(root).current(work_id)
        WashEventLedger(root).append(
            StageEvent(
                schema_version=2,
                work_id=work_id,
                source_id=digest,
                source_kind="github-skill",
                stage="github_skill_planned",
                result="ok",
                artifact_label="github-skill-plan",
                artifact_sha256=plan_sha,
                timestamp=_timestamp(),
                previous_event_sha256=event_sha256(current),
            )
        )
        return plan
    except (OSError, SourceContractError, SourceLedgerError):
        if published:
            try:
                plan_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise GitHubSkillAcquisitionError("github-skill-publication-failed") from None


def _approved_plan(
    root: Path, work_id: str, approval: Path
) -> tuple[GitHubSkillActionPlan, Path, Path, GitHubSkillTarget]:
    try:
        current = WashEventLedger(root).current(work_id)
        if (
            current.source_kind != "github-skill"
            or current.stage != "github_skill_planned"
            or current.artifact_label != "github-skill-plan"
        ):
            raise SourceContractError("approval-scope-mismatch")
        private_root = WorkbenchPaths(root).private_source(work_id)
        source = verify_private_relative(private_root, "source-input.txt")
        plan_path = verify_private_relative(
            private_root, "github-skill-acquisition-plan.json"
        )
        value = load_exact_json(plan_path, _PLAN_KEYS)
        input_sha256 = value.get("input_sha256")
        if not isinstance(input_sha256, str):
            raise SourceContractError("action-plan-invalid")
        plan = _expected_plan(work_id, input_sha256)
        if (
            value != _plan_value(plan)
            or sha256_file(plan_path) != current.artifact_sha256
            or plan.input_sha256 != current.source_id
        ):
            raise SourceContractError("action-plan-invalid")
        verify_approval_payload(value, approval, request_count=REQUEST_BUDGET)
    except (
        OSError,
        SourceContractError,
        SourceLedgerError,
        TypeError,
        ValueError,
    ):
        raise GitHubSkillAcquisitionError("github-skill-not-approved") from None
    try:
        digest = sha256_file(source)
    except SourceContractError:
        raise GitHubSkillAcquisitionError("github-skill-input-changed") from None
    if digest != plan.input_sha256:
        raise GitHubSkillAcquisitionError("github-skill-input-changed")
    try:
        lines = source.read_text("utf-8").splitlines()
        if len(lines) != 1:
            raise ValueError
        target = parse_github_skill_url(lines[0])
    except (GitHubSkillProviderError, OSError, UnicodeError, ValueError):
        raise GitHubSkillAcquisitionError("github-skill-input-changed") from None
    return plan, source, private_root, target


def _allowed_url(url: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
    ):
        raise GitHubSkillAcquisitionError("github-skill-response-invalid")


class _HttpGitHubTransport:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def _get(self, url: str, *, accept: str, limit: int) -> tuple[bytes, int, str]:
        _allowed_url(url)
        try:
            with self._client.stream(
                "GET",
                url,
                headers={
                    "accept": accept,
                    "accept-encoding": "identity",
                    "user-agent": "BoomEarth-GitHubSkill/1.0",
                    "x-github-api-version": "2022-11-28",
                },
                follow_redirects=False,
            ) as response:
                encoding = response.headers.get("content-encoding", "").strip().lower()
                if encoding not in {"", "identity"}:
                    raise GitHubSkillAcquisitionError(
                        "github-skill-response-invalid"
                    )
                declared = response.headers.get("content-length")
                if declared is not None:
                    if not declared.isascii() or not declared.isdigit():
                        raise GitHubSkillAcquisitionError(
                            "github-skill-response-invalid"
                        )
                    if int(declared) > limit:
                        raise GitHubSkillAcquisitionError(
                            "github-skill-byte-budget-exceeded"
                        )
                payload = bytearray()
                for chunk in response.iter_raw():
                    if len(payload) + len(chunk) > limit:
                        raise GitHubSkillAcquisitionError(
                            "github-skill-byte-budget-exceeded"
                        )
                    payload.extend(chunk)
                return bytes(payload), response.status_code, response.headers.get(
                    "content-type", ""
                ).split(";", 1)[0].strip().lower()
        except GitHubSkillAcquisitionError:
            raise
        except httpx.HTTPError:
            raise GitHubSkillAcquisitionError(
                "github-skill-repository-unavailable"
            ) from None

    def get_json(self, url: str) -> tuple[object, int]:
        payload, status, content_type = self._get(
            url, accept="application/vnd.github+json", limit=_API_MAX_BYTES
        )
        if content_type not in {"application/json", "application/vnd.github+json"}:
            raise GitHubSkillAcquisitionError("github-skill-response-invalid")
        try:
            return json.loads(payload), status
        except (UnicodeError, json.JSONDecodeError):
            raise GitHubSkillAcquisitionError("github-skill-response-invalid") from None

    def get_text(self, url: str) -> tuple[bytes, int]:
        payload, status, content_type = self._get(
            url,
            accept="text/plain,text/markdown,application/octet-stream",
            limit=MAX_FILE_BYTES,
        )
        if content_type not in {
            "text/plain",
            "text/markdown",
            "application/octet-stream",
        }:
            raise GitHubSkillAcquisitionError("github-skill-response-invalid")
        return payload, status


@dataclass(slots=True)
class _RequestCounter:
    transport: GitHubSkillTransport
    count: int = 0

    def _use(self) -> None:
        if self.count >= REQUEST_BUDGET:
            raise GitHubSkillAcquisitionError(
                "github-skill-request-budget-exceeded"
            )
        self.count += 1

    def json(self, url: str) -> tuple[object, int]:
        _allowed_url(url)
        self._use()
        return self.transport.get_json(url)

    def text(self, url: str) -> tuple[bytes, int]:
        _allowed_url(url)
        self._use()
        return self.transport.get_text(url)


def _repository_metadata(
    value: object, target: GitHubSkillTarget
) -> tuple[str, str, str, object]:
    if not isinstance(value, dict):
        raise GitHubSkillAcquisitionError("github-skill-response-invalid")
    full_name = value.get("full_name")
    description = value.get("description")
    default_branch = value.get("default_branch")
    license_value = value.get("license")
    if (
        not isinstance(full_name, str)
        or full_name.casefold() != f"{target.owner}/{target.repo}".casefold()
        or description is not None
        and not isinstance(description, str)
        or isinstance(description, str)
        and ("\n" in description or "\r" in description or len(description) > 4096)
        or not isinstance(default_branch, str)
        or _REF_RE.fullmatch(default_branch) is None
    ):
        raise GitHubSkillAcquisitionError("github-skill-response-invalid")
    return full_name, description or "", default_branch, license_value


def _commit_sha(value: object) -> str:
    if not isinstance(value, dict):
        raise GitHubSkillAcquisitionError("github-skill-ref-invalid")
    sha = value.get("sha")
    if not isinstance(sha, str) or _SHA1_RE.fullmatch(sha) is None:
        raise GitHubSkillAcquisitionError("github-skill-ref-invalid")
    return sha


def _license_status(metadata: object, value: object, status: int) -> str:
    if status == 404:
        return "UNDECLARED" if metadata is None else "UNKNOWN"
    if status != 200 or not isinstance(value, dict):
        raise GitHubSkillAcquisitionError("github-skill-response-invalid")
    license_value = value.get("license")
    if not isinstance(license_value, dict):
        return "UNKNOWN"
    spdx = license_value.get("spdx_id")
    if (
        not isinstance(spdx, str)
        or spdx in {"", "NOASSERTION", "OTHER"}
        or _SPDX_RE.fullmatch(spdx) is None
    ):
        return "UNKNOWN"
    return spdx


def _git_blob_sha(payload: bytes) -> str:
    prefix = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(prefix + payload).hexdigest()


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        raise GitHubSkillAcquisitionError(
            "github-skill-publication-failed"
        ) from None


def _after_staging_files(_source_root: Path) -> None:
    return None


def _after_pending_manifest(_source_root: Path) -> None:
    return None


def _after_formal_directory_move(_source_root: Path) -> None:
    return None


def _before_ledger_append(_manifest: Path) -> None:
    return None


def _initial_selection(
    target: GitHubSkillTarget, entries: tuple[GitTreeEntry, ...]
) -> tuple[list[str], dict[str, str]]:
    by_path = {entry.path: entry for entry in entries}
    root_readmes = [
        path
        for path in by_path
        if "/" not in path and path.casefold() == "readme.md"
    ]
    skill_paths = sorted(
        path for path in by_path if PurePosixPath(path).name == "SKILL.md"
    )
    if len(skill_paths) > MAX_SKILL_FILES:
        raise GitHubSkillAcquisitionError(
            "github-skill-request-budget-exceeded"
        )
    roles: dict[str, str] = {path: "root-readme" for path in root_readmes}
    if target.scope == "repository":
        selected_skills = skill_paths
    else:
        expected = (
            target.requested_path
            if target.scope == "skill-file"
            else f"{target.requested_path}/SKILL.md"
        )
        if expected not in by_path or expected not in skill_paths:
            raise GitHubSkillAcquisitionError("github-skill-skill-missing")
        selected_skills = [expected]
    if not selected_skills:
        raise GitHubSkillAcquisitionError("github-skill-skill-missing")
    roles.update({path: "skill" for path in selected_skills})
    selected = sorted(set(root_readmes + selected_skills))
    if len(selected) > MAX_MARKDOWN_FILES:
        raise GitHubSkillAcquisitionError(
            "github-skill-request-budget-exceeded"
        )
    if any(by_path[path].size_bytes > MAX_FILE_BYTES for path in selected):
        raise GitHubSkillAcquisitionError("github-skill-byte-budget-exceeded")
    return selected, roles


def _raw_url(target: GitHubSkillTarget, commit: str, path: str) -> str:
    return (
        f"https://raw.githubusercontent.com/{target.owner}/{target.repo}/"
        f"{commit}/{path}"
    )


def _validated_text(payload: bytes, entry: GitTreeEntry) -> str:
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > MAX_FILE_BYTES
        or b"\x00" in payload
        or payload.startswith(b"version https://git-lfs.github.com/spec/v1")
    ):
        raise GitHubSkillAcquisitionError("github-skill-byte-budget-exceeded")
    if len(payload) != entry.size_bytes or _git_blob_sha(payload) != entry.blob_sha:
        raise GitHubSkillAcquisitionError("github-skill-response-invalid")
    try:
        return payload.decode("utf-8")
    except UnicodeError:
        raise GitHubSkillAcquisitionError("github-skill-response-invalid") from None


def _publish_failure_receipt(
    private_root: Path,
    *,
    plan: GitHubSkillActionPlan,
    stage: str,
    error: str,
    request_count: int,
) -> None:
    try:
        target = verify_private_relative(private_root, FAILURE_RECEIPT)
        value: dict[str, object] = {
            "schema_version": 1,
            "work_id": plan.work_id,
            "input_sha256": plan.input_sha256,
            "plan_sha256": hashlib.sha256(
                canonical_json_bytes(_plan_value(plan))
            ).hexdigest(),
            "stage": stage if stage in _FAILURE_STAGES else "preflight",
            "error": (
                error
                if error in _FAILURE_ERRORS
                else "github-skill-response-invalid"
            ),
            "request_count": (
                request_count
                if isinstance(request_count, int)
                and 0 <= request_count <= REQUEST_BUDGET
                else 0
            ),
            "created_at": _timestamp(),
        }
        publish_json_exclusive(private_root, target, value)
    except (OSError, SourceContractError, TypeError, ValueError):
        return


def run_github_skill_acquisition(
    root: Path,
    work_id: str,
    approval: Path,
    *,
    transport: GitHubSkillTransport | None = None,
) -> ArtifactRecord:
    """Execute one approved bounded GitHub snapshot without retry or fallback."""

    plan, source, private_root, target = _approved_plan(
        Path(root), work_id, Path(approval)
    )
    formal_source = verify_private_relative(private_root, "github-skill-source")
    formal_manifest = verify_private_relative(
        private_root, "github-skill-manifest.json"
    )
    staging: Path | None = None
    source_published = False
    manifest_published = False
    completed = False
    interrupted = False
    owned_client: httpx.Client | None = None
    requests: _RequestCounter | None = None
    stage = "preflight"
    try:
        if formal_source.exists() or formal_manifest.exists():
            raise GitHubSkillAcquisitionError("github-skill-not-approved")
        if transport is None:
            owned_client = httpx.Client(
                follow_redirects=False,
                timeout=httpx.Timeout(
                    connect=10.0, read=30.0, write=10.0, pool=10.0
                ),
            )
            active_transport: GitHubSkillTransport = _HttpGitHubTransport(
                owned_client
            )
        else:
            active_transport = transport
        requests = _RequestCounter(active_transport)
        api_root = f"https://api.github.com/repos/{target.owner}/{target.repo}"
        stage = "repository-metadata"
        metadata_value, status = requests.json(api_root)
        if status != 200:
            raise GitHubSkillAcquisitionError(
                "github-skill-repository-unavailable"
            )
        repository, description, default_branch, license_metadata = (
            _repository_metadata(metadata_value, target)
        )
        requested_ref = target.requested_ref or default_branch
        stage = "commit"
        commit_value, status = requests.json(f"{api_root}/commits/{requested_ref}")
        if status != 200:
            raise GitHubSkillAcquisitionError("github-skill-ref-invalid")
        commit = _commit_sha(commit_value)
        stage = "license"
        license_value, license_status_code = requests.json(f"{api_root}/license")
        license_status = _license_status(
            license_metadata, license_value, license_status_code
        )
        stage = "tree"
        tree_value, status = requests.json(
            f"{api_root}/git/trees/{commit}?recursive=1"
        )
        if status != 200:
            raise GitHubSkillAcquisitionError(
                "github-skill-repository-unavailable"
            )
        entries = parse_tree_response(tree_value, max_entries=MAX_TREE_ENTRIES)
        entry_by_path = {entry.path: entry for entry in entries}
        stage = "selection"
        selected, roles = _initial_selection(target, entries)
        documents: dict[str, bytes] = {}
        descriptors: list[GitHubSkillDescriptor] = []
        total_text_bytes = 0
        for path in selected:
            stage = "document-fetch"
            payload, file_status = requests.text(_raw_url(target, commit, path))
            if file_status != 200:
                raise GitHubSkillAcquisitionError(
                    "github-skill-repository-unavailable"
                )
            text = _validated_text(payload, entry_by_path[path])
            total_text_bytes += len(payload)
            if total_text_bytes > MAX_TOTAL_TEXT_BYTES:
                raise GitHubSkillAcquisitionError(
                    "github-skill-byte-budget-exceeded"
                )
            documents[path] = payload
            if roles[path] == "skill":
                stage = "skill-parse"
                descriptors.append(
                    parse_skill_document(path, entry_by_path[path].blob_sha, text)
                )

        if target.scope != "repository":
            stage = "reference-discovery"
            skill_path = next(path for path, role in roles.items() if role == "skill")
            references = discover_markdown_references(
                skill_path, documents[skill_path].decode("utf-8")
            )
            if len(references) > MAX_REFERENCE_FILES:
                raise GitHubSkillAcquisitionError(
                    "github-skill-request-budget-exceeded"
                )
            for path in references:
                entry = entry_by_path.get(path)
                if entry is None:
                    raise GitHubSkillAcquisitionError("github-skill-path-invalid")
                if entry.size_bytes > MAX_FILE_BYTES:
                    raise GitHubSkillAcquisitionError(
                        "github-skill-byte-budget-exceeded"
                    )
                roles[path] = "reference"
                stage = "document-fetch"
                payload, file_status = requests.text(
                    _raw_url(target, commit, path)
                )
                if file_status != 200:
                    raise GitHubSkillAcquisitionError(
                        "github-skill-repository-unavailable"
                    )
                _validated_text(payload, entry)
                total_text_bytes += len(payload)
                if total_text_bytes > MAX_TOTAL_TEXT_BYTES:
                    raise GitHubSkillAcquisitionError(
                        "github-skill-byte-budget-exceeded"
                    )
                documents[path] = payload
        if len(documents) > MAX_MARKDOWN_FILES:
            raise GitHubSkillAcquisitionError(
                "github-skill-request-budget-exceeded"
            )

        stage = "snapshot-render"
        repository_markdown = render_repository_markdown(
            repository=repository,
            commit_sha=commit,
            license_status=license_status,
            documents=tuple(documents.items()),
            description=description,
            default_branch=default_branch,
            skills=tuple(descriptors),
        )
        stage = "publication"
        staging = Path(
            tempfile.mkdtemp(prefix=".github-skill-staging-", dir=private_root)
        )
        staged_source = staging / "github-skill-source"
        selected_root = staged_source / "selected-files"
        file_rows: list[dict[str, object]] = []
        for path in sorted(documents):
            payload = documents[path]
            local_relative = f"github-skill-source/selected-files/{path}"
            _write_exclusive(selected_root / Path(*PurePosixPath(path).parts), payload)
            file_rows.append(
                {
                    "path": path,
                    "blob_sha": entry_by_path[path].blob_sha,
                    "local_path": local_relative,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                    "role": roles[path],
                }
            )
        _write_exclusive(staged_source / "repository.md", repository_markdown)
        manifest_value: dict[str, object] = {
            "schema_version": 1,
            "work_id": work_id,
            "source_kind": "github-skill",
            "source_input_sha256": plan.input_sha256,
            "repository": repository,
            "requested_scope": target.scope,
            "requested_path": target.requested_path,
            "resolved_commit": commit,
            "default_branch": default_branch,
            "license_status": license_status,
            "tree_sha256": hashlib.sha256(
                canonical_json_bytes(tree_value)
            ).hexdigest(),
            "repository_markdown_path": "github-skill-source/repository.md",
            "repository_markdown_sha256": hashlib.sha256(
                repository_markdown
            ).hexdigest(),
            "request_count": requests.count,
            "total_text_bytes": total_text_bytes,
            "files": file_rows,
            "created_at": _timestamp(),
        }
        _after_staging_files(staged_source)
        _write_exclusive(
            staged_source / RECOVERY_MANIFEST,
            canonical_json_bytes(manifest_value),
        )
        _after_pending_manifest(staged_source)
        if sha256_file(source) != plan.input_sha256:
            raise GitHubSkillAcquisitionError("github-skill-input-changed")
        staged_source.rename(formal_source)
        source_published = True
        _after_formal_directory_move(formal_source)
        manifest_sha = publish_json_exclusive(
            private_root, formal_manifest, manifest_value
        )
        manifest_published = True
        _before_ledger_append(formal_manifest)
        current = WashEventLedger(root).current(work_id)
        WashEventLedger(root).append(
            StageEvent(
                schema_version=2,
                work_id=work_id,
                source_id=plan.input_sha256,
                source_kind="github-skill",
                stage="github_skill_ready",
                result="ok",
                artifact_label="github-skill-manifest",
                artifact_sha256=manifest_sha,
                timestamp=_timestamp(),
                previous_event_sha256=event_sha256(current),
            )
        )
        completed = True
        try:
            (formal_source / RECOVERY_MANIFEST).unlink(missing_ok=True)
        except OSError:
            pass
        return ArtifactRecord(
            relative_path="github-skill-manifest.json",
            sha256=manifest_sha,
            size_bytes=formal_manifest.stat().st_size,
            path=formal_manifest,
        )
    except GitHubSkillAcquisitionError as error:
        _publish_failure_receipt(
            private_root,
            plan=plan,
            stage=stage,
            error=str(error),
            request_count=requests.count if requests is not None else 0,
        )
        raise
    except GitHubSkillProviderError as error:
        code = str(error)
        allowed = {
            "github-skill-input-invalid",
            "github-skill-path-invalid",
            "github-skill-tree-truncated",
            "github-skill-response-invalid",
            "github-skill-request-budget-exceeded",
            "github-skill-byte-budget-exceeded",
        }
        mapped = code if code in allowed else "github-skill-response-invalid"
        _publish_failure_receipt(
            private_root,
            plan=plan,
            stage=stage,
            error=mapped,
            request_count=requests.count if requests is not None else 0,
        )
        raise GitHubSkillAcquisitionError(mapped) from None
    except (OSError, SourceContractError, SourceLedgerError, TypeError, ValueError):
        _publish_failure_receipt(
            private_root,
            plan=plan,
            stage=stage,
            error="github-skill-publication-failed",
            request_count=requests.count if requests is not None else 0,
        )
        raise GitHubSkillAcquisitionError(
            "github-skill-publication-failed"
        ) from None
    except BaseException:
        interrupted = True
        raise
    finally:
        if owned_client is not None:
            owned_client.close()
        if staging is not None and not interrupted:
            shutil.rmtree(staging, ignore_errors=True)
        if manifest_published and not completed and not interrupted:
            try:
                formal_manifest.unlink(missing_ok=True)
            except OSError:
                pass
        if source_published and not completed and not interrupted:
            shutil.rmtree(formal_source, ignore_errors=True)


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _recovery_context(
    root: Path, work_id: str
) -> tuple[GitHubSkillActionPlan, Path, GitHubSkillTarget, str]:
    try:
        current = WashEventLedger(root).current(work_id)
        if (
            current.source_kind != "github-skill"
            or current.stage not in {"github_skill_planned", "github_skill_ready"}
        ):
            raise ValueError
        private_root = WorkbenchPaths(root).private_source(work_id)
        plan_path = verify_private_relative(
            private_root, "github-skill-acquisition-plan.json"
        )
        value = load_exact_json(plan_path, _PLAN_KEYS)
        input_sha256 = value.get("input_sha256")
        if not isinstance(input_sha256, str):
            raise ValueError
        plan = _expected_plan(work_id, input_sha256)
        if value != _plan_value(plan):
            raise ValueError
        source = verify_private_relative(private_root, "source-input.txt")
        if sha256_file(source) != plan.input_sha256:
            raise ValueError
        lines = source.read_text("utf-8").splitlines()
        if len(lines) != 1:
            raise ValueError
        target = parse_github_skill_url(lines[0])
        return plan, private_root, target, current.stage
    except (
        GitHubSkillProviderError,
        OSError,
        SourceContractError,
        SourceLedgerError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise GitHubSkillAcquisitionError(
            "github-skill-publication-failed"
        ) from None


def _valid_private_manifest(
    private_root: Path,
    manifest_path: Path,
    *,
    source_root: Path,
    plan: GitHubSkillActionPlan,
    target: GitHubSkillTarget,
) -> dict[str, object]:
    try:
        value = load_exact_json(manifest_path, _MANIFEST_KEYS)
        files = value["files"]
        repository = value["repository"]
        if (
            value["schema_version"] != 1
            or value["work_id"] != plan.work_id
            or value["source_kind"] != "github-skill"
            or value["source_input_sha256"] != plan.input_sha256
            or not isinstance(repository, str)
            or repository.casefold()
            != f"{target.owner}/{target.repo}".casefold()
            or value["requested_scope"] != target.scope
            or value["requested_path"] != target.requested_path
            or not isinstance(value["resolved_commit"], str)
            or _SHA1_RE.fullmatch(value["resolved_commit"]) is None
            or not isinstance(value["default_branch"], str)
            or _REF_RE.fullmatch(value["default_branch"]) is None
            or not isinstance(value["license_status"], str)
            or value["license_status"] not in {"UNDECLARED", "UNKNOWN"}
            and _SPDX_RE.fullmatch(value["license_status"]) is None
            or not isinstance(value["tree_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["tree_sha256"]) is None
            or value["repository_markdown_path"]
            != "github-skill-source/repository.md"
            or not isinstance(value["repository_markdown_sha256"], str)
            or re.fullmatch(
                r"[0-9a-f]{64}", value["repository_markdown_sha256"]
            )
            is None
            or type(value["request_count"]) is not int
            or not 1 <= value["request_count"] <= REQUEST_BUDGET
            or type(value["total_text_bytes"]) is not int
            or not 0 < value["total_text_bytes"] <= MAX_TOTAL_TEXT_BYTES
            or not isinstance(files, list)
            or not 1 <= len(files) <= MAX_MARKDOWN_FILES
            or not _valid_timestamp(value["created_at"])
        ):
            raise ValueError
        repository_markdown = source_root / "repository.md"
        if (
            sha256_file(repository_markdown)
            != value["repository_markdown_sha256"]
        ):
            raise ValueError
        seen: set[str] = set()
        total = 0
        for raw in files:
            if not isinstance(raw, dict) or set(raw) != _MANIFEST_FILE_KEYS:
                raise ValueError
            path = raw["path"]
            local_path = raw["local_path"]
            if not isinstance(path, str) or not isinstance(local_path, str):
                raise ValueError
            pure = PurePosixPath(path)
            if (
                pure.is_absolute()
                or ".." in pure.parts
                or len(pure.parts) > MAX_PATH_DEPTH
                or pure.suffix.lower() != ".md"
                or path.casefold() in seen
                or local_path != f"github-skill-source/selected-files/{path}"
                or not isinstance(raw["blob_sha"], str)
                or _SHA1_RE.fullmatch(raw["blob_sha"]) is None
                or not isinstance(raw["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", raw["sha256"]) is None
                or type(raw["bytes"]) is not int
                or not 0 < raw["bytes"] <= MAX_FILE_BYTES
                or raw["role"] not in {"root-readme", "skill", "reference"}
            ):
                raise ValueError
            selected = source_root / "selected-files" / Path(*pure.parts)
            if selected.stat().st_size != raw["bytes"]:
                raise ValueError
            if sha256_file(selected) != raw["sha256"]:
                raise ValueError
            total += raw["bytes"]
            seen.add(path.casefold())
        if total != value["total_text_bytes"]:
            raise ValueError
        return value
    except (
        KeyError,
        OSError,
        SourceContractError,
        TypeError,
        ValueError,
    ):
        raise GitHubSkillAcquisitionError(
            "github-skill-publication-failed"
        ) from None


def _record(formal_manifest: Path) -> ArtifactRecord:
    try:
        return ArtifactRecord(
            relative_path="github-skill-manifest.json",
            sha256=sha256_file(formal_manifest),
            size_bytes=formal_manifest.stat().st_size,
            path=formal_manifest,
        )
    except (OSError, SourceContractError):
        raise GitHubSkillAcquisitionError(
            "github-skill-publication-failed"
        ) from None


def _remove_empty_staging_roots(staging_roots: list[Path]) -> None:
    for staging_root in staging_roots:
        try:
            staging_root.rmdir()
        except OSError:
            continue


def recover_github_skill_acquisition(root: Path, work_id: str) -> ArtifactRecord:
    """Recover only hash-bound local publication state without network access."""

    plan, private_root, target, stage = _recovery_context(Path(root), work_id)
    formal_source = verify_private_relative(private_root, "github-skill-source")
    formal_manifest = verify_private_relative(
        private_root, "github-skill-manifest.json"
    )
    staging_roots = sorted(private_root.glob(".github-skill-staging-*"))
    if stage == "github_skill_ready":
        if not formal_source.is_dir() or not formal_manifest.is_file():
            raise GitHubSkillAcquisitionError("github-skill-publication-failed")
        try:
            current = WashEventLedger(root).current(work_id)
            if sha256_file(formal_manifest) != current.artifact_sha256:
                raise ValueError
        except (SourceContractError, SourceLedgerError, ValueError):
            raise GitHubSkillAcquisitionError(
                "github-skill-publication-failed"
            ) from None
        _valid_private_manifest(
            private_root,
            formal_manifest,
            source_root=formal_source,
            plan=plan,
            target=target,
        )
        try:
            (formal_source / RECOVERY_MANIFEST).unlink(missing_ok=True)
        except OSError:
            pass
        _remove_empty_staging_roots(staging_roots)
        return _record(formal_manifest)

    source_root: Path | None = None
    staging_root: Path | None = None
    if formal_source.exists() or formal_source.is_symlink():
        if formal_source.is_symlink() or not formal_source.is_dir():
            raise GitHubSkillAcquisitionError("github-skill-publication-failed")
        source_root = formal_source
    elif len(staging_roots) == 1:
        staging_root = staging_roots[0]
        candidate = staging_root / "github-skill-source"
        if staging_root.is_symlink() or not staging_root.is_dir() or not candidate.is_dir():
            raise GitHubSkillAcquisitionError("github-skill-publication-failed")
        source_root = candidate
    elif staging_roots:
        raise GitHubSkillAcquisitionError("github-skill-publication-failed")

    if source_root is None:
        raise GitHubSkillAcquisitionError("github-skill-publication-failed")
    pending = source_root / RECOVERY_MANIFEST
    manifest_source = pending if pending.is_file() else formal_manifest
    if not manifest_source.is_file():
        if staging_root is not None:
            shutil.rmtree(staging_root, ignore_errors=True)
        raise GitHubSkillAcquisitionError("github-skill-publication-failed")
    value = _valid_private_manifest(
        private_root,
        manifest_source,
        source_root=source_root,
        plan=plan,
        target=target,
    )
    if formal_manifest.exists():
        formal_value = _valid_private_manifest(
            private_root,
            formal_manifest,
            source_root=source_root,
            plan=plan,
            target=target,
        )
        if canonical_json_bytes(formal_value) != canonical_json_bytes(value):
            raise GitHubSkillAcquisitionError("github-skill-publication-failed")
    try:
        if source_root != formal_source:
            if formal_source.exists():
                raise OSError
            source_root.rename(formal_source)
            source_root = formal_source
            if staging_root is not None:
                staging_root.rmdir()
        if not formal_manifest.exists():
            manifest_sha = publish_json_exclusive(
                private_root, formal_manifest, value
            )
        else:
            manifest_sha = sha256_file(formal_manifest)
        current = WashEventLedger(root).current(work_id)
        WashEventLedger(root).append(
            StageEvent(
                schema_version=2,
                work_id=work_id,
                source_id=plan.input_sha256,
                source_kind="github-skill",
                stage="github_skill_ready",
                result="ok",
                artifact_label="github-skill-manifest",
                artifact_sha256=manifest_sha,
                timestamp=_timestamp(),
                previous_event_sha256=event_sha256(current),
            )
        )
        try:
            (formal_source / RECOVERY_MANIFEST).unlink(missing_ok=True)
        except OSError:
            pass
        _remove_empty_staging_roots(staging_roots)
        return _record(formal_manifest)
    except (
        OSError,
        SourceContractError,
        SourceLedgerError,
    ):
        raise GitHubSkillAcquisitionError(
            "github-skill-publication-failed"
        ) from None


__all__ = [
    "GitHubSkillActionPlan",
    "GitHubSkillAcquisitionError",
    "GitHubSkillTransport",
    "plan_github_skill_acquisition",
    "recover_github_skill_acquisition",
    "run_github_skill_acquisition",
]

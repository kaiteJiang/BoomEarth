"""Pure, source-redacted parsing primitives for public GitHub Skill repositories."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit


_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_PATH_PART_RE = re.compile(r"[A-Za-z0-9._-]+")
_SKILL_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_SHA1_RE = re.compile(r"[0-9a-f]{40}")
_LICENSE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}")
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^()\s]+)\)")
_MAX_PATH_DEPTH = 12
_MAX_SKILL_NAME_CHARS = 64
_MAX_SKILL_DESCRIPTION_CHARS = 1024


class GitHubSkillProviderError(ValueError):
    """A fixed-message provider parsing failure that never renders source text."""

    def __repr__(self) -> str:
        return "GitHubSkillProviderError(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class GitHubSkillTarget:
    owner: str
    repo: str
    scope: Literal["repository", "skill-directory", "skill-file"]
    requested_ref: str | None
    requested_path: str

    def __repr__(self) -> str:
        return "GitHubSkillTarget(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class GitTreeEntry:
    path: str
    blob_sha: str
    size_bytes: int

    def __repr__(self) -> str:
        return "GitTreeEntry(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class GitHubSkillDescriptor:
    path: str
    name: str
    description: str
    blob_sha: str

    def __repr__(self) -> str:
        return "GitHubSkillDescriptor(<redacted>)"


def _safe_path(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError
    if allow_empty and value == "":
        return value
    if (
        not value
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "%" in value
        or "\x00" in value
    ):
        raise ValueError
    parts = value.split("/")
    if (
        len(parts) > _MAX_PATH_DEPTH
        or any(part in {"", ".", ".."} for part in parts)
        or any(_PATH_PART_RE.fullmatch(part) is None for part in parts)
    ):
        raise ValueError
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != value:
        raise ValueError
    return normalized


def _owner(value: str) -> str:
    if _OWNER_RE.fullmatch(value) is None or value.endswith("-"):
        raise ValueError
    return value


def _repo(value: str, *, allow_dot_git: bool) -> str:
    candidate = value
    if allow_dot_git and candidate.endswith(".git"):
        candidate = candidate[:-4]
    if (
        not candidate
        or candidate in {".", ".."}
        or candidate.endswith(".git")
        or candidate.endswith(".wiki")
        or _REPO_RE.fullmatch(candidate) is None
    ):
        raise ValueError
    return candidate


def parse_github_skill_url(value: str) -> GitHubSkillTarget:
    """Parse one canonical public GitHub repository or Skill URL without I/O."""

    try:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or "\n" in value
            or "\r" in value
        ):
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        raw_parts = parsed.path.split("/")
        if not raw_parts or raw_parts[0] != "":
            raise ValueError
        parts = raw_parts[1:]
        if len(parts) == 2:
            owner = _owner(parts[0])
            repo = _repo(parts[1], allow_dot_git=True)
            return GitHubSkillTarget(owner, repo, "repository", None, "")
        if len(parts) < 5 or parts[2] not in {"tree", "blob"}:
            raise ValueError
        owner = _owner(parts[0])
        repo = _repo(parts[1], allow_dot_git=False)
        requested_ref = parts[3]
        if _REF_RE.fullmatch(requested_ref) is None:
            raise ValueError
        requested_path = _safe_path("/".join(parts[4:]))
        if parts[2] == "blob":
            if PurePosixPath(requested_path).name != "SKILL.md":
                raise ValueError
            scope: Literal["skill-directory", "skill-file"] = "skill-file"
        else:
            scope = "skill-directory"
        return GitHubSkillTarget(
            owner,
            repo,
            scope,
            requested_ref,
            requested_path,
        )
    except (UnicodeError, ValueError):
        raise GitHubSkillProviderError("github-skill-input-invalid") from None


def _tree_entry(value: object) -> GitTreeEntry | None:
    if not isinstance(value, dict):
        raise ValueError
    entry_type = value.get("type")
    path = value.get("path")
    sha = value.get("sha")
    url = value.get("url")
    if (
        not isinstance(path, str)
        or not path
        or "\x00" in path
        or _SHA1_RE.fullmatch(sha if isinstance(sha, str) else "") is None
        or not isinstance(url, str)
    ):
        raise ValueError
    try:
        path.encode("utf-8")
    except UnicodeError:
        raise ValueError from None
    if entry_type == "tree":
        if (
            set(value) != {"path", "mode", "type", "sha", "url"}
            or value["mode"] != "040000"
        ):
            raise ValueError
        return None
    if entry_type == "commit":
        if (
            set(value) != {"path", "mode", "type", "sha", "url"}
            or value["mode"] != "160000"
        ):
            raise ValueError
        return None
    if (
        entry_type != "blob"
        or set(value) != {"path", "mode", "type", "sha", "size", "url"}
        or value["mode"] not in {"100644", "100755", "120000"}
        or type(value["size"]) is not int
        or value["size"] < 0
    ):
        raise ValueError
    if value["mode"] == "120000" or not path.lower().endswith(".md"):
        return None
    safe_path = _safe_path(path)
    return GitTreeEntry(safe_path, sha, value["size"])


def parse_tree_response(
    value: object, *, max_entries: int
) -> tuple[GitTreeEntry, ...]:
    """Validate a recursive Git tree and return safe Markdown blob entries."""

    try:
        if (
            type(max_entries) is not int
            or max_entries <= 0
            or not isinstance(value, dict)
            or set(value) != {"sha", "url", "tree", "truncated"}
            or _SHA1_RE.fullmatch(value["sha"] if isinstance(value["sha"], str) else "")
            is None
            or not isinstance(value["url"], str)
            or type(value["truncated"]) is not bool
            or not isinstance(value["tree"], list)
        ):
            raise ValueError
        if value["truncated"] is True:
            raise GitHubSkillProviderError("github-skill-tree-truncated")
        if len(value["tree"]) > max_entries:
            raise GitHubSkillProviderError(
                "github-skill-request-budget-exceeded"
            )
        entries: list[GitTreeEntry] = []
        seen: set[str] = set()
        for raw_entry in value["tree"]:
            entry = _tree_entry(raw_entry)
            if entry is None:
                continue
            alias = entry.path.casefold()
            if alias in seen:
                raise ValueError
            seen.add(alias)
            entries.append(entry)
        return tuple(sorted(entries, key=lambda item: item.path))
    except GitHubSkillProviderError:
        raise
    except (TypeError, ValueError):
        raise GitHubSkillProviderError("github-skill-response-invalid") from None


def _frontmatter_scalar(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError
    if candidate.startswith('"') or candidate.endswith('"'):
        if not (candidate.startswith('"') and candidate.endswith('"')):
            raise ValueError
        decoded = json.loads(candidate)
        if not isinstance(decoded, str):
            raise ValueError
        candidate = decoded
    elif candidate.startswith("'") or candidate.endswith("'"):
        if not (candidate.startswith("'") and candidate.endswith("'")):
            raise ValueError
        candidate = candidate[1:-1].replace("''", "'")
    if not candidate or any(ord(character) < 32 for character in candidate):
        raise ValueError
    return candidate


def _frontmatter_block_scalar(style: str, lines: list[str]) -> str:
    if style not in {">", ">-", ">+", "|", "|-", "|+"} or not lines:
        raise ValueError
    values: list[str] = []
    for line in lines:
        if line and (not line[0].isspace() or line[0] == "\t"):
            raise ValueError
        values.append(line.strip())
    candidate = (
        " ".join(value for value in values if value)
        if style.startswith(">")
        else "\n".join(values).strip("\n")
    )
    if not candidate or any(
        ord(character) < 32 and character != "\n" for character in candidate
    ):
        raise ValueError
    return candidate


def parse_skill_document(
    path: str, blob_sha: str, text: str
) -> GitHubSkillDescriptor:
    """Extract the inert name and description from one Skill frontmatter block."""

    try:
        safe_path = _safe_path(path)
        if (
            PurePosixPath(safe_path).name != "SKILL.md"
            or _SHA1_RE.fullmatch(blob_sha) is None
            or not isinstance(text, str)
            or "\x00" in text
        ):
            raise ValueError
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            raise ValueError
        try:
            closing = lines.index("---", 1)
        except ValueError:
            raise ValueError from None
        values: dict[str, str] = {}
        seen_keys: set[str] = set()
        frontmatter = lines[1:closing]
        index = 0
        while index < len(frontmatter):
            line = frontmatter[index]
            if not line:
                index += 1
                continue
            if line[0].isspace() or ":" not in line:
                raise ValueError
            key, raw_value = line.split(":", 1)
            if (
                re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", key) is None
                or key in seen_keys
            ):
                raise ValueError
            seen_keys.add(key)
            if key not in {"name", "description"}:
                index += 1
                while index < len(frontmatter) and (
                    not frontmatter[index] or frontmatter[index][0].isspace()
                ):
                    if frontmatter[index].startswith("\t"):
                        raise ValueError
                    index += 1
                continue
            style = raw_value.strip()
            if key == "description" and style in {">", ">-", ">+", "|", "|-", "|+"}:
                index += 1
                block: list[str] = []
                while index < len(frontmatter) and (
                    not frontmatter[index] or frontmatter[index][0].isspace()
                ):
                    block.append(frontmatter[index])
                    index += 1
                values[key] = _frontmatter_block_scalar(style, block)
                continue
            values[key] = _frontmatter_scalar(raw_value)
            index += 1
        if not {"name", "description"}.issubset(values):
            raise ValueError
        name = values["name"]
        description = values["description"]
        if (
            len(name) > _MAX_SKILL_NAME_CHARS
            or _SKILL_NAME_RE.fullmatch(name) is None
            or len(description) > _MAX_SKILL_DESCRIPTION_CHARS
        ):
            raise ValueError
        return GitHubSkillDescriptor(safe_path, name, description, blob_sha)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise GitHubSkillProviderError("github-skill-response-invalid") from None


def discover_markdown_references(skill_path: str, text: str) -> tuple[str, ...]:
    """Resolve direct local Markdown links without escaping the selected Skill."""

    try:
        safe_skill = _safe_path(skill_path)
        if PurePosixPath(safe_skill).name != "SKILL.md" or not isinstance(text, str):
            raise ValueError
        base = PurePosixPath(safe_skill).parent
        references: set[str] = set()
        for match in _MARKDOWN_LINK_RE.finditer(text):
            target = match.group(1)
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            if parsed.query or not parsed.path.lower().endswith(".md"):
                continue
            if (
                parsed.path.startswith("/")
                or "\\" in parsed.path
                or "%" in parsed.path
            ):
                raise GitHubSkillProviderError("github-skill-path-invalid")
            relative = _safe_path(parsed.path)
            combined = _safe_path((base / PurePosixPath(relative)).as_posix())
            try:
                PurePosixPath(combined).relative_to(base)
            except ValueError:
                raise GitHubSkillProviderError("github-skill-path-invalid") from None
            references.add(combined)
        return tuple(sorted(references))
    except GitHubSkillProviderError:
        raise
    except (TypeError, ValueError):
        raise GitHubSkillProviderError("github-skill-path-invalid") from None


def _repository(value: str) -> str:
    if not isinstance(value, str) or value.count("/") != 1:
        raise ValueError
    owner, repo = value.split("/", 1)
    return f"{_owner(owner)}/{_repo(repo, allow_dot_git=False)}"


def render_repository_markdown(
    *,
    repository: str,
    commit_sha: str,
    license_status: str,
    documents: tuple[tuple[str, bytes], ...],
    description: str = "",
    default_branch: str = "",
    skills: tuple[GitHubSkillDescriptor, ...] = (),
) -> bytes:
    """Render hash-bound selected Markdown bytes into one deterministic digest."""

    try:
        full_name = _repository(repository)
        if (
            _SHA1_RE.fullmatch(commit_sha) is None
            or license_status not in {"UNDECLARED", "UNKNOWN"}
            and _LICENSE_RE.fullmatch(license_status) is None
            or not isinstance(documents, tuple)
            or not documents
            or not isinstance(description, str)
            or "\n" in description
            or "\r" in description
            or not isinstance(default_branch, str)
            or (default_branch and _REF_RE.fullmatch(default_branch) is None)
            or not isinstance(skills, tuple)
            or any(not isinstance(skill, GitHubSkillDescriptor) for skill in skills)
        ):
            raise ValueError
        selected: list[tuple[str, bytes, str]] = []
        seen: set[str] = set()
        for item in documents:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError
            path, payload = item
            safe_path = _safe_path(path)
            if (
                PurePosixPath(safe_path).suffix.lower() != ".md"
                or not isinstance(payload, bytes)
                or not payload
                or b"\x00" in payload
            ):
                raise ValueError
            payload.decode("utf-8")
            alias = safe_path.casefold()
            if alias in seen:
                raise ValueError
            seen.add(alias)
            selected.append(
                (safe_path, payload, hashlib.sha256(payload).hexdigest())
            )
        output = (
            "# Private GitHub Skill repository digest\n\n"
            f"- Repository: `{full_name}`\n"
            f"- Commit: `{commit_sha}`\n"
            f"- License: `{license_status}`\n\n"
            f"- Description: {description or '(none)'}\n"
            f"- Default branch: `{default_branch or '(unknown)'}`\n"
            f"- Skills: {len(skills)} Skill documents\n\n"
            "## Skill index\n"
        ).encode("utf-8")
        for skill in sorted(skills, key=lambda item: item.path):
            output += (
                f"\n- `{skill.path}` — `{skill.name}`: {skill.description}\n"
            ).encode("utf-8")
        output += (
            "\n"
            "## Selected Markdown files\n"
        ).encode("utf-8")
        for path, payload, digest in sorted(selected, key=lambda item: item[0]):
            output += (
                f"\n### `{path}`\n\n"
                f"SHA-256: `{digest}`\n\n"
                f"----- BEGIN FILE: {path} -----\n"
            ).encode("utf-8")
            output += payload
            if not payload.endswith(b"\n"):
                output += b"\n"
            output += f"----- END FILE: {path} -----\n".encode("utf-8")
        return output
    except (TypeError, UnicodeError, ValueError):
        raise GitHubSkillProviderError("github-skill-response-invalid") from None


__all__ = [
    "GitHubSkillDescriptor",
    "GitHubSkillProviderError",
    "GitHubSkillTarget",
    "GitTreeEntry",
    "discover_markdown_references",
    "parse_github_skill_url",
    "parse_skill_document",
    "parse_tree_response",
    "render_repository_markdown",
]

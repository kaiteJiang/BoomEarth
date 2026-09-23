"""Read-only GitHub transport ladder with bounded retries and private diagnostics.

Nothing fetched from a repository is installed, checked out, or executed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, unquote

import httpx

from boomearth.workbench.github_skill_acquisition import (
    GitHubSkillAcquisitionError, _HttpGitHubTransport, _API_MAX_BYTES,
    MAX_FILE_BYTES, MAX_TREE_ENTRIES, _allowed_url, _validated_text,
)


class ResilientGitHubTransport:
    """Try configured HTTP, direct HTTP, then the Git read protocol.

    A successful method stays preferred. Physical HTTP attempts and Git network
    commands share one budget. Logs contain no response bodies or credentials.
    """

    def __init__(self, target, private_root: Path, request_budget: int = 288,
                 *, clients=None, sleep=time.sleep):
        self.target = target
        self.private_root = private_root
        self.request_budget = request_budget
        self.request_count = 0
        self.sleep = sleep
        self.clients = clients or [
            httpx.Client(timeout=15, follow_redirects=False, trust_env=True),
            httpx.Client(timeout=15, follow_redirects=False, trust_env=False),
        ]
        self.transports = [_HttpGitHubTransport(c) for c in self.clients]
        self.preferred = 0
        self.git_only = False
        self.scratch = None
        self.git_root = None
        self.commit = None
        self.branch = None
        self.diagnostics = []

    def _use(self):
        if self.request_count >= self.request_budget:
            raise GitHubSkillAcquisitionError("github-skill-request-budget-exceeded")
        self.request_count += 1

    def _record(self, method, attempt, *, status=None, error=None):
        self.diagnostics.append(dict(method=method, attempt=attempt,
                                     status=status, error=error))
        # Flush at each attempt so interruption does not erase the diagnosis.
        destination = self.private_root / "github-acquisition-attempts.json"
        pending = destination.with_suffix(".tmp")
        pending.write_text(json.dumps(self.diagnostics, indent=2), "utf-8")
        pending.replace(destination)

    def _read(self, kind, url, entry=None):
        _allowed_url(url)
        if not self.git_only:
            for index in dict.fromkeys((self.preferred, 0, 1)):
                for attempt in range(1, 4):
                    self._use()
                    try:
                        value, status = getattr(self.transports[index], kind)(url)
                        self._record(("http-environment", "http-direct")[index],
                                     attempt, status=status)
                        # A missing license is a valid finding, not a failed read.
                        license_missing = url.endswith("/license") and status == 404
                        truncated = isinstance(value, dict) and value.get("truncated") is True
                        if (status == 200 and not truncated) or license_missing:
                            if entry is not None:
                                _validated_text(value, entry)
                            self.preferred = index
                            return value, status
                        if status in (400, 401, 403, 404, 422, 429) or truncated:
                            break  # Retrying a denied/limited route adds no value.
                    except GitHubSkillAcquisitionError as exc:
                        if str(exc) == "github-skill-byte-budget-exceeded":
                            raise
                        cause = getattr(self.transports[index], "last_error", None)
                        status = getattr(self.transports[index], "last_status", None)
                        self._record(("http-environment", "http-direct")[index],
                                     attempt, status=status, error=cause or str(exc))
                        if status in (400, 401, 403, 404, 422, 429):
                            break
                    if attempt < 3:
                        self.sleep(attempt)
            self.git_only = True
        for attempt in range(1, 4):
            try:
                result = self._git_read(kind, url)
                if entry is not None:
                    _validated_text(result[0], entry)
                self._record("git-read", attempt, status=result[1])
                return result
            except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
                self._record("git-read", attempt, error="git-read-failed")
                if attempt < 3:
                    self.sleep(attempt)
        raise GitHubSkillAcquisitionError("github-skill-methods-exhausted")

    def get_json(self, url):
        return self._read("get_json", url)

    def get_text(self, url):
        return self._read("get_text", url)

    def get_verified_text(self, url, entry):
        return self._read("get_text", url, entry)

    def _git(self, *args, network=False, limit=_API_MAX_BYTES):
        if network:
            self._use()
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_LFS_SKIP_SMUDGE="1")
        command = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=" + os.devnull,
                   "-c", "http.followRedirects=false", "-c", "protocol.file.allow=never"]
        if self.git_root:
            command += ["--git-dir", str(self.git_root)]
        # Disk-backed output avoids unbounded subprocess stdout in memory.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(command + list(args), env=env, stdout=output,
                                    stderr=subprocess.DEVNULL, timeout=45, check=False)
            if result.returncode:
                raise ValueError("git-read-failed")
            if output.tell() > limit:
                raise GitHubSkillAcquisitionError("github-skill-byte-budget-exceeded")
            output.seek(0)
            return output.read()

    def _init_git(self):
        if self.commit:
            return
        if self.scratch is None:
            self.scratch = tempfile.TemporaryDirectory(prefix="github-read-", dir=self.private_root)
            repo_path = Path(self.scratch.name) / "repo.git"
            self._git("init", "--bare", str(repo_path))
            self.git_root = repo_path
        remote = f"https://github.com/{self.target.owner}/{self.target.repo}.git"
        refs = self._git("ls-remote", "--symref", remote, "HEAD", network=True).decode()
        self.branch = next((line.split("refs/heads/", 1)[1].split("\t", 1)[0]
                            for line in refs.splitlines() if line.startswith("ref: refs/heads/")), None)
        if not self.branch:
            raise ValueError("missing-default-branch")
        ref = self.target.requested_ref or self.branch
        self._git("fetch", "--depth=1", "--no-tags", remote, ref, network=True)
        self.commit = self._git("rev-parse", "FETCH_HEAD^{commit}").decode().strip()

    def _ensure_commit(self, commit):
        # A fallback after HTTP must use that HTTP-pinned commit, never current HEAD.
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise ValueError("invalid-commit")
        try:
            self._git("cat-file", "-e", commit + "^{commit}")
        except ValueError:
            remote = f"https://github.com/{self.target.owner}/{self.target.repo}.git"
            self._git("fetch", "--depth=1", "--no-tags", remote, commit, network=True)

    def _git_read(self, kind, url):
        self._init_git()
        root = f"https://api.github.com/repos/{self.target.owner}/{self.target.repo}"
        if kind == "get_text":
            parts = unquote(urlsplit(url).path).split("/", 4)
            commit, path = parts[3:5]
            self._ensure_commit(commit)
            size = int(self._git("cat-file", "-s", f"{commit}:{path}"))
            if size > MAX_FILE_BYTES:
                raise GitHubSkillAcquisitionError("github-skill-byte-budget-exceeded")
            return self._git("cat-file", "blob", f"{commit}:{path}", limit=MAX_FILE_BYTES), 200
        if url == root:
            return {"full_name": f"{self.target.owner}/{self.target.repo}",
                    "description": "", "default_branch": self.branch, "license": {}}, 200
        if "/commits/" in url:
            return {"sha": self.commit}, 200
        if url.endswith("/license"):
            # Git cannot establish an SPDX identity; do not invent MIT/UNDECLARED.
            return {"license": None}, 200
        if "/git/trees/" in url:
            commit = url.split("/git/trees/", 1)[1].split("?", 1)[0]
            self._ensure_commit(commit)
            raw = self._git("ls-tree", "-r", "-l", "-z", commit)
            entries = []
            for row in raw.split(b"\0"):
                if not row:
                    continue
                header, path = row.split(b"\t", 1)
                mode, kind, sha, size = header.decode().split()
                entry = dict(path=path.decode("utf-8"), mode=mode, type=kind,
                             sha=sha, url=root + "/git/blobs/" + sha)
                if kind == "blob":
                    entry["size"] = int(size)
                entries.append(entry)
                if len(entries) > MAX_TREE_ENTRIES:
                    raise GitHubSkillAcquisitionError("github-skill-request-budget-exceeded")
            tree_sha = self._git("rev-parse", commit + "^{tree}").decode().strip()
            return dict(sha=tree_sha, url=url, tree=entries, truncated=False), 200
        raise ValueError("unsupported-git-read")

    def close(self):
        for client in self.clients:
            client.close()
        if self.scratch:
            self.scratch.cleanup()

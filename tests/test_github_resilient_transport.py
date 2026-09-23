import json

import httpx
import pytest

from boomearth.providers.github_skill import parse_github_skill_url
from boomearth.workbench.github_resilient_transport import ResilientGitHubTransport
from boomearth.workbench.github_skill_acquisition import GitHubSkillAcquisitionError

URL = "https://api.github.com/repos/acme/skills"


def transport(tmp_path, handlers, budget=288):
    def streaming(handler):
        def call(request):
            response = handler(request)
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=httpx.ByteStream(response.content))
        return call
    clients = [httpx.Client(transport=httpx.MockTransport(streaming(h))) for h in handlers]
    return ResilientGitHubTransport(parse_github_skill_url("https://github.com/acme/skills"),
                                   tmp_path, budget, clients=clients, sleep=lambda _: None)


def test_timeout_retries_then_switches_and_retains_successful_route(tmp_path):
    def down(request):
        raise httpx.ConnectTimeout("private proxy details must not leak")
    with_good = lambda request: httpx.Response(200, json={"ok": True})
    t = transport(tmp_path, [down, with_good])
    assert t.get_json(URL) == ({"ok": True}, 200)
    assert t.request_count == 4
    t.get_json(URL)
    assert t.request_count == 5
    log = (tmp_path / "github-acquisition-attempts.json").read_text()
    assert "ConnectTimeout" in log and "private proxy" not in log
    t.close()


def test_rate_limit_switches_without_burning_retries(tmp_path):
    t = transport(tmp_path, [lambda _: httpx.Response(403, json={"message": "rate limited"}),
                            lambda _: httpx.Response(200, json={"ok": True})])
    assert t.get_json(URL)[1] == 200
    assert t.request_count == 2
    assert t.diagnostics[0]["status"] == 403
    t.close()


def test_both_http_paths_fail_uses_git_and_does_not_invent_http_success(tmp_path, monkeypatch):
    t = transport(tmp_path, [lambda _: httpx.Response(404, json={})] * 2)
    calls = []
    def git_read(kind, url):
        calls.append((kind, url))
        return {"from": "git"}, 200
    monkeypatch.setattr(t, "_git_read", git_read)
    assert t.get_json(URL)[0] == {"from": "git"}
    assert calls == [("get_json", URL)]
    assert t.diagnostics[-1]["method"] == "git-read"
    t.close()


def test_exhausted_methods_never_return_partial_success(tmp_path, monkeypatch):
    t = transport(tmp_path, [lambda _: httpx.Response(404, json={})] * 2)
    def unavailable(*_):
        raise ValueError()
    monkeypatch.setattr(t, "_git_read", unavailable)
    with pytest.raises(GitHubSkillAcquisitionError, match="methods-exhausted"):
        t.get_json(URL)
    assert len(t.diagnostics) == 5
    t.close()


def test_budget_counts_every_physical_attempt(tmp_path):
    t = transport(tmp_path, [lambda _: httpx.Response(503, json={})] * 2, budget=2)
    with pytest.raises(GitHubSkillAcquisitionError, match="request-budget-exceeded"):
        t.get_json(URL)
    assert t.request_count == 2
    t.close()


def test_git_mid_snapshot_reads_the_pinned_commit(tmp_path, monkeypatch):
    t = transport(tmp_path, [lambda _: httpx.Response(404, json={})] * 2)
    t.commit = "a" * 40
    pinned = "b" * 40
    checked = []
    monkeypatch.setattr(t, "_ensure_commit", checked.append)
    commands = []
    def git(*args, **kwargs):
        commands.append(args)
        return b"3" if args[:2] == ("cat-file", "-s") else b"abc"
    monkeypatch.setattr(t, "_git", git)
    assert t.get_text(f"https://raw.githubusercontent.com/acme/skills/{pinned}/README.md") == (b"abc", 200)
    assert checked == [pinned]
    assert commands[-1] == ("cat-file", "blob", pinned + ":README.md")
    t.close()


def test_wrong_blob_hash_on_http_200_switches_to_verified_route(tmp_path):
    from boomearth.providers.github_skill import GitTreeEntry
    from boomearth.workbench.github_skill_acquisition import _git_blob_sha
    entry = GitTreeEntry("README.md", _git_blob_sha(b"good"), 4)
    t = transport(tmp_path, [
        lambda _: httpx.Response(200, content=b"evil", headers={"content-type": "text/plain"}),
        lambda _: httpx.Response(200, content=b"good", headers={"content-type": "text/plain"}),
    ])
    assert t.get_verified_text("https://raw.githubusercontent.com/acme/skills/" + "a" * 40 + "/README.md", entry) == (b"good", 200)
    assert t.request_count == 4
    t.close()
